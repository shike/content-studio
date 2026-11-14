"""选题中心：idea 深研（联网检索增强）与热榜雷达的任务执行器。"""
from __future__ import annotations

import asyncio

from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import ResearchReport, Topic
from ..prompts import load, render
from ..search import batch_search, search_enabled



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
                max_tokens=1024)
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
    from ..tenant_brand import audience_note_of, brand_of

    with Session(engine) as s:
        t = s.get(Topic, topic_id)
        _tenant = t.tenant_id if t else 1
        persona_text = brand_of(_tenant)["persona"]
        audience_note = audience_note_of(_tenant)
    report_md = await gateway.complete(
        [{"role": "system", "content": render("idea_research_report", PERSONA=persona_text, AUDIENCE_NOTE=audience_note)},
         {"role": "user", "content": text + search_context}],
        max_tokens=5000)

    ctx.set_progress(60, "结构化评分中")
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "idea_research_struct", AUDIENCE_NOTE=audience_note,
            RECENT_TITLES="\n".join(f"- {t}" for t in recent_topic_titles(14)) or "（近 14 天无出题记录）")},
         {"role": "user", "content": report_md}])

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
        # 自动拒：低于 7.0 直接 rejected，不进待审队列
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
        mark_similar(topic.title, topic.id, s, line=topic.line if topic.line in ("persona", "leadgen") else "")
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


def recent_topic_titles(days: int = 14, limit: int = 24, line: str = "") -> list[str]:
    """近 N 天已有选题标题（防撞车清单：生成前注入 prompt，让模型主动避开）。三库独立按线。"""
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)  # created_at 是 naive UTC
    with Session(engine) as s:
        q = select(Topic.title).where(Topic.created_at >= cutoff)  # type: ignore[attr-defined]
        if line in ("persona", "leadgen"):
            q = q.where(Topic.line == line)  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(Topic.id.desc())  # type: ignore[attr-defined]
        ).all()
    out = []
    for t in rows:
        t = str(t or "").strip()
        if t:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def is_similar_title(title: str, days: int = 14, threshold: float = 0.55,
                     line: str = "") -> Optional[int]:
    """标题相似拦截：与近 N 天已有选题归一化相似度 ≥阈值时返回撞车选题 id。

    生成链路（热点/拆解）落库前硬拦截；人工链路（快速记题/深研）只标记不拦。
    三库独立（2026-11-02）：line 传入时只与本线选题比对，跨线不拦。"""
    from datetime import datetime, timedelta, timezone

    if not (title or "").strip():
        return None
    def _norm(t: str) -> str:
        import re as _re
        return _re.sub(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]", "", str(t)).lower()
    n = _norm(title)
    if not n:
        return None
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)  # created_at 是 naive UTC
    with Session(engine) as s:
        q = select(Topic).where(Topic.created_at >= cutoff)  # type: ignore[attr-defined]
        if line in ("persona", "leadgen"):
            q = q.where(Topic.line == line)  # type: ignore[attr-defined]
        for t in s.exec(q).all():
            if not t.title:
                continue
            if SequenceMatcher(None, n, _norm(t.title)).ratio() >= threshold:
                return t.id
    return None


def mark_similar(title: str, topic_id: int, session: Session, line: str = "") -> None:
    """相似选题标记（R1.1c）：与既有未产出选题标题相似度 ≥0.62 时记入 evidence.similar_to。
    三库独立：line 传入时只与本线比对。"""
    q = select(Topic).where(Topic.status.in_(["draft", "approved"]))  # type: ignore[union-attr]
    if line in ("persona", "leadgen"):
        q = q.where(Topic.line == line)  # type: ignore[attr-defined]
    rows = session.exec(q).all()
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
