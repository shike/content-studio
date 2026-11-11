"""选题中心：idea 深研（联网检索增强）与热榜雷达的任务执行器。"""
from __future__ import annotations

import asyncio
import json

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


@register_executor("trending_topics")
async def trending_topics(ctx: JobContext, payload: dict) -> dict:
    """今日热点靶子提炼（2026-11-11 观点怼热点主轴，原 R1.4 选题切角线升级）：
    四路原料 × 租户画像 → ≤3 个「今天能怼」的靶子（事件/为什么值得怼/切角×3/论据带源/辣度）。

    四路原料：百度热搜今日榜（主力）/ 联网检索（按画像生成的查询词，逐条 1 发）/ 对标圈
    近 2 天（议题参考，禁止当热点）/ 近 7 天已出题热点（禁写清单）。靶子过三层硬校验
    （对标圈拦截、7 天热点去重、批内去重）才落库，宁缺毋滥；批次替代：当日无下游的旧
    draft 让位新批次；无可怼=诚实空过（不挂失败）。原料收集失败不阻塞，LLM 输入里如实标注缺料。"""
    import re as _re
    from datetime import datetime, timedelta

    from ..models import Article as _A, BenchmarkVideo, Script as _S
    from ..tenant_brand import audience_note_of, brand_of
    from .hotlist import fetch_baidu_hot, fetch_tech_news

    tenant_id = payload.get("tenant_id") or 1
    with Session(engine) as s:
        persona_text = brand_of(tenant_id)["persona"]
        audience_note = audience_note_of(tenant_id)

    # 热点雷达·抖音话题侧（原 topic_radar 并入，2026-10-30）：先巡检已监测话题，
    # 飙升命中照旧走质量闸自动拆解入候选；失败不阻塞站外热点提炼
    radar_notes: list[str] = []
    try:
        from ..models import RadarTopic
        from ..radar.service import check_topic
        with Session(engine) as s:
            radar_rows = s.exec(
                select(RadarTopic).where(RadarTopic.enabled == True)  # noqa: E712
            ).all()
        for rrow in radar_rows:
            try:
                r = await check_topic(rrow)
                radar_notes.append(f"#{rrow.name}：新增 {r['new']} 条" + ("（飙升）" if r["spiked"] else ""))
            except Exception as e:  # noqa: BLE001 单话题失败不拖垮整轮
                radar_notes.append(f"#{rrow.name}：{type(e).__name__}: {str(e)[:60]}")
            await asyncio.sleep(3)  # 话题接口 pacing
    except Exception as e:  # noqa: BLE001 雷达侧整体失败不阻塞
        radar_notes.append(f"话题巡检失败：{type(e).__name__}: {str(e)[:60]}")

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

    ctx.set_progress(62, "结合创作者画像提炼靶子")
    material_parts = []
    material_parts.append("【国内科技/AI 资讯（爱范儿·IT之家，近 48 小时）——创作者领域的主力原料】\n"
                          + ("\n".join(f"- {x['title']}（{x['source']}{('/' + x['date']) if x['date'] else ''}）"
                                       for x in news_items)
                             if news_items else f"（未生效：{news_meta.get('status')}）"))
    material_parts.append("【百度热搜·今日实时榜（带在榜排名=热度硬证据，靶子第一选择）】\n"
                          + ("\n".join(f"- 热搜第{i}位：{x['word']}" + (f"｜{x['desc']}" if x.get("desc") else "")
                                       for i, x in enumerate(hot_items, 1))
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

    # ── 两段式挑靶（2026-11-11 三版修正的根治：口头「在榜优先」没用，改成结构上没退路）──
    # Stage 1：只给榜（board 专属 prompt + 程序化校验靶子确实长在榜内条目上）；
    # Stage 2：榜内挑不出才允许科技媒体/检索备胎，且批次明确标注备胎。
    ctx.set_progress(58, "整理你的近期爆款（续集靶子上下文）")
    own_hits: list[str] = []
    with Session(engine) as s:
        from ..models import SelfVideo
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
        ranked = sorted(vids, key=lambda v: int((v.stats or {}).get("play") or 0), reverse=True)[:4]
        for v in ranked:
            play = int((v.stats or {}).get("play") or 0)
            own_hits.append(f"- 「{(v.title or '')[:40]}」（播放 {play}）")
    own_hits_text = ("\n".join(own_hits)) if own_hits else "（暂无自有视频数据——续集打法本批不可用）"

    board_material = material_parts[1]  # 百度热搜段（带排名）
    board_words_norm = [_norm(x.get("word") or "") for x in hot_items if len(_norm(x.get("word") or "")) >= 6]

    def _board_only_check(batch_items: list[dict]) -> list[dict]:
        """程序化闸：stage1 靶子的 hotspot 必须真长在榜内条目上（防模型偷跑备料）。"""
        out: list[dict] = []
        for x in batch_items:
            hs = _norm(str(x.get("hotspot") or ""))
            if any(w in hs or hs in w for w in board_words_norm if w):
                out.append(x)
        return out

    async def _llm_targets(system_prompt: str, user_hint: str) -> list[dict]:
        data = await gateway.complete_json(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_hint}],
            purpose="trending_target", max_tokens=8000, thinking="disabled")
        return [x for x in (data.get("targets") or []) if isinstance(x, dict) and x.get("title")]

    ctx.set_progress(64, "第一段：热榜内挑靶（在榜优先，无退路）")
    items = _board_only_check(await _llm_targets(
        render("trending_target_board", PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
               TODAY=datetime.now().strftime("%Y-%m-%d"), OWN_HITS=own_hits_text,
               BOARD=board_material),
        "从热榜里挑靶子（最多 2 个，挑不出就空数组）。"))
    stage = "board" if items else "backup"
    if not items:
        ctx.set_progress(70, "榜内无可结合——第二段：科技媒体备胎（如实标注未在榜）")
        items = await _llm_targets(
            render("trending_target", PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
                   TODAY=datetime.now().strftime("%Y-%m-%d"), MATERIAL=material,
                   OWN_HITS=own_hits_text, BAN_LIST="\n".join(ban_lines)
                   or "（近 14 天无出题记录）"),
            "挑出今天能怼的靶子（最多 3 个，宁缺毋滥；榜内无可选时的备胎批）。")

    # 三层硬校验（程序化兜底，宁缺毋滥凑数不如少出）：对标圈拦截 → 7 天去重 → 批内去重 → 对标变体拦截
    watch_norms = [_norm(x["title"]) for x in watch_items if len(_norm(x["title"])) >= 10]
    batch: list[dict] = []
    seen_norms: list[str] = []
    for x in items:
        hotspot = str(x.get("hotspot") or "").strip()
        if not hotspot or "对标圈" in hotspot:
            continue
        if is_similar_title(str(x.get("title") or ""), line="persona"):  # 标题撞车兜底（提炼走人设线）
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
        # 诚实空过（2026-11-11 靶子产线定版：无可怼≠失败，失败是异常不是工作流）
        ctx.set_progress(100, "今日无可怼靶子（原料不足或全部未过新鲜度校验），诚实空过")
        return {"radar_notes": radar_notes, "topic_ids": [], "superseded": [],
                "targets": 0,
                "material": {"watch": len(watch_items), "news": news_meta,
                             "hot": hot_meta, "queries": queries, "web": search_metas}}

    ctx.set_progress(85, f"靶子落库（{len(batch)} 条过校验，选题库 draft）")
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
            tgt = {
                "event": str(x.get("event") or "")[:400],
                "why": str(x.get("why") or "")[:300],
                "angles": [str(a)[:90] for a in (x.get("angles") or []) if str(a).strip()][:3],
                "evidences": [
                    {"text": str(e.get("text") or "")[:220], "source": str(e.get("source") or "")[:60]}
                    for e in (x.get("evidences") or []) if isinstance(e, dict) and e.get("text")
                ][:3],
                "spice": x.get("spice") if x.get("spice") in ("low", "mid", "high") else "mid",
                "heat": str(x.get("heat") or ("百度热搜（在榜）" if stage == "board" else "未在榜（备胎）"))[:60],
            }
            t = Topic(
                title=str(x.get("title"))[:60],
                angle="",  # 主切角由用户备稿时点选（PUT /topics/{id}/angle）
                audience=x.get("audience") if x.get("audience") in ("boss", "fde", "both") else "both",
                source_type="trending",
                source_ref=str(x.get("hotspot") or "")[:200],
                line="persona",  # 站外热点提炼走人设线（获客号有独立挖号进料，2026-11-02）
                evidence={
                    "hotspot": x.get("hotspot", ""),
                    "target": tgt,  # 靶子产线（2026-11-11 观点怼热点主轴）
                    "material": {"watch_count": len(watch_items), "news": news_meta,
                                 "hot": hot_meta, "queries": q_status, "web": search_metas},
                },
                status="draft",
            )
            s.add(t)
            s.flush()
            created.append(t.id)
        s.commit()

    ctx.set_progress(100, f"完成：{len(created)} 条靶子入选题库（{'热榜内挑靶' if stage == 'board' else '备胎批：榜内无可结合'}；替代 {len(superseded)} 条当日旧批次）")
    return {"radar_notes": radar_notes, "topic_ids": created, "superseded": superseded,
            "targets": len(created), "stage": stage,
            "material": {"watch": len(watch_items), "news": news_meta,
                         "hot": hot_meta, "queries": queries, "web": search_metas}}


def recent_topic_titles(days: int = 14, limit: int = 24, line: str = "") -> list[str]:
    """近 N 天已有选题标题（防撞车清单：生成前注入 prompt，让模型主动避开）。三库独立按线。"""
    from datetime import datetime, timedelta

    cutoff = datetime.now() - timedelta(days=days)
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
