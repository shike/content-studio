"""同行拆解流水线：下载（在线）→ ASR 转写 → LLM 拆解 → 选题候选沉淀。"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncio
import json
import os
from pathlib import Path

from sqlmodel import Session, select

from ..asr import transcribe
from ..db import engine
from ..settings import settings
from ..jobs.runner import JobContext, register_executor, register_owed, runner
from ..llm import gateway
from ..models import BenchmarkVideo, Topic
from ..prompts import load, render
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

_BENCHMARK_DIR = settings.data_dir / "benchmarks"



_BENCHMARK_DIR = settings.data_dir / "benchmarks"

async def auto_dissect_rows(rows: list, source_tag: str) -> int:
    """质量闸自动拆解（2026-10-28 用户裁定"全自动化，高质量提前拆解好"）：
    达标行自动批准并提交拆解，其余保持待定夺。

    - 评分口径 = content_score（赞粉比×赞数量结合，0~10），门槛 settings.auto_dissect_min_score
    - 上限：今日已自动批准数 < settings.auto_dissect_daily_cap（控下载与 LLM 成本，
      计数口径=notes 带自动批准标记且 created_at 为今天的行）
    - 同批按分数降序取优；未达标/超限行原样保留待定夺（人不看也不碍事，想捞随时可批），
      分数落行（quality_score）供选题库排序。
    返回本次自动批准条数。"""
    import json as _json

    if not settings.auto_dissect_enabled or not rows:
        return 0
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with Session(engine) as s:
        already = sum(
            1 for r in s.exec(
                select(BenchmarkVideo).where(  # type: ignore[attr-defined]
                    BenchmarkVideo.notes.like("%自动批准%")  # type: ignore[attr-defined]
                )).all()
            if (r.created_at or "")[:10] == today)
    budget = max(0, settings.auto_dissect_daily_cap - already)
    if budget <= 0:
        return 0

    from ..quality import content_score

    def _score(r):
        try:
            st = _json.loads(r.stats) if isinstance(r.stats, str) else (r.stats or {})
        except Exception:  # noqa: BLE001 stats 缺损按 0 分
            st = {}
        return content_score(int(st.get("digg") or 0), int(st.get("followers") or 0))

    ranked = sorted(rows, key=_score, reverse=True)
    approved = 0
    for r in ranked:
        if approved >= budget:
            break
        score = _score(r)
        if score < settings.auto_dissect_min_score:
            continue
        with Session(engine) as s:
            row = s.get(BenchmarkVideo, r.id)
            if row is None or row.scan_status != "pending":
                continue  # 已被人工处理/重复触发
            row.scan_status = "approved"
            row.quality_score = score
            row.notes = f"自动批准（{source_tag}，热度分 {score}：赞粉比×赞数量）"
            s.add(row)
            s.commit()
        await runner.submit("benchmark_analyze", {"benchmark_id": r.id},
                            dedup_key=f"bm:{r.id}")
        approved += 1
    return approved



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
        s.commit()
    return deleted


def owed_benchmarks() -> list[tuple[dict, str]]:
    """欠任务声明（P2 reaper 用）：approved 但 analysis 为空的拆解应有活跃任务。

    6 小时失败退避保留在声明里：最近失败过的不补——确定性失败（如 ASR 词表越界）
    补队也是白烧；in_flight 判定含 parked（额度挂起的任务不算缺失）。
    """
    from datetime import datetime, timedelta

    from sqlmodel import Session, select, select

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
        import os as _os
        _min_gap = float(_os.environ.get("CS_DOWNLOAD_MIN_INTERVAL", "45"))
        global _LAST_DOWNLOAD_TS
        now_ts = _time.time()
        if _LAST_DOWNLOAD_TS and now_ts - _LAST_DOWNLOAD_TS < _min_gap:
            wait_s = _min_gap - (now_ts - _LAST_DOWNLOAD_TS)
            ctx.set_progress(8, f"下载节流：等待 {int(wait_s)} 秒（防触发抖音风控）")
            await asyncio.sleep(wait_s)
        _LAST_DOWNLOAD_TS = _time.time()
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
    # 双模式判定式拆解（2026-10-28 两类号策略修正）：类型是内容本身的属性，
    # 拆解时由 LLM 判定（content_type），账号类型只作先验记录——判定结果回写样本行
    analysis = await gateway.complete_json(
        [{"role": "system", "content": render(
            "benchmark_analyze", PERSONA=persona_text,
            RECENT_TITLES="\n".join(f"- {t}" for t in recent_topic_titles(14))
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
        dup_of = is_similar_title(str(candidate.get("title") or "")) if candidate.get("title") else None
        if dup_of is not None:
            analysis["topic_candidate_suppressed"] = f"与近期选题 #{dup_of} 撞车，候选未入库"
        if candidate.get("title") and dup_of is None:
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
                    "account_type": analysis.get("content_type") or account_type,
                    "benchmark_quality_score": b_row.quality_score if b_row is not None else 0,
                },
            )
            if (topic.score or 0) < 7.0:
                topic.status = "rejected"  # 自动拒：低于 7.0 不进待审队列
            s.add(topic)
            s.flush()  # 拿 id 即可（commit+refresh 在 WAL 并发下偶发 refresh 找不到行——错误类消灭）
            from ..topics.service import mark_similar
            mark_similar(topic.title, topic.id, s)
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
