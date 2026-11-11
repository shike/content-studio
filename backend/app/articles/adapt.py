"""改编器（2026-11-11 源文改编产线）：每日从内容清单取一项，把源文改编成我们号的文章。

与挖坑线/深读线的本质区别：不再凭空综合——清单项就是一篇被评估过的高质量源文，
改编 = 保源文骨架与逐字原话（≤3 处标注引用），换我们号的判断口径与读者钩子。
出口闸门：原创度（与源文重复率 ≤30%，超线定向重写一次仍超=当日空过退回清单）；
违禁词机检沿用 service 的正则与定向重写。当日账落 daily_ledger。"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Article, ContentItem, DailyLedger
from ..prompts import load, render
from .service import (
    _article_generate_fail,
    _attach_charts,
    _attach_cover,
    _content_signature,
    _finalize_article,
    _gen_titles,
    _kb_style_violations,
    length_specs,
)

_SRC_CAP = 30000      # 源文全文上限
_DUP_LINE = 30.0      # 原创度线：与源文重复率百分比上限
_MIN_CHARS = 1500     # 正文下限（规格 1900~2200）

_PUNCT = re.compile(r"[\s，。！？；：、“”‘’「」『』（）()【】\[\]—…·.,;:!?\"'<>《》]")


def _norm(t: str) -> str:
    return _PUNCT.sub("", t or "")


def _dup_ratio(adapted: str, source: str) -> tuple[float, list[str]]:
    """原创度检测：源文中 ≥14 字的实质句被原样搬进改编稿的字符占比，及雷同句清单。"""
    a = _norm(adapted)
    total = hit = 0
    hits: list[str] = []
    for sent in re.split(r"[。！？；\n]", source or ""):
        s = _norm(sent)
        if len(s) < 14:
            continue
        total += len(s)
        if s in a:
            hit += len(s)
            hits.append(sent.strip()[:60])
    return (round(hit / total * 100, 1) if total else 0.0), hits[:8]


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _strip_fence(body: str) -> str:
    body = (body or "").strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-z]*\n?", "", body)
        body = re.sub(r"\n?```$", "", body).strip()
    return body


@register_executor("article_adapt", on_fail=_article_generate_fail)
async def article_adapt(ctx: JobContext, payload: dict) -> dict:
    """每日改编：清单取项（CAS）→ 拉源文全文 → 保骨架保原话换口径成文 → 原创度闸 → 机检 → 排版。"""
    from ..tenant_brand import audience_note_of, brand_of
    from .ima_kb import fetch_full_doc, kb_config

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("改编器未启用：缺少 ima_kb_enabled / 凭证配置")
        # 卡死自愈：改编中超 2 小时无进展的项退回待改编（job 被杀/超时的残留）
        stale_cut = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        for it in s.exec(select(ContentItem).where(  # type: ignore[attr-defined]
                ContentItem.status == "running")).all():  # type: ignore[attr-defined]
            if it.updated_at and it.updated_at < stale_cut:
                it.status = "pending"
                s.add(it)
        s.commit()
        item = s.exec(select(ContentItem).where(  # type: ignore[attr-defined]
            ContentItem.status == "pending")  # type: ignore[attr-defined]
            .order_by(ContentItem.priority.desc(), ContentItem.quality_score.desc(),  # type: ignore[attr-defined]
                      ContentItem.id)).first()  # type: ignore[attr-defined]
        if item is None:
            s.add(DailyLedger(day=_today(), phase="adapt", ok=True,
                              produced="", reason="清单空：无待改编项（宁缺勿滥）"))
            s.commit()
            ctx.set_progress(100, "清单空，今日不出稿")
            return {"adapted": False, "message": "清单空"}
        item.status = "running"  # CAS 占位（单 worker + 日级 dedup_key，无并发竞争）
        s.add(item)
        s.commit()
        s.refresh(item)
        item_id, mid, src_title = item.id, item.media_id, item.title
        angle, quotes = item.adapt_angle, [str(q) for q in (item.best_quotes or [])]
        score = item.quality_score
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    def _release(reason: str, item_status: str = "pending", tag: str = "") -> None:
        """空过/未过收尾：清单项退回指定状态 + 当日账写原因。"""
        with Session(engine) as s:
            it = s.get(ContentItem, item_id)
            if it is not None:
                it.status = item_status
                if tag:
                    it.note = ((it.note or "") + f"｜{tag}").strip("｜")[:300]
                s.add(it)
            s.add(DailyLedger(day=_today(), phase="adapt", ok=True,
                              produced="", reason=reason))
            s.commit()

    # ── 拉源文全文 → 建稿 → 成文 → 原创度闸 → 机检（瞬时失败统一退项走梯子）──
    try:
        try:
            source = await fetch_full_doc(client_id, api_key, mid, cap=_SRC_CAP)
        except Exception as e:  # noqa: BLE001
            raise TransientError(f"源文全文拉取失败（{type(e).__name__}），稍后自动重试")
        if not source.strip():
            _release(f"源文全文不可读（media 已失效），跳过清单项 #{item_id}")
            ctx.set_progress(100, "源文不可读，今日不出稿")
            return {"adapted": False, "message": "源文不可读"}

        # 建稿（复用同源遗留行，防梯子重试堆行）
        with Session(engine) as s:
            stale = [a for a in s.exec(select(Article).where(  # type: ignore[attr-defined]
                Article.status.in_(["generating", "failed"]))).all()  # type: ignore[attr-defined]
                if (a.note or "").startswith(f"改编：{src_title[:20]}")]
            art = stale[0] if stale else Article(title="", status="pending", note="")
            if art.id is None:
                s.add(art)
                s.commit()
                s.refresh(art)
            art.note = (f"改编：{src_title[:40]}（源文评分 {score}）｜角度：{angle[:80]}")
            art.status = "generating"
            s.add(art)
            s.commit()
            s.refresh(art)
            article_id = art.id

        # 标题组 + 改编成文
        persona_text = brand_of(1)["persona"]
        title_from_call, title_alts = await _gen_titles(
            persona_text, length_specs("deep", audience_note_of(1)),
            "\n\n选题素材：\n" + source[:6000])
        ctx.set_progress(30, "改编成文（保骨架保原话换口径）")
        quotes_block = "\n".join(f"- {q}" for q in quotes) or "（无，本次不引用原话）"
        body = await gateway.complete(
            [{"role": "user", "content": render("article_adapt_write",
                                                SOURCE=source, ANGLE=angle,
                                                QUOTES=quotes_block)}],
            purpose="article_adapt_write", max_tokens=8192, thinking="disabled")
        body = _strip_fence(body)
        lines = body.split("\n")
        if not lines or not lines[0].lstrip().startswith("# "):
            raise TransientError("改编输出缺「# 标题」首行（疑似空正文或截断），稍后自动重试")
        title = lines[0].lstrip()[2:].strip() or title_from_call or src_title[:30]
        md_text = "\n".join(lines[1:]).strip()
        n_chars = len(re.sub(r"\s", "", md_text))
        if n_chars < _MIN_CHARS:
            raise TransientError(f"改编稿仅 {n_chars} 字（规格 1900~2200、下限 1500），疑似截断，稍后自动重试")

        # ── 原创度闸：与源文重复率 ≤30%，超线定向重写一次，仍超=当日空过退回清单 ──
        ctx.set_progress(55, "原创度检测（与源文比对）")
        ratio, hits = _dup_ratio(md_text, source)
        if ratio > _DUP_LINE:
            ctx.set_progress(62, f"重复率 {ratio}% 超线，定向降重写")
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_adapt_reduce",
                                                    HITS="\n".join(f"- {h}" for h in hits),
                                                    DRAFT=md_text, SOURCE=source[:_SRC_CAP])}],
                purpose="article_adapt_reduce", max_tokens=8192, thinking="disabled")
            polished = _strip_fence(polished)
            p_lines = polished.split("\n")
            if p_lines and p_lines[0].lstrip().startswith("# "):
                polished_md = "\n".join(p_lines[1:]).strip()
                # 签名守卫：重写不许动正文里的数字与结构（动了就弃用重写稿）
                if polished_md and _content_signature(polished_md) == _content_signature(md_text):
                    md_text = polished_md
                    ratio, hits = _dup_ratio(md_text, source)
        if ratio > _DUP_LINE:
            _release(f"改编稿重复率 {ratio}% 超 {_DUP_LINE}% 线（重写一次仍超），退回清单 #{item_id}")
            with Session(engine) as s:
                art = s.get(Article, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.md = md_text
                    art.note = (art.note or "") + f"｜原创度未过（重复率 {ratio}%），不进待发"
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"原创度未过（{ratio}%），本项退回清单")
            return {"adapted": False, "ratio": ratio, "message": "原创度未过"}

        # ── 违禁词机检 → 定向重写（签名守卫）──
        anchor_texts = [x for x in load("article_kb_anchors").split("## 范本")[1:] if len(x) > 200]
        violations = _kb_style_violations(md_text, anchor_texts)
        if violations:
            ctx.set_progress(78, f"机检命中 {len(violations)} 项，定向重写")
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_kb_polish",
                                                    VIOLATIONS="\n".join(f"- {v}" for v in violations),
                                                    DRAFT=md_text)}],
                purpose="article_kb_polish", max_tokens=8192, thinking="disabled")
            polished = _strip_fence(polished)
            if polished and _content_signature(polished) == _content_signature(md_text):
                md_text = polished

        # ── 审校组（事实对源 + 读者弃读点）：问题清单制，修订一次复审，二次不过计数退避 ──
        from .review import review_facts, review_reader, revise_once, score_titles
        ctx.set_progress(82, "审校组：事实对源 + 读者弃读点")
        issues = await review_facts(md_text, source)
        issues += await review_reader(md_text)
        if issues:
            revised = await revise_once(md_text, issues)
            if revised and _content_signature(revised) == _content_signature(md_text):
                md_text = revised
                issues = await review_facts(md_text, source)
                issues += await review_reader(md_text)
        if issues:
            with Session(engine) as s:
                it0 = s.get(ContentItem, item_id)
                fails = (it0.note or "").count("审校未过") if it0 is not None else 0
            tag = f"审校未过×{fails + 1}"
            _release(f"审校二次未过（{'；'.join(issues[:2])}），清单项 #{item_id} 落 low 不再自动重试"
                     if fails >= 1 else
                     f"审校未过（{'；'.join(issues[:2])}），退回清单 #{item_id}",
                     item_status="low" if fails >= 1 else "pending", tag=tag)
            with Session(engine) as s:
                art = s.get(Article, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.md = md_text
                    art.note = (art.note or "") + f"｜审校未过（{issues[0][:40]}），不进待发"
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"审校未过（{len(issues)} 项），本篇不进待发")
            return {"adapted": False, "issues": len(issues), "message": "审校未过"}

        # ── 标题评分（五维，自动选主标题；正题/来源题/备选全进候选池）──
        ctx.set_progress(88, "标题评分")
        cands = [title] + [t for t in (title_alts or []) if t]
        if title_from_call and title_from_call not in cands:
            cands.append(title_from_call)
        ranked = await score_titles(cands)
        if ranked and ranked[0][1] > 0:
            title = ranked[0][0]
        title_alts = [t for t, _ in ranked if t != title] or title_alts
    except TransientError:
        with Session(engine) as s:
            it = s.get(ContentItem, item_id)
            if it is not None and it.status == "running":
                it.status = "pending"  # 梯子重试时同一清单项重新来过
                s.add(it)
                s.commit()
        raise

    # ── 图表与头图（失败不阻塞）──
    md_text = await _attach_charts(md_text, 1, "[adapt]")
    ctx.set_progress(90, "渲染头图")
    md_text = await _attach_cover(md_text, title, 1, "[adapt]")

    # ── 定稿排版 + 清单项收尾 + 当日账 ──
    _finalize_article(article_id, title=title, title_alts=title_alts,
                      kb_sources=[{"media_id": mid, "title": src_title}], md_text=md_text)
    from .renderer import render_wechat_html
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is not None and (art.md or "").strip():
            art.html = render_wechat_html(art.md)
            art.status = "rendered"
            s.add(art)
            s.commit()
        it = s.get(ContentItem, item_id)
        if it is not None:
            it.status = "done"
            it.article_id = article_id
            s.add(it)
            s.commit()
        s.add(DailyLedger(day=_today(), phase="adapt", ok=True,
                          produced=f"清单项 #{item_id} → 文章 #{article_id}《{title[:24]}》（重复率 {ratio}%）"))
        s.commit()
    ctx.set_progress(100, f"完成：《{title[:24]}》已排版待发（改编自《{src_title[:20]}》，重复率 {ratio}%）")
    return {"adapted": True, "article_id": article_id, "item_id": item_id,
            "title": title, "ratio": ratio, "source": src_title}
