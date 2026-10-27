"""认证 API：登录 / 登出 / 当前用户 / 修改密码（单管理员版；多租户已拆 2026-10-27）。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import (LOGIN_GUARD, SESSION_COOKIE, SESSION_DAYS, CurrentUser, _DUMMY_HASH,
                    client_ip, create_session, hash_password, require_user, verify_password)
from ..db import engine
from ..models import Tenant, User, UserSession
from ..settings import settings

router = APIRouter(prefix="/api/auth")


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def _me_payload(cu: CurrentUser, tenant_name: str = "") -> dict:
    return {"user_id": cu.id, "username": cu.username, "role": cu.role,
            "display_name": cu.display_name or cu.username,
            "must_change_password": cu.must_change_password,
            "tenant_id": cu.tenant_id,
            "tenant_name": tenant_name}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    username = body.username.strip()
    ip = client_ip(request)
    wait = LOGIN_GUARD.retry_after(username, ip)
    if wait > 0:  # 失败限流：账号或来源 IP 触顶即锁（防爆破/撞库）
        raise HTTPException(status_code=429,
                            detail=f"登录失败次数过多，请约 {max(1, round(wait / 60))} 分钟后再试",
                            headers={"Retry-After": str(wait)})
    with Session(engine) as s:
        user = s.exec(select(User).where(User.username == username)).first()
        tenant_name = ""
        t = s.get(Tenant, user.tenant_id) if user is not None else None
        if t is not None:
            tenant_name = t.name
    matched = (user is not None and user.status == "active"
               and verify_password(body.password, user.password_hash))
    if user is None:  # 时序均匀化：用户不存在也走一次 scrypt（防按耗时枚举用户名）
        verify_password(body.password, _DUMMY_HASH)
    if not matched:
        LOGIN_GUARD.record_fail(username, ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    LOGIN_GUARD.clear(username, ip)
    token, expires = create_session(user)
    response.set_cookie(SESSION_COOKIE, token, expires=expires,
                        httponly=True, samesite="lax",
                        secure=settings.cookie_secure)  # HTTPS 部署置 COOKIE_SECURE=1
    return _me_payload(CurrentUser(id=user.id, tenant_id=user.tenant_id, role=user.role,
                                   username=user.username, display_name=user.display_name,
                                   must_change_password=user.must_change_password),
                       tenant_name)


@router.post("/logout")
def logout(request: Request, response: Response):
    """登出：删除 Cookie **并**在服务端作废该会话（原实现只删 Cookie，token 过期前仍有效）。"""
    import hashlib as _hashlib

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        token_hash = _hashlib.sha256(token.encode()).hexdigest()
        with Session(engine) as s:
            row = s.exec(select(UserSession).where(UserSession.token_hash == token_hash)).first()
            if row is not None:
                s.delete(row)
                s.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, response: Response,
                    user: CurrentUser = Depends(require_user)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    with Session(engine) as s:
        u = s.get(User, user.id)
        if u is None or not verify_password(body.old_password, u.password_hash):
            raise HTTPException(status_code=400, detail="原密码错误")
        u.password_hash = hash_password(body.new_password)
        u.must_change_password = False
        s.add(u)
        # 改密即全端重登：作废该用户全部会话（含当前），随后补发一枚新会话
        for row in s.exec(select(UserSession).where(UserSession.user_id == u.id)).all():
            s.delete(row)
        s.commit()
        raw = s.get(User, user.id)
        s.expunge(raw)
    token, expires = create_session(raw)
    response.set_cookie(SESSION_COOKIE, token, expires=expires,
                        httponly=True, samesite="lax", secure=settings.cookie_secure)
    return {"ok": True}


@router.get("/me")
def me(user: CurrentUser = Depends(require_user)):
    with Session(engine) as s:
        t = s.get(Tenant, user.tenant_id)
    return _me_payload(user, t.name if t else "")
