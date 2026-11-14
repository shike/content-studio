"""同行拆解流水线：下载（在线）→ ASR 转写 → LLM 拆解 → 选题候选沉淀。"""
from __future__ import annotations

import asyncio
import json
import os

from sqlmodel import Session

from ..asr import transcribe
from ..db import engine
from ..settings import settings
from ..jobs.runner import _DEAD_ERROR_MARKERS, JobContext, register_executor, register_owed, runner
from ..llm import gateway
from ..models import BenchmarkVideo, Topic
from ..prompts import render
from ..jobs.errors import CaptchaError
from . import downloader

import time as _time

_CAPTCHA_FAILS: list[float] = []
_LAST_DOWNLOAD_TS: float = 0.0
_CAPTCHA_WINDOW = 1800.0
_CAPTCHA_TRIP = 2


def _captcha_record() -> None:
    now = _time.time()
    _CAPTCHA_FAILS[:] = [t for t in _CAPTCHA_FAILS if now - t < _CAPTCHA_WINDOW]
    _CAPTCHA_FAILS.append(now)


def _captcha_breaker() -> None:
    now = _time.time()
    _CAPTCHA_FAILS[:] = [t for t in _CAPTCHA_FAILS if now - t < _CAPTCHA_WINDOW]
    if len(_CAPTCHA_FAILS) >= _CAPTCHA_TRIP:
        wait_min = max(1, int((max(_CAPTCHA_FAILS) + _CAPTCHA_WINDOW - now) // 60))
        raise CaptchaError(
            f"下载熔断：抖音风控窗中（近期 {len(_CAPTCHA_FAILS)} 次验证码失败），"
            f"约 {wait_min} 分钟后自动重试")


def _download_wait_s() -> float:
    """当前还需等待的节流秒数（0=可立即下载）。供进度回显用。"""
    _min_gap = float(os.environ.get("CS_DOWNLOAD_MIN_INTERVAL", "45"))
    now_ts = _time.time()
    if _LAST_DOWNLOAD_TS and now_ts - _LAST_DOWNLOAD_TS < _min_gap:
        return _min_gap - (now_ts - _LAST_DOWNLOAD_TS)
    return 0.0


def _throttle_download() -> None:
    """全局下载节流：两次下载最小间隔（默认 45s，CS_DOWNLOAD_MIN_INTERVAL 可调）——
    批量连续高频下载会触发抖音 IP 级验证码（家宽出口 IP 被 punishing）。
    同步阻塞版（配 asyncio.to_thread 用）。2026-11-13 从 benchmark_analyze 内联
    提取为公共闸：self_analyze 的自有下载此前绕过此闸与熔断，统一收编。"""
    global _LAST_DOWNLOAD_TS
    wait_s = _download_wait_s()
    if wait_s > 0:
        _time.sleep(wait_s)
    _LAST_DOWNLOAD_TS = _time.time()


_BENCHMARK_DIR = settings.data_dir / "benchmarks"


def _heat_score(stats: dict) -> float:
    """热度分（2026-11-11 用户定版指标：评论为主、赞为辅的透明公式）：
    评论/2 + 赞/60，封顶 10。播放数匿名通道拿不到（实测恒 0），不入公式。"""
    comment = stats.get("comment") or 0
    digg = stats.get("digg") or 0
    return round(min(10.0, comment / 2 + digg / 60), 1)


def _gate_verdict(stats: dict) -> tuple[bool, str]:
    """拆解闸（2026-11-11 用户定版：评论≥20 或 赞≥300，任一即拆）。

    评论未知时只按赞线判，理由如实注明。线走设置页
    dissect_comment_min / dissect_digg_min，可运行时调。"""
    from ..settings import settings

    c_min, d_min = settings.dissect_comment_min, settings.dissect_digg_min
    comment = stats.get("comment")
    digg = stats.get("digg") or 0
    c_txt = f"评论{comment}" if comment is not None else "评论?"
    if comment is not None and comment >= c_min:
        return True, f"{c_txt}≥{c_min}"
    if digg >= d_min:
        return True, f"赞{digg}≥{d_min}"
    return False, f"{c_txt}/{digg}赞 未达线（评论≥{c_min}或赞≥{d_min}）"


async def auto_dissect_rows(rows: list, source_tag: str) -> int:
    """自动拆解，质量闸回归（2026-11-11 用户定版：评论≥20或赞≥300 才拆）。

    11-08 曾定版「监控内容全拆无闸」，7 天灌了 222 篇低赞拆解（中位数 ~30 赞，
    0 赞也拆）+ 58 篇垃圾选题，实测次日撤销：机器在给垃圾抛光。
    流程：评论缺失先 detail 探测补全（每条新视频一次页开）→ 闸判定 →
    过线批准排队拆解；不过线标 ignored 保留行（页面上可见，手动批准可强制拆）。
    返回本次批准条数。"""
    from .downloader import probe_video_stats

    n = 0
    for r in rows:
        passed, why = False, ""
        with Session(engine) as s:
            row = s.get(BenchmarkVideo, r.id)
            if row is None or row.scan_status != "pending":
                continue  # 已被处理/重复触发
            stats = dict(row.stats or {})
            if stats.get("comment") is None and row.source == "douyin" and row.url:
                probed = await asyncio.to_thread(probe_video_stats, row.url)
                if probed:
                    stats.update(probed)
                    row.stats = stats
            passed, why = _gate_verdict(stats)
            row.quality_score = _heat_score(stats)
            if passed:
                row.scan_status = "approved"
                row.notes = f"自动拆解（{source_tag}：{why}）"
            else:
                row.scan_status = "ignored"
                row.notes = f"低质未拆（{source_tag}：{why}）；手动批准可强制拆解"
            s.add(row)
            s.commit()
        if passed:
            await runner.submit("benchmark_analyze", {"benchmark_id": r.id},
                                dedup_key=f"bm:{r.id}")
            n += 1
    return n


def cleanup_completed_media() -> int:
    """媒体卫生（磁盘防线）：删除已完成/已忽略拆解的媒体文件（即用即弃，重拆解重新下载）。

    排除仍有活跃任务的行（拆解进行中的媒体不能删）；执行器成功路径已即时删除，
    这里兜底清理失败残留与历史存量。2026-11-13 扩展：SelfVideo 媒体此前只增不减
    （self_analyze 成功路径不删、media_cleanup 只清 BenchmarkVideo），一并收编。"""
    import os
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    from ..models import Job, SelfVideo
    from sqlmodel import select as _select
    deleted = 0
    with Session(engine) as s:
        active = {
            (j.payload or {}).get("benchmark_id")
            for j in s.exec(
                _select(Job).where(
                    Job.type == "benchmark_analyze",  # type: ignore[attr-defined]
                    Job.status.in_(["queued", "running"]),  # type: ignore[attr-defined]
                )  # type: ignore[attr-defined]
            ).all()
        }
        rows = s.exec(_select(BenchmarkVideo).where(
            BenchmarkVideo.media_path != "",  # type: ignore[arg-type]
        )).all()
        for b in rows:
            if b.id in active:
                continue  # 拆解进行中，媒体在用
            done = (b.analysis or "{}") != "{}" or b.scan_status == "ignored"
            if not done:
                continue  # 未拆解：媒体留给下次分析
            try:
                if b.media_path and os.path.exists(b.media_path):
                    os.remove(b.media_path)
                b.media_path = ""
                s.add(b)
                deleted += 1
            except OSError:
                continue

        # SelfVideo：已分析完的媒体即用即弃；下载后 24h 仍未分析完且无活跃任务
        # 的行按死残留清（转写空等终态失败行不会自己恢复）
        active_self = {
            (j.payload or {}).get("video_id")
            for j in s.exec(
                _select(Job).where(
                    Job.type == "self_analyze",  # type: ignore[attr-defined]
                    Job.status.in_(["queued", "running"]),  # type: ignore[attr-defined]
                )  # type: ignore[attr-defined]
            ).all()
        }
        stale_line = _dt.now(_tz.utc).replace(tzinfo=None) - _td(hours=24)
        for v in s.exec(_select(SelfVideo).where(
                SelfVideo.media_path != "",  # type: ignore[arg-type]
        )).all():
            if v.id in active_self:
                continue
            done = v.analyzed_at is not None or (
                not (v.transcript or "").strip() and v.created_at is not None
                and v.created_at < stale_line)
            if not done:
                continue
            try:
                if v.media_path and os.path.exists(v.media_path):
                    os.remove(v.media_path)
                v.media_path = ""
                s.add(v)
                deleted += 1
            except OSError:
                continue
        s.commit()
    return deleted


def owed_benchmarks() -> list[tuple[dict, str]]:
    """欠任务声明（P2 reaper 用）：approved 但 analysis 为空的拆解应有活跃任务。

    6 小时失败退避保留在声明里：最近失败过的不补——确定性失败（如 ASR 词表越界）
    补队也是白烧。内容已死的不补（永久排除，不受 6 小时窗限制）：「匿名不可见/已删除/转写空」是
    永不可成的终态，任务侧已当场归档；但这里扫的是实体状态（approved+analysis 空），
    不认识死标记就会每过冷却窗把同一条死视频复活一次（2026-11-10 生产实证：5 条
    死视频 × 每 6.5h 一轮 × 3 轮 = 15 条重复失败）。手动重试入口不受影响。
    """
    from datetime import datetime, timedelta

    from sqlmodel import Session, select

    from ..db import engine
    from ..models import BenchmarkVideo, Job

    with Session(engine) as s:
        approved = s.exec(
            select(BenchmarkVideo).where(
                BenchmarkVideo.scan_status == "approved",  # type: ignore[attr-defined]
                BenchmarkVideo.analysis == "{}",  # type: ignore[arg-type]
            )  # type: ignore[attr-defined]
        ).all()
        active = {
            (j.payload or {}).get("benchmark_id")
            for j in s.exec(
                select(Job).where(
                    Job.type == "benchmark_analyze",  # type: ignore[attr-defined]
                    Job.status.in_(["queued", "running"]),  # type: ignore[attr-defined]
                )  # type: ignore[attr-defined]
            ).all()
        }
        cool = datetime.utcnow() - timedelta(hours=6)
        recent_failed = {
            (j.payload or {}).get("benchmark_id")
            for j in s.exec(
                select(Job).where(
                    Job.type == "benchmark_analyze",  # type: ignore[attr-defined]
                    Job.status == "failed",  # type: ignore[attr-defined]
                    Job.updated_at > cool,  # type: ignore[attr-defined]
                )  # type: ignore[attr-defined]
            ).all()
        }
        dead_ids = {
            (j.payload or {}).get("benchmark_id")
            for j in s.exec(
                select(Job).where(
                    Job.type == "benchmark_analyze",  # type: ignore[attr-defined]
                    Job.status == "failed",  # type: ignore[attr-defined]
                )  # type: ignore[attr-defined]
            ).all()
            if any(m in (j.error or "") for m in _DEAD_ERROR_MARKERS)
        }
        out = []
        for b in approved:
            if b.id in active or b.id in recent_failed or b.id in dead_ids:
                continue
            # 第三位=归属（reaper 系统上下文无 ACTOR，须显式带视频租户）
            out.append(({"benchmark_id": b.id}, f"bm:{b.id}", (b.tenant_id, 0, "")))
        return out


register_owed("benchmark_analyze", owed_benchmarks)


@register_executor("benchmark_analyze")
async def benchmark_analyze(ctx: JobContext, payload: dict) -> dict:
    benchmark_id = payload["benchmark_id"]
    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        if b is None:
            raise RuntimeError(f"benchmark {benchmark_id} 不存在")
        url, source, media_path = b.url, b.source, b.media_path
        stats = b.stats or {}
        tenant_id = b.tenant_id
        from ..tenant_brand import brand_of
        from ..topics.service import is_similar_title, recent_topic_titles

        persona_text = brand_of(tenant_id)["persona"]
        b_row = s.get(BenchmarkVideo, benchmark_id)
        account_type = (b_row.account_type if b_row is not None else "") or "persona"

    if not media_path:
        if source != "douyin" or not url:
            raise RuntimeError("本地原文件已按「拆解完即删」策略清理，请重新上传后再拆解")
        _captcha_breaker()  # 下载熔断：风控窗中不再撞墙（快速失败进 30min 退避重试）
        # 全局下载节流：两次下载最小间隔（默认 45s）——批量拆解连续高频下载会触发抖音
        # IP 级验证码（家宽出口 IP 被 punishing）；夜间低密度批次全部成功证明低频可过
        wait_s = _download_wait_s()
        if wait_s > 0:
            ctx.set_progress(8, f"下载节流：等待 {int(wait_s)} 秒（防触发抖音风控）")
        await asyncio.to_thread(_throttle_download)
        ctx.set_progress(10, "打开视频页获取无水印直链")
        try:
            media_path = await asyncio.to_thread(downloader.download_video, url, _BENCHMARK_DIR)
        except Exception as e:
            if "验证码" in str(e):
                _captcha_record()
                raise CaptchaError(f"自动下载失败（抖音风控验证码）：{str(e)[:120]}") from e
            raise
        with Session(engine) as s:
            b = s.get(BenchmarkVideo, benchmark_id)
            b.media_path = media_path
            s.add(b)
            s.commit()

    ctx.set_progress(45, f"ASR 转写中（{settings.asr_model}，首次运行含模型下载）")
    try:  # 内存防线：available 低于水位时显式告警（3.6G 机器 large-v3 转写曾把系统拖入 swap 颠簸）
        import shutil as _sh
        with open("/proc/meminfo") as _mi:
            avail_mb = next(int(l.split()[1]) // 1024 for l in _mi if l.startswith("MemAvailable"))
        if avail_mb < 800:
            ctx.set_progress(46, f"⚠️ 可用内存仅 {avail_mb}MB，转写将变慢——建议 ASR 切 medium 或升级内存")
            print(f"[asr] ⚠️ 转写前可用内存仅 {avail_mb}MB（水位 800MB），存在 swap 颠簸风险")
    except Exception:  # noqa: BLE001 非 Linux/读取失败则跳过，只防线不阻断
        pass
    from ..tenant_brand import asr_vocab_of

    # tenant_id 必须用首会话捕获的变量：下载分支里 b 被第二会话重取并 commit（expire_on_commit
    # 使属性过期），会话关闭后再访问 b.tenant_id 会报"实例未绑定 Session"——
    # b_tenant_id/脱管两连击都出在 ASR 这一行（2026-10-02）
    transcript = await asyncio.to_thread(transcribe, media_path, asr_vocab_of(tenant_id))
    if not transcript.strip():
        # 无口播内容：自动忽略（Q19 用户裁定不进待处置），任务正常终结并写明原因
        with Session(engine) as s:
            b = s.get(BenchmarkVideo, benchmark_id)
            if b is not None:
                b.scan_status = "ignored"
                if b.media_path:
                    try:
                        os.remove(b.media_path)
                    except OSError:
                        pass
                    b.media_path = ""
                s.add(b)
                s.commit()
        ctx.set_progress(100, "无口播内容，已自动忽略")
        return {"benchmark_id": benchmark_id, "auto_ignored": True,
                "reason": "转写为空：视频没有可识别的中文语音"}
    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        b.transcript = transcript
        s.add(b)
        s.commit()

    ctx.set_progress(75, "LLM 拆解中")
    llm_input = transcript[:6000]
    if stats:
        # R3.4b 数据归因：把赞/评/转带给 LLM，分析"为什么这条（没）爆"
        llm_input += f"\n\n互动数据（做数据归因用）：{json.dumps(stats, ensure_ascii=False)}"
    from ..tenant_brand import audience_note_of
    # 双模式判定式拆解（2026-10-28 两类号策略修正）：类型是内容本身的属性，
    # 拆解时由 LLM 判定（content_type），账号类型只作先验记录——判定结果回写样本行
    analysis = await gateway.complete_json(
        [{"role": "system", "content": render(
            "benchmark_analyze", PERSONA=persona_text,
            # 2026-11-13 修复：模板里的 {{AUDIENCE_NOTE}} 此前无人传，占位符字面量
            # 原样进了每一条拆解的 system prompt
            AUDIENCE_NOTE=audience_note_of(tenant_id),
            RECENT_TITLES="\n".join(f"- {t}" for t in recent_topic_titles(
                14, line=account_type if account_type in ("persona", "leadgen") else ""))
            or "（近 14 天无出题记录）")},
         {"role": "user", "content": llm_input}],
        purpose="benchmark_analyze", max_tokens=5000)

    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        b.analysis = analysis
        judged = str(analysis.get("content_type") or "").strip()
        if judged in ("persona", "leadgen") and judged != b.account_type:
            b.account_type = judged  # LLM 判定覆盖先验（账号类型只是初始猜测）
        if not b.title and analysis.get("summary"):
            b.title = analysis["summary"][:80]
        s.add(b)
        # 沉淀选题候选（source=benchmark，draft 状态待人工定审）；标题撞车直接不入库——
        # 不同同行视频产出同一角度是选题趋同的主通道（2026-10-10 用户裁定拦截）
        candidate = (analysis.get("topic_candidate") or {})
        topic_id = None
        judged = (analysis.get("content_type") or account_type) or ""
        dup_of = (is_similar_title(str(candidate.get("title") or ""),
                                   line=judged if judged in ("persona", "leadgen") else "")
                  if candidate.get("title") else None)
        if dup_of is not None:
            analysis["topic_candidate_suppressed"] = f"与近期选题 #{dup_of} 撞车，候选未入库"
        if candidate.get("title") and dup_of is None:
            audience = candidate.get("audience")
            judged_line = "leadgen" if judged == "leadgen" else "persona"
            topic = Topic(
                title=str(candidate["title"])[:60],
                angle=candidate.get("angle", ""),
                audience=audience if audience in ("boss", "fde", "both") else "both",
                source_type="benchmark",
                source_ref=f"benchmark:{benchmark_id}",
                line=judged_line,  # 三库独立：按判定分流（2026-11-02）
                score=_safe_score(analysis.get("score")),
                evidence={
                    "summary": analysis.get("summary", ""),
                    "replicable_points": analysis.get("replicable_points") or [],
                    "hook": analysis.get("hook") or {},
                    "account_type": analysis.get("content_type") or account_type,
                    "benchmark_quality_score": b_row.quality_score if b_row is not None else 0,
                },
            )
            if (topic.score or 0) < 7.0:
                topic.status = "rejected"  # 自动拒：低于 7.0 不进待审队列
            s.add(topic)
            s.flush()  # 拿 id 即可（commit+refresh 在 WAL 并发下偶发 refresh 找不到行——错误类消灭）
            from ..topics.service import mark_similar
            mark_similar(topic.title, topic.id, s, line=judged_line)
            s.commit()
            topic_id = topic.id
        else:
            s.commit()

    # 媒体即用即弃（磁盘防线）：拆解成功后删除视频文件——转写/拆解已入库，
    # 重拆解会重新下载；失败不删（重试还要用）
    try:
        if media_path and os.path.exists(media_path):
            await asyncio.to_thread(os.remove, media_path)
        with Session(engine) as s:
            b = s.get(BenchmarkVideo, benchmark_id)
            if b is not None and b.media_path:
                b.media_path = ""
                s.add(b)
                s.commit()
    except Exception as e:  # noqa: BLE001 清理失败不影响拆解成果，reaper 巡检兜底
        print(f"[benchmark] 拆解后媒体清理失败（reaper 会兜底）: {type(e).__name__}: {str(e)[:100]}")

    ctx.set_progress(100, "完成")
    return {"benchmark_id": benchmark_id, "topic_id": topic_id,
            "transcript_chars": len(transcript)}


def _safe_score(value) -> float | None:
    try:
        return max(0.0, min(10.0, float(value)))
    except (TypeError, ValueError):
        return None
