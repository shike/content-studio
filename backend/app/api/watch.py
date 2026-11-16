"""自有账号 API（P2 收窄）：仅 self 账号 CRUD——同行账号/候选/扫描已迁素材库（/api/sources）。

self 账号走自我研究链（self_scan/self_analyze/创作者中心回流），表仍 watch_accounts，
P4 数据域迁移 accounts。creator_cookie 绝不出后端（回显只有 has_cookie 位）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..channels.engine import channel_id_of
from ..models import Account, User
from ._listing import require_douyin_url

router = APIRouter(prefix="/api/watch")


def _account_out(acc: Account) -> dict:
    """账号出参统一脱敏（唯一出口）：creator_cookie 绝不出后端，回显只有 has_cookie 位。
    P4：数据在 accounts 表；输出保持旧 watch_accounts 形状（Style 前端零改动）。"""
    auth = acc.auth or {}
    return {
        "id": acc.id, "tenant_id": acc.tenant_id, "platform": acc.platform,
        "name": acc.name, "url": acc.profile_url, "note": acc.note,
        "kind": "self", "account_type": "persona", "enabled": bool(acc.enabled),
        "has_cookie": bool(auth.get("creator_cookie")),
        "last_scan_at": acc.last_sync_at,
        "created_at": acc.created_at, "updated_at": acc.updated_at,
    }


@router.get("/accounts")
def list_accounts(account_type: str = "", user: User = Depends(require_user)) -> dict:
    """自有账号清单（恒 kind=self；同行账号在 /api/sources）。"""
    with Session(engine) as s:
        rows = s.exec(select(Account).order_by(Account.id)).all()  # type: ignore[attr-defined]
        return {"items": [_account_out(r) for r in rows]}


class AccountIn(BaseModel):
    platform: str = "douyin"
    name: str
    url: str = ""
    note: str = ""
    account_type: str = "persona"
    creator_cookie: str = ""  # 创作者中心登录态（回显只给 has_cookie 不回 cookie）


class AccountUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    note: str | None = None
    enabled: bool | None = None
    account_type: str | None = None
    creator_cookie: str | None = None  # 传空串=清除（换号/作废）


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn, user: User = Depends(require_user)) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="账号名不能为空")
    if body.platform not in ("douyin", "channels"):
        raise HTTPException(status_code=400, detail="platform 必须是 douyin / channels")
    if body.platform == "douyin" and not body.url.strip():
        raise HTTPException(status_code=400,
                            detail="抖音账号需要主页链接（App 内分享主页 → 复制链接）")
    if body.url.strip():
        require_douyin_url(body.url.strip())
    with Session(engine) as s:
        acc = Account(tenant_id=user.tenant_id, channel_id=channel_id_of(s, "persona"),
                      platform=body.platform, name=body.name.strip(),
                      handle=body.name.strip()[:40], profile_url=body.url.strip(),
                      note=body.note.strip()[:300],
                      auth={"creator_cookie": body.creator_cookie.strip()} if body.creator_cookie.strip() else {})
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return _account_out(acc)


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="account not found")
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="账号名不能为空")
            acc.name = body.name.strip()
            acc.handle = body.name.strip()[:40]
        if body.url is not None:
            if body.url.strip():
                require_douyin_url(body.url.strip())
            acc.profile_url = body.url.strip()
        if body.note is not None:
            acc.note = body.note
        if body.enabled is not None:
            acc.enabled = body.enabled
        if body.creator_cookie is not None:
            auth = dict(acc.auth or {})
            auth["creator_cookie"] = body.creator_cookie.strip()  # 传空串=清除
            acc.auth = auth
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return _account_out(acc)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        acc = s.get(Account, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="account not found")
        s.delete(acc)
        s.commit()
    return {"deleted": account_id}


@router.post("/accounts/{account_id}/scan", status_code=202)
async def scan_account(account_id: int, user: User = Depends(require_user)) -> dict:
    """自有账号手动扫描（自我研究链输入）。"""
    with Session(engine) as s:
        if s.get(Account, account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
    from datetime import datetime

    job = await runner.submit("self_scan", {"auto": True},
                              dedup_key=f"{user.tenant_id}:selfscan:{datetime.now():%Y%m%d}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}
