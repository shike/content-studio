"""素材加工（PRD R1.5）：下载→高赞评论存档+分型路由→ASR 转写→LLM 拆解→版本留痕。

执行器入口在 executors.py（material_analyze）；本模块是管线与共用件。
拆解产物落 material_analyses（breakdown/comments 两个 kind），素材行只留
transcript 与 quality 元数据——分析版本可回溯，重拆不清旧档。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from ..asr import transcribe
from ..db import engine
from ..jobs.runner import _DEAD_ERROR_MARKERS, JobContext, register_owed
from ..llm import gateway
from ..models import Job, Material, MaterialAnalysis, Source
from ..prompts import render
from ..settings import settings
from .download import (captcha_breaker, captcha_record, download_wait_s,
                       download_video, throttle_download)

MEDIA_DIR = settings.data_dir / "materials"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fetch_top_comments(video_url: str, cap: int = 20) -> list[dict]:
    """拆解时抓高赞评论原文（R1.4 进料：评论区=观点的矿脉）。拦 comment/list XHR
    免登录，首屏即热序；采深轮滚动评论容器翻页（40 条/12s 静默窗/60s 截止）；
    去重按赞排序取前 cap 条 [{t,d}]。失败抛 RuntimeError（调用方不挡拆解）。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

    m = re.search(r"video/(\d+)", video_url)
    if not m:
        raise RuntimeError("无法从链接解析视频 id")
    rid = m.group(1)
    comments: list[dict] = []
    seen: set[str] = set()
    with sync_playwright() as p:
        browser = launch(p)
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if "aweme/v1/web/comment/list/" in resp.url and len(comments) < 80:
                        d = resp.json()
                        for c in (d.get("comments") or []):
                            t = (c.get("text") or "").strip()
                            if t and t not in seen:
                                seen.add(t)
                                comments.append({"t": t[:60], "d": int(c.get("digg_count") or 0)})
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            page.goto(f"https://www.douyin.com/video/{rid}",
                      timeout=45000, wait_until="domcontentloaded")
            deadline = time.monotonic() + 60
            last_n, quiet = 0, 0
            while time.monotonic() < deadline:
                page.wait_for_timeout(3000)
                if not comments:
                    try:
                        page.mouse.wheel(0, 600)
                        page.wait_for_timeout(1200)
                        page.mouse.wheel(0, -600)
                    except Exception:  # noqa: BLE001
                        break
                    continue
                if len(comments) >= 40:
                    break
                try:
                    page.evaluate("""() => {
                      const els = [...document.querySelectorAll('div')]
                        .filter(e => e.scrollHeight > e.clientHeight + 80);
                      els.sort((a, b) => b.scrollHeight - a.scrollHeight);
                      for (const e of els.slice(0, 3)) e.scrollTop = e.scrollHeight;
                    }""")
                except Exception:  # noqa: BLE001
                    pass
                quiet = quiet + 1 if len(comments) <= last_n else 0
                last_n = len(comments)
                if quiet >= 4:
                    break
            ctx.close()
        finally:
            browser.close()
    if not comments:
        raise RuntimeError("未拦到评论（评论区可能已关闭或接口未触发）")
    comments.sort(key=lambda c: c["d"], reverse=True)
    return comments[:cap]


# ── 评论区分型路由（R1.4）：求学打卡型=获客需求信号 → 获客线；观点型=人设矿脉 ──
_STUDY_KEYWORDS = ("求带", "带带我", "求资料", "求教程", "求文档", "求源码", "求链接",
                   "求分享", "求模板", "跪求", "怎么学", "在哪里学", "在哪学", "怎么考",
                   "在哪里考", "在哪考", "报名", "怎么报名", "想学", "请教", "跟学",
                   "学习", "打卡", "扣1", "扣个1")


def classify_comments(comments: list) -> tuple[str, float]:
    """求学密度 → 分型（persona_ore|leadgen_ore；空评论=('' ,0)）。
    阈值=comment_leadgen_route_min（百分比，默认 60%）。"""
    texts = [str(c.get("t") or "") for c in (comments or [])
             if isinstance(c, dict) and str(c.get("t") or "").strip()]
    if not texts:
        return "", 0.0
    hits = sum(1 for t in texts if any(k in t for k in _STUDY_KEYWORDS))
    share = round(hits / len(texts), 2)
    line = max(1, int(settings.comment_leadgen_route_min or 60)) / 100.0
    return ("leadgen_ore" if share >= line else "persona_ore"), share


def _rejudge_source_type(tenant_id: int, source_id: Optional[int], author: str) -> Optional[str]:
    """账号层联动：该源近 30 天已分型素材（最多看 5 条）里获客矿 ≥3 →
    config.account_type 自动改判 leadgen。返回改判说明或 None。"""
    cutoff = _now() - timedelta(days=30)
    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.tenant_id == tenant_id,  # type: ignore[attr-defined]
            Material.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        rows = [r for r in rows
                if (r.quality or {}).get("comment_kind") and (
                    r.source_id == source_id or (author and r.author == author))]
        recent = sorted(rows, key=lambda x: x.created_at or _now(), reverse=True)[:5]
        lg = sum(1 for x in recent if (x.quality or {}).get("comment_kind") == "leadgen_ore")
        if len(recent) < 3 or lg < 3:
            return None
        acc = None
        if source_id:
            acc = s.get(Source, source_id)
        if acc is None and author:
            acc = s.exec(select(Source).where(  # type: ignore[attr-defined]
                Source.kind == "peer_account",  # type: ignore[attr-defined]
                Source.name == author,  # type: ignore[attr-defined]
                Source.tenant_id == tenant_id)).first()  # type: ignore[call-overload]
        if acc is None or (acc.config or {}).get("account_type") == "leadgen":
            return None
        cfg = dict(acc.config or {})
        cfg["account_type"] = "leadgen"
        acc.config = cfg
        s.add(acc)
        s.commit()
        return (f"{acc.name} 近 {len(recent)} 条拆解素材 {lg} 条为获客矿（求学型评论区），"
                "账号类型已自动改判 leadgen——清单挪动请在采集源页人工操作")


def _save_analysis(s: Session, material_id: int, tenant_id: int, kind: str,
                   payload: dict, text: str = "", model: str = "") -> None:
    """分析版本追加（version=同 material+kind 现有最大版本+1）。"""
    last = s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
        MaterialAnalysis.material_id == material_id,  # type: ignore[attr-defined]
        MaterialAnalysis.kind == kind)  # type: ignore[attr-defined]
        .order_by(MaterialAnalysis.version.desc())).first()  # type: ignore[attr-defined]
    s.add(MaterialAnalysis(material_id=material_id, tenant_id=tenant_id, kind=kind,
                           payload=payload or {}, text=text or "", model=model,
                           version=(last.version + 1) if last else 1))


async def run_analyze(ctx: JobContext, material_id: int) -> dict:
    """拆解管线（material_analyze 执行器体）：下载→评论→ASR→LLM 拆解→即用即弃。"""
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise RuntimeError(f"material {material_id} 不存在")
        url = m.origin_url
        origin_type = m.origin_type
        media = dict(m.media or {})
        stats = m.stats or {}
        tenant_id = m.tenant_id
        from ..tenant_brand import brand_of

        persona_text = brand_of(tenant_id)["persona"]

    media_path = (media or {}).get("path") or ""
    if not media_path:
        if origin_type not in ("douyin_video", "video") or not url:
            raise RuntimeError("本地原文件已按「拆解完即弃」策略清理，请重新上传后再拆解")
        captcha_breaker()  # 下载熔断：风控窗中不再撞墙（快速失败进 30min 退避重试）
        wait_s = download_wait_s()
        if wait_s > 0:
            ctx.set_progress(8, f"下载节流：等待 {int(wait_s)} 秒（防触发抖音风控）")
        await asyncio.to_thread(throttle_download)
        ctx.set_progress(10, "打开视频页获取无水印直链")
        try:
            media_path = await asyncio.to_thread(download_video, url, MEDIA_DIR)
        except Exception as e:
            if "验证码" in str(e):
                captcha_record()
                from ..jobs.errors import CaptchaError

                raise CaptchaError(f"自动下载失败（抖音风控验证码）：{str(e)[:120]}") from e
            raise
        with Session(engine) as s:
            m = s.get(Material, material_id)
            m.media = {**(m.media or {}), "path": media_path}
            s.add(m)
            s.commit()

    def _drop_media() -> None:
        try:
            if media_path and os.path.exists(media_path):
                os.remove(media_path)
        except OSError:
            pass

    # 高赞评论存档（观点的矿脉）：抓取失败不挡拆解（增益不是产线）
    if origin_type == "douyin_video" and url:
        try:
            ctx.set_progress(30, "抓高赞评论原文（评论区=观点的矿脉）")
            top_comments = await asyncio.to_thread(_fetch_top_comments, url)
            kind, share = classify_comments(top_comments)
            author = ""
            with Session(engine) as s:
                m = s.get(Material, material_id)
                author = m.author or ""
                _save_analysis(s, material_id, tenant_id, "comments",
                               {"items": top_comments}, model="xhr")
                q = dict(m.quality or {})
                q["comment_kind"], q["leadgen_share"] = kind, share
                m.quality = q
                s.add(m)
                s.commit()
            if kind == "leadgen_ore":
                ctx.set_progress(31, f"评论区判获客矿（求学密度 {share:.0%}）——素材归获客线，人设号不吃")
                note = _rejudge_source_type(tenant_id, (m.source_id if m else None), author)
                if note:
                    ctx.set_progress(31, note)
            else:
                ctx.set_progress(31, f"评论区判人设矿（求学密度 {share:.0%}）")
        except Exception as e:  # noqa: BLE001
            ctx.set_progress(30, f"高赞评论抓取失败（不挡拆解）：{type(e).__name__}: {str(e)[:60]}")

    ctx.set_progress(45, f"ASR 转写中（{settings.asr_model}，首次运行含模型下载）")
    try:  # 内存防线：available 低于水位时显式告警
        with open("/proc/meminfo") as _mi:
            avail_mb = next(int(l.split()[1]) // 1024 for l in _mi if l.startswith("MemAvailable"))
        if avail_mb < 800:
            ctx.set_progress(46, f"⚠️ 可用内存仅 {avail_mb}MB，转写将变慢——建议 ASR 切 medium 或升级内存")
            print(f"[asr] ⚠️ 转写前可用内存仅 {avail_mb}MB（水位 800MB），存在 swap 颠簸风险")
    except Exception:  # noqa: BLE001 非 Linux/读取失败则跳过
        pass
    from ..tenant_brand import asr_vocab_of

    transcript = await asyncio.to_thread(transcribe, media_path, asr_vocab_of(tenant_id))
    if not transcript.strip():
        # 无口播内容：自动忽略（Q19 用户裁定不进待处置），任务正常终结并写明原因
        with Session(engine) as s:
            m = s.get(Material, material_id)
            if m is not None:
                m.status = "ignored"
                if (m.media or {}).get("path"):
                    _drop_media()
                    m.media = {k: v for k, v in (m.media or {}).items() if k != "path"}
                m.note = f"{(m.note + '｜' if m.note else '')}转写为空：无口播内容，已自动忽略"[:300]
                s.add(m)
                s.commit()
        ctx.set_progress(100, "无口播内容，已自动忽略")
        return {"material_id": material_id, "auto_ignored": True,
                "reason": "转写为空：视频没有可识别的中文语音"}
    with Session(engine) as s:
        m = s.get(Material, material_id)
        m.transcript = transcript
        s.add(m)
        s.commit()

    ctx.set_progress(75, "LLM 拆解中")
    llm_input = transcript[:6000]
    if stats:
        # R3.4b 数据归因：把赞/评/转带给 LLM，分析"为什么这条（没）爆"
        llm_input += f"\n\n互动数据（做数据归因用）:{json.dumps(stats, ensure_ascii=False)}"
    from ..tenant_brand import audience_note_of

    analysis = await gateway.complete_json(
        [{"role": "system", "content": render(
            "benchmark_analyze", PERSONA=persona_text,
            AUDIENCE_NOTE=audience_note_of(tenant_id))},
         {"role": "user", "content": llm_input}],
        max_tokens=5000)

    with Session(engine) as s:
        m = s.get(Material, material_id)
        _save_analysis(s, material_id, tenant_id, "breakdown", analysis,
                       model=gateway.active().get("model") or "")
        q = dict(m.quality or {})
        judged = str(analysis.get("content_type") or "").strip()
        if judged in ("persona", "leadgen"):
            q["account_type"] = judged  # LLM 判定覆盖先验（账号类型只是初始猜测）
        if not m.title and analysis.get("summary"):
            m.title = analysis["summary"][:80]
        m.quality = q
        s.add(m)
        s.commit()

    # 媒体即用即弃（磁盘防线）：拆解成功后删除视频文件；失败不删（重试还要用）
    try:
        if media_path and os.path.exists(media_path):
            await asyncio.to_thread(os.remove, media_path)
        with Session(engine) as s:
            m = s.get(Material, material_id)
            if m is not None and (m.media or {}).get("path"):
                m.media = {k: v for k, v in (m.media or {}).items() if k != "path"}
                s.add(m)
                s.commit()
    except Exception as e:  # noqa: BLE001 清理失败不影响拆解成果，reaper 巡检兜底
        print(f"[materials] 拆解后媒体清理失败（reaper 会兜底）: {type(e).__name__}: {str(e)[:100]}")

    ctx.set_progress(100, "完成")
    return {"material_id": material_id, "transcript_chars": len(transcript)}


def cleanup_completed_media() -> int:
    """媒体卫生（磁盘防线）：删除已拆解/已忽略素材与已分析自有视频的媒体文件。
    排除仍有活跃任务的行（拆解进行中的媒体不能删）。"""
    from ..models import SelfVideo

    deleted = 0
    stale_line = _now() - timedelta(hours=24)
    with Session(engine) as s:
        active_mat = {
            (j.payload or {}).get("material_id")
            for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "material_analyze",  # type: ignore[attr-defined]
                Job.status.in_(["queued", "running"]))).all()  # type: ignore[attr-defined]
        }
        rows = s.exec(select(Material).where(
            Material.status.in_(["ready", "ignored", "archived"]))).all()  # type: ignore[attr-defined]
        for b in rows:
            if b.id in active_mat:
                continue
            media = b.media or {}
            path = media.get("path") or ""
            if not path:
                continue
            has_breakdown = any(
                a.kind == "breakdown"
                for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                    MaterialAnalysis.material_id == b.id)).all())  # type: ignore[attr-defined]
            if not has_breakdown and b.status == "ready" and (b.transcript or "").strip():
                has_breakdown = True  # 转写完成即算加工过（拆解失败的行重试还要用媒体？不——失败行保留）
            if b.status == "ready" and not has_breakdown and not (b.transcript or "").strip():
                continue  # 待拆解：媒体留给下次分析
            try:
                if path and os.path.exists(path):
                    os.remove(path)
                b.media = {k: v for k, v in media.items() if k != "path"}
                s.add(b)
                deleted += 1
            except OSError:
                continue

        # SelfVideo（P4 迁移前保留）：已分析完的媒体即用即弃；24h 死残留清
        active_self = {
            (j.payload or {}).get("video_id")
            for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "self_analyze",  # type: ignore[attr-defined]
                Job.status.in_(["queued", "running"]))).all()  # type: ignore[attr-defined]
        }
        for v in s.exec(select(SelfVideo).where(
                SelfVideo.media_path != "")).all():  # type: ignore[arg-type]
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


def owed_materials() -> list[tuple[dict, str]]:
    """欠任务声明：ready 且无 breakdown 的抖音素材应有活跃 material_analyze 任务。

    6 小时失败退避保留；死内容（匿名不可见/已删除/转写空）不补——终态，别每窗复活一次。"""
    from datetime import timedelta

    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.status == "ready",  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video")).all()  # type: ignore[attr-defined]
        have_analysis = {r.material_id for r in s.exec(
            select(MaterialAnalysis.material_id)).all()}  # type: ignore[attr-defined]
        active = {
            (j.payload or {}).get("material_id")
            for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "material_analyze",  # type: ignore[attr-defined]
                Job.status.in_(["queued", "running"]))).all()  # type: ignore[attr-defined]
        }
        cool = datetime.utcnow() - timedelta(hours=6)
        recent_failed = {
            (j.payload or {}).get("material_id")
            for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "material_analyze",  # type: ignore[attr-defined]
                Job.status == "failed",  # type: ignore[attr-defined]
                Job.updated_at > cool)).all()  # type: ignore[attr-defined]
        }
        dead_ids = {
            (j.payload or {}).get("material_id")
            for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "material_analyze",  # type: ignore[attr-defined]
                Job.status == "failed")).all()  # type: ignore[attr-defined]
            if any(m in (j.error or "") for m in _DEAD_ERROR_MARKERS)
        }
        out = []
        for b in rows:
            if b.id in have_analysis or b.id in active or b.id in recent_failed or b.id in dead_ids:
                continue
            out.append(({"material_id": b.id}, f"mat:{b.id}", (b.tenant_id, 0, "")))
        return out


register_owed("material_analyze", owed_materials)
