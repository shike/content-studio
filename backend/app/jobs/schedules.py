"""统一调度中心：所有周期任务一处注册、一处派发、一处可见可管。

- 注册表（REGISTRY）是代码事实：key/默认节奏/执行体；
- DB `schedules` 表只存运行态：enabled/钟点覆盖/上次/下次/最近状态；
- `_schedule_loop`（runner 内受监督的循环）每分钟巡检到期项并执行。
此前每个周期任务各写一段硬编码循环（main 调度器/reaper 体内）——半吊子的根源。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from sqlmodel import Session, select

from ..db import engine
from ..models import ScheduleState
from ..settings import settings


@dataclass
class Schedule:
    key: str
    label: str
    run: Callable[[], Awaitable[str]]  # 执行体，返回一句话结果（提交任务或直接干活）
    daily_hour: Optional[int] = None       # 本地时区每日定点
    interval_hours: Optional[float] = None  # 或间隔式（小时）
    hour_from_settings: bool = False       # 钟点取 settings.watch_scan_hour（三个扫描类）
    enabled_by: str = ""                   # settings 开关字段名（空=注册表常开）


REGISTRY: list[Schedule] = []


def register_schedule(sch: Schedule) -> None:
    REGISTRY.append(sch)


def _by_key(key: str) -> Optional[Schedule]:
    return next((s for s in REGISTRY if s.key == key), None)


def effective_enabled(state: ScheduleState) -> bool:
    if not state.enabled:
        return False
    sch = _by_key(state.key)
    if sch and sch.enabled_by:
        return bool(getattr(settings, sch.enabled_by, True))
    return True


def effective_hour(state: ScheduleState) -> Optional[int]:
    sch = _by_key(state.key)
    if state.hour is not None:
        return state.hour
    if sch and sch.hour_from_settings:
        return settings.watch_scan_hour % 24
    return sch.daily_hour if sch else None


def _next_run(state: ScheduleState, now_utc: datetime) -> datetime:
    """下次运行时间。daily → 本地时区下一个定点；interval → now + N 小时。"""
    sch = _by_key(state.key)
    h = effective_hour(state)
    if sch and (sch.daily_hour is not None or sch.hour_from_settings):
        local_now = datetime.now().astimezone()
        target = local_now.replace(hour=(h or 0) % 24, minute=0, second=0, microsecond=0)
        if target <= local_now:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc).replace(tzinfo=None)
    interval = (sch.interval_hours if sch else None) or 24
    return now_utc + timedelta(hours=interval)


def ensure_states() -> None:
    """启动时对齐：注册表新增的 key 建行，注册表已删的 key 清行。"""
    with Session(engine) as s:
        existing = {r.key: r for r in s.exec(select(ScheduleState)).all()}
        for sch in REGISTRY:
            if sch.key not in existing:
                s.add(ScheduleState(key=sch.key))
        for key, row in existing.items():
            if _by_key(key) is None:
                s.delete(row)
        s.commit()


def _set_result(key: str, status: str) -> None:
    with Session(engine) as s:
        st = s.exec(select(ScheduleState).where(ScheduleState.key == key)).first()
        if st is not None:
            st.last_status = status[:200]
            s.add(st)
            s.commit()


async def _execute(key: str) -> str:
    sch = _by_key(key)
    if sch is None:
        raise RuntimeError(f"未知调度项 {key}")
    return (await sch.run()) or "完成"


async def dispatch_due() -> list[str]:
    """巡检到期项并执行。返回本轮执行过的 key。"""
    ran: list[str] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        for st in s.exec(select(ScheduleState)).all():
            sch = _by_key(st.key)
            if sch is None or not effective_enabled(st):
                continue
            if st.next_run_at is None:
                st.next_run_at = _next_run(st, now)  # 首次：只排程不立即执行
                s.add(st)
            elif now >= st.next_run_at:
                ran.append(st.key)
                st.last_run_at = now
                st.next_run_at = _next_run(st, now)
                st.run_count = (st.run_count or 0) + 1
                s.add(st)
        s.commit()
    for key in ran:
        try:
            msg = await _execute(key)
            _set_result(key, f"ok：{msg}")
        except Exception as e:  # noqa: BLE001 单项失败不影响其他调度项
            # 失败不再静默等明天：首次失败 2 小时后补跑一次（给 job 层的 1h 退避重试让路），
            # 连续失败才回落常规节奏——此前定时任务失败零重试，当天彻底丢失
            retry_at: Optional[datetime] = None
            with Session(engine) as s2:
                st = s2.exec(select(ScheduleState).where(ScheduleState.key == key)).first()
                if st is not None:
                    prev_failed = (st.last_status or "").startswith("失败")
                    if not prev_failed:
                        retry_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
                        st.next_run_at = retry_at
                    else:
                        st.next_run_at = _next_run(st, datetime.now(timezone.utc).replace(tzinfo=None))
                    s2.add(st)
                    s2.commit()
            when = "2 小时后补跑一次" if retry_at else "按常规节奏重试"
            _set_result(key, f"失败：{type(e).__name__}: {str(e)[:140]}（{when}）")
            print(f"[scheduler] {key} 执行失败: {type(e).__name__}: {str(e)[:140]}（{when}）")
    return ran


# ---------- 内置周期任务注册 ----------

async def _run_watch_scan() -> str:
    from .runner import runner
    job = await runner.submit("watch_scan", {"auto": True},
                              dedup_key=f"1:scan:all:{datetime.now():%Y%m%d}")
    return f"job #{job.id}"


async def _run_self_scan() -> str:
    from .runner import runner
    job = await runner.submit("self_scan", {"auto": True},
                              dedup_key=f"1:selfscan:all:{datetime.now():%Y%m%d}")
    return f"job #{job.id}"


async def _run_topic_radar() -> str:
    from .runner import runner
    job = await runner.submit("topic_radar", {"auto": True},
                              dedup_key=f"1:radar:{datetime.now():%Y%m%d}")
    return f"job #{job.id}"


async def _run_daily_backup() -> str:
    from ..db import backup_db
    return f"快照 {backup_db('daily').name}"


async def _run_media_cleanup() -> str:
    from ..benchmarks.service import cleanup_completed_media
    n = await asyncio.to_thread(cleanup_completed_media)
    return f"清理 {n} 个媒体文件"


register_schedule(Schedule(key="watch_scan", label="同行扫描（全部监控账号）",
                           daily_hour=1, hour_from_settings=True,
                           enabled_by="watch_scan_enabled", run=_run_watch_scan))
async def _run_watch_discover() -> str:
    from .runner import runner

    # 关键词选择统一收口 watch.pick_discover_keyword：设置页配置 > 对标圈动态提炼 > 静态池
    from ..watch.service import pick_discover_keyword

    keyword = await pick_discover_keyword()
    job = await runner.submit("watch_discover", {"keyword": keyword, "auto": True},
                              dedup_key=f"1:discover:{datetime.now():%Y%m%d}",
                              actor=(1, 0, "platform_admin"))
    return f"job #{job.id}（今日关键词：{keyword}）"


# 钟点 2 点与扫描错峰（不可 hour_from_settings：全局 watch_scan_hour 恒有值会把它永久
# 覆盖成 1 点——2026-10-06 四项任务挤在 1 点、发现任务"每日 2 点"设计失效的根因）
register_schedule(Schedule(key="watch_discover_daily", label="同行发现（每日清单外高赞同行）",
                           daily_hour=2,
                           enabled_by="watch_scan_enabled", run=_run_watch_discover))
register_schedule(Schedule(key="self_scan", label="自我扫描（我的账号）",
                           daily_hour=1, hour_from_settings=True,
                           enabled_by="watch_scan_enabled", run=_run_self_scan))
register_schedule(Schedule(key="topic_radar", label="话题雷达巡检",
                           daily_hour=1, hour_from_settings=True, run=_run_topic_radar))
async def _run_trending_daily() -> str:
    from .runner import runner
    # 与 API 端点同款 dedup_key（部分唯一索引只挡活跃状态，跨天可重复）
    job = await runner.submit("trending_topics", {"tenant_id": 1},
                              dedup_key="1:trending_topics:daily",
                              actor=(1, 0, "platform_admin"))
    return f"job #{job.id}"


register_schedule(Schedule(key="trending_daily", label="今日热点提炼（每日 7 点·示例租户）",
                           daily_hour=7, run=_run_trending_daily))
register_schedule(Schedule(key="daily_backup", label="业务库每日备份",
                           daily_hour=4, run=_run_daily_backup))
register_schedule(Schedule(key="media_cleanup", label="拆解媒体清扫",
                           interval_hours=6, run=_run_media_cleanup))
def _run_crawl_probe() -> Awaitable[str]:
    async def _go():
        from ..crawler_probe import CN_LABEL, probe

        r = await probe()
        return f"采集探针：{CN_LABEL.get(r['status'], r['status'])}"
    return _go()


register_schedule(Schedule(key="crawl_probe", label="抖音采集探针（15 分钟）",
                           interval_hours=0.25, run=_run_crawl_probe))
