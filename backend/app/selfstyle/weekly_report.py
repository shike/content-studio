"""视频表现周报（2026-10-12，课程定位北极星）。

数据：self_videos（创作者中心每日刷新的播放/赞/评/转/藏+发布时间）
     + account_metrics_daily（粉丝/主页访问日级）。
计算：纯 SQL/Python 聚合（质量分=收藏率×3+转发率×3+评论×2+点赞×1.5+播放规模封顶3）；
解读：一次 LLM 小调用（按周缓存，绝不重复烧钱）。
周期：上一个完整的自然周（周一~周日）。"""
from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta

from sqlmodel import Session, select

from ..db import engine
from ..models import AccountMetricsDaily, SelfVideo


def _week_window(today: date | None = None) -> tuple[date, date]:
    """上一个完整自然周（周一~周日）。"""
    today = today or date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    return last_monday, last_monday + timedelta(days=6)


def _norm(t: str) -> str:
    import re
    return re.sub(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]", "", str(t)).lower()


def video_score(play: int, digg: int, comment: int, share: int, collect: int) -> float:
    """质量分：课程号的钱途信号加权（收藏=学习意图、转发=圈层传播）。"""
    p = max(play, 1)
    return round(collect / p * 100 * 3 + share / p * 100 * 3
                 + digg / p * 100 * 1.5 + comment / p * 100 * 2
                 + min(play / 300, 3), 1)


def _collect(tenant_id: int, week_start: date, week_end: date) -> dict:
    week_s = week_start.isoformat()
    week_e = week_end.isoformat() + "~"  # 字符串比较含当日（发布时间到分钟）
    with Session(engine) as s:
        vids = s.exec(select(SelfVideo).where(
            SelfVideo.tenant_id == tenant_id,  # type: ignore[attr-defined]
            SelfVideo.published_at >= week_s,  # type: ignore[attr-defined]
            SelfVideo.published_at <= week_e)).all()  # type: ignore[attr-defined]
        daily = s.exec(select(AccountMetricsDaily).where(
            AccountMetricsDaily.tenant_id == tenant_id,  # type: ignore[attr-defined]
            AccountMetricsDaily.date >= week_start.isoformat(),  # type: ignore[attr-defined]
            AccountMetricsDaily.date <= week_end.isoformat())).all()  # type: ignore[attr-defined]
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
            purpose="weekly_report", max_tokens=2048, thinking="disabled")
        return (out or "").strip()[:600]
    except Exception as e:  # noqa: BLE001 解读失败不拦周报
        print(f"[weekly] 解读失败保留数字版: {type(e).__name__}: {str(e)[:80]}")
        return ""


async def build_weekly_report(tenant_id: int, force: bool = False) -> dict:
    """生成（或读取缓存的）上周周报。返回 {week, facts, summary}。"""
    from ..models import WeeklyReport

    ws, we = _week_window()
    week_key = ws.isoformat()
    with Session(engine) as s:
        row = s.exec(select(WeeklyReport).where(
            WeeklyReport.tenant_id == tenant_id,  # type: ignore[attr-defined]
            WeeklyReport.week_start == week_key)).first()  # type: ignore[attr-defined]
        if row is not None and not force:
            return {"week": row.week_start, **(row.report or {})}
    facts = _collect(tenant_id, ws, we)
    facts["summary"] = await _interpret(facts)
    with Session(engine) as s:
        row = s.exec(select(WeeklyReport).where(
            WeeklyReport.tenant_id == tenant_id,  # type: ignore[attr-defined]
            WeeklyReport.week_start == week_key)).first()  # type: ignore[attr-defined]
        if row is None:
            row = WeeklyReport(tenant_id=tenant_id, week_start=week_key)
        row.report = facts
        s.add(row)
        s.commit()
    return {"week": week_key, **facts}


def cached_weekly_report(tenant_id: int) -> dict | None:
    """API 只读：取已生成的最新周报（不触发 LLM）。"""
    from ..models import WeeklyReport

    with Session(engine) as s:
        row = s.exec(select(WeeklyReport).where(
            WeeklyReport.tenant_id == tenant_id))  # type: ignore[attr-defined]
        rows = sorted(row, key=lambda r: r.week_start, reverse=True)
        if not rows:
            return None
        r = rows[0]
        return {"week": r.week_start, **(r.report or {})}
