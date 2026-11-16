"""数据域 API（PRD R6）：自有账号统一面 + 经营看板（总览/渠道对比/账号趋势/作品表现）。

数据源=metrics_snapshots 事实表（缺数显式「未回流」，不补零）；
发布登记走 /api/channels/{key}/contents/{id}/publish（P3）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels.engine import channel_id_of
from ..db import engine
from ..jobs.runner import runner
from ..models import Account, MetricsSnapshot, User

router = APIRouter(prefix="/api/accounts")

# 账号指标键（创作者中心回流口径）——出参统一排序基准
_ACCOUNT_METRIC_KEYS = ("fans", "new_fans", "cancel_fans", "profile_views", "play",
                        "digg", "comment", "share", "account_search", "post_search", "leads")


def _account_out(a: Account, with_metrics: bool = False) -> dict:
    d = {
        "id": a.id, "channel_id": a.channel_id, "platform": a.platform,
        "name": a.name, "handle": a.handle, "profile_url": a.profile_url,
        "enabled": bool(a.enabled), "last_sync_at": a.last_sync_at,
        "note": a.note, "created_at": a.created_at,
        "has_auth": bool((a.auth or {}).get("creator_cookie")),
    }
    return d


@router.get("")
def list_accounts(user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        rows = s.exec(select(Account).order_by(Account.id)).all()  # type: ignore[attr-defined]
        return {"items": [_account_out(r) for r in rows]}


class AccountIn(BaseModel):
    channel_key: str = "persona"
    platform: str = "douyin"
    name: str
    handle: str = ""
    profile_url: str = ""
    note: str = ""
    creator_cookie: str = ""  # 只写不读


@router.post("", status_code=201)
def create_account(body: AccountIn, user: User = Depends(require_user)) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="账号名不能为空")
    with Session(engine) as s:
        a = Account(tenant_id=user.tenant_id, channel_id=channel_id_of(s, body.channel_key),
                    platform=body.platform, name=body.name.strip()[:60],
                    handle=body.handle.strip()[:40], profile_url=body.profile_url.strip(),
                    note=body.note.strip()[:300],
                    auth={"creator_cookie": body.creator_cookie.strip()} if body.creator_cookie.strip() else {})
        s.add(a)
        s.commit()
        s.refresh(a)
        return _account_out(a)


class AccountUpdate(BaseModel):
    name: str | None = None
    handle: str | None = None
    profile_url: str | None = None
    note: str | None = None
    enabled: bool | None = None
    creator_cookie: str | None = None


@router.put("/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        a = s.get(Account, account_id)
        if a is None:
            raise HTTPException(status_code=404, detail="account not found")
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="账号名不能为空")
            a.name = body.name.strip()[:60]
        if body.handle is not None:
            a.handle = body.handle.strip()[:40]
        if body.profile_url is not None:
            a.profile_url = body.profile_url.strip()
        if body.note is not None:
            a.note = body.note.strip()[:300]
        if body.enabled is not None:
            a.enabled = body.enabled
        if body.creator_cookie is not None:
            auth = dict(a.auth or {})
            auth["creator_cookie"] = body.creator_cookie.strip()
            a.auth = auth
        s.add(a)
        s.commit()
        s.refresh(a)
        return _account_out(a)


@router.delete("/{account_id}")
def delete_account(account_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        a = s.get(Account, account_id)
        if a is None:
            raise HTTPException(status_code=404, detail="account not found")
        n = s.exec(select(func.count(MetricsSnapshot.id)).where(  # type: ignore[arg-type]
            MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
            MetricsSnapshot.subject_id == account_id)).one() or 0  # type: ignore[attr-defined]
        if n:
            raise HTTPException(status_code=409,
                                detail=f"该账号已有 {n} 天指标——先停用（enabled=false）而非删除")
        s.delete(a)
        s.commit()
    return {"deleted": account_id}


@router.post("/{account_id}/sync", status_code=202)
async def sync_account(account_id: int, user: User = Depends(require_user)) -> dict:
    """手动触发日级指标回流（调度每日 4 点自动跑，kill switch=creator_data_enabled）。"""
    with Session(engine) as s:
        if s.get(Account, account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
    job = await runner.submit("creator_data_scan", {},
                              dedup_key=f"data:creator:{datetime.now():%Y%m%d}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


@router.get("/{account_id}/metrics")
def account_metrics_series(account_id: int, days: int = 30,
                           user: User = Depends(require_user)) -> dict:
    """单账号日级指标序列（升序，1~120 天封顶）。"""
    days = max(1, min(days, 120))
    since = (date.today() - timedelta(days=days)).isoformat()
    with Session(engine) as s:
        rows = s.exec(
            select(MetricsSnapshot)
            .where(MetricsSnapshot.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
                   MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                   MetricsSnapshot.subject_id == account_id,  # type: ignore[attr-defined]
                   MetricsSnapshot.date >= since)  # type: ignore[attr-defined]
            .order_by(MetricsSnapshot.date)).all()  # type: ignore[attr-defined]
    return {"items": [{"date": r.date, **{k: int((r.metrics or {}).get(k) or 0)
                                          for k in _ACCOUNT_METRIC_KEYS},
                       "source": r.source} for r in rows]}
