"""同行拆解流水线：下载（在线）→ ASR 转写 → LLM 拆解 → 选题候选沉淀。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from sqlmodel import Session

from ..asr import transcribe
from ..db import engine
from ..jobs.runner import JobContext, register_executor, register_owed
from ..llm import gateway
from ..models import BenchmarkVideo, Topic
from ..prompts import load, render
from ..settings import settings
from ..jobs.errors import CaptchaError
from . import downloader


# 下载熔断（2026-10-07）：批量拆解连续下载会持续触发验证码（家宽出口 IP 高频抓取），
# 队列里后来的任务会继续一头撞墙。30 分钟内 ≥2 次 captcha 失败 → 后续下载直接快速失败
# 进 30min 退避重试，让 IP 惩罚窗自然冷却。
import time as _time

_CAPTCHA_FAILS: list[float] = []
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

_BENCHMARK_DIR = settings.data_dir / "benchmarks"


def cleanup_completed_media() -> int:
    """媒体卫生（磁盘防线）：删除已完成/已忽略拆解的媒体文件（即用即弃，重拆解重新下载）。

    排除仍有活跃任务的行（拆解进行中的媒体不能删）；执行器成功路径已即时删除，
    这里兜底清理失败残留与历史存量。"""
    import os

    from ..models import Job
    from sqlmodel import select as _select
    deleted = 0
    with Session(engine) as s:
        active = {
            (j.payload or {}).get("benchmark_id")
            for j in s.exec(
                _select(Job).where(
                    Job.type == "benchmark_analyze",  # type: ignore[attr-defined]
                    Job.status.in_(["queued", "running", "parked"]),  # type: ignore[attr-defined]
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
        s.commit()
    return deleted


def owed_benchmarks() -> list[tuple[dict, str]]:
    """欠任务声明（P2 reaper 用）：approved 但 analysis 为空的拆解应有活跃任务。

    6 小时失败退避保留在声明里：最近失败过的不补——确定性失败（如 ASR 词表越界）
    补队也是白烧；in_flight 判定含 parked（额度挂起的任务不算缺失）。
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
                    Job.status.in_(["queued", "running", "parked"]),  # type: ignore[attr-defined]
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
        out = []
        for b in approved:
            if b.id in active or b.id in recent_failed:
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

        persona_text = brand_of(tenant_id)["persona"]

    if not media_path:
        if source != "douyin" or not url:
            raise RuntimeError("本地原文件已按「拆解完即删」策略清理，请重新上传后再拆解")
        _captcha_breaker()  # 下载熔断：风控窗中不再撞墙（快速失败进 30min 退避重试）
        ctx.set_progress(10, "通过下载容器获取无水印视频")
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
    analysis = await gateway.complete_json(
        [{"role": "system", "content": render("benchmark_analyze", PERSONA=persona_text)},
         {"role": "user", "content": llm_input}],
        purpose="benchmark_analyze", max_tokens=5000)

    with Session(engine) as s:
        b = s.get(BenchmarkVideo, benchmark_id)
        b.analysis = analysis
        if not b.title and analysis.get("summary"):
            b.title = analysis["summary"][:80]
        s.add(b)
        # 沉淀选题候选（source=benchmark，draft 状态待人工定审）
        candidate = (analysis.get("topic_candidate") or {})
        topic_id = None
        if candidate.get("title"):
            audience = candidate.get("audience")
            topic = Topic(
                title=str(candidate["title"])[:60],
                angle=candidate.get("angle", ""),
                audience=audience if audience in ("boss", "fde", "both") else "both",
                source_type="benchmark",
                source_ref=f"benchmark:{benchmark_id}",
                score=_safe_score(analysis.get("score")),
                evidence={
                    "summary": analysis.get("summary", ""),
                    "replicable_points": analysis.get("replicable_points") or [],
                    "hook": analysis.get("hook") or {},
                },
            )
            if (topic.score or 0) < 7.0:
                topic.status = "rejected"  # 自动拒：低于 7.0 不进待审队列
            s.add(topic)
            s.commit()
            s.refresh(topic)
            from ..topics.service import mark_similar
            mark_similar(topic.title, topic.id, s)
            s.commit()
            s.refresh(topic)
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
