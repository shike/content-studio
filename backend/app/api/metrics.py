"""账号运营指标接口：自有账号日级数据（创作者中心回流）。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from ..db import engine
from ..models import MetricsSnapshot, User
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
            select(MetricsSnapshot)
            .where(MetricsSnapshot.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
                   MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                   MetricsSnapshot.date >= since)  # type: ignore[attr-defined]
            .order_by(MetricsSnapshot.date)  # type: ignore[attr-defined]
        ).all()
    items = []
    for r in rows:
        m = r.metrics or {}
        items.append({
            "date": r.date, "fans": m.get("fans") or 0, "new_fans": m.get("new_fans") or 0,
            "cancel_fans": m.get("cancel_fans") or 0,
            "profile_views": m.get("profile_views") or 0, "play": m.get("play") or 0,
            "digg": m.get("digg") or 0, "comment": m.get("comment") or 0,
            "share": m.get("share") or 0,
            "account_search": m.get("account_search") or 0,
            "post_search": m.get("post_search") or 0,
        })
    summary = {"days": len(items)}
    return {"items": items, "summary": summary}


@router.get("/weekly")
def weekly_report(user: User = Depends(require_user)) -> dict:
    """最新一期视频周报（只读缓存；无则提示先生成）。"""
    from ..selfstyle.weekly_report import cached_weekly_report

    rep = cached_weekly_report(user.tenant_id)
    if rep is None:
        return {"week": "", "message": "还没有周报：点「生成周报」跑一次（数据来自每日回流）"}
    return rep
