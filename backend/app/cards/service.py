"""选题工场（人设号靶子卡，PRD R1.4——2026-11-14 三原则定版）。

三车间流水线（`card_daily` 单任务五段）：
车间零·信号采集（0 LLM）→ 车间一·池子构建（一手直产 + pool_pick 二手补位）
→ 车间二·主线锻造（spine_forge + 主线闸）→ 车间三·钩子铸造（hook_cast + 三秒闸）
→ 总法官（judge_cards）→ 落卡（draft 前 N / jail 可捞，批次替代，空过合法）。

三原则（法律，见 PRD R1.4）：
①三秒钟=钩子吸引力装置：观点说清是底线、抓人是目的；时长 ≤3 秒——
  字数线=3 秒×语速口径（时长是法律，字数只是代理），计长剔除标点；
②半句话=视频主线：全程论证但理论上面不出口（不进台词/字幕/收尾）；
③广度×热度=域内×覆盖人口×此刻有流量。

进料优先级：一手·抖音原生（监控号新视频评论 ≥pool_peer_min_comments 直产池子卡 /
话题雷达飙升 / 抖音热点榜）＞二手·外站五路（pool_pick ≤pool_pick_max 条补位，
需法官高分）＋自有转写（常青）＋手动投喂（card_seed 立即走主线→钩子→法官）。
对标圈素材禁当靶子（纪律不变）。每日 LLM 共 4 次。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Optional

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..models import BenchmarkVideo, RadarTopic, Script, SelfVideo, TopicCard
from ..prompts import render
from ..settings import settings

# ── 域内词根（继承自旧靶子产线的 _TECH_KW：域内闸=词根表兜底，一手赛道卡豁免）──
_DOMAIN_KW = ("AI", "人工智能", "大模型", "模型", "智能体", "agent", "机器人", "芯片", "算力",
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

_ADVICE_OPENER = re.compile(r"^(别|先|快|应|该|要|记得|建议|试试|赶紧|需要|必须|最好|千万|不妨)")
_PREDICT_KW = ("迟早", "注定", "必死", "要凉")
_JUDGE_KW = ("就是", "全是", "纯", "垃圾", "收割", "智商税", "骗局", "割韭菜", "倒闭",
             "没法用", "没人用", "认输", "吹爆", "吹上", "吹过", "神话", "泡沫",
             "凉了", "烂", "难用", "复杂", "别碰", "高估", "低估", "幻觉", "伪需求",
             "套壳", "翻车", "接盘", "过时", "淘汰", "取代", "终结", "杀死", "白干", "白嫖",
             "白送", "硬刚", "落后", "耍猴", "当猴耍", "镰刀", "韭菜",
             "干掉", "吃掉", "抢走", "饭碗", "消失", "出局", "上位", "没出路", "上岸")
_CLAUSE_PUNCT = re.compile(r"[，,。？?：:；;、]")
_PUNCT_STRIP = re.compile(r"[\s，,。.！!？?：:；;、「」『』《》\"'（）()·|…—\-+#]")
_NORM_RE = re.compile(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]")

_COVER_TABLE = {
    "boss": ("老板", "企业主", "厂长", "创始人", "个体", "创业", "小微", "开厂", "开公司", "做生意的", "生意人"),
    "middle": ("中层", "经理", "主管", "部门", "管理岗", "组长"),
    "job_wide": ("财务", "会计", "销售", "行政", "人事", "文员", "工人", "车间", "客服",
                 "运营", "设计师", "程序员", "司机", "仓管", "采购", "质检", "员工",
                 "打工", "上班", "职场"),
}
_COVER_RANK = {"boss": 3, "middle": 2, "job_wide": 1, "job_narrow": 0}


def _norm(t: str) -> str:
    return _NORM_RE.sub("", str(t)).lower()


def _pron_len(t: str) -> int:
    """可发音字符计长（三秒时长的字数代理）：标点不发音，剔除后计数。"""
    return len(_PUNCT_STRIP.sub("", str(t)))


def _max_chars() -> int:
    """三秒字数线 = 3 秒 × 语速口径（时长是法律，字数是代理，不设任何宽裕）。"""
    return 3 * max(1, int(settings.hook_speed_cps or 5))


def _coverage_of(role: str) -> str:
    """主语角色→覆盖面档位（广度闸：认不出的角色一律按窄处理——宁拦勿放）。"""
    r = _norm(role)
    for tier, kws in _COVER_TABLE.items():
        if any(k in r for k in kws):
            return tier
    return "job_narrow"


def _pool_dict(*, domain: str, heat: str, level: str, role: str = "", coverage: str = "",
               secondary: bool = False, douyin: Optional[dict] = None,
               external: Optional[dict] = None, peer_covered: bool = False) -> dict:
    return {"domain_evidence": domain[:80], "heat_evidence": heat[:80],
            "heat_level": level[:20], "role": role[:30], "coverage": coverage,
            "secondary": bool(secondary), "douyin_evidence": douyin,
            "external_evidence": external, "peer_covered": bool(peer_covered)}


def _gate(gates: list, gate: str, ok: bool, detail: str = "") -> bool:
    gates.append({"gate": gate, "pass": bool(ok), "detail": str(detail)[:160]})
    return ok


def _kw_hit(text: str) -> bool:
    low = text.lower()
    return any(k.lower() in low for k in _DOMAIN_KW)


def _local_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _recent_card_spines(days: int = 14) -> list[str]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with Session(engine) as s:
        rows = s.exec(select(TopicCard).where(  # type: ignore[attr-defined]
            TopicCard.created_at >= cutoff)).all()  # type: ignore[attr-defined]
    return [c.spine for c in rows if c.spine]


def _published_titles() -> list[str]:
    """已发视频标题（冷饭判据：判冷饭不问人，清单就是判据）。"""
    with Session(engine) as s:
        rows = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
    return [v.title for v in rows if v.title]


def _fewshot_lines(limit: int = 14) -> str:
    """few-shot 句式标尺：拆解库近 30 天高分样本的钩子/高光 + 已发视频标题（正样本）。"""
    from datetime import timedelta as _td

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(days=30)
    lines: list[str] = []
    with Session(engine) as s:
        rows = s.exec(select(BenchmarkVideo).where(  # type: ignore[attr-defined]
            BenchmarkVideo.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        rows = sorted((b for b in rows if b.analysis or b.title),
                      key=lambda b: -(b.quality_score or 0))
        for b in rows:
            taken = False
            for k, v in (b.analysis or {}).items():
                key = str(k).lower()
                if any(w in key for w in ("hook", "钩", "开场", "高光", "金句")):
                    vals = v if isinstance(v, list) else [v]
                    for x in vals:
                        if isinstance(x, str) and x.strip():
                            lines.append(f"- 「{x.strip()[:44]}」（拆解库 {b.author or '?'}）")
                            taken = True
                            break
                if taken:
                    break
            if not taken and b.title:
                lines.append(f"- 「{b.title[:44]}」（拆解库 {b.author or '?'}）")
            if len(lines) >= limit:
                break
    with Session(engine) as s:
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
        own = sorted(vids, key=lambda v: int((v.stats or {}).get("play") or 0),
                     reverse=True)[:6]
    lines += [f"- 「{(v.title or '')[:44]}」（自己的爆款）" for v in own if v.title]
    return "\n".join(lines[:limit + 6]) or "（暂无已验证样本）"


# ── 三段 LLM（pick/forge/cast/judge 列表驱动，card_seed 复用）──

async def _llm_pick(menu_text: str, persona: str, audience_note: str) -> list[dict]:
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "pool_pick", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            POOL_MAX=str(int(settings.pool_pick_max or 8)),
            MENU=menu_text[:6000])}],
        max_tokens=4000, thinking="disabled")
    return [x for x in (data.get("picks") or []) if isinstance(x, dict) and x.get("title")]


async def _llm_forge(pools: list[dict], persona: str, audience_note: str) -> dict[int, dict]:
    pool_inputs = [{"i": i, "source": p["source"],
                    "由头": (p["pool"].get("heat_evidence") or "")[:60],
                    "域内证据": (p["pool"].get("domain_evidence") or "")[:60]}
                   for i, p in enumerate(pools, 1)]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "spine_forge", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            MAX_CHARS=str(_max_chars()), POOLS=json.dumps(pool_inputs, ensure_ascii=False))}],
        max_tokens=2000, thinking="disabled")
    return {int(x["i"]): x for x in (data.get("cards") or [])
            if isinstance(x, dict) and str(x.get("i") or "").isdigit()
            and str(x.get("spine") or "").strip()} if isinstance(data, dict) else {}


async def _llm_cast(items: list[dict], persona: str, audience_note: str) -> dict[int, dict]:
    spine_inputs = [{"i": it["i"], "spine": it.get("spine") or "", "role": it.get("role") or "",
                     "由头": (it["pool"].get("heat_evidence") or "")[:50]}
                    for it in items]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "hook_cast", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            MAX_CHARS=str(_max_chars()), FEWSHOT=_fewshot_lines(),
            SPINES=json.dumps(spine_inputs, ensure_ascii=False))}],
        max_tokens=2000, thinking="disabled")
    return {int(x["i"]): x for x in (data.get("cards") or [])
            if isinstance(x, dict) and str(x.get("i") or "").isdigit()
            and str(x.get("hook") or "").strip()} if isinstance(data, dict) else {}


async def _llm_judge(items: list[dict]) -> dict[int, dict]:
    card_inputs = [{"i": it["i"], "source": it["source"], "spine": it.get("spine") or "",
                    "hook": it.get("hook") or "", "coverage": (it["pool"] or {}).get("coverage") or "",
                    "热度证据": (it["pool"].get("heat_evidence") or "")[:60],
                    "一手证据": bool(it["pool"].get("douyin_evidence")),
                    "peer_covered": bool(it["pool"].get("peer_covered"))}
                   for it in items]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "judge_cards", JUDGE_LINE=str(int(settings.judge_pass_line or 7)),
            CARDS=json.dumps(card_inputs, ensure_ascii=False))}],
        max_tokens=4000, thinking="disabled")
    return {int(x["i"]): x for x in (data.get("cards") or [])
            if isinstance(x, dict) and str(x.get("i") or "").isdigit()} if isinstance(data, dict) else {}


def _spine_gate(spine: str, role: str, source: str, gates: list,
                seen_spine_norms: list[str], video_norms: list[str]) -> bool:
    """主线闸（程序）：字数线/劝告开头/复合句/预言腔/判词词根（热源系）/覆盖面/撞车/批内。"""
    spine = spine.strip()
    n = _pron_len(spine)
    if not _gate(gates, "主线字数线", 0 < n <= _max_chars(), f"{spine[:24]}（{n} 字，线 {_max_chars()}）"):
        return False
    if not _gate(gates, "劝告开头", not _ADVICE_OPENER.match(spine), spine[:24]):
        return False
    if not _gate(gates, "复合句闸", not _CLAUSE_PUNCT.search(spine), spine[:24]):
        return False
    if not _gate(gates, "预言腔负向", not any(k in spine for k in _PREDICT_KW), spine[:24]):
        return False
    if source not in ("self_story", "manual"):  # 交付/投喂线的靶形态是续集与反差，判词词根不适用
        if not _gate(gates, "判词词根", any(k in spine for k in _JUDGE_KW), spine[:24]):
            return False
    cov = _coverage_of(role)
    floor = _COVER_RANK.get(str(settings.coverage_min or "job_wide"), 1)
    if not _gate(gates, "覆盖面闸", _COVER_RANK.get(cov, 0) >= max(floor, 1),
                 f"role={role[:16]}→{cov}（线 {settings.coverage_min}）"):
        return False
    sn = _norm(spine)
    if not _gate(gates, "14 天撞车", not any(
            SequenceMatcher(None, sn, _norm(t)).ratio() >= 0.55 or
            (len(tn := _norm(t)) >= 8 and (tn in sn or sn in tn))
            for t in _recent_card_spines(14)), spine[:24]):
        return False
    if not _gate(gates, "冷饭闸", not any(
            (SequenceMatcher(None, sn, tn).ratio() >= 0.55) or
            (len(tn) >= 8 and (tn in sn or sn in tn)) for tn in video_norms), spine[:24]):
        return False
    if not _gate(gates, "批内去重", not any(
            SequenceMatcher(None, sn, b).ratio() >= 0.55 for b in seen_spine_norms), spine[:24]):
        return False
    seen_spine_norms.append(sn)
    return True


def _hook_gate(hook: str, gates: list, spine: str = "") -> bool:
    """三秒闸（程序）：字数线 + 开头词根 + 钩子≠主线（照抄=主线出口，违反半句话原则）。"""
    hook = hook.strip()
    n = _pron_len(hook)
    if not _gate(gates, "三秒字数线", 0 < n <= _max_chars(), f"{hook[:24]}（{n} 字，线 {_max_chars()}）"):
        return False
    banned = [w.strip() for w in str(settings.hook_ban_prefixes or "").split(",") if w.strip()]
    hit = next((w for w in banned if hook.startswith(w)), "")
    if not _gate(gates, "开头词根闸", not hit, f"{hook[:24]}" + (f"（命中「{hit}」）" if hit else "")):
        return False
    hn, sn = _norm(hook), _norm(spine or "")
    same = bool(sn) and (hn == sn or (len(sn) >= 8 and (sn in hn or hn in sn)))
    return _gate(gates, "钩子≠主线", not same, f"{hook[:24]}" + ("（照抄主线）" if same else ""))


def _merge_card(i: int, pool_entry: dict, forged: dict, hook: str,
                gates: list) -> TopicCard:
    pool = dict(pool_entry["pool"])
    role = str(forged.get("role") or pool.get("role") or "").strip()
    spice = forged.get("spice") if forged.get("spice") in ("low", "mid", "high") else "mid"
    pool["role"] = role
    pool["coverage"] = _coverage_of(role)
    return TopicCard(batch_date=_local_day(), source=pool_entry["source"],
                     pool=pool, spine=str(forged.get("spine") or "").strip(),
                     hook=hook.strip(), spice=spice, gates=gates, status="draft")


# ── 车间零：雷达巡检（收编自旧 trending 内联驱动；低粉高赞自动拆解口径不变）──

async def _radar_round(ctx: JobContext) -> tuple[list[str], list[dict]]:
    notes: list[str] = []
    spikes: list[dict] = []
    try:
        with Session(engine) as s:
            radar_rows = s.exec(select(RadarTopic).where(  # type: ignore[attr-defined]
                RadarTopic.enabled == True)).all()  # noqa: E712
        for rrow in radar_rows:
            try:
                from ..radar.service import check_topic
                r = await check_topic(rrow)
                notes.append(f"#{rrow.name}：新增 {r['new']} 条" + ("（飙升）" if r["spiked"] else ""))
                if r["spiked"]:
                    spikes.append({"name": rrow.name, "new": r["new"]})
            except Exception as e:  # noqa: BLE001 单话题失败不拖垮整轮
                notes.append(f"#{rrow.name}：{type(e).__name__}: {str(e)[:60]}")
            await asyncio.sleep(3)  # 话题接口 pacing
    except Exception as e:  # noqa: BLE001 雷达侧整体失败不阻塞
        notes.append(f"话题巡检失败：{type(e).__name__}: {str(e)[:60]}")
    return notes, spikes


@register_executor("card_daily")
async def card_daily(ctx: JobContext, payload: dict) -> dict:
    """人设号选题工场五段（R1.4）。空过合法：无可怼=诚实空过，失败是异常不是工作流。"""
    from datetime import timedelta as _td

    from ..tenant_brand import audience_note_of, brand_of
    from .hotlist import (fetch_aihot_hot, fetch_baidu_hot, fetch_douyin_hot,
                          fetch_github_hot, fetch_tech_news, fetch_zhihu_hot)

    tenant_id = payload.get("tenant_id") or 1
    persona = brand_of(tenant_id)["persona"]
    audience_note = audience_note_of(tenant_id)
    ctx.set_progress(5, "车间零：话题雷达巡检 + 采集四层信号（0 LLM）")
    radar_notes, radar_spikes = await _radar_round(ctx)

    # ── 车间一：池子构建 ──
    ctx.set_progress(22, "车间一：一手直产（监控号爆款/雷达飙升/抖音榜）")
    pools: list[dict] = []  # [{source, pool}]
    meta: dict = {"peer": 0, "radar": 0, "douyin_hot": 0, "external": 0}

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(hours=30)
    with Session(engine) as s:
        recent_cards = s.exec(select(TopicCard).where(  # type: ignore[attr-defined]
            TopicCard.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - _td(days=14)
        )).all()  # type: ignore[attr-defined]
        seen_refs = {str(((c.pool or {}).get("douyin_evidence") or {}).get("ref") or "")
                     for c in recent_cards}
        peer_rows = s.exec(select(BenchmarkVideo).where(  # type: ignore[attr-defined]
            BenchmarkVideo.created_at >= cutoff)).all()  # type: ignore[attr-defined]
    peer_min = max(1, int(settings.pool_peer_min_comments or 50))
    for b in peer_rows:
        stats = b.stats or {}
        comment = int(stats.get("comment") or 0)
        if comment < peer_min or not b.title or b.url in seen_refs:
            continue
        pools.append({"source": "douyin_peer", "pool": _pool_dict(
            domain=f"监控对标号 {b.author or '?'}（AI 落地赛道，用户精选清单）",
            heat=f"{b.author or '?'}《{b.title[:36]}》评论 {comment}（抖音实数）",
            level="高", douyin={"kind": "peer_video", "ref": b.url,
                                "comments": comment, "diggs": int(stats.get("digg") or 0)},
            peer_covered=True)})
        meta["peer"] += 1
        seen_refs.add(b.url)
        if meta["peer"] >= 10:
            break

    for sp in radar_spikes[:6]:
        key = f"radar:{sp['name']}"
        if key in seen_refs:
            continue
        pools.append({"source": "radar", "pool": _pool_dict(
            domain=f"话题雷达监控项（AI 话题清单）#{sp['name']}",
            heat=f"话题 #{sp['name']} 巡检新增 {sp['new']} 条视频（飙升线 5）",
            level="高", douyin={"kind": "radar_spike", "ref": sp["name"],
                                "spike_speed": sp["new"]})})
        meta["radar"] += 1

    dy_items, _dy_meta = await asyncio.to_thread(fetch_douyin_hot, 50)
    for rank, x in enumerate(dy_items, 1):
        word = str(x.get("word") or "").strip()
        desc = str(x.get("desc") or "")
        if not word or f"douyin_hot:{word}" in seen_refs or not _kw_hit(word + " " + desc):
            continue
        pools.append({"source": "douyin_hot", "pool": _pool_dict(
            domain=f"抖音热点榜在榜，命中域内词根",
            heat=f"抖音热点榜第 {rank} 位：{word[:30]}",
            level="高", douyin={"kind": "douyin_hot", "ref": word[:40], "rank": rank})})
        meta["douyin_hot"] += 1
        seen_refs.add(f"douyin_hot:{word}")
        if meta["douyin_hot"] >= 8:
            break

    ctx.set_progress(34, f"车间一：二手外站精选（一手 {meta['peer'] + meta['radar'] + meta['douyin_hot']} 条已入池）")
    hot_items, _ = await asyncio.to_thread(fetch_baidu_hot, 50)
    zh_items, _ = await asyncio.to_thread(fetch_zhihu_hot, 30)
    news_items, news_meta = await asyncio.to_thread(fetch_tech_news)
    gh_items, _ = await asyncio.to_thread(fetch_github_hot)
    ah_items, ah_meta = await asyncio.to_thread(fetch_aihot_hot)
    menu_parts = [
        "【三榜（百度/知乎）科技条目】" + ("\n".join(
            f"- {x.get('word')}（{x.get('desc', '')[:30]}）" for x in (hot_items + zh_items)
            if _kw_hit(str(x.get('word', '')) + str(x.get('desc', '')))) or "（无科技条目）"),
        "【科技媒体+AI 垂媒（48h）】" + ("\n".join(
            f"- {x['title']}（{x['source']}）" for x in news_items) or f"（未生效：{news_meta.get('status')}）"),
        "【GitHub 新起爆仓库】" + ("\n".join(
            f"- {x['word']}（★{x['stars']}）{x['desc']}" for x in gh_items) or "（空）"),
        "【AIHot 精选】" + ("\n".join(
            f"- {x['title']}｜{x.get('summary', '')[:40]}" for x in ah_items) or f"（未生效：{ah_meta.get('status')}）"),
    ]
    try:
        picks = await _llm_pick("\n\n".join(menu_parts), persona, audience_note)
    except Exception as e:  # noqa: BLE001 二手通道失败不阻塞一手
        picks = []
        meta["external_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    floor = _COVER_RANK.get(str(settings.coverage_min or "job_wide"), 1)
    for x in picks:
        cov = str(x.get("coverage") or "")
        if _COVER_RANK.get(cov, 0) < max(floor, 1):
            continue  # 池子闸：窄主语二手卡直接拦
        pools.append({"source": "external", "pool": _pool_dict(
            domain=str(x.get("domain_evidence") or "")[:80],
            heat=f"外站热榜｜{str(x.get('heat_evidence') or '')[:60]}",
            level="中", role=str(x.get("role") or "")[:30], coverage=cov,
            secondary=True, external={"src": "外站五路", "title": str(x.get("title") or "")[:60]})})
        meta["external"] += 1
    if not pools:
        ctx.set_progress(100, "今日池子空（四层进料全空或全被闸），诚实空过")
        return {"cards": 0, "jail": 0, "pools": 0, "meta": meta, "radar_notes": radar_notes}

    # ── 车间二：主线锻造 + 主线闸 ──
    ctx.set_progress(48, f"车间二：主线锻造（{len(pools)} 个池子）")
    forged = await _llm_forge(pools, persona, audience_note)
    video_norms = [_norm(t) for t in _published_titles() if t]
    seen_spine_norms: list[str] = []
    survivors: list[dict] = []
    for i, p in enumerate(pools, 1):
        f = forged.get(i)
        if not f:
            continue
        gates: list = []
        if _spine_gate(str(f.get("spine") or ""), str(f.get("role") or ""),
                       p["source"], gates, seen_spine_norms, video_norms):
            survivors.append({"i": i, "source": p["source"], "pool": p["pool"],
                              "spine": str(f.get("spine") or "").strip(),
                              "forged": f, "gates": gates})
    print(f"[cards] 车间二：{len(pools)} 池 → {len(survivors)} 过主线闸")

    if not survivors:
        ctx.set_progress(100, "今日无卡（全部未过主线闸），诚实空过")
        return {"cards": 0, "jail": 0, "pools": len(pools), "meta": meta,
                "radar_notes": radar_notes}

    # ── 车间三：钩子铸造 + 三秒闸 ──
    ctx.set_progress(70, f"车间三：钩子铸造（{len(survivors)} 条主线，few-shot=拆解库高分句式）")
    cast = await _llm_cast(survivors, persona, audience_note)
    hooked: list[dict] = []
    for it in survivors:
        h = cast.get(it["i"])
        if not h:
            continue
        if _hook_gate(str(h.get("hook") or ""), it["gates"], it.get("spine") or ""):
            it["hook"] = str(h["hook"]).strip()
            hooked.append(it)
    print(f"[cards] 车间三：{len(survivors)} 主线 → {len(hooked)} 过三秒闸")
    if not hooked:
        ctx.set_progress(100, "今日无卡（全部未过三秒闸），诚实空过")
        return {"cards": 0, "jail": 0, "pools": len(pools), "meta": meta,
                "radar_notes": radar_notes}

    # ── 总法官 + 落卡 ──
    ctx.set_progress(88, f"总法官终审（{len(hooked)} 张候选卡）")
    judged = await _llm_judge(hooked)
    line = max(1, int(settings.judge_pass_line or 7))
    passed: list[tuple[int, dict]] = []
    killed: list[dict] = []
    for it in hooked:
        j = judged.get(it["i"])
        dims = {k: max(0, min(10, int(j.get(k) or 0)))
                for k in ("hook_pull", "spine_single", "traffic_potential")} if j else {}
        ok = bool(j) and all(v >= line for v in dims.values()) and j.get("verdict") != "kill"
        judge = {"hook_pull": dims.get("hook_pull", 0), "spine_single": dims.get("spine_single", 0),
                 "traffic_potential": dims.get("traffic_potential", 0),
                 "verdict": "pass" if ok else "kill",
                 "reason": str((j or {}).get("reason") or "法官无回卷（按废处理）")[:80]}
        (passed if ok else killed).append((it["i"], {**it, "judge": judge}))

    ctx.set_progress(96, f"落卡（法官 pass {len(passed)} / kill {len(killed)}）")
    limit = max(1, int(settings.daily_card_limit or 20))
    passed.sort(key=lambda x: -(x[1]["judge"]["hook_pull"] + x[1]["judge"]["spine_single"]
                                + x[1]["judge"]["traffic_potential"]))
    jail_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(
        days=max(1, int(settings.jail_window_days or 7)))
    _local_mid = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = (_local_mid - _local_mid.utcoffset()).replace(tzinfo=None)  # naive UTC 本地日零点
    draft_ids: list[int] = []
    n_jail = 0
    n_superseded = 0
    with Session(engine) as s:
        # jail 过期清扫（窗口外物理删——弃卡是生成内容不是用户数据）
        for c in s.exec(select(TopicCard).where(  # type: ignore[attr-defined]
                TopicCard.status == "jail",  # type: ignore[attr-defined]
                TopicCard.created_at < jail_cutoff)).all():  # type: ignore[attr-defined]
            s.delete(c)
        # 批次替代：当日更早批次且无下游的 draft 让位（approved/有 script_id 的不动）
        old_drafts = s.exec(select(TopicCard).where(  # type: ignore[attr-defined]
            TopicCard.status == "draft",  # type: ignore[attr-defined]
            TopicCard.created_at < day_start)).all()  # type: ignore[attr-defined]
        old_ids = [c.id for c in old_drafts if not c.script_id]
        for rank, (_, it) in enumerate(passed):
            judge = dict(it["judge"])
            if rank >= limit:
                judge["verdict"] = "overflow"
                judge["reason"] = (judge["reason"] + "｜超出每日上限").strip("｜")
            card = _merge_card(it["i"], {"source": it["source"], "pool": it["pool"]},
                               it["forged"], it["hook"], it["gates"])
            card.judge = judge
            card.status = "jail" if rank >= limit else "draft"
            s.add(card)
            s.flush()
            if card.status == "draft":
                draft_ids.append(card.id)
            else:
                n_jail += 1
        for i, (_, it) in enumerate(killed):
            card = _merge_card(it["i"], {"source": it["source"], "pool": it["pool"]},
                               it["forged"], it["hook"], it["gates"])
            card.judge = it["judge"]
            card.status = "jail"
            s.add(card)
            s.flush()
            n_jail += 1
        for oid in old_ids:
            c = s.get(TopicCard, oid)
            if c is not None:
                c.status = "discarded"
                c.superseded_by = draft_ids[0] if draft_ids else None
                s.add(c)
                n_superseded += 1
        s.commit()
    ctx.set_progress(100, f"完成：{len(draft_ids)} 张靶子卡进箱（jail {n_jail}，替代 {n_superseded} 张旧卡）")
    return {"cards": len(draft_ids), "jail": n_jail, "superseded": n_superseded,
            "pools": len(pools), "meta": meta, "radar_notes": radar_notes}


@register_executor("card_seed")
async def card_seed(ctx: JobContext, payload: dict) -> dict:
    """手动投喂（用户拍板）：一句话 → 池子卡立即走主线→钩子→法官（当日可备稿）。"""
    from ..tenant_brand import audience_note_of, brand_of

    text = str(payload.get("text") or "").strip()
    if not text:
        raise RuntimeError("投喂内容不能为空")
    tenant_id = payload.get("tenant_id") or 1
    persona = brand_of(tenant_id)["persona"]
    audience_note = audience_note_of(tenant_id)
    pool_entry = {"source": "manual", "pool": _pool_dict(
        domain="", heat=f"手动投喂｜{text[:100]}", level="待法官判定",
        external={"src": "手动投喂", "title": text[:60]})}
    ctx.set_progress(20, "锻主线")
    forged = await _llm_forge([pool_entry], persona, audience_note)
    f = forged.get(1)
    gates: list = []
    if not f:
        raise RuntimeError("主线锻造空回卷（模型没给出 spine），请换个说法再投")
    video_norms = [_norm(t) for t in _published_titles() if t]
    if not _spine_gate(str(f.get("spine") or ""), str(f.get("role") or ""),
                       "manual", gates, [], video_norms):
        failed = next((g["detail"] for g in gates if not g["pass"]), "")
        raise RuntimeError(f"主线未过闸：{failed}")
    ctx.set_progress(60, "铸钩子")
    cast = await _llm_cast([{"i": 1, "spine": str(f["spine"]), "role": str(f.get("role") or ""),
                             "pool": pool_entry["pool"]}], persona, audience_note)
    h = cast.get(1)
    if not h or not _hook_gate(str(h.get("hook") or ""), gates, str(f["spine"])):
        failed = next((g["detail"] for g in gates if not g["pass"] and g["gate"] in ("三秒字数线", "开头词根闸")), "")
        raise RuntimeError(f"钩子未过闸：{failed or '空回卷'}")
    ctx.set_progress(85, "法官终审")
    item = {"i": 1, "source": "manual", "pool": pool_entry["pool"], "forged": f,
            "spine": str(f["spine"]).strip(),
            "hook": str(h["hook"]).strip(), "gates": gates}
    judged = await _llm_judge([item])
    j = judged.get(1) or {}
    line = max(1, int(settings.judge_pass_line or 7))
    dims = {k: max(0, min(10, int(j.get(k) or 0)))
            for k in ("hook_pull", "spine_single", "traffic_potential")}
    ok = bool(j) and all(v >= line for v in dims.values()) and j.get("verdict") != "kill"
    judge = {"hook_pull": dims.get("hook_pull", 0), "spine_single": dims.get("spine_single", 0),
             "traffic_potential": dims.get("traffic_potential", 0),
             "verdict": "pass" if ok else "kill",
             "reason": str(j.get("reason") or "法官无回卷（按废处理）")[:80]}
    with Session(engine) as s:
        card = _merge_card(1, pool_entry, f, str(h["hook"]), gates)
        card.judge = judge
        card.status = "draft" if ok else "jail"
        s.add(card)
        s.commit()
        card_id = card.id
    if not ok:
        raise RuntimeError(f"法官判废（{judge['reason']}）——卡已落弃卡池可查看")
    ctx.set_progress(100, f"投喂成卡 #{card_id}（draft，可备稿）")
    return {"card_id": card_id, "judge": judge}


async def compose_card(*, card_id: int, hook: str = "", spine: str = "",
                       angle: str = "", user=None) -> dict:
    """备稿：靶子卡（draft/approved）→ 脚本任务。钩子/主线可被用户改写后传入。"""
    from ..scripts.service import SIX_ANGLES

    angle = (angle or "").strip()
    if angle not in SIX_ANGLES:
        raise ValueError(f"未知打法：{angle or '（未选主打打法）'}")
    with Session(engine) as s:
        card = s.get(TopicCard, card_id)
        if card is None:
            raise KeyError("card not found")
        if card.status not in ("draft", "approved"):
            raise RuntimeError(f"状态 {card.status} 不可备稿（仅 draft/approved）")
        dup = s.exec(select(Script).where(  # type: ignore[attr-defined]
            Script.card_id == card_id,  # type: ignore[attr-defined]
            Script.status.in_(["generating", "polishing"]))).first()  # type: ignore[attr-defined]
        if dup is not None:
            raise RuntimeError(f"该卡正在生成脚本（#{dup.id}），等完成或删除后再试")
        hook_v = (hook or card.hook or "").strip()
        spine_v = (spine or card.spine or "").strip()
        if not hook_v or not spine_v:
            raise RuntimeError("卡上没有钩子/主线，不可备稿")
        spice_v = card.spice if card.spice in ("low", "mid", "high") else "mid"
        script = Script(card_id=card_id, account_type="persona", status="generating")
        s.add(script)
        s.flush()
        card.status = "approved"
        card.script_id = script.id
        s.add(card)
        s.commit()
        s.refresh(script)
        script_id = script.id
    actor = (user.tenant_id, user.id, user.role) if user is not None else None
    from ..jobs.runner import runner
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "card_id": card_id,
                               "hook": hook_v[:60], "spine": spine_v[:40],
                               "angle": angle, "spice": spice_v,
                               "length": "short", "account_type": "persona",
                               "use_style": True, "angles": [angle], "template_id": 0},
                              actor=actor)
    return {"script_id": script_id, "job_id": job.id, "card_id": card_id}
