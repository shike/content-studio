"""视频表现周报（2026-10-12，课程定位北极星）。

数据：self_videos（创作者中心每日刷新的播放/赞/评/转/藏+发布时间）
     + account_metrics_daily（粉丝/主页访问日级）。
计算：纯 SQL/Python 聚合（质量分=播放定档〔<1000<30、<1万<70、≥1万 70~100〕+互动率档内 ±5 修正，2026-11-18 重设）；
解读：一次 LLM 小调用（按周缓存，绝不重复烧钱）。
周期：上一个完整的自然周（周一~周日）。"""
from __future__ import annotations

import json
import math
import statistics
from datetime import date, timedelta

from sqlmodel import Session, select

from ..db import engine
from ..models import MetricsSnapshot, SelfVideo


def _week_window(today: date | None = None) -> tuple[date, date]:
    """上一个完整自然周（周一~周日）。today 传次日日期即可取"本周"（周日当晚用）。"""
    today = today or date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    return last_monday, last_monday + timedelta(days=6)


def video_score(play: int, digg: int, comment: int, share: int, collect: int) -> float:
    """视频质量分（2026-11-18 用户裁决重设权重：播放定档，互动率只做档内修正）。

    硬规则：播放 <1000 → 分 <30；<10000 → 分 <70；≥10000 → 70~100。
    - 档位基线：<1000 线性 0→30；1000~9999 对数 30→70；≥1万 对数 70→100（10万封顶）。
    - 互动率（收藏/转发=学习意图权重最高）换算 ±5 的档内修正，封顶在档上限内
      （29.9 / 69.9），保证不跨档——播放量是门槛，互动只决定同档里谁更好。
    """
    p = max(play, 0)
    if p < 1000:
        base = 30.0 * p / 1000
        cap = 29.9
    elif p < 10000:
        base = 30.0 + 40.0 * math.log10(p / 1000.0)
        cap = 69.9
    else:
        base = 70.0 + 30.0 * min(1.0, math.log10(p / 10000.0))
        cap = 100.0
    r = max(p, 1)
    eng = (collect / r * 100 * 3 + share / r * 100 * 3
           + comment / r * 100 * 2 + digg / r * 100 * 1.5)
    adj = max(-5.0, min(5.0, (eng - 10.0) / 4.0))  # 典型率值 ~10 → 0 修正
    return round(max(0.0, min(cap, base + adj)), 1)


def _card_matches(tenant_id: int) -> tuple[list[dict], int]:
    """M3 校准回路（PRD R1.4）：备稿卡 ↔ 已发视频 模糊匹配 → 「靶子卡→视频表现」对照。

    规则：视频发布日 ∈ [卡创建日, +14 天] 且 归一化标题 SequenceMatcher ≥0.40
    或互相包含（≥8 字）；一卡取质量分最高的一条。R1.6 边界内：只出对照表，
    不回写任何评分——校准由用户看表人肉判断。"""
    import re as _re
    from difflib import SequenceMatcher

    from ..models import Idea

    def _norm(t: str) -> str:
        return _re.sub(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]", "", str(t)).lower()

    with Session(engine) as s:
        from ..channels.engine import channel_id_of

        cards = s.exec(select(Idea).where(  # type: ignore[attr-defined]
            Idea.tenant_id == tenant_id,  # type: ignore[attr-defined]
            Idea.channel_id == channel_id_of(s, "persona"),  # type: ignore[attr-defined]
            Idea.status == "approved")).all()  # type: ignore[attr-defined]
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.tenant_id == tenant_id,  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
    pubs = []
    for v in vids:
        st = v.stats or {}
        play = int(st.get("play") or 0)
        pubs.append({"title": v.title or "", "published": (v.published_at or "")[:10],
                     "play": play, "digg": int(st.get("digg") or 0),
                     "comment": int(st.get("comment") or 0),
                     "share": int(st.get("share") or 0), "collect": int(st.get("collect") or 0)})
    for v in pubs:
        v["score"] = video_score(v["play"], v["digg"], v["comment"], v["share"], v["collect"])
    out: list[dict] = []
    unmatched = 0
    for c in cards:
        if not c.hook:
            continue
        cd = c.created_at.date() if c.created_at else None
        best: dict | None = None
        for v in pubs:
            try:
                vd = date.fromisoformat(v["published"])
            except ValueError:
                continue
            if cd is None or not (cd <= vd <= cd + timedelta(days=14)):
                continue
            n1, n2 = _norm(c.hook), _norm(v["title"])
            contain = len(n1) >= 8 and (n1 in n2 or n2 in n1)
            if SequenceMatcher(None, n1, n2).ratio() < 0.40 and not contain:
                continue
            if best is None or v["score"] > best["score"]:
                best = v
        if best is None:
            unmatched += 1
            continue
        out.append({"hook": c.hook[:30], "spine": c.spine[:30],
                    "coverage": ((c.payload or {}).get("pool") or {}).get("coverage") or "",
                    "published": best["published"], "video": best["title"][:30],
                    "play": best["play"], "comment": best["comment"], "score": best["score"]})
    out.sort(key=lambda x: -x["score"])
    return out, unmatched


def _collect(tenant_id: int, week_start: date, week_end: date) -> dict:
    week_s = week_start.isoformat()
    week_e = week_end.isoformat() + "~"  # 字符串比较含当日（发布时间到分钟）
    with Session(engine) as s:
        vids = s.exec(select(SelfVideo).where(
            SelfVideo.tenant_id == tenant_id,  # type: ignore[attr-defined]
            SelfVideo.published_at >= week_s,  # type: ignore[attr-defined]
            SelfVideo.published_at <= week_e)).all()  # type: ignore[attr-defined]
        daily_rows = s.exec(select(MetricsSnapshot).where(  # type: ignore[attr-defined]
            MetricsSnapshot.tenant_id == tenant_id,  # type: ignore[attr-defined]
            MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
            MetricsSnapshot.date >= week_start.isoformat(),  # type: ignore[attr-defined]
            MetricsSnapshot.date <= week_end.isoformat())).all()  # type: ignore[attr-defined]
        daily = []
        for d in daily_rows:
            m = d.metrics or {}
            daily.append(type("D", (), {"date": d.date,
                                        "play": int(m.get("play") or 0),
                                        "new_fans": int(m.get("new_fans") or 0),
                                        "profile_views": int(m.get("profile_views") or 0)}))
    videos = []
    for v in vids:
        st = v.stats or {}
        play = int(st.get("play") or 0)
        videos.append({
            "title": (v.title or "")[:40], "published_at": v.published_at[:10],
            "duration": v.duration or 0, "play": play,
            "digg": int(st.get("digg") or 0), "comment": int(st.get("comment") or 0),
            "share": int(st.get("share") or 0), "collect": int(st.get("collect") or 0),
            "score": video_score(play, int(st.get("digg") or 0), int(st.get("comment") or 0),
                                 int(st.get("share") or 0), int(st.get("collect") or 0)),
        })
    videos = [v for v in videos if v["play"] > 0]  # play=0=平台统计滞后，不进榜（等下轮刷新）
    videos.sort(key=lambda x: -x["score"])
    daily = [{"date": d.date, "play": d.play, "new_fans": d.new_fans,
              "profile_views": d.profile_views} for d in
             sorted(daily, key=lambda x: x.date)]
    pub_days = {v["published_at"] for v in videos}
    pub_day_play = sum(d["play"] for d in daily if d["date"] in pub_days)
    idle_day_play = sum(d["play"] for d in daily if d["date"] not in pub_days)
    n_pub = len(pub_days)
    return {
        "week": f"{week_s}~{week_end.isoformat()}",
        "videos": videos, "daily": daily,
        "median_play": statistics.median([v["play"] for v in videos]) if videos else 0,
        "pub_day_avg_play": round(pub_day_play / n_pub) if n_pub else 0,
        "idle_day_avg_play": round(idle_day_play / max(len(daily) - n_pub, 1)) if len(daily) > n_pub else 0,
        "fans_delta_week": sum(d["new_fans"] for d in daily),
        "profile_views_week": sum(d["profile_views"] for d in daily),
    }


async def _interpret(facts: dict) -> str:
    """LLM 解读（3~5 句：为什么好/下周建议）。失败返回空串（周报数字仍在）。"""
    try:
        from ..llm import gateway
        from ..prompts import load

        prompt = (load("weekly_report") + json.dumps(facts, ensure_ascii=False))
        out = await gateway.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=2048, thinking="disabled")
        return (out or "").strip()[:600]
    except Exception as e:  # noqa: BLE001 解读失败不拦周报
        print(f"[weekly] 解读失败保留数字版: {type(e).__name__}: {str(e)[:80]}")
        return ""


async def build_weekly_report(tenant_id: int, force: bool = False,
                              today: date | None = None) -> dict:
    """生成（或读取缓存的）上周周报。缓存=analytics_reports(kind=weekly)。返回 {week, facts, summary}。"""
    from ..models import AnalyticsReport

    ws, we = _week_window(today)
    week_key = ws.isoformat()
    with Session(engine) as s:
        row = s.exec(select(AnalyticsReport).where(
            AnalyticsReport.tenant_id == tenant_id,  # type: ignore[attr-defined]
            AnalyticsReport.kind == "weekly",  # type: ignore[attr-defined]
            AnalyticsReport.period_start == week_key)).first()  # type: ignore[attr-defined]
        if row is not None and not force:
            return {"week": row.period_start, **(row.payload or {})}
    facts = _collect(tenant_id, ws, we)
    facts["cards"], facts["cards_unmatched"] = _card_matches(tenant_id)
    facts["summary"] = await _interpret(facts)
    with Session(engine) as s:
        row = s.exec(select(AnalyticsReport).where(
            AnalyticsReport.tenant_id == tenant_id,  # type: ignore[attr-defined]
            AnalyticsReport.kind == "weekly",  # type: ignore[attr-defined]
            AnalyticsReport.period_start == week_key)).first()  # type: ignore[attr-defined]
        if row is None:
            row = AnalyticsReport(tenant_id=tenant_id, kind="weekly", period_start=week_key)
        row.payload = facts
        s.add(row)
        s.commit()
    return {"week": week_key, **facts}


def cached_weekly_report(tenant_id: int) -> dict | None:
    """API 只读：取已生成的最新周报（不触发 LLM）。"""
    from ..models import AnalyticsReport

    with Session(engine) as s:
        rows = s.exec(select(AnalyticsReport).where(  # type: ignore[attr-defined]
            AnalyticsReport.tenant_id == tenant_id,  # type: ignore[attr-defined]
            AnalyticsReport.kind == "weekly")).all()  # type: ignore[attr-defined]
        rows = sorted(rows, key=lambda r: r.period_start, reverse=True)
        if not rows:
            return None
        r = rows[0]
        return {"week": r.period_start, **(r.payload or {})}
