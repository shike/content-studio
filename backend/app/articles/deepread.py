"""公众号深读产线（2026-11-11 拆锁定版）：每天读一个域的完整文档，判断从读里长出来。

与池子线（article_kb_topics 抽卡）的本质区别：选题不是「标题清单+3000 字摘录」的产物，
是当天全域深读消化后综合出来的——读什么写什么。域=ima 目录轮读（游标记住进度，
app_settings.deepread_cursor="idx:pos"），大目录分日读完、读完换下一个；当天就从当天
读到的内容里综合成文，不攒不凑。池子线的取材上限（选题 3 篇摘录/成文单篇 8000 字）
在此全部废除：逐篇全文消化（单篇 3 万字上限只是防异常大文件），判断硬规则不变
（跨文档综合、不复述单篇、落在读者利害）。

闸门有牙（2026-11-11 用户定版「低于可发线的稿不进待发」）：质量重写一次仍 <7 分，
文章置 failed + 原因注记，不排版不进待发——宁缺勿滥，量服从质（日产 1 篇）。"""
from __future__ import annotations

import asyncio
import json
import re

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import AppSetting, Article
from ..prompts import load, render
from .service import (
    ACTIVE_ARTICLE_STATUSES,
    _article_generate_fail,
    _attach_charts,
    _attach_cover,
    _content_signature,
    _finalize_article,
    _gen_titles,
    _kb_style_violations,
    _quality_gate,
    length_specs,
)

_PER_DAY_DOCS = 18   # 每天全读的文档数（游标续读，大目录分几天读完）
_DOC_CAP = 30000     # 单篇全文上限（防异常大文件；库内常规文档 3~15KB）
_READ_SLEEP = 0.8    # ima 限流退避（密集调用 403 教训）
_GATE_LINE = 7.0     # 可发线：重写一次仍低于此分 = 不进待发


def _load_cursor(s: Session) -> tuple[int, int]:
    row = s.get(AppSetting, "deepread_cursor")
    raw = (row.value or "").strip() if row is not None else ""
    m = re.match(r"^(\d+):(\d+)$", raw)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _save_cursor(s: Session, idx: int, pos: int) -> None:
    row = s.get(AppSetting, "deepread_cursor")
    val = f"{idx}:{pos}"
    if row is None:
        s.add(AppSetting(key="deepread_cursor", value=val))
    else:
        row.value = val
        s.add(row)
    s.commit()


async def _digest_doc(ctx: JobContext, cfg: dict, title: str, text: str,
                      no: int, total: int) -> dict | None:
    """逐篇消化：全文进，消化笔记出（主张+逐字原文资产+未回答的问题）。"""
    ctx.set_progress(10 + int(52 * no / max(1, total)), f"消化 {no}/{total}：《{title[:22]}》")
    try:
        note = await gateway.complete_json(
            [{"role": "user", "content": render("article_deepread_digest",
                                                DOC=f"《{title}》\n\n{text}")}],
            purpose="article_deepread_digest", max_tokens=2000, thinking="disabled")
    except Exception as e:  # noqa: BLE001 单篇消化失败跳过（该篇内容当天缺席，不阻塞整批）
        print(f"[deepread] 消化失败（{title[:24]}）: {type(e).__name__}: {str(e)[:80]}")
        return None
    if not isinstance(note, dict):
        return None
    assets = [str(a).strip() for a in (note.get("assets") or []) if str(a).strip()][:4]
    return {"title": title, "thesis": str(note.get("thesis") or "").strip(),
            "assets": assets, "open": str(note.get("open") or "").strip()}


def _digest_book(notes: list[dict]) -> str:
    """消化笔记 → 写作素材册（assets 是逐字原文，成文的细节全分辨率来自这里）。"""
    blocks = []
    for n in notes:
        lines = [f"【{n['title']}】", f"主张：{n['thesis'] or '（未提炼）'}"]
        if n["assets"]:
            lines.append("原文资产：")
            lines += [f"- {a}" for a in n["assets"]]
        if n["open"]:
            lines.append(f"未回答：{n['open']}")
        blocks.append("\n".join(lines))
    return (f"以下为今日深读 {len(notes)} 篇文档的逐篇消化笔记（assets 为逐字原文，"
            "可作正文细节引用）：\n\n" + "\n\n".join(blocks))


@register_executor("article_deepread", on_fail=_article_generate_fail)
async def article_deepread(ctx: JobContext, payload: dict) -> dict:
    """深读写：域轮读（游标）→ 逐篇全文消化 → 跨文档综合判断 → 范本成文 → 带牙质量闸。"""
    from ..tenant_brand import brand_of
    from .ima_kb import fetch_full_doc, kb_config, list_folder_docs, topic_folders, topic_kb_id

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("深读产线未启用：缺少 ima_kb_enabled / 凭证配置")
        folders = [f for f in topic_folders(s) if f[0]]
        kb_id = topic_kb_id(s)
        idx, pos = _load_cursor(s)
    if not folders:
        raise RuntimeError("深读目录为空：请配置 ima_topic_folders")
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    # ── 定位今天读的域与批次（空目录/越界游标顺延，最多绕一圈）──
    folder_name = ""
    batch: list[dict] = []
    for _ in range(len(folders) + 1):
        idx = idx % len(folders)
        fid, fname = folders[idx]
        docs = await list_folder_docs(client_id, api_key, kb_id, fid)
        if pos >= len(docs):  # 本域读完 → 下一个域
            idx, pos = idx + 1, 0
            continue
        folder_name = fname
        batch = docs[pos:pos + _PER_DAY_DOCS]
        break
    if not batch:
        with Session(engine) as s:
            _save_cursor(s, 0, 0)
        ctx.set_progress(100, "全库一轮读完，今日不出稿（游标已重置）")
        return {"picked": 0, "message": "全库读完一轮"}

    # ── 全读：逐篇拉全文（一篇都拉不到 = ima 限流窗，走退避梯子改日再读）──
    ctx.set_progress(4, f"域「{folder_name}」第 {pos + 1}~{pos + len(batch)} 篇，全文拉取")
    fulltexts: dict[str, str] = {}
    for d in batch:
        mid = str(d.get("media_id") or "")
        if not mid:
            continue
        try:
            t = await fetch_full_doc(client_id, api_key, mid, cap=_DOC_CAP)
        except Exception as e:  # noqa: BLE001
            print(f"[deepread] 全文拉取失败（{d.get('title', '')[:24]}）: {type(e).__name__}: {str(e)[:80]}")
            t = ""
        if t.strip():
            fulltexts[mid] = t
        await asyncio.sleep(_READ_SLEEP)
    if not fulltexts:
        raise TransientError("本批文档全文全部拉取失败（ima 限流或网络），稍后自动重试")
    ctx.set_progress(10, f"全文到手 {len(fulltexts)}/{len(batch)} 篇，开始逐篇消化")

    # ── 逐篇消化 ──
    notes: list[dict] = []
    for i, d in enumerate(batch, 1):
        mid = str(d.get("media_id") or "")
        if mid not in fulltexts:
            continue
        note = await _digest_doc(ctx, cfg, str(d.get("title") or f"文档{i}"),
                                 fulltexts[mid], i, len(batch))
        if note is not None:
            notes.append(note)
        await asyncio.sleep(0.3)
    if len(notes) < 2:
        raise TransientError(f"消化成功仅 {len(notes)} 篇（<2 无法跨文档综合），稍后自动重试")
    ctx.set_progress(64, f"消化完成 {len(notes)} 篇，跨文档综合判断")

    # ── 跨文档综合（判断从读里长出来；避开近期已写）──
    with Session(engine) as s:
        recent = [r[0] for r in s.exec(select(Article.title).where(  # type: ignore[attr-defined]
            Article.status.in_(ACTIVE_ARTICLE_STATUSES))).all()]  # type: ignore[attr-defined]
    recent = [t for t in recent if t][-30:]
    recent_block = "\n".join(f"- {t[:40]}" for t in recent) or "（无）"
    try:
        brief = await gateway.complete_json(
            [{"role": "user", "content": render("article_deepread_synth",
                                                RECENT=recent_block,
                                                DIGESTS=_digest_book(notes))}],
            purpose="article_deepread_synth", max_tokens=4000, thinking="disabled")
    except Exception as e:  # noqa: BLE001
        raise TransientError(f"跨文档综合失败（{type(e).__name__}），稍后自动重试")
    judgment = str((brief or {}).get("judgment") or "").strip()
    if not judgment:
        cut0 = str(((brief or {}).get("cut") or [""])[0])[:80]
        with Session(engine) as s:
            _save_cursor(s, idx, pos + len(batch))
        ctx.set_progress(100, f"今日深读 {len(notes)} 篇，撑不出合格增量判断（{cut0 or '宁缺勿滥'}），不出稿")
        return {"picked": 0, "message": "今日深读未产出（宁缺勿滥）"}

    # ── 建稿（复用同域遗留行，避免梯子重试堆行）──
    with Session(engine) as s:
        stale = [a for a in s.exec(select(Article).where(  # type: ignore[attr-defined]
            Article.status.in_(["generating", "failed"]))).all()  # type: ignore[attr-defined]
            if (a.note or "").startswith(f"深读：{folder_name}")]
        art = stale[0] if stale else Article(title="", status="pending", note="")
        if art.id is None:
            s.add(art)
            s.commit()
            s.refresh(art)
        art.title = judgment[:60]
        art.note = (f"深读：{folder_name}（{len(notes)} 篇全文）｜判断：{judgment[:80]}"
                    f"｜带走：{str((brief or {}).get('takeaway') or '')[:80]}")
        art.status = "generating"
        s.add(art)
        s.commit()
        s.refresh(art)
        article_id = art.id

    # ── 标题组 + 范本成文（与池子线同一写作 prompt：范本对齐+硬红线继承）──
    material = _digest_book(notes)
    from ..tenant_brand import audience_note_of

    persona_text = brand_of(1)["persona"]
    title_from_call, title_alts = await _gen_titles(
        persona_text, length_specs("deep", audience_note_of(1)),
        "\n\n选题素材：\n" + material[:9000])
    ctx.set_progress(72, "成文（范本对齐+硬红线）")
    anchors_text = load("article_kb_anchors")
    body = await gateway.complete(
        [{"role": "user", "content": render("article_kb_write", ANCHORS=anchors_text,
                                            BRIEF=json.dumps(brief, ensure_ascii=False),
                                            MATERIAL=material)}],
        purpose="article_kb_write", max_tokens=8192, thinking="disabled")
    body = (body or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    lines = body.split("\n")
    if not lines or not lines[0].lstrip().startswith("# "):
        raise TransientError("成文输出缺「# 标题」首行（疑似空正文或截断），稍后自动重试")
    title = lines[0].lstrip()[2:].strip() or title_from_call or judgment[:40]
    md_text = "\n".join(lines[1:]).strip()
    n_chars = len(re.sub(r"\s", "", md_text))
    if n_chars < 1500:
        raise TransientError(f"成文仅 {n_chars} 字（规格 1900~2200、下限 1500），疑似截断，稍后自动重试")

    # ── 禁令机检 → 定向重写（签名守卫防数字被改）──
    anchor_texts = [x for x in anchors_text.split("## 范本")[1:] if len(x) > 200]
    violations = _kb_style_violations(md_text, anchor_texts)
    if violations:
        ctx.set_progress(80, f"机检命中 {len(violations)} 项，定向重写")
        try:
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_kb_polish",
                                                    VIOLATIONS="\n".join(f"- {v}" for v in violations),
                                                    DRAFT=md_text)}],
                purpose="article_kb_polish", max_tokens=8192, thinking="disabled")
            polished = (polished or "").strip()
            if polished and _content_signature(polished) == _content_signature(md_text):
                md_text = polished
        except Exception as e:  # noqa: BLE001 重写失败不阻塞成文
            print(f"[deepread] 定向重写失败，保留原稿: {type(e).__name__}: {str(e)[:80]}")

    # ── 图表与头图（失败不阻塞）──
    md_text = await _attach_charts(md_text, 1, "[deepread]")
    ctx.set_progress(90, "渲染头图")
    md_text = await _attach_cover(md_text, title, 1, "[deepread]")

    # ── 带牙质量闸：重写一次仍 <7 分 = 不进待发 ──
    ctx.set_progress(94, "质量自评")
    score = await _quality_gate(ctx, md_text)
    if score is not None and score < _GATE_LINE:
        with Session(engine) as s:
            art = s.get(Article, article_id)
            if art is not None:
                art.status = "failed"
                art.title = title
                art.md = md_text
                art.note = (art.note or "") + f"｜质量闸未过 {score:.1f}，不进待发"
                s.add(art)
                s.commit()
            _save_cursor(s, idx, pos + len(batch))
        ctx.set_progress(100, f"质量闸未过（{score:.1f} < {_GATE_LINE:.0f}），本篇不进待发")
        return {"article_id": article_id, "quality": score, "queued": False,
                "message": "质量闸未过，宁缺勿滥"}

    kb_sources = [{"media_id": str(d.get("media_id") or ""), "title": str(d.get("title") or "")}
                  for d in batch if d.get("media_id")]
    _finalize_article(article_id, title=title, title_alts=title_alts,
                      kb_sources=kb_sources, md_text=md_text)
    from .renderer import render_wechat_html
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is not None and (art.md or "").strip():
            art.html = render_wechat_html(art.md)
            art.status = "rendered"
            if score is not None:
                art.note = (art.note or "") + f"｜质量自评 {score:.1f}"
            s.add(art)
            s.commit()
        _save_cursor(s, idx, pos + len(batch))
    ctx.set_progress(100, f"完成：《{title[:24]}》已排版待发（读 {len(notes)} 篇，自评 {score and round(score, 1)}）")
    return {"article_id": article_id, "title": title, "quality": score,
            "read": len(notes), "folder": folder_name}
