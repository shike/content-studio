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
    """今日热点靶子提炼（2026-11-12 第六版=扩供应不降标准，观点怼热点主轴）：
    五路原料 × 租户画像 → ≤6 个半句话判决靶子（sentence/context/heat/spice）。

    五路原料（2026-11-12 生产服务器逐一实测）：百度热搜前 50 / 抖音热点榜 50（他发布
    平台自己的榜，第一优先）/ 知乎热榜 30 / 科技媒体 RSS 四源 48h / 对标圈近 2 天
    （议题参考，禁止当热点）。三榜条目跨榜合并：同一件事多榜同在=真热点，复合热度
    证据排最前。靶子过三层硬校验（对标圈拦截、7 天热点去重、批内去重）才落库，
    宁缺毋滥；批次替代：当日无下游的旧 draft 让位新批次；无可怼=诚实空过（不挂失败）。
    原料收集失败不阻塞，LLM 输入里如实标注缺料。"""
    import re as _re
    from datetime import datetime, timedelta

    from ..models import Article as _A, BenchmarkVideo, Script as _S
    from ..tenant_brand import audience_note_of, brand_of
    from .hotlist import fetch_baidu_hot, fetch_douyin_hot, fetch_github_hot, fetch_tech_news, fetch_zhihu_hot

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

    ctx.set_progress(24, "抓国内热点榜与科技资讯（三榜+六源科技媒体+GitHub 新库）")
    hot_items, hot_meta = await asyncio.to_thread(fetch_baidu_hot, 50)
    dy_items, dy_meta = await asyncio.to_thread(fetch_douyin_hot, 50)
    zh_items, zh_meta = await asyncio.to_thread(fetch_zhihu_hot, 30)
    news_items, news_meta = await asyncio.to_thread(fetch_tech_news)
    gh_items, gh_meta = await asyncio.to_thread(fetch_github_hot)

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

    # ── 行业挑靶（2026-11-11 第四版定版：靶子=AI/科技行业内大事件，社会热点一律不选）──
    # 四轮失败反推的用户拍摄史事实：他只拍行业内的事（Anthropic/Workbuddy/提示词工程/业务宣言），
    # 零条社会热点。泛榜借热点（泡面套验收）整类废除；热度=行业内刷屏（媒体+同行+在榜加分）。
    ctx.set_progress(58, "整理你的近期爆款（类目标准+续集上下文）")
    own_hits: list[str] = []
    with Session(engine) as s:
        from ..models import SelfVideo
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
        ranked = sorted(vids, key=lambda v: int((v.stats or {}).get("play") or 0), reverse=True)[:4]
        for v in ranked:
            play = int((v.stats or {}).get("play") or 0)
            own_hits.append(f"- 「{(v.title or '')[:40]}」（播放 {play}）")
    own_hits_text = ("\n".join(own_hits)) if own_hits else "（暂无自有视频数据）"

    # 行业菜单：四源科技媒体 + 三榜在榜科技条目（跨榜合并=复合热度证据）+ 对标圈 + 检索线索
    _TECH_KW = ("AI", "人工智能", "大模型", "模型", "智能体", "agent", "机器人", "芯片", "算力",
                "开源", "自动驾驶", "智能驾驶", "代码", "软件", "互联网", "科技", "数字", "算法",
                "半导体", "云计算", "无人机", "手机", "汽车",
                "字节", "阿里", "腾讯", "百度", "华为", "小米", "京东", "美团", "网易", "快手",
                "deepseek", "openai", "anthropic", "谷歌", "google", "微软", "meta", "苹果",
                "特斯拉", "英伟达", "亚马逊", "claude", "chatgpt", "gemini", "copilot",
                "豆包", "通义", "文心", "夸克", "可灵", "即梦", "扣子", "coze", "dify",
                "飞书", "钉钉", "workbuddy", "智谱", "kimi", "minimax", "阶跃",
                "gpu", "显卡", "服务器", "数据中心", "v100", "a100", "h100", "4090", "5090",
                "鸿蒙", "昇腾", "问界", "比亚迪", "蔚来", "小鹏", "极氪", "联想", "大疆",
                "宇树", "unitree", "manus", "suno", "cursor", "notion", "figma", "vscode",
                "qwen", "grok", "xai", "deepmind", "saas", "办公", "ios", "android", "app", "github", "仓库")
    raw_board = []
    for label, rows in (("抖音热点", dy_items), ("知乎热榜", zh_items), ("百度热搜", hot_items)):
        raw_board += [(label, i + 1, str(x.get("word") or ""), str(x.get("desc") or ""))
                      for i, x in enumerate(rows)]
    groups: list[dict] = []
    for label, rank, word, desc in raw_board:
        nw = _norm(word)
        if not nw:
            continue
        g = next((g for g in groups
                  if nw == g["norm"] or (len(nw) >= 4 and nw in g["norm"])
                  or (len(g["norm"]) >= 4 and g["norm"] in nw)), None)
        if g:
            g["boards"].append(f"{label}第{rank}位")
            if desc and len(g["descs"]) < 2:
                g["descs"].append(desc[:36])
        else:
            groups.append({"norm": nw, "word": word[:44], "boards": [f"{label}第{rank}位"],
                           "descs": [desc[:36]] if desc else []})

    def _kw_hit(g: dict) -> bool:
        text = (g["word"] + " " + " ".join(g["descs"])).lower()
        return any(k.lower() in text for k in _TECH_KW)

    def _bl(g: dict) -> str:
        d = f"｜{g['descs'][0]}" if g["descs"] else ""
        return f"- {g['word']}（{'·'.join(g['boards'])}）{d}"

    board_multi = [_bl(g) for g in groups if len(g["boards"]) > 1 and _kw_hit(g)]
    board_single = [_bl(g) for g in groups if len(g["boards"]) == 1 and _kw_hit(g)]
    board_lines = ([f"- ★真热点·多榜同在：{ln}" for ln in board_multi] + board_single)
    gh_lines = [f"- {x['word']}（★{x['stars']}）｜{x['desc']}" for x in gh_items]
    industry_parts = [
        "【科技媒体+AI 垂媒（行业动态，48h 内，六源）】\n"
        + ("\n".join(f"- {x['title']}（{x['source']}{('/' + x['date']) if x['date'] else ''}）"
                     for x in news_items) if news_items else f"（未生效：{news_meta.get('status')}）"),
        "【三榜在榜科技条目（在榜排名=热度硬证据；多榜同在=真热点第一优先）】\n"
        + ("\n".join(board_lines) if board_lines else "（当前三榜均无科技类条目）"),
        "【GitHub 近 5 天新起爆仓库（开源圈内刷屏信号，星数=知名度证据）】\n"
        + ("\n".join(gh_lines) if gh_lines else f"（未生效：{gh_meta.get('status')}）"),
    ]
    if web_lines:
        industry_parts.append("【联网检索线索（今日补充，可能含隔夜大事件）】\n"
                              + "\n".join(f"- {x}" for x in web_lines[:10]))
    if watch_items:
        # 圈内刷屏主题聚类（2026-11-12 v7）：多家同行同题=争论已被验证（比榜排名更硬的价值
        # 信号——同行已经用互动量投过票），供模型锻自己的判决；同行标题本身仍禁当靶子
        theme_groups: list[dict] = []
        for x in watch_items:
            tn = _norm(x["title"])
            if len(tn) < 6:
                continue
            g = next((g for g in theme_groups
                      if SequenceMatcher(None, tn, g["norm"]).ratio() >= 0.42), None)
            if g:
                g["members"].append(x)
                if len(tn) < len(g["norm"]):
                    g["norm"] = tn
                    g["label"] = x["title"]
            else:
                theme_groups.append({"norm": tn, "label": x["title"], "members": [x]})
        hot_themes = [g for g in theme_groups
                      if len({m["author"] for m in g["members"]}) >= 3]
        parts = ["【对标圈近 2 天新视频（圈内热度证据；同行视频标题本身不当靶子）】"]
        if hot_themes:
            parts.append("★多家同行在拍的主题=圈内争论已被验证——锻你自己的判决，别复述同行：")
            parts += [f"- ★{g['label'][:40]}（{len({m['author'] for m in g['members']})} 家同行在拍）"
                      for g in hot_themes]
        parts += [f"- {x['author']}｜{x['title']}｜{x['likes']}" for x in watch_items]
        industry_parts.append("\n".join(parts))
    industry = "\n\n".join(industry_parts)

    async def _llm_targets(system_prompt: str, user_hint: str) -> list[dict]:
        data = await gateway.complete_json(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": user_hint}],
            purpose="trending_target", max_tokens=8000, thinking="disabled")
        return [x for x in (data.get("targets") or []) if isinstance(x, dict) and x.get("sentence")]

    ctx.set_progress(64, "行业事件挑靶（社会热点不在菜单）")
    items = await _llm_targets(
        render("trending_target_industry", PERSONA=persona_text, AUDIENCE_NOTE=audience_note,
               TODAY=datetime.now().strftime("%Y-%m-%d"), OWN_HITS=own_hits_text,
               INDUSTRY=industry, BAN_LIST="\n".join(ban_lines) or "（近 14 天无出题记录）"),
        "把菜单里的行业实体逐个过一遍，每个够格的大事件先锻造半句话判决，锻不出来就跳过；"
        "多榜同在的真热点和同行多家在拍的优先。最多 6 个，宁缺毋滥，没有就空数组。")

    # 程序化科技闸：靶子（标题+hotspot）必须含科技/行业关键词，防模型拿社会新闻凑数
    _CLAUSE_PUNCT = _re.compile(r"[，,。？?：:；;、]")
    # 劝告开头闸（2026-11-12 实战漏网「显卡跳水别急着抄底」）：行动指导不是判决——
    # 他的判决只有「靶子名+判词」（祝XX倒闭/XX就是收割），劝告句式一律废
    _ADVICE_OPENER = _re.compile(r"^(别|先|快|应|该|要|记得|建议|试试|赶紧|需要|必须|最好|千万|不妨|记得)")
    # 判词词根闸（2026-11-12 v7）：句子必须含判词词根——「谁干了什么」的新闻复述不算判决
    # （实战漏网「英伟达敢罢免黄仁勋」「软银别想买下ABB」全是转发新闻，没有立场）
    _JUDGE_KW = ("就是", "全是", "纯", "垃圾", "收割", "智商税", "骗局", "割韭菜", "倒闭", "必死",
                 "迟早", "注定", "没法用", "没人用", "认输", "吹爆", "吹上", "吹过", "神话", "泡沫",
                 "凉了", "要凉", "烂", "难用", "复杂", "别碰", "高估", "低估", "幻觉", "伪需求",
                 "套壳", "翻车", "接盘", "过时", "淘汰", "取代", "终结", "杀死", "白干", "白嫖",
                 "白送", "硬刚", "落后", "耍猴", "当猴耍", "镰刀", "韭菜")
    def _industry_gate(batch_items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for x in batch_items:
            sentence = str(x.get("sentence") or "").strip()
            text = _norm(f"{sentence} {x.get('context') or ''}").lower()
            if not any(k.lower() in text for k in _TECH_KW):
                continue  # 非行业靶子
            if not sentence or len(sentence) > 16 or _CLAUSE_PUNCT.search(sentence):
                continue  # 半句话闸：超长/复合句物理废掉（三秒半句话方法论，不信任模型自觉）
            if _ADVICE_OPENER.match(sentence):
                continue  # 劝告开头=行动指导，废
            if not any(k in sentence for k in _JUDGE_KW):
                continue  # 无判词词根=新闻复述，废（判决必须有立场词）
            x["sentence"] = sentence
            out.append(x)
        return out
    gated = _industry_gate(items)
    if len(items) != len(gated):
        print(f"[trending] 行业闸+半句话闸：{len(items)} 条进 → {len(gated)} 条出")
    items = gated

    # 三层硬校验（程序化兜底，宁缺毋滥凑数不如少出）：对标圈拦截 → 7 天去重 → 批内去重 → 对标变体拦截
    watch_norms = [_norm(x["title"]) for x in watch_items if len(_norm(x["title"])) >= 10]
    batch: list[dict] = []
    seen_norms: list[str] = []
    for x in items:
        hotspot = str(x.get("context") or x.get("hotspot") or "").strip()  # v5 起 schema 无 hotspot，=context
        if not hotspot or "对标圈" in hotspot:
            continue
        if is_similar_title(str(x.get("sentence") or ""), line="persona"):  # 标题撞车兜底（提炼走人设线）
            continue
        hn = _norm(hotspot)
        if any(SequenceMatcher(None, hn, p).ratio() >= 0.55 or (len(p) >= 12 and (hn in p or p in hn))
               for p in prev_norms):
            continue
        if any(SequenceMatcher(None, hn, b).ratio() >= 0.55 for b in seen_norms):
            continue
        if any(hn in w for w in watch_norms):
            continue  # hotspot 照抄对标视频标题（v7 起只拦照抄——圈内刷屏主题锻自己的判决合法）
        seen_norms.append(hn)
        batch.append(x)
        if len(batch) >= 6:
            break
    if not batch:
        # 诚实空过（2026-11-11 靶子产线定版：无可怼≠失败，失败是异常不是工作流）
        ctx.set_progress(100, "今日无可怼靶子（原料不足或全部未过新鲜度校验），诚实空过")
        return {"radar_notes": radar_notes, "topic_ids": [], "superseded": [],
                "targets": 0,
                "material": {"watch": len(watch_items), "news": news_meta,
                             "hot": hot_meta, "douyin": dy_meta, "zhihu": zh_meta,
                             "github": gh_meta, "queries": queries, "web": search_metas}}

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
                "sentence": str(x.get("sentence") or "")[:20],
                "context": str(x.get("context") or "")[:60],  # 事件一句话（他在怼什么）
                "spice": x.get("spice") if x.get("spice") in ("low", "mid", "high") else "mid",
                "heat": str(x.get("heat") or "科技媒体头条")[:60],
            }
            t = Topic(
                title=str(x.get("sentence"))[:60],  # 标题=半句话判决（靶子即视频）
                angle="",  # 判决句本身就是主切角（备稿时回填）
                audience="both",
                source_type="trending",
                source_ref=str(x.get("context") or "")[:200],
                line="persona",  # 站外热点提炼走人设线（获客号有独立挖号进料，2026-11-02）
                evidence={
                    "hotspot": x.get("context", ""),
                    "target": tgt,  # 半句话靶子（2026-11-12 三秒半句话方法论定版）
                    "material": {"watch_count": len(watch_items), "news": news_meta,
                                 "hot": hot_meta, "douyin": dy_meta, "zhihu": zh_meta,
                                 "github": gh_meta, "queries": q_status, "web": search_metas},
                },
                status="draft",
            )
            s.add(t)
            s.flush()
            created.append(t.id)
        s.commit()

    ctx.set_progress(100, f"完成：{len(created)} 条行业靶子入选题库（替代 {len(superseded)} 条当日旧批次）")
    return {"radar_notes": radar_notes, "topic_ids": created, "superseded": superseded,
            "targets": len(created),
            "material": {"watch": len(watch_items), "news": news_meta,
                         "hot": hot_meta, "douyin": dy_meta, "zhihu": zh_meta,
                         "github": gh_meta, "queries": queries, "web": search_metas}}


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
