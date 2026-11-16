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
from ..models import User, WatchAccount
from ._listing import require_douyin_url

router = APIRouter(prefix="/api/watch")


def _account_out(acc: WatchAccount) -> dict:
    """账号出参统一脱敏（唯一出口）：creator_cookie 绝不出后端，回显只有 has_cookie 位。"""
    d = acc.model_dump()
    d.pop("creator_cookie", None)
    d["has_cookie"] = bool(acc.creator_cookie)
    return d


@router.get("/accounts")
def list_accounts(account_type: str = "", user: User = Depends(require_user)) -> dict:
    """自有账号清单（恒 kind=self；同行账号在 /api/sources）。"""
    with Session(engine) as s:
        q = select(WatchAccount).where(WatchAccount.kind == "self")  # type: ignore[attr-defined]
        if account_type in ("persona", "leadgen"):
            q = q.where(WatchAccount.account_type == account_type)  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(WatchAccount.id)).all()  # type: ignore[attr-defined]
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
        acc = WatchAccount(platform=body.platform, name=body.name.strip(),
                           url=body.url.strip(), note=body.note.strip(), kind="self",
                           account_type=body.account_type if body.account_type in ("persona", "leadgen") else "persona",
                           creator_cookie=body.creator_cookie.strip())
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return _account_out(acc)


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        acc = s.get(WatchAccount, account_id)
        if acc is None or acc.kind != "self":
            raise HTTPException(status_code=404, detail="account not found")
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="账号名不能为空")
            acc.name = body.name.strip()
        if body.url is not None:
            if body.url.strip():
                require_douyin_url(body.url.strip())
            acc.url = body.url.strip()
        if body.note is not None:
            acc.note = body.note
        if body.enabled is not None:
            acc.enabled = body.enabled
        if body.account_type is not None:
            if body.account_type not in ("persona", "leadgen"):
                raise HTTPException(status_code=400, detail="account_type 必须是 persona / leadgen")
            acc.account_type = body.account_type
        if body.creator_cookie is not None:
            acc.creator_cookie = body.creator_cookie.strip()  # 传空串=清除
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return _account_out(acc)


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        acc = s.get(WatchAccount, account_id)
        if acc is None or acc.kind != "self":
            raise HTTPException(status_code=404, detail="account not found")
        s.delete(acc)
        s.commit()
    return {"deleted": account_id}


@router.post("/accounts/{account_id}/scan", status_code=202)
async def scan_account(account_id: int, user: User = Depends(require_user)) -> dict:
    """自有账号手动扫描（自我研究链输入）。"""
    with Session(engine) as s:
        if s.get(WatchAccount, account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
    from datetime import datetime

    job = await runner.submit("self_scan", {"auto": True},
                              dedup_key=f"{user.tenant_id}:selfscan:{datetime.now():%Y%m%d}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}
