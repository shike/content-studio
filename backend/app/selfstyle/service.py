"""自我风格研究（R9）：扫我的账号 → 逐条风格拆解 → 滚动汇总画像。

与同行拆解的区别：self 链只提取**形式特征**（我怎么说话），不判内容价值、
不产选题候选、不进拆解库。画像保留历史版本，供脚本生成/打磨注入。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from ..asr import transcribe
from ..benchmarks import downloader
from ..benchmarks.downloader import resolve_aweme_id
from ..db import engine
from ..jobs.runner import JobContext, register_concurrent, register_executor, runner
from ..llm import gateway
from ..models import SelfVideo, StyleProfile, WatchAccount
from ..prompts import load
from ..settings import settings
from ..watch.service import (
    _list_account_videos_playwright,
    _list_author_videos_via_anchor,
    _list_author_videos_via_share_page,
)

_SELF_DIR = settings.data_dir / "self_videos"
_PROFILE_EVERY = 5  # 每新增 N 条风格标注，自动重算一次画像
_PROFILE_FIRST = 3  # 首版画像门槛（低一点，早点能用）

_FALLBACK_STYLE = """- 像跟熟识的老客户面对面聊天，不像站在台上演讲
- 句子短。一句话说不完的事就拆两句
- 举的例子必须具体到"哪家客户、投入多少、效果从多少到多少"这种程度，模糊的"某企业大幅提效"一律不许出现
- 敢下判断："这就是烂尾项目""这个钱不该花"——和稀泥的话删掉
- 讲完观点就收尾。不喊"关注我"，不让人"评论区扣 1"，不承诺"保证见效\""""


def latest_profile() -> Optional[StyleProfile]:
    with Session(engine) as s:
        return s.exec(
            select(StyleProfile).order_by(StyleProfile.version.desc())  # type: ignore[attr-defined]
        ).first()


def voice_samples(max_videos: int = 6, max_chars: int = 110) -> list[str]:
    """从已拆解的自有视频转写中逐字截取开头 1~2 句作口吻样本（few-shot 用）。

    摘要/标签会丢掉口语节奏——原句才是口吻的唯一载体，这是脚本"像不像他"的根。"""
    import re

    with Session(engine) as s:
        vids = s.exec(
            select(SelfVideo).where(SelfVideo.analyzed_at != None)  # noqa: E712
            .order_by(SelfVideo.analyzed_at.desc())  # type: ignore[attr-defined]
        ).all()
    samples: list[str] = []
    for v in vids:
        t = (v.transcript or "").strip()
        if len(t) < 120:
            continue
        out = ""
        for piece in re.split(r"(?<=[。！？!?\n])", t):
            piece = piece.strip()
            if not piece:
                continue
            if len(out) + len(piece) > max_chars:
                break
            out += piece
            if len(out) >= 40:
                break
        out = out.strip().rstrip(",，")
        if len(out) >= 30:
            samples.append(out)
        if len(samples) >= max_videos:
            break
    return samples


def build_my_style(enabled: bool = True) -> str:
    """R9.4 风格注入：有画像注入画像，无画像/关闭时回落内置人设（降级可见）。

    口吻专项（Q2/Q6/Q7）：除摘要外，追加转写逐字原话样本——脚本"不像他"的根因
    是只给了摘要丢了口语节奏，原句 few-shot 才是载体。生成与打磨两条链共用。"""
    if not enabled:
        return _FALLBACK_STYLE
    p = latest_profile()
    if p is None:
        return _FALLBACK_STYLE
    traits = "、".join(p.traits[:10]) if p.traits else ""
    exemplars = "\n".join(f"- 原话：「{e}」" for e in p.exemplars[:3]) if p.exemplars else ""
    parts = [f"以下是本人 {p.based_on} 条视频实测提炼的风格画像（优先级高于一切模板腔）：", p.digest]
    if traits:
        parts.append(f"特征标签：{traits}")
    if exemplars:
        parts.append("代表原句（学他的原话感，逐字级模仿）：\n" + exemplars)
    try:
        samples = voice_samples()
    except Exception as e:  # noqa: BLE001 样本提取失败不阻塞生成
        print(f"[my-style] 原话样本提取失败: {type(e).__name__}: {str(e)[:100]}")
        samples = []
    if samples:
        parts.append(
            "他的原话样本（从他自己发布的视频逐字截取——句子的节奏/用词/语气以这些为准，"
            "写出来的每一段读起来都要像这些句子）：\n"
            + "\n".join(f"- 「{s_}」" for s_ in samples))
    return "\n".join(parts)


def _list_self_videos(url: str) -> tuple[Optional[list], Optional[str]]:
    """与同行扫描同款四链路，返回 (entries, error)。"""
    entries, err, is_anchor = _list_author_videos_via_anchor(url)
    if entries is not None:
        return entries, None
    share_entries, share_err, _ = _list_author_videos_via_share_page(url)
    if share_entries is not None:
        return share_entries, None
    if is_anchor is False:
        entries, err = _list_account_videos_playwright(url)
        if entries is not None:
            return entries, None
    return None, err


register_concurrent("self_scan")
@register_executor("self_scan")
async def self_scan(ctx: JobContext, payload: dict) -> dict:
    with Session(engine) as s:
        accounts = s.exec(
            select(WatchAccount).where(  # type: ignore[attr-defined]
                WatchAccount.kind == "self",  # type: ignore[attr-defined]
                WatchAccount.enabled == True,  # noqa: E712
            )
        ).all()
    if not accounts:
        raise RuntimeError("未添加我的账号：在同行监测页添加并勾选「这是我的账号」")

    notes: list[str] = []
    queued = 0
    with Session(engine) as s:
        known = {v.aweme_id for v in s.exec(select(SelfVideo)).all() if v.aweme_id}

    for i, acc in enumerate(accounts):
        ctx.set_progress(5 + int(80 * i / len(accounts)),
                         f"扫描我的账号 {acc.name}（{i + 1}/{len(accounts)}）")
        entries, err = await asyncio.to_thread(_list_self_videos, acc.url)
        if entries is None:
            notes.append(f"{acc.name}：扫描失败（{err}）")
            continue
        created = 0
        for e in entries:
            vid = str(e.get("id") or "")
            if not vid or vid in known or resolve_aweme_id(f"https://www.douyin.com/video/{vid}") is None:
                continue
            with Session(engine) as s:
                v = SelfVideo(tenant_id=acc.tenant_id,  # 父账号继承（调度上下文无 ACTOR 归属）
                              url=f"https://www.douyin.com/video/{vid}",
                              aweme_id=vid, title=str(e.get("title") or "")[:80],
                              stats=e.get("stats") or {},
                              scan_status="pending")  # 待定夺，批准后才风格拆解
                s.add(v)
                s.commit()
            known.add(vid)
            created += 1
            queued += 1
        with Session(engine) as s:
            row = s.get(WatchAccount, acc.id)
            if row is not None:
                row.last_scan_at = datetime.now(timezone.utc).replace(tzinfo=None)
                s.add(row)
                s.commit()
        notes.append(f"{acc.name}：新增 {created} 条，已排队风格拆解")

    ctx.set_progress(100, f"完成：新增 {queued} 条")
    return {"queued": queued, "notes": notes}


@register_executor("self_analyze")
async def self_analyze(ctx: JobContext, payload: dict) -> dict:
    video_id = payload["video_id"]
    with Session(engine) as s:
        v = s.get(SelfVideo, video_id)
        if v is None:
            raise RuntimeError(f"self_video {video_id} 不存在")

    ctx.set_progress(10, "下载视频")
    media_path = v.media_path
    if not media_path:
        media_path = await asyncio.to_thread(downloader.download_video, v.url, _SELF_DIR)
        with Session(engine) as s:
            v = s.get(SelfVideo, video_id)
            v.media_path = media_path
            s.add(v)
            s.commit()

    ctx.set_progress(45, "ASR 转写")
    with Session(engine) as s:
        v = s.get(SelfVideo, video_id)
        transcript = v.transcript
    if not transcript:
        from ..tenant_brand import asr_vocab_of

        transcript = await asyncio.to_thread(transcribe, media_path, asr_vocab_of(v.tenant_id if v else 1))
        if not transcript.strip():
            raise RuntimeError("转写结果为空：视频可能没有可识别的中文语音")

    ctx.set_progress(70, "风格拆解")
    data = await gateway.complete_json(
        [{"role": "user", "content": load("self_style_extract") + transcript[:6000]}],
        purpose="self_style_extract", max_tokens=12000)

    with Session(engine) as s:
        v = s.get(SelfVideo, video_id)
        v.transcript = transcript
        v.style_analysis = data
        v.analyzed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        s.add(v)
        s.commit()
        analyzed = s.exec(
            select(SelfVideo).where(SelfVideo.analyzed_at != None)  # noqa: E712
        ).all()
    prev = latest_profile()

    ctx.set_progress(95, "检查画像更新阈值")
    if prev is None:
        if len(analyzed) >= _PROFILE_FIRST:
            await runner.submit("self_profile_update", {}, dedup_key="profile:update")
    else:
        fresh = [v for v in analyzed
                 if v.analyzed_at and v.analyzed_at > prev.created_at]
        if len(fresh) >= _PROFILE_EVERY:
            await runner.submit("self_profile_update", {}, dedup_key="profile:update")

    ctx.set_progress(100, "完成")
    return {"video_id": video_id, "traits": list((data.get("phrases") or []))}


@register_executor("self_profile_update")
async def self_profile_update(ctx: JobContext, payload: dict) -> dict:
    with Session(engine) as s:
        videos = s.exec(
            select(SelfVideo).where(SelfVideo.analyzed_at != None)  # noqa: E712
            .order_by(SelfVideo.analyzed_at.desc())  # type: ignore[attr-defined]
        ).all()
    if not videos:
        raise RuntimeError("还没有已分析的视频，先扫描我的账号")

    ctx.set_progress(30, f"汇总 {len(videos)} 条风格标注")
    marks = []
    skipped_invalid = 0
    for v in videos[:60]:
        sa = v.style_analysis or {}
        if isinstance(sa, str):
            try:
                sa = json.loads(sa)
            except Exception:
                sa = {}
        # 过滤无效标注：转写无口播内容的视频（水印/环境音），LLM 只能填"无法判断"
        if str(sa.get("opening_pattern") or "").startswith("无法判断") or str(sa.get("tone") or "").startswith("无法判断"):
            skipped_invalid += 1
            continue
        marks.append({"title": v.title, **sa})
    if not marks:
        raise RuntimeError("已分析视频里没有有效的风格标注（可能都是转写无口播内容的视频）")
    prev = latest_profile()
    prev_data = ({"digest": prev.digest, "traits": prev.traits, "exemplars": prev.exemplars}
                 if prev else None)
    data = await gateway.complete_json(
        [{"role": "user", "content": (load("self_profile_update")
                                      + "【上一版画像】\n" + str(prev_data)
                                      + "\n\n【风格标注】\n" + str(marks))}],
        purpose="self_profile_update", max_tokens=16000)

    with Session(engine) as s:
        acc = s.exec(select(WatchAccount).where(WatchAccount.kind == "self")).first()  # type: ignore[attr-defined]
        profile = StyleProfile(
            account_name=acc.name if acc else "",
            version=(prev.version + 1) if prev else 1,
            digest=str(data.get("digest") or ""),
            traits=list(data.get("traits") or []),
            exemplars=list(data.get("exemplars") or []),
            based_on=len(marks),
        )
        s.add(profile)
        s.commit()
        s.refresh(profile)

    ctx.set_progress(100, f"画像 v{profile.version} 已生成")
    return {"version": profile.version, "based_on": profile.based_on}
