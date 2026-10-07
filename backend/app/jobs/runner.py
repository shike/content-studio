"""进程内任务框架（docs/技术方案.md §7，P1 骨架升级）。

- submit(type, payload, dedup_key) 入库为 queued，投入 asyncio 队列；
  dedup_key 相同的活跃任务（queued/running/parked）只存在一个，重复提交幂等返回原任务
- 消费者按注册表分发：协程函数直接 await；同步函数丢线程池（ASR/FFmpeg 等 CPU 密集）
- 执行器通过 JobContext.set_progress 上报进度
- 失败按 errors.classify 分类：
  transient  → 按类型策略自动重试（指数退避，failed + next_retry_at，到点由重试循环放行）
  quota      → 任务转 parked 挂起（不算失败），30 分钟粒度到点重查，超出挂起上限才失败
  captcha    → 失败并打 fail_class=captcha（可人工重试，人工重试清零计数）
  deterministic（默认）→ 立即失败，永不自动重试
- 服务重启时：queued 自动重新入队（排队位不该因重启清零），running 标 failed；
  parked 不受影响（next_retry_at 持久化，重试循环到点照常放行）
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from ..auth import ACTOR
from ..db import engine
from ..models import FailurePattern, Job
from .errors import classify, fingerprint

Executor = Callable[["JobContext", dict], Any]
_EXECUTORS: dict[str, Executor] = {}
# 实体失败回退钩子（P6）：注册执行器时声明 on_fail(payload, exc)，失败时 runner 统一调用，
# 实体状态回退不再依赖各执行器自觉 try/except（幽灵行根治）
_ON_FAIL: dict[str, Callable[[dict, Exception], None]] = {}
# IO 密集的轻任务（扫描/识别）注册为并发执行：旁路串行队列立即跑，不排在 LLM 大任务后面
_CONCURRENT_TYPES: set[str] = set()
# 声明式欠任务（P2 收编）：类型 → 函数，返回 [(payload, dedup_key)]，
# 表示"这类实体应该有活跃任务但现在没有"；reaper 周期性检查并补队
_OWNED: dict[str, Callable[[], list[tuple[dict, str]]]] = {}


@dataclass
class RetryPolicy:
    """按任务类型的自动重试策略（策略一处定义，见 P1 方案）。"""

    max_retries: int = 0
    base: int = 60  # 首次退避秒数
    cap: int = 1800  # 退避封顶秒数
    classes: tuple = ("transient",)  # 参与自动重试的 fail_class
    park_backoff: int = 1800  # quota 挂起的重查间隔（秒）
    max_parks: int = 16  # quota 挂起次数上限（16×30min=8h，超过转失败）
    stuck_after: int = 7200  # running 超过此秒数无更新 → 看门狗判定卡死（transient，自动重试）
    task_timeout: Optional[int] = None  # 任务级硬超时（秒）：超时即失败重试，防单任务卡死唯一 heavy worker


# 策略注册表：LLM 类失败多为通道抖动/额度窗口，允许 1 次跨任务重试 + 挂起；
# 扫描类按原补丁语义（1h 后重试 ≤3 次）；确定性失败默认零重试。
# task_timeout：hard 单 worker，任何任务卡死=全队列停摆——按类型给硬上限（超时线程不中断，
# 会以僵尸形态跑完，但完成回调有状态护栏不再覆写结果）。
_POLICIES: dict[str, RetryPolicy] = {
    # 拆解含 captcha 自动重试（2026-10-07）：验证码是 IP 级惩罚窗（几十分钟），
    # 30min 指数退避 ≤2 次正好覆盖窗体——此前 classes 只有 transient，验证码失败直接终态纯靠人工
    "benchmark_analyze": RetryPolicy(max_retries=2, base=300, cap=1800,
                                     classes=("transient", "captcha"), task_timeout=2700),
    "script_generate": RetryPolicy(max_retries=1, base=300, task_timeout=2400),
    "script_polish": RetryPolicy(max_retries=1, base=300, task_timeout=2400),
    "script_finalize": RetryPolicy(max_retries=1, base=300, task_timeout=1800),
    "article_generate": RetryPolicy(max_retries=1, base=300, task_timeout=3600),
    "idea_research": RetryPolicy(max_retries=1, base=300, task_timeout=1800),
    "trending_topics": RetryPolicy(max_retries=1, base=300, task_timeout=900),
    "self_analyze": RetryPolicy(max_retries=1, base=300, cap=1800,
                                classes=("transient", "captcha"), task_timeout=2700),
    "self_profile_update": RetryPolicy(max_retries=1, base=300, task_timeout=1800),
    "watch_scan": RetryPolicy(max_retries=3, base=3600, cap=3600,
                              classes=("transient", "captcha"), task_timeout=3600),
    "self_scan": RetryPolicy(max_retries=3, base=3600, cap=3600, task_timeout=2700),
    "watch_resolve": RetryPolicy(max_retries=1, base=3600, cap=3600,
                                 classes=("transient", "captcha"), task_timeout=1800),  # Q19：风控退避拉长到 1 小时
    "radar_extract": RetryPolicy(max_retries=1, base=120, task_timeout=900),  # 话题提取风控瞬时失败
    "watch_discover": RetryPolicy(max_retries=2, base=3600, cap=3600,
                                  classes=("transient", "captcha"), task_timeout=3600),  # Q19：DDG/抖音风控统一 1 小时退避
    "avatar_video": RetryPolicy(max_retries=1, base=120, task_timeout=3600),
}
_DEFAULT_POLICY = RetryPolicy()


def policy_of(job_type: str) -> RetryPolicy:
    return _POLICIES.get(job_type, _DEFAULT_POLICY)


def _now_naive() -> datetime:
    """SQLite DATETIME 读回是 naive UTC：Python 侧所有比较/落库统一用 naive，避免 aware/naive 混比 TypeError。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def register_concurrent(job_type: str) -> None:
    _CONCURRENT_TYPES.add(job_type)


def register_owed(job_type: str, fn: Callable[[], list[tuple[dict, str]]]) -> None:
    _OWNED[job_type] = fn


def register_executor(job_type: str, on_fail: Optional[Callable[[dict, Exception], None]] = None):
    def deco(fn: Executor) -> Executor:
        _EXECUTORS[job_type] = fn
        if on_fail is not None:
            _ON_FAIL[job_type] = on_fail
        return fn
    return deco


def _lane_of(job_type: str) -> str:
    """分道：并发旁路注册的轻任务=light，其余 heavy（scheduled/external 分道在 P2/P3 落地）。"""
    return "light" if job_type in _CONCURRENT_TYPES else "heavy"


class JobContext:
    """执行器用它上报进度；失败时抛异常即可。"""

    def __init__(self, job_id: int) -> None:
        self.job_id = int(job_id)

    def set_progress(self, progress: int, message: str = "") -> None:
        with Session(engine) as s:
            job = s.get(Job, self.job_id)
            if job is None:
                return
            job.progress = max(0, min(100, int(progress)))
            if message:
                job.message = message
            # 心跳真实化（2026-10-07）：updated_at 必须随进度刷新——reaper 的 stuck 判定
            # 量的是"无更新时长"，updated_at 不动等于量"任务总寿命"，活跃长任务会被误杀
            job.updated_at = _now_naive()
            history = list(job.history or [])
            history.append({"p": job.progress, "m": message or job.message, "t": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            job.history = history[-80:]  # 防膨胀：只留最近 80 个阶段点
            s.add(job)
            s.commit()


async def recover_interrupted_jobs() -> None:
    """启动自愈：queued **原行**直接回内存队列（不新建任务行——旧行为"改 superseded 再 submit"
    因 dedup 必 miss 导致每次重启每任务膨胀一行、且新行断租户归属）；running 标失败并按
    transient 排自动重试。须在 runner.start() 之后调用。parked 不动。
    """
    requeued: list[tuple[int, int]] = []
    with Session(engine) as s:
        rows = s.exec(
            select(Job).where(Job.status.in_(["queued", "running"]))  # type: ignore[attr-defined]
        ).all()
        for job in rows:
            if job.status == "queued":
                # 原行入内存队列即可：worker 只认内存队列，DB 行保持 queued 语义不变
                requeued.append((job.id, job.tenant_id))
            else:
                # 重启中断不是任务的错：按 transient 排 90 秒后自动重试
                job.status = "failed"
                job.fail_class = "transient"
                job.error_fp = "infra:restart"
                job.next_retry_at = _now_naive() + timedelta(seconds=90)
                job.error = "服务重启中断，90 秒后自动重试"
            s.add(job)
        s.commit()
    for job_id, tenant_id in requeued:
        runner._put(tenant_id, job_id)


class JobRunner:
    # light 分道并发上限：扫描/识别类任务同时最多 3 个，防止几十个并发把抖音打出风控
    _LIGHT_SEM: Optional[asyncio.Semaphore] = None

    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[int]] = {}  # 租户(0=系统)→FIFO，heavy 按租户轮转
        self._rr_pos = 0
        self._started = False
        self._inflight: dict[str, int] = {}

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._queues.setdefault(0, asyncio.Queue())
        self._LIGHT_SEM = asyncio.Semaphore(3)
        self._spawn("worker", self._consume)
        self._spawn("retry", self._retry_loop)
        self._spawn("reaper", self._reaper_loop)
        self._spawn("scheduler", self._schedule_loop)

    async def _schedule_loop(self) -> None:
        """统一调度中心派发循环：每分钟巡检 schedules 表到期项（schedules.py 注册表）。"""
        from . import schedules

        schedules.ensure_states()
        print("[scheduler] 统一调度中心启动，已注册 "
              f"{len(schedules.REGISTRY)} 个周期任务")
        while True:
            try:
                await schedules.dispatch_due()
            except Exception as e:  # noqa: BLE001 循环不许死（另有监督器兜底）
                print(f"[scheduler] {type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(60)

    def _spawn(self, name: str, coro_factory) -> None:
        """后台循环监督：循环一旦退出（崩溃/意外返回）大声记日志并 5 秒后重启。

        队列消费/重试/巡逻任何一个静默死亡 = 全部任务永久滞留且无人报警
        （naive/aware TypeError 曾这样带病运行数周），监督器是最后一道防线。
        """
        def _on_done(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            detail = f"{type(exc).__name__}: {str(exc)[:160]}" if exc else "无异常返回"
            print(f"[supervisor] {name} 循环退出（{detail}），5 秒后重启")
            asyncio.get_running_loop().call_later(5.0, self._spawn, name, coro_factory)
        t = asyncio.create_task(coro_factory())
        t.add_done_callback(_on_done)

    async def submit(self, job_type: str, payload: Optional[dict] = None,
                     dedup_key: str = "", retry_of: int = 0,
                     actor: Optional[tuple[int, int, str]] = None,
                     points: int = 0) -> Job:
        """提交任务。dedup_key 相同的活跃任务（queued/running/parked）幂等返回原任务。

        归属：默认读 ACTOR contextvar（require_user 在请求上下文设值）；
        人工重试/复活传 actor=原任务归属；系统调度与自动重试为 (0,0,"")。
        points>0 且归属租户时在任务落库后预扣积分（幂等命中/合并跳过不扣，
        失败不退是既定口径）；余额闸在 API 层 require_points 把守。"""
        if not self._started:
            raise RuntimeError("JobRunner 未启动")
        from ..auth import ACTOR
        if actor is None:
            actor = ACTOR.get()
        tenant_id, user_id, _role = actor
        if dedup_key:
            hit: Optional[Job] = None
            with Session(engine) as s:
                existing = s.exec(
                    select(Job).where(
                        Job.type == job_type,  # type: ignore[attr-defined]
                        Job.dedup_key == dedup_key,  # type: ignore[attr-defined]
                        Job.status.in_(["queued", "running", "parked"]),  # type: ignore[attr-defined]
                    )
                ).first()
                if existing is not None:
                    # 脱管壳实例：会话关闭后调用方读 .id 不触发 refresh
                    hit = Job(id=existing.id, type=existing.type, status=existing.status,
                              dedup_key=existing.dedup_key, payload=dict(existing.payload or {}),
                              tenant_id=existing.tenant_id, user_id=existing.user_id)
            if hit is not None:
                return hit
        if tenant_id:
            _check_tenant_quota(tenant_id)
        policy = policy_of(job_type)
        with Session(engine) as s:
            job = Job(type=job_type, payload=payload or {}, dedup_key=dedup_key,
                      lane=_lane_of(job_type), max_retries=policy.max_retries,
                      retry_of=retry_of, tenant_id=tenant_id, user_id=user_id)
            s.add(job)
            s.commit()
            s.refresh(job)
        if job_type in _CONCURRENT_TYPES:
            if self._inflight.get(job_type):
                # 同类扫描幂等，执行中的时候后来者直接合并跳过
                merged = job.id
                skip_msg = "已有同类任务在执行，本次合并跳过（承接标记，非错误）"
                with Session(engine) as s:
                    j = s.get(Job, merged)
                    j.status = "superseded"
                    j.message = skip_msg
                    s.add(j)
                    s.commit()
                # 脱管壳实例：会话已关，调用方读 .id 不触发 refresh
                return Job(id=merged, type=job_type, status="superseded",
                           message=skip_msg)
            self._inflight[job_type] = job.id
            if points > 0 and tenant_id:
                _deduct(tenant_id, points, job.id, job_type)
            asyncio.create_task(self._run_light(job.id, job_type))
            return job
        if points > 0 and tenant_id:
            _deduct(tenant_id, points, job.id, job_type)
        self._put(tenant_id, job.id)
        return job

    async def _retry_loop(self) -> None:
        """到点放行：failed(带 next_retry_at) 与 parked 的任务到期后重新入队。"""
        while True:
            await asyncio.sleep(30)
            try:
                now = _now_naive()
                due_ids: list[int] = []
                with Session(engine) as s:
                    rows = s.exec(
                        select(Job).where(
                            Job.status.in_(["failed", "parked"]),  # type: ignore[attr-defined]
                            Job.next_retry_at is not None,  # type: ignore[attr-defined]
                        )  # type: ignore[attr-defined]
                    ).all()
                    for job in rows:
                        if job.next_retry_at and job.next_retry_at <= now:
                            due_ids.append(job.id)
                for job_id in due_ids:
                    await self._reenqueue(job_id)
            except Exception as e:  # noqa: BLE001 重试循环自身不许死
                print(f"[job-retry] {type(e).__name__}: {str(e)[:120]}")

    async def _reaper_loop(self) -> None:
        """reaper（P2 收编）：①看门狗——running 超过类型策略 stuck_after 无更新判卡死，
        按 transient 自动重试；②欠任务——周期执行各类型注册的 owed 声明，补队缺失的工作。
        取代原先散在 main.py 的卡死看门狗与拆解孤儿补偿两块补丁。
        """
        import os

        interval = max(5, int(os.environ.get("CS_REAPER_SEC", "1800")))
        await asyncio.sleep(min(interval, 15))  # 启动后先快速巡一轮
        while True:
            try:
                now = _now_naive()
                # ① 看门狗：按类型的 stuck_after 阈值清扫 running
                with Session(engine) as s:
                    running = s.exec(
                        select(Job).where(Job.status == "running")  # type: ignore[attr-defined]
                    ).all()
                    stuck_ids: list[tuple[int, int]] = []
                    for job in running:
                        stuck_after = policy_of(job.type).stuck_after
                        age = (now - (job.updated_at or job.created_at)).total_seconds()
                        if age > stuck_after:
                            stuck_ids.append((job.id, stuck_after))
                for job_id, stuck_after in stuck_ids:
                    with Session(engine) as s:
                        job = s.get(Job, job_id)
                        if job is None or job.status != "running":
                            continue
                        policy = policy_of(job.type)
                        job.status = "failed"
                        job.fail_class = "transient"
                        job.error_fp = "infra:watchdog"
                        job.retry_count = (job.retry_count or 0) + 1
                        # 重试次数上限（2026-10-07）：此前不查上限会无限 2h 轮重试
                        if policy.max_retries and job.retry_count <= policy.max_retries:
                            delay = min(policy.cap, policy.base) if policy.max_retries else 0
                            job.next_retry_at = now + timedelta(seconds=delay)
                            job.error = f"运行超时（超过 {stuck_after // 60} 分钟无更新），看门狗判定卡死，自动重试 {job.retry_count}/{policy.max_retries}"
                        else:
                            job.next_retry_at = None
                            job.error = f"运行超时（超过 {stuck_after // 60} 分钟无更新），已达重试上限，转终态失败（可人工重试）"
                        s.add(job)
                        s.commit()
                if stuck_ids:
                    print(f"[reaper] marked {len(stuck_ids)} stuck job(s): {[j for j, _ in stuck_ids]}")
                # ② 欠任务：各类型声明检查
                for job_type, fn in _OWNED.items():
                    try:
                        items = await asyncio.to_thread(fn)
                    except Exception as e:  # noqa: BLE001
                        print(f"[reaper] owed({job_type}) 检查失败: {type(e).__name__}: {str(e)[:120]}")
                        continue
                    for item in items:
                        payload, dedup_key = item[0], item[1]
                        actor = item[2] if len(item) > 2 else None
                        await self.submit(job_type, payload, dedup_key=dedup_key,
                                          actor=actor)
                    if items:
                        print(f"[reaper] owed({job_type}) 补队 {len(items)} 条")
                # 磁盘余量告警（写入/下载在空间不足时另有硬防御）
                import shutil
                from ..settings import settings as _settings
                free_gb = shutil.disk_usage(_settings.data_dir).free / 1e9
                if free_gb < 5:
                    print(f"[reaper] ⚠️ 磁盘剩余 {free_gb:.1f}GB，低于安全线（5GB）")
                # 数据卫生：7 天前的终态任务自动归档（证据保全：不物理删，列表默认不显示，
                # 错误指纹库的统计基础永不丢失）
                cutoff = _now_naive() - timedelta(days=7)
                with Session(engine) as s:
                    old_rows = s.exec(
                        select(Job).where(
                            Job.status.in_(["failed", "superseded", "succeeded"]),  # type: ignore[attr-defined]
                            Job.archived == 0,  # type: ignore[attr-defined]
                            Job.updated_at < cutoff,  # type: ignore[attr-defined]
                        )  # type: ignore[attr-defined]
                    ).all()
                    for j in old_rows:
                        j.archived = 1
                        s.add(j)
                    if old_rows:
                        s.commit()
                        print(f"[reaper] archived {len(old_rows)} terminal job(s) older than 7d")
            except Exception as e:  # noqa: BLE001 reaper 自身不许死
                print(f"[reaper] {type(e).__name__}: {str(e)[:120]}")
            await asyncio.sleep(interval)

    async def _reenqueue(self, job_id: int) -> None:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job is None or job.status not in ("failed", "parked"):
                return
            job_type = job.type
            # light 同类在跑：保持 failed+到期时间不动（下轮到点再查），先行置 queued 会让
            # 该行永远滞留（light 不进内存队列无人消费）且污染 dedup
            if job_type in _CONCURRENT_TYPES and self._inflight.get(job_type):
                return
            job.status = "queued"
            job.message = f"自动重试第 {job.retry_count + 1} 次"
            job.next_retry_at = None
            s.add(job)
            s.commit()
            payload = dict(job.payload or {})
            tenant_id = job.tenant_id
        from .revive import revive_entity
        try:
            revive_entity(job_type, payload)  # 重试放行前实体先回在途态（failed 不跨重试周期）
        except Exception as e:  # noqa: BLE001
            print(f"[job-retry] revive_entity({job_type}) 异常: {type(e).__name__}: {str(e)[:120]}")
        if job_type in _CONCURRENT_TYPES:
            self._inflight[job_type] = job_id
            asyncio.create_task(self._run_light(job_id, job_type))
        else:
            self._put(tenant_id, job_id)

    async def _run_light(self, job_id: int, job_type: str) -> None:
        """light 分道：并发池上限内执行（P3 正式化，替代无界并发）。"""
        async with self._LIGHT_SEM:
            try:
                await self._run_one(asyncio.get_running_loop(), job_id)
            except Exception as e:  # noqa: BLE001 执行器外异常（如 DB 抖动）：任务不得卡 running
                print(f"[job-run] {job_type}#{job_id} 执行器外异常: {type(e).__name__}: {str(e)[:120]}")
                with Session(engine) as s:
                    job = s.get(Job, job_id)
                    if job is not None and job.status == "running":
                        job.status = "failed"
                        job.fail_class = "transient"
                        job.error = f"执行器外异常: {scrub_paths(str(e))[:300]}"
                        s.add(job)
                        s.commit()
            finally:
                if self._inflight.get(job_type) == job_id:
                    self._inflight.pop(job_type, None)

    def _put(self, tenant_id: int, job_id: int) -> None:
        q = self._queues.get(tenant_id)
        if q is None:
            q = self._queues[tenant_id] = asyncio.Queue()
        q.put_nowait(job_id)

    async def _consume(self) -> None:
        """heavy 消费者：跨租户轮转（round-robin），租户内保持 FIFO。
        防止一个租户的重活把其他租户的任务堵在队尾。"""
        loop = asyncio.get_running_loop()
        while True:
            pending = [t for t, q in self._queues.items() if not q.empty()]
            if not pending:
                await asyncio.sleep(0.4)
                continue
            self._rr_pos = (self._rr_pos + 1) % max(1, len(pending))
            job_id = await self._queues[pending[self._rr_pos]].get()
            await self._run_one(loop, job_id)

    async def _run_one(self, loop: asyncio.AbstractEventLoop, job_id: int) -> None:
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = "running"
            job.updated_at = _now_naive()  # 拾起即心跳：stuck 从拾起时刻起算
            s.add(job)
            s.commit()
            executor = _EXECUTORS.get(job.type)
            job_type = job.type
            job_tenant, job_user = job.tenant_id, job.user_id
        # 执行期租户上下文：执行器内的查询/实体创建/LLM 台账都归属任务租户
        actor_token = ACTOR.set((job_tenant, job_user, ""))
        try:
            if executor is None:
                raise RuntimeError(f"未注册的任务类型: {job_type}")
            ctx = JobContext(job_id)
            if asyncio.iscoroutinefunction(executor):
                run_coro = executor(ctx, _payload_of(job_id))
            else:
                # 必须走 to_thread（拷贝 contextvars）：run_in_executor 会丢 ACTOR，
                # 同步执行器将退化成系统身份——租户查询不过滤、新实体/LLM 台账不归属
                run_coro = asyncio.to_thread(executor, ctx, _payload_of(job_id))
            # 任务级硬超时：唯一 heavy worker 被单任务卡死=全队列停摆（to_thread 线程
            # 不可取消，超时后线程以僵尸形态跑完，但完成回调有状态护栏不覆写结果）
            timeout = policy_of(job_type).task_timeout
            out = await asyncio.wait_for(run_coro, timeout=timeout)
            result = out if isinstance(out, dict) else {"value": out}
            with Session(engine) as s:
                job = s.get(Job, job_id)
                if job is None:
                    return
                if job.status != "running":
                    # 状态护栏：看门狗已把本任务判死重试（本执行是超时僵尸线程的迟到完成），
                    # 结果丢弃不覆写——否则重试行与僵尸行互相踩状态
                    print(f"[job-run] #{job_id} 迟到完成（状态已被改写为 {job.status}），结果丢弃")
                    return
                job.status = "succeeded"
                job.progress = 100
                job.result = result
                job.error = None
                job.next_retry_at = None
                job.updated_at = _now_naive()
                s.add(job)
                s.commit()
        except asyncio.TimeoutError:
            await self._handle_failure(
                job_id, job_type,
                TimeoutError(f"任务执行超过 {policy_of(job_type).task_timeout} 秒硬上限，强制失败重试"))
        except Exception as e:  # noqa: BLE001
            await self._handle_failure(job_id, job_type, e)
        finally:
            ACTOR.reset(actor_token)

    async def _handle_failure(self, job_id: int, job_type: str, e: Exception) -> None:
        """失败分类处置：transient 自动重试 / quota 挂起 / 其余立即失败。"""
        fc = classify(e)
        fp = fingerprint(e)
        policy = policy_of(job_type)
        now = _now_naive()
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            if job.status != "running":
                # 状态护栏：僵尸执行（超时后仍在跑的线程/协程）迟到失败，或看门狗已接手——
                # 不覆写现状态，否则与重试行互相踩踏
                print(f"[job-run] #{job_id} 迟到失败（状态已为 {job.status}），丢弃: {type(e).__name__}")
                return
            job.updated_at = now
            job.fail_class = fc
            job.error_fp = fp
            count = (job.retry_count or 0) + 1
            if fc == "quota":
                if count <= max(policy.max_parks, policy.max_retries):
                    job.status = "parked"
                    job.retry_count = count
                    job.next_retry_at = now + timedelta(seconds=policy.park_backoff)
                    job.error = scrub_paths(str(e))[:2000]
                    job.message = f"额度窗口挂起（第 {count} 次），{policy.park_backoff // 60} 分钟后自动重查"
                else:
                    job.status = "failed"
                    job.retry_count = count
                    job.next_retry_at = None
                    job.error = str(e)[:2000]
                    job.message = "额度窗口持续不可用，停止挂起（可人工重试）"
            elif fc in policy.classes and (job.retry_count or 0) < policy.max_retries:
                delay = min(policy.cap, policy.base * 2 ** (job.retry_count or 0))
                job.status = "failed"  # 失败但已排期：next_retry_at 到点由重试循环放行
                job.retry_count = count
                job.next_retry_at = now + timedelta(seconds=delay)
                job.error = str(e)[:2000]
                job.message = f"自动重试 {count}/{policy.max_retries}（{delay} 秒后）"
            else:
                job.status = "failed"
                job.next_retry_at = None
                job.error = str(e)[:2000]
                job.message = "失败"
            _upsert_failure_pattern(s, fp, fc, job_type, str(e), job_id)
            s.add(job)
            s.commit()
        # 实体状态回退钩子：失败时把关联实体拉回稳定态（不掩盖异常，只兜底）
        hook = _ON_FAIL.get(job_type)
        if hook is not None:
            try:
                hook(_payload_of(job_id), e)
            except Exception as he:  # noqa: BLE001 钩子故障不干扰失败处置
                print(f"[job-onfail] {job_type} 回退钩子异常: {type(he).__name__}: {str(he)[:120]}")

    def manual_retry_reset(self, job_id: int) -> None:
        """人工重试时清零自动重试状态（jobs API 的 retry 端点在复制提交前调用）。"""
        with Session(engine) as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.retry_count = 0
            job.next_retry_at = None
            job.fail_class = ""
            s.add(job)
            s.commit()


def _check_tenant_quota(tenant_id: int) -> None:
    """每租户活跃任务（queued/running/parked）上限，防单租户灌爆全局队列。"""
    if not tenant_id:
        return
    import os

    from fastapi import HTTPException
    from sqlmodel import func
    cap = int(os.environ.get("CS_MAX_ACTIVE_PER_TENANT", "30"))
    with Session(engine) as s:
        n = s.exec(select(func.count(Job.id)).where(  # type: ignore[attr-defined]
            Job.tenant_id == tenant_id,  # type: ignore[attr-defined]
            Job.status.in_(["queued", "running", "parked"]),  # type: ignore[attr-defined]
        )).one()
        if int(n[0] if isinstance(n, tuple) else n) >= cap:
            raise HTTPException(status_code=429,
                                detail=f"本租户活跃任务已达上限（{cap}），请等队列消化或清理失败任务后再提交")


def scrub_paths(text: str) -> str:
    """任务错误脱敏：绝对路径（仓库根/用户主目录）替换为 <server>。

    任务详情对租户可见——未捕获异常常带本机路径（/Users/xxx/...），
    泄露服务器目录结构与部署账号，属信息泄露。"""
    import re as _re

    from ..settings import settings as _settings

    out = str(text or "")
    for raw in (str(_settings.data_dir), str(_settings.data_dir.parent), str(Path.home())):
        if raw and raw not in ("/", "."):
            out = out.replace(raw, "<server>")
    return _re.sub(r"/(?:Users|home)/[\w.-]+", "<server>", out)


def _deduct(tenant_id: int, points: int, job_id: int, job_type: str) -> None:
    """预扣积分。记账失败不拦任务，但必须响亮报出来（except 静默=账目黑洞）。"""
    try:
        from ..credits import deduct
        deduct(tenant_id, points, reason=f"{job_type} 预扣积分", ref=str(job_id))
    except Exception as e:  # noqa: BLE001
        print(f"[credits] 预扣失败 job#{job_id} {points} 分: {type(e).__name__}: {e}")


def _payload_of(job_id: int) -> dict:
    with Session(engine) as s:
        job = s.get(Job, job_id)
        return dict(job.payload) if job and job.payload else {}


def _upsert_failure_pattern(s: Session, fp: str, fc: str, job_type: str,
                            error: str, job_id: int) -> None:
    """失败 → 错误指纹库 upsert：首次建档（open=待处置），重复累计次数并刷新样本。"""
    from .errors import fingerprint_label
    row = s.exec(select(FailurePattern).where(FailurePattern.fp == fp)).first()
    if row is None:
        s.add(FailurePattern(fp=fp, kind="job", fail_class=fc,
                             label=fingerprint_label(fp), count=1,
                             sample_error=error[:2000], sample_job_id=job_id,
                             last_type=job_type))
    else:
        row.count += 1
        row.fail_class = fc
        row.last_type = job_type
        row.sample_error = error[:2000]
        row.sample_job_id = job_id
        if row.status == "fixed":
            row.status = "open"  # 修过的坑又出现 → 自动回到待处置
        s.add(row)


runner = JobRunner()
