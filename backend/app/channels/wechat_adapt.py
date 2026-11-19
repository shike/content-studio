"""改编器（2026-11-11 源文改编产线）：每日从内容清单取一项，把源文改编成我们号的文章。

与挖坑线/深读线的本质区别：不再凭空综合——清单项就是一篇被评估过的高质量源文，
改编 = 保源文骨架与逐字原话（≤3 处标注引用），换我们号的判断口径与读者钩子。
出口闸门：原创度（与源文重复率 ≤30%，超线定向重写一次仍超=当日空过退回清单）；
违禁词机检沿用 service 的正则与定向重写。当日账落 daily_ledger。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import Content, Material, PipelineLedger
from ..prompts import load, render
from .wechat import (
    _article_generate_fail,
    _attach_charts,
    _attach_cover,
    _content_signature,
    _finalize_article,
    _gen_titles,
    _kb_style_violations,
    length_specs,
)
from ..articles.quality import (
    _structure_violations,
    dedupe_paragraphs,
    min_chars_or_raise,
    strip_fence as _strip_fence,
)

_SRC_CAP = 100000     # 源文全文上限（2026-11-18：3 万字腰斩，长手册改编只读到半篇）
_DUP_LINE = 30.0      # 原创度线：与源文重复率百分比上限
_MIN_CHARS = 1500     # 正文下限（规格 1500~2500，2026-11-19 用户定版）
_MAX_CHARS = 2500     # 正文上限（超线定向压缩一次；公众号正文长度限制）

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


_CHAPTER_RE = re.compile(r"^#{1,3}\s*第[一二三四五六七八九十百]+章\s*[　:：]?\s*(.{2,40})$", re.M)


def _chapter_titles(source: str) -> list[str]:
    """源文章节标题（「## 第五章　侦察：…」→「侦察：…」）。非分章文档返回空。"""
    titles: list[str] = []
    for m in _CHAPTER_RE.finditer(source or ""):
        t = (m.group(1) or "").strip()
        if len(t) >= 2:
            titles.append(t[:40])
    return list(dict.fromkeys(titles))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@register_executor("article_adapt", on_fail=_article_generate_fail)
async def article_adapt(ctx: JobContext, payload: dict) -> dict:
    """每日改编：清单取项（CAS）→ 拉源文全文 → 保骨架保原话换口径成文 → 原创度闸 → 机检 → 排版。

    payload.item_id 存在时改编指定清单项（手动生成直接执行），否则取队首（每日 05:00 调度）。"""
    from ..tenant_brand import brand_of
    from ..articles.ima_kb import fetch_full_doc, kb_config

    with Session(engine) as s:
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("改编器未启用：缺少 ima_kb_enabled / 凭证配置")
        # 卡死自愈：改编中超 2 小时无进展的项退回待改编（job 被杀/超时的残留）
        stale_cut = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        for it in s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                Material.stage == "running")).all():  # type: ignore[attr-defined]
            if it.updated_at and it.updated_at < stale_cut:
                it.stage = "pending"
                s.add(it)
        s.commit()
        target_id = int(payload.get("item_id") or 0)  # 2026-11-18：手动生成直接改编指定项（知识库「生成内容」入口）
        if target_id:
            item = s.get(Material, target_id)
            if item is None or item.origin_type != "kb_doc":  # type: ignore[attr-defined]
                raise RuntimeError(f"改编目标不存在（#{target_id}）")
            if (item.stage or "") != "pending":
                raise RuntimeError(f"清单项 #{target_id} 状态为 {item.stage}，不重复改编")
        else:
            item = s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                Material.stage == "pending")  # type: ignore[attr-defined]
                .order_by(Material.quality.op('->')('priority').desc().nulls_last(),
                          Material.quality.op('->')('score').desc().nulls_last(),
                          Material.id)).first()  # type: ignore[attr-defined]
        if item is None:
            s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
                              produced="", reason="清单空：无待改编项（宁缺勿滥）"))
            s.commit()
            ctx.set_progress(100, "清单空，今日不出稿")
            return {"adapted": False, "message": "清单空"}
        item.stage = "running"  # CAS 占位（单 worker + 日级 dedup_key，无并发竞争）
        s.add(item)
        s.commit()
        s.refresh(item)
        item_id, mid, src_title = item.id, item.origin_id, item.title
        q0 = item.quality or {}
        angle = str(q0.get("adapt_angle") or "")
        quotes = [str(x) for x in (q0.get("best_quotes") or [])]
        score = float(q0.get("score") or 0)
    client_id, api_key = cfg["client_id"], cfg["api_key"]

    def _release(reason: str, item_status: str = "pending", tag: str = "") -> None:
        """空过/未过收尾：清单项退回指定状态 + 当日账写原因。"""
        with Session(engine) as s:
            it = s.get(Material, item_id)
            if it is not None:
                it.stage = item_status
                it.status = "ignored" if item_status == "low" else "ready"
                if tag:
                    it.note = ((it.note or "") + f"｜{tag}").strip("｜")[:300]
                s.add(it)
            s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
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
            stale = [a for a in s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.kind == "long_article",  # type: ignore[attr-defined]
                Content.status.in_(["drafting", "failed"]))).all()  # type: ignore[attr-defined]
                if ((a.payload or {}).get("note") or "").startswith(f"改编：{src_title[:20]}")]
            from ..channels.engine import new_article_content

            if stale:
                art = stale[0]
                p0 = dict(art.payload or {})
                p0["article_state"] = "generating"
                art.payload = p0
                art.status = "drafting"
            else:
                art = new_article_content(
                    s, tenant_id=1, state="generating",
                    note=f"改编：{src_title[:40]}（源文评分 {score}）｜角度：{angle[:80]}")
            p = dict(art.payload or {})
            p["note"] = (f"改编：{src_title[:40]}（源文评分 {score}）｜角度：{angle[:80]}")
            art.payload = p
            s.add(art)
            s.commit()
            s.refresh(art)
            article_id = art.id

        # 标题组 + 改编成文
        persona_text = brand_of(1)["persona"]
        title_from_call, title_alts = await _gen_titles(
            persona_text, length_specs("deep"),
            "\n\n选题素材：\n" + source[:6000])
        from ..articles.prod_kit import ensure_prod_kit, kit_block

        ctx.set_progress(28, "抽取生产资料包（因果链/操作/边界/数字账）")
        kit = await ensure_prod_kit(item_id, text=source)
        kit_text = kit_block(kit)
        ctx.set_progress(30, "改编成文（保骨架保原话换口径，含深度四件套）")
        quotes_block = "\n".join(f"- {q}" for q in quotes) or "（无，本次不引用原话）"
        body = await gateway.complete(
            [{"role": "user", "content": render("article_adapt_write",
                                                PERSONA=persona_text,
                                                SOURCE=source, ANGLE=angle,
                                                QUOTES=quotes_block, KIT=kit_text)}],
            max_tokens=20000, thinking="disabled")  # 2026-11-19：4 万字源文开思考链会无限推理（40000 预算仍空正文），关掉后 83s 正常出稿
        body = _strip_fence(body)
        lines = body.split("\n")
        if not lines or not lines[0].lstrip().startswith("# "):
            raise TransientError("改编输出缺「# 标题」首行（疑似空正文或截断），稍后自动重试")

        # ── 深度覆盖核检（2026-11-18 车道一）：对照资料包逐条核对，缺环节定向补写一次 ──
        if kit_text:
            import json as _json

            ctx.set_progress(40, "深度覆盖核检（对照资料包）")
            chk = await gateway.complete_json(
                [{"role": "user", "content":
                  "对照生产资料包逐条核检这篇长文的覆盖：chain/actions/boundaries 每条判定是否被讲到"
                  "（numbers 不查；讲到一半也算 covered）。\n资料包：\n" + _json.dumps(kit, ensure_ascii=False)
                  + "\n\n成文：\n" + body[:6000] +
                  '\n输出 JSON：{"missing": ["没讲到的资料包条目原文，最多 3 条"]}，全覆盖给空数组'}],
                max_tokens=1024, thinking="disabled")
            missing = [str(x)[:120] for x in (chk or {}).get("missing") or []][:3]
            if missing:
                ctx.set_progress(45, f"深度缺 {len(missing)} 项，定向补写")
                body = _strip_fence(await gateway.complete(
                    [{"role": "user", "content":
                      "下面这篇公众号长文漏掉了资料包里的关键环节，定向补写：只补缺失环节的讲透"
                      "（因果链一步步、操作落到步骤），其余内容原样保留，不新增编造。\n"
                      "缺失环节：\n- " + "\n- ".join(missing) +
                      "\n\n资料包对照：\n" + kit_text +
                      "\n\n成文全文（在此基础修订，输出完整修订后全文，# 标题开头）：\n" + body}],
                    max_tokens=20000, thinking="disabled"))  # 2026-11-19：4 万字源文开思考链会无限推理（40000 预算仍空正文），关掉后 83s 正常出稿
                lines = body.split("\n")
                if not lines or not lines[0].lstrip().startswith("# "):
                    raise TransientError("深度补写输出缺「# 标题」首行，稍后自动重试")

        # ── 章节覆盖核检（2026-11-19）：分章源文整章不许无声消失（五阶段稿丢「回灌」事故）──
        chapters = _chapter_titles(source)
        if len(chapters) >= 3:
            async def _missing_by_llm(draft: str) -> list[str]:
                chk = await gateway.complete_json(
                    [{"role": "user", "content":
                      "这是一篇改编长文与源文的章节清单。逐章判断成文是否有**实质性对应内容**"
                      "（讲到了该章的核心判断或关键动作；只出现一个词、一句话带过都不算）。\n"
                      "章节清单：\n- " + "\n- ".join(chapters) +
                      "\n\n成文：\n" + draft[:8000] +
                      '\n输出 JSON：{"missing": ["未实质覆盖的章名，最多 6 条"]}，全部覆盖给空数组'}],
                    max_tokens=1024, thinking="disabled")
                return [str(x)[:40] for x in (chk or {}).get("missing") or []][:6]

            missing_ch = await _missing_by_llm(body)
            if missing_ch:
                ctx.set_progress(50, f"源文分章缺 {len(missing_ch)} 章（{'、'.join(missing_ch)}），定向补写")
                body = _strip_fence(await gateway.complete(
                    [{"role": "user", "content":
                      "下面这篇公众号长文改编自一篇分章源文，但整章内容缺失——读者会以为这本书只有这些内容。"
                      "请补写缺失的章节：每章至少一段，讲清这一章的核心判断与关键动作，放到正确的顺序位置；"
                      "其余内容原样保留，不许新增源文没有的事实与数字。\n"
                      "缺失章节：\n- " + "\n- ".join(missing_ch) +
                      "\n\n源文全文：\n" + source +
                      "\n\n成文全文（在此基础修订，输出完整修订后全文，# 标题开头）：\n" + body}],
                    max_tokens=20000, thinking="disabled"))  # 2026-11-19：4 万字源文开思考链会无限推理（40000 预算仍空正文），关掉后 83s 正常出稿
                lines = body.split("\n")
                if not lines or not lines[0].lstrip().startswith("# "):
                    raise TransientError("章节补写输出缺「# 标题」首行，稍后自动重试")
                missing_ch = await _missing_by_llm(body)
        else:
            missing_ch = []
        if missing_ch:
            # 补写一次仍缺章：整章丢失的稿子不进待发（确定性质量问题）
            with Session(engine) as s:
                art = s.get(Content, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = (lines[0].lstrip()[2:].strip() if lines else "")
                    art.body = "\n".join(lines[1:]).strip() if lines else ""
                    p = dict(art.payload or {})
                    p["article_state"] = "failed"
                    p["note"] = ((p.get("note") or "")
                                 + f"｜章节覆盖未过（缺 {'、'.join(missing_ch)}），不进待发")
                    art.payload = p
                    s.add(art)
                    s.commit()
            _release(f"章节覆盖未过（缺 {'、'.join(missing_ch)}），退回清单 #{item_id}", tag="缺章")
            ctx.set_progress(100, f"章节覆盖未过（缺 {'、'.join(missing_ch)}），本篇不进待发")
            return {"adapted": False, "missing_chapters": missing_ch, "message": "章节覆盖未过"}
        title = lines[0].lstrip()[2:].strip() or title_from_call or src_title[:30]
        md_text = "\n".join(lines[1:]).strip()
        min_chars_or_raise(md_text, "改编稿")

        # ── 原创度闸：与源文重复率 ≤30%，超线定向重写一次，仍超=当日空过退回清单 ──
        ctx.set_progress(55, "原创度检测（与源文比对）")
        ratio, hits = _dup_ratio(md_text, source)
        if ratio > _DUP_LINE:
            ctx.set_progress(62, f"重复率 {ratio}% 超线，定向降重写")
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_adapt_reduce",
                                                    HITS="\n".join(f"- {h}" for h in hits),
                                                    DRAFT=md_text, SOURCE=source[:_SRC_CAP])}],
                max_tokens=8192, thinking="disabled")
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
                art = s.get(Content, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.body = md_text
                    p = dict(art.payload or {})
                    p["article_state"] = "failed"
                    p["note"] = (p.get("note") or "") + f"｜原创度未过（重复率 {ratio}%），不进待发"
                    art.payload = p
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"原创度未过（{ratio}%），本项退回清单")
            return {"adapted": False, "ratio": ratio, "message": "原创度未过"}

        # ── 违禁词 + 结构机检 → 定向重写（签名守卫）──
        anchor_texts = [x for x in load("article_kb_anchors").split("## 范本")[1:] if len(x) > 200]
        violations = _kb_style_violations(md_text, anchor_texts) + _structure_violations(md_text)
        if violations:
            ctx.set_progress(78, f"机检命中 {len(violations)} 项，定向重写")
            polished = await gateway.complete(
                [{"role": "user", "content": render("article_kb_polish",
                                                    VIOLATIONS="\n".join(f"- {v}" for v in violations),
                                                    DRAFT=md_text)}],
                max_tokens=8192, thinking="disabled")
            polished = _strip_fence(polished)
            if polished and _content_signature(polished) == _content_signature(md_text):
                md_text = polished

        # ── 语言审校（2026-11-18 用户裁决：专业严谨优先）：AI味/翻译腔/硬造词/口水 → 定向修订一次 ──
        from ..articles.review import review_facts, review_language, review_reader, revise_once, score_titles
        ctx.set_progress(80, "语言审校（AI味/翻译腔/硬造词/口水）")
        lang_issues = await review_language(md_text)
        if lang_issues:
            revised = await revise_once(md_text, lang_issues)
            if revised and _content_signature(revised) == _content_signature(md_text):
                md_text = revised
            lang_left = await review_language(md_text) if lang_issues else []
        else:
            lang_left = []

        # ── 长度闸（2026-11-19 用户定版：公众号正文 1500~2500 字）──
        if len(md_text) > _MAX_CHARS:
            ctx.set_progress(81, f"正文 {len(md_text)} 字超上限 {_MAX_CHARS}，定向压缩")
            trimmed = _strip_fence(await gateway.complete(
                [{"role": "user", "content":
                 f"这篇公众号长文 {len(md_text)} 字，超过公众号正文长度上限 {_MAX_CHARS} 字。"
                 "把它压缩到 1500~2500 字：只删不增——删掉重复解释、修饰、与主线无关的枝节，"
                 "保留全部事实、数字、引用的原话、分章结构与各节判断；不许改写事实、不许新增内容。"
                 "\n\n输出完整修订后全文（# 标题开头）：\n" + md_text}],
                max_tokens=8192, thinking="disabled"))
            t_lines = trimmed.split("\n")
            if t_lines and t_lines[0].lstrip().startswith("# "):
                t_md = "\n".join(t_lines[1:]).strip()
                if (t_md and _MIN_CHARS <= len(t_md) <= _MAX_CHARS
                        and _content_signature(t_md) == _content_signature(md_text)):
                    md_text = t_md

        # ── 审校组（问题清单制）：事实对源=硬闸（拦编造），读者弃读点=建议（落 note 不拦）──
        ctx.set_progress(82, "审校组：事实对源（硬闸）+ 读者弃读点（建议）")
        issues = await review_facts(md_text, source)
        reader_points = await review_reader(md_text, persona_text)
        if issues:
            revised = await revise_once(md_text, issues)
            if revised and _content_signature(revised) == _content_signature(md_text):
                md_text = revised
                issues = await review_facts(md_text, source)
        if issues:
            with Session(engine) as s:
                it0 = s.get(Material, item_id)
                fails = (it0.note or "").count("审校未过") if it0 is not None else 0
            tag = f"审校未过×{fails + 1}"
            _release(f"事实审校二次未过（{'；'.join(issues[:2])}），清单项 #{item_id} 落 low 不再自动重试"
                     if fails >= 1 else
                     f"事实审校未过（{'；'.join(issues[:2])}），退回清单 #{item_id}",
                     item_status="low" if fails >= 1 else "pending", tag=tag)
            with Session(engine) as s:
                art = s.get(Content, article_id)
                if art is not None:
                    art.status = "failed"
                    art.title = title
                    art.body = md_text
                    p = dict(art.payload or {})
                    p["article_state"] = "failed"
                    p["note"] = (p.get("note") or "") + f"｜事实审校未过（{issues[0][:40]}），不进待发"
                    art.payload = p
                    s.add(art)
                    s.commit()
            ctx.set_progress(100, f"事实审校未过（{len(issues)} 项），本篇不进待发")
            return {"adapted": False, "issues": len(issues), "message": "事实审校未过"}

        # ── 标题评分（五维，自动选主标题；正题/来源题/备选全进候选池）──
        ctx.set_progress(88, "标题评分")
        cands = [title] + [t for t in (title_alts or []) if t]
        if title_from_call and title_from_call not in cands:
            cands.append(title_from_call)
        ranked = await score_titles(cands, persona_text)
        if ranked and ranked[0][1] > 0:
            title = ranked[0][0]

        # ── 结构兜底：完全重复的长段确定性去重（#56 实测：检验清单同时出现在开头与结尾）──
        deduped = dedupe_paragraphs(md_text)
        if deduped != md_text:
            md_text = deduped
            ctx.set_progress(85, "结构兜底：删除完全重复的段落")
    except TransientError:
        with Session(engine) as s:
            it = s.get(Material, item_id)
            if it is not None and it.stage == "running":
                it.stage = "pending"  # 梯子重试时同一清单项重新来过
                s.add(it)
                s.commit()
        raise

    # ── 图表与头图（失败不阻塞）──
    md_text = await _attach_charts(md_text, 1, "[adapt]")
    ctx.set_progress(90, "渲染头图")
    md_text = await _attach_cover(md_text, title, 1, "[adapt]")

    # ── 定稿排版 + 清单项收尾 + 当日账 ──
    _finalize_article(article_id, title=title,
                      kb_sources=[{"media_id": mid, "title": src_title}], md_text=md_text)
    from ..articles.renderer import render_wechat_html
    with Session(engine) as s:
        art = s.get(Content, article_id)
        if art is not None and (art.body or "").strip():
            p = dict(art.payload or {})
            p["html"] = render_wechat_html(art.body)
            p["article_state"] = "rendered"
            if lang_left:  # 语言审校修订后仍有残留：落 note 让用户终审时知道（不拦发布）
                p["note"] = (p.get("note") or "") + "｜语言审校残留：" + "；".join(
                    x[:44] for x in lang_left[:2])[:200]
            if reader_points:  # 读者审校=编辑口味建议，落 note 给用户终审参考，不拦发布
                p["note"] = (p.get("note") or "") + "｜读者审校建议：" + "；".join(
                    x[:44] for x in reader_points[:2])[:200]
            art.payload = p
            art.status = "ready"
            s.add(art)
            s.commit()
        it = s.get(Material, item_id)
        if it is not None:
            it.stage = "done"
            q1 = dict(it.quality or {})
            q1["article_id"] = article_id
            it.quality = q1
            s.add(it)
            s.commit()
            from ..materials.service import record_usage

            record_usage(s, tenant_id=it.tenant_id or 1, material_id=item_id,
                         target_type="content", target_id=article_id, role="source")
        s.add(PipelineLedger(day=_today(), pipeline="wechat_adapt", ok=True,
                          produced=f"清单项 #{item_id} → 文章 #{article_id}《{title[:24]}》（重复率 {ratio}%）"))
        s.commit()
    ctx.set_progress(100, f"完成：《{title[:24]}》已排版待发（改编自《{src_title[:20]}》，重复率 {ratio}%）")
    return {"adapted": True, "article_id": article_id, "item_id": item_id,
            "title": title, "ratio": ratio, "source": src_title}
