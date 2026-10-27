"""账号运营指标接口：自有账号日级数据（创作者中心回流）。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..db import engine
from ..models import AccountMetricsDaily, User
from ..auth import require_user
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/metrics")


@router.get("/account")
def account_metrics(days: int = 30, user: User = Depends(require_user)) -> dict:
    """本租户自有账号近 N 天日级指标（升序），含最新摘要。"""
    days = max(1, min(days, 120))
    since = (date.today() - timedelta(days=days)).isoformat()
    with Session(engine) as s:
        rows = s.exec(
            select(AccountMetricsDaily)
            .where(AccountMetricsDaily.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
                   AccountMetricsDaily.date >= since)  # type: ignore[attr-defined]
            .order_by(AccountMetricsDaily.date)  # type: ignore[attr-defined]
        ).all()
    items = []
    for r in rows:
        items.append({
            "date": r.date, "fans": r.fans, "new_fans": r.new_fans,
            "profile_views": r.profile_views, "play": r.play,
            "digg": r.digg, "comment": r.comment, "share": r.share,
            "account_search": r.account_search, "post_search": r.post_search,
        })
    latest = items[-1] if items else None
    week = items[-7:]
    summary = {
        "fans": latest["fans"] if latest else 0,
        "new_fans_7d": sum(x["new_fans"] for x in week),
        "profile_views_7d": sum(x["profile_views"] for x in week),
        "play_7d": sum(x["play"] for x in week),
        "days": len(items),
    }
    return {"items": items, "summary": summary}


@router.get("/weekly")
def weekly_report(user: User = Depends(require_user)) -> dict:
    """最新一期视频周报（只读缓存；无则提示先生成）。"""
    from ..selfstyle.weekly_report import cached_weekly_report

    rep = cached_weekly_report(user.tenant_id)
    if rep is None:
        return {"week": "", "message": "还没有周报：点「生成周报」跑一次（数据来自每日回流）"}
    return rep


@router.post("/weekly/generate", status_code=202)
async def generate_weekly(user: User = Depends(require_user)) -> dict:
    from ..jobs.runner import runner

    job = await runner.submit("video_weekly_report", {"force": True},
                              dedup_key=f"{user.tenant_id}:weeklyreport:manual:{__import__('datetime').datetime.now():%Y%m%d%H%M}")
    return {"job_id": job.id}
