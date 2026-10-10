"""选题中心：idea 深研（联网检索增强）与热榜雷达的任务执行器。"""
from __future__ import annotations

import asyncio
import json

import httpx
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import ResearchReport, Topic
from ..prompts import load, render
from ..search import batch_search, search_enabled
from ..settings import settings



@register_executor("idea_research")
async def idea_research(ctx: JobContext, payload: dict) -> dict:
    topic_id = payload["topic_id"]
    text = payload.get("text", "")

    # 联网检索增强：单查询 + 缓存（限流纪律），执行结果如实记录，绝不静默吞
    search_meta: dict = {"query": "", "refs": 0, "status": "skipped"}
    search_context = ""
    if search_enabled() and gateway.is_configured():
        ctx.set_progress(5, "生成检索词")
        try:
            q_data = await gateway.complete_json(
                [{"role": "system", "content": load("research_queries")},
                 {"role": "user", "content": text}],
                purpose="research_queries", max_tokens=1024)
            queries = [str(q) for q in (q_data.get("queries") or []) if str(q).strip()]
            if queries:
                ctx.set_progress(8, f"联网检索：{queries[0][:24]}")
                refs, meta = await asyncio.to_thread(batch_search, queries)
                search_meta = meta
                if refs:
                    search_context = "\n\n【联网检索参考】\n" + refs
        except Exception as e:  # noqa: BLE001 检索失败不阻塞深研，但要留痕
            search_meta = {"query": "", "refs": 0, "status": f"error: {str(e)[:60]}"}

    ctx.set_progress(15, "深度研究中（报告生成）")
    from ..models import Topic as _Topic
    from ..tenant_brand import audience_note_of, brand_of

    with Session(engine) as s:
        t = s.get(_Topic, topic_id)
        _tenant = t.tenant_id if t else 1
        persona_text = brand_of(_tenant)["persona"]
        audience_note = audience_note_of(_tenant)
    report_md = await gateway.complete(
        [{"role": "system", "content": render("idea_research_report", PERSONA=persona_text, AUDIENCE_NOTE=audience_note)},
         {"role": "user", "content": text + search_context}],
        purpose="idea_research_report", max_tokens=5000)

    ctx.set_progress(60, "结构化评分中")
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "idea_research_struct", AUDIENCE_NOTE=audience_note,
            RECENT_TITLES="\n".join(f"- {t}" for t in recent_topic_titles(14)) or "（近 14 天无出题记录）")},
         {"role": "user", "content": report_md}],
        purpose="idea_research_struct")

    audience = data.get("audience")
    with Session(engine) as s:
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise RuntimeError(f"topic {topic_id} 不存在")
        topic.title = (data.get("title") or text)[:60]
        topic.angle = data.get("angle", "")
        topic.audience = audience if audience in ("boss", "fde", "both") else "both"
        try:
            topic.score = max(0.0, min(10.0, float(data.get("score") or 0)))
        except (TypeError, ValueError):
            topic.score = 0.0
        # 自动拒（PRD R1.2b）：低于 7.0 直接 rejected，不进待审队列
        if topic.score < 7.0 and topic.status == "draft":
            topic.status = "rejected"
        topic.score_breakdown = data.get("score_breakdown") or {}
        topic.evidence = {
            "pain_points": data.get("pain_points") or [],
            "hooks": data.get("hooks") or [],
            "business_fit": data.get("business_fit") or {},
            "competitors": data.get("competitors", ""),
            "reason": data.get("reason", ""),
            "search": search_meta,
        }
        mark_similar(topic.title, topic.id, s)
        s.add(topic)
        s.add(ResearchReport(topic_id=topic_id, content=report_md,
                             model=gateway.active()["model"]))
        s.commit()

    if search_meta["status"].startswith("ok"):
        tail = (f"完成（联网检索 {search_meta['refs']} 条参考，"
                f"引擎 {search_meta.get('provider', '?')}）")
    else:
        tail = f"完成（联网检索未生效：{search_meta['status']}，本报告为纯模型知识）"
    ctx.set_progress(100, tail)
    return {"topic_id": topic_id, "search": search_meta}


@register_executor("trending_topics")
async def trending_topics(ctx: JobContext, payload: dict) -> dict:
    """今日热点选题提炼（R1.4，2026-10-08 质量重构）：四路原料 × 租户画像 → ≤3 个切角。

    四路原料：百度热搜今日榜（主力）/ 联网检索（按画像生成的查询词，逐条 1 发）/ 对标圈
    近 2 天（议题参考，禁止当热点）/ 近 7 天已出题热点（禁写清单）。切角过三层硬校验
    （对标圈拦截、7 天热点去重、批内去重）才落库，宁缺毋滥；批次替代：当日无下游的旧
    draft 让位新批次。原料收集失败不阻塞，LLM 输入里如实标注缺料。"""
    import re as _re
    from datetime import datetime, timedelta

    from ..models import Article as _A, BenchmarkVideo, Script as _S
    from ..tenant_brand import audience_note_of, brand_of
    from .hotlist import fetch_baidu_hot, fetch_tech_news

    tenant_id = payload.get("tenant_id") or 1
    with Session(engine) as s:
        persona_text = brand_of(tenant_id)["persona"]
        audience_note = audience_note_of(tenant_id)

    def _norm(t: str) -> str:
        return _re.sub(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-]+", "", str(t)).lower()

    ctx.set_progress(8, "收集站内原料（对标圈近 2 天议题）")
    cutoff = datetime.now() - timedelta(days=2)
    with Session(engine) as s:
        rows = s.exec(select(BenchmarkVideo).where(
            BenchmarkVideo.created_at >= cutoff,  # type: ignore[attr-defined]
            BenchmarkVideo.scan_status != "ignored")).all()  # type: ignore[attr-defined]
        watch_items = [
            {"author": b.author, "title": b.title,
             "likes": (b.stats or {}).get("digg_count") or (b.stats or {}).get("likes", "")}
            for b in rows if b.title
        ][:40]

    # 近 7 天已出题热点（旧闻连出两天=质量事故：最高法 9-07 旧闻曾连续两天被当"今日"出题）
    ctx.set_progress(16, "整理禁写清单（7 天热点 + 14 天已有选题）")
    prev_hotspots: list[str] = []
    prev_norms: list[str] = []
    pcutoff = datetime.now() - timedelta(days=7)
    with Session(engine) as s:
        for t in s.exec(select(Topic).where(
                Topic.source_type == "trending",  # type: ignore[attr-defined]
                Topic.created_at >= pcutoff)).all():  # type: ignore[attr-defined]
            h = str((t.evidence or {}).get("hotspot") or t.source_ref or "").strip()
            if h:
                prev_hotspots.append(h[:110])
                prev_norms.append(_norm(h))
    recent_titles = recent_topic_titles(14)  # 本模块尾部定义的防撞车件
    ban_lines = [f"- {h}" for h in prev_hotspots]
    ban_lines += [f"- （已有选题）{t}" for t in recent_titles]

    ctx.set_progress(24, "抓国内热点榜与科技资讯（百度热搜+爱范儿/IT之家）")
    hot_items, hot_meta = await asyncio.to_thread(fetch_baidu_hot, 15)
    news_items, news_meta = await asyncio.to_thread(fetch_tech_news)

    ctx.set_progress(36, "按画像生成今日检索词")
    queries: list[str] = []
    q_status = "skipped"
    if search_enabled() and gateway.is_configured():
        try:
            pick = await gateway.complete_json(
                [{"role": "user", "content": render(
                    "trending_queries", PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
                    TODAY=datetime.now().strftime("%Y-%m-%d %A"),
                    NEWS_TITLES=json.dumps([x["title"] for x in news_items[:10]],
                                           ensure_ascii=False),
                    HOT_WORDS=json.dumps([x["word"] for x in hot_items[:12]], ensure_ascii=False),
                    WATCH_TITLES=json.dumps([x["title"] for x in watch_items[:8]],
                                            ensure_ascii=False))}],
                purpose="trending_queries", max_tokens=1024, thinking="disabled")
            queries = [str(q).strip() for q in (pick.get("queries") or [])
                       if str(q).strip()][:4]
            q_status = "ok" if queries else "empty"
        except Exception as e:  # noqa: BLE001 降级默认词，留痕
            q_status = f"error: {type(e).__name__}: {str(e)[:60]}"
    if not queries:
        queries = ["AI 大模型 企业应用 最新消息 今天"]

    ctx.set_progress(50, f"联网检索（{len(queries)} 条查询·国内源优先）")
    web_lines: list[str] = []
    search_metas: list[dict] = []
    for q in queries:  # 每查询 1 发（限流纪律；重复词命中缓存不计新检索）
        try:
            refs, meta = await asyncio.to_thread(batch_search, [q])
            search_metas.append({"query": q, **meta})
            for ln in (refs or "").split("\n"):
                if ln.strip():
                    web_lines.append(f"（检索词：{q}）{ln}")
        except Exception as e:  # noqa: BLE001 单查询失败不拖垮整批
            search_metas.append({"query": q, "status": f"error: {str(e)[:60]}"})
    web_lines = web_lines[:15]

    ctx.set_progress(62, "结合创作者画像提炼切角")
    material_parts = []
    material_parts.append("【国内科技/AI 资讯（爱范儿·IT之家，近 48 小时）——创作者领域的主力原料】\n"
                          + ("\n".join(f"- {x['title']}（{x['source']}{('/' + x['date']) if x['date'] else ''}）"
                                       for x in news_items)
                             if news_items else f"（未生效：{news_meta.get('status')}）"))
    material_parts.append("【百度热搜·今日实时榜（泛热点——只有能与创作者领域强结合的才可选）】\n"
                          + ("\n".join(f"- {x['word']}" + (f"｜{x['desc']}" if x.get("desc") else "")
                                       for x in hot_items)
                             if hot_items else f"（未生效：{hot_meta.get('status')}）"))
    material_parts.append("【联网检索·按画像生成的检索词】\n" + ("\n".join(
        f"- {x}" for x in web_lines) if web_lines else f"（未生效：{q_status}）"))
    if watch_items:
        material_parts.append("【对标圈近 2 天新视频（同行在写什么——议题参考，不是热点事实）】\n"
                              + "\n".join(f"- {x['author']}｜{x['title']}｜{x['likes']}"
                                          for x in watch_items))
    else:
        material_parts.append("【对标圈近 2 天新视频】（无——站内采集空窗）")
    material = "\n\n".join(material_parts)

    data = await gateway.complete_json(
        [{"role": "system", "content": render("trending_topics",
                                              PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
                                              TODAY=datetime.now().strftime("%Y-%m-%d"),
                                              MATERIAL=material,
                                              BAN_LIST="\n".join(ban_lines)
                                              or "（近 14 天无出题记录）")},
         {"role": "user", "content": "提炼今天可写的 3 个选题切角。"}],
        purpose="trending_topics", max_tokens=8000, thinking="disabled")

    items = [x for x in (data.get("topics") or []) if isinstance(x, dict) and x.get("title")]

    # 三层硬校验（程序化兜底，宁缺毋滥凑数不如少出）：对标圈拦截 → 7 天去重 → 批内去重 → 对标变体拦截
    watch_norms = [_norm(x["title"]) for x in watch_items if len(_norm(x["title"])) >= 10]
    batch: list[dict] = []
    seen_norms: list[str] = []
    for x in items:
        hotspot = str(x.get("hotspot") or "").strip()
        if not hotspot or "对标圈" in hotspot:
            continue
        if is_similar_title(str(x.get("title") or "")):  # 标题撞车：不同热点落到同一角度的兜底
            continue
        hn = _norm(hotspot)
        if any(SequenceMatcher(None, hn, p).ratio() >= 0.55 or (len(p) >= 12 and (hn in p or p in hn))
               for p in prev_norms):
            continue
        if any(SequenceMatcher(None, hn, b).ratio() >= 0.55 for b in seen_norms):
            continue
        if any(hn in w or SequenceMatcher(None, hn, w).ratio() >= 0.5 for w in watch_norms):
            continue  # hotspot 实为对标视频标题的变体
        seen_norms.append(hn)
        batch.append(x)
        if len(batch) >= 3:
            break
    if not batch:
        raise RuntimeError("热点提炼无有效切角（原料不足或全部未过新鲜度校验），请重试")

    ctx.set_progress(85, f"切角落库（{len(batch)} 条过校验，选题库 draft）")
    created: list[int] = []
    superseded: list[int] = []
    with Session(engine) as s:
        # 批次替代：当日更早批次且无下游产物的 draft 让位（今日热点=今日最新一批）
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for t in s.exec(select(Topic).where(
                Topic.source_type == "trending",  # type: ignore[attr-defined]
                Topic.status == "draft",  # type: ignore[attr-defined]
                Topic.created_at >= day_start)).all():  # type: ignore[attr-defined]
            has_down = s.exec(select(_S).where(_S.topic_id == t.id)).first() is not None \
                or s.exec(select(_A).where(_A.topic_id == t.id)).first() is not None
            if not has_down:
                ev = dict(t.evidence or {})
                ev["superseded"] = f"被 {datetime.now():%H:%M} 新批次替代"
                t.evidence = ev
                t.status = "rejected"
                s.add(t)
                superseded.append(t.id)
        for x in batch:
            t = Topic(
                title=str(x.get("title"))[:60],
                angle=str(x.get("angle") or ""),
                audience=x.get("audience") if x.get("audience") in ("boss", "fde", "both") else "both",
                source_type="trending",
                source_ref=str(x.get("hotspot") or "")[:200],
                evidence={
                    "hotspot": x.get("hotspot", ""),
                    "why_now": x.get("why_now", ""),
                    "material": {"watch_count": len(watch_items), "news": news_meta,
                                 "hot": hot_meta, "queries": q_status, "web": search_metas},
                },
                status="draft",
            )
            s.add(t)
            s.flush()
            created.append(t.id)
        s.commit()

    ctx.set_progress(100, f"完成：{len(created)} 个切角已入选题库"
                          f"（替代 {len(superseded)} 条当日旧批次）")
    return {"topic_ids": created, "superseded": superseded,
            "material": {"watch": len(watch_items), "news": news_meta,
                         "hot": hot_meta, "queries": queries, "web": search_metas}}


def recent_topic_titles(days: int = 14, limit: int = 24) -> list[str]:
    """近 N 天已有选题标题（防撞车清单：生成前注入 prompt，让模型主动避开）。"""
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=days)
    with Session(engine) as s:
        rows = s.exec(
            select(Topic.title).where(Topic.created_at >= cutoff)  # type: ignore[attr-defined]
            .order_by(Topic.id.desc())  # type: ignore[attr-defined]
        ).all()
    out = []
    for t in rows:
        t = str(t or "").strip()
        if t:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def is_similar_title(title: str, days: int = 14, threshold: float = 0.55) -> Optional[int]:
    """标题相似拦截：与近 N 天已有选题归一化相似度 ≥阈值时返回撞车选题 id。

    生成链路（热点/拆解）落库前硬拦截；人工链路（快速记题/深研）只标记不拦。"""
    from datetime import datetime, timedelta

    if not (title or "").strip():
        return None
    def _norm(t: str) -> str:
        import re as _re
        return _re.sub(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]", "", str(t)).lower()
    n = _norm(title)
    if not n:
        return None
    cutoff = datetime.now() - timedelta(days=days)
    with Session(engine) as s:
        for t in s.exec(select(Topic).where(Topic.created_at >= cutoff)).all():  # type: ignore[attr-defined]
            if not t.title:
                continue
            if SequenceMatcher(None, n, _norm(t.title)).ratio() >= threshold:
                return t.id
    return None


def mark_similar(title: str, topic_id: int, session: Session) -> None:
    """相似选题标记（R1.1c）：与既有未产出选题标题相似度 ≥0.62 时记入 evidence.similar_to。"""
    rows = session.exec(
        select(Topic).where(Topic.status.in_(["draft", "approved"]))).all()  # type: ignore[union-attr]
    similar = []
    for t in rows:
        if t.id == topic_id or not t.title:
            continue
        if SequenceMatcher(None, title, t.title).ratio() >= 0.62:
            similar.append(t.id)
    if similar:
        topic = session.get(Topic, topic_id)
        if topic is not None:
            ev = dict(topic.evidence or {})
            ev["similar_to"] = similar[:5]
            topic.evidence = ev
            session.add(topic)
