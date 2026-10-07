"""选题中心：idea 深研（联网检索增强）与热榜雷达的任务执行器。"""
from __future__ import annotations

import asyncio
import json

import httpx
from difflib import SequenceMatcher

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
        [{"role": "system", "content": render("idea_research_struct", AUDIENCE_NOTE=audience_note)},
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
    """今日热点选题提炼（R1.4）：站内对标议题 + 联网今日热点 × 租户画像 → 3 个切角落选题库。

    原料收集失败不阻塞（对标清单可空、联网可关），LLM 输入里如实标注缺料。"""
    from datetime import datetime, timedelta

    from ..models import BenchmarkVideo
    from ..tenant_brand import audience_note_of, brand_of

    ctx.set_progress(10, "收集站内热点原料（对标圈近 2 天议题）")
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

    ctx.set_progress(30, "联网搜今日行业热点（国内源优先）")
    web_lines: list[str] = []
    web_meta = {"refs": 0, "status": "skipped"}
    if search_enabled():
        try:
            # 单查询纪律（搜索通道限流）：中文时效查询，国内热点源优先
            refs, meta = await asyncio.to_thread(
                batch_search, ["AI 大模型 企业应用 最新消息 今天"])
            web_meta = meta
            web_lines = [r for r in (refs or "").split("\n\n") if r.strip()][:12]
        except Exception as e:  # noqa: BLE001 联网失败降级为纯站内，留痕
            web_meta = {"refs": 0, "status": f"error: {str(e)[:60]}"}

    ctx.set_progress(55, "结合创作者画像提炼切角")
    with Session(engine) as s:
        tenant_id = payload.get("tenant_id") or 1
        persona_text = brand_of(tenant_id)["persona"]
        audience_note = audience_note_of(tenant_id)

    material_parts = []
    if watch_items:
        material_parts.append("【对标圈近 2 天新视频（作者｜标题｜互动）】\n" + "\n".join(
            f"- {x['author']}｜{x['title']}｜{x['likes']}" for x in watch_items))
    else:
        material_parts.append("【对标圈近 2 天新视频】（无——站内采集空窗）")
    if web_lines:
        material_parts.append("【联网检索·今日行业热点】\n" + "\n".join(f"- {x}" for x in web_lines))
    else:
        material_parts.append(f"【联网检索·今日行业热点】（未生效：{web_meta.get('status')}）")
    material = "\n\n".join(material_parts)

    data = await gateway.complete_json(
        [{"role": "system", "content": render("trending_topics",
                                              PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
                                              MATERIAL=material)},
         {"role": "user", "content": "提炼今天可写的 3 个选题切角。"}],
        purpose="trending_topics", max_tokens=8000, thinking="disabled")

    items = [x for x in (data.get("topics") or []) if isinstance(x, dict) and x.get("title")]
    if not items:
        raise RuntimeError("热点提炼无有效产出（LLM 返回为空或结构不符），请重试")

    ctx.set_progress(85, "切角落库（选题库 draft）")
    created: list[int] = []
    with Session(engine) as s:
        for x in items[:3]:
            t = Topic(
                title=str(x.get("title"))[:60],
                angle=str(x.get("angle") or ""),
                audience=x.get("audience") if x.get("audience") in ("boss", "fde", "both") else "both",
                source_type="trending",
                source_ref=str(x.get("hotspot") or "")[:200],
                evidence={
                    "hotspot": x.get("hotspot", ""),
                    "why_now": x.get("why_now", ""),
                    "material": {"watch_count": len(watch_items), "web": web_meta},
                },
                status="draft",
            )
            s.add(t)
            s.flush()
            created.append(t.id)
        s.commit()

    ctx.set_progress(100, f"完成：{len(created)} 个切角已入选题库（trending）")
    return {"topic_ids": created, "material": {"watch": len(watch_items), "web": web_meta}}


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
