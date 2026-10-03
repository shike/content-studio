"""认证 API：登录 / 登出 / 当前用户 / 我的租户与切换 / 修改密码。"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import (LOGIN_GUARD, SESSION_COOKIE, SESSION_DAYS, CurrentUser, _DUMMY_HASH,
                    _memberships_of, client_ip, create_session, get_current_user,
                    hash_password, pick_active_tenant, require_user,
                    session_tenant_of, switch_session_tenant, verify_password)
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


class SwitchTenantIn(BaseModel):
    tenant_id: int


def _tenant_names(s: Session) -> dict[int, str]:
    return {t.id: t.name for t in s.exec(select(Tenant)).all()}


def _me_payload(cu: CurrentUser, names: dict[int, str]) -> dict:
    """登录/me/切换三处共用的会话响应：活跃租户 + 可切换租户列表。"""
    return {"user_id": cu.id, "username": cu.username, "role": cu.role,
            "display_name": cu.display_name or cu.username,
            "must_change_password": cu.must_change_password,
            "tenant_id": cu.tenant_id,
            "tenant_name": names.get(cu.tenant_id, ""),
            "tenants": [{"tenant_id": m["tenant_id"],
                         "tenant_name": names.get(m["tenant_id"], f"租户 {m['tenant_id']}"),
                         "role": m["role"]} for m in cu.memberships]}


def _session_cu(user: User, active: int, mems: dict[int, str]) -> CurrentUser:
    """登录/改密后手工构造用户上下文（ memberships 快照口径与 _resolve_user 一致）。"""
    return CurrentUser(id=user.id, tenant_id=active,
                       role="platform_admin" if user.role == "platform_admin" else mems[active],
                       username=user.username, display_name=user.display_name,
                       must_change_password=user.must_change_password,
                       memberships=[{"tenant_id": t, "role": r} for t, r in mems.items()])


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
        mems = _memberships_of(s, user.id) if user is not None else {}
        names = _tenant_names(s)
    matched = (user is not None and user.status == "active"
               and verify_password(body.password, user.password_hash))
    if user is None:  # 时序均匀化：用户不存在也走一次 scrypt（防按耗时枚举用户名）
        verify_password(body.password, _DUMMY_HASH)
    if not matched:
        LOGIN_GUARD.record_fail(username, ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    active = pick_active_tenant(user, mems, s)
    if active is None:  # 无任何可用归属租户（默认租户停用且无其他归属）
        LOGIN_GUARD.record_fail(username, ip)
        raise HTTPException(status_code=403, detail="租户已停用，请联系管理员")
    LOGIN_GUARD.clear(username, ip)
    token, expires = create_session(user, tenant_id=active)
    response.set_cookie(SESSION_COOKIE, token, expires=expires,
                        httponly=True, samesite="lax",
                        secure=settings.cookie_secure)  # HTTPS 部署置 COOKIE_SECURE=1
    return _me_payload(_session_cu(user, active, mems), names)


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
    active = session_tenant_of(request) or user.tenant_id
    with Session(engine) as s:
        u = s.get(User, user.id)
        if u is None or not verify_password(body.old_password, u.password_hash):
            raise HTTPException(status_code=400, detail="原密码错误")
        u.password_hash = hash_password(body.new_password)
        u.must_change_password = False
        s.add(u)
        # 改密即全端重登：作废该用户全部会话（含当前），随后补发一枚新会话（继承活跃租户）
        for row in s.exec(select(UserSession).where(UserSession.user_id == u.id)).all():
            s.delete(row)
        s.commit()
        raw = s.get(User, user.id)
        s.expunge(raw)
    token, expires = create_session(raw, tenant_id=active)
    response.set_cookie(SESSION_COOKIE, token, expires=expires,
                        httponly=True, samesite="lax", secure=settings.cookie_secure)
    return {"ok": True}


@router.post("/switch-tenant")
def switch_tenant(body: SwitchTenantIn, request: Request,
                  user: CurrentUser = Depends(require_user)):
    """切换活跃租户：校验归属 + 租户 active，改会话行（cookie 不变），返回新会话口径。"""
    switch_session_tenant(request, user, body.tenant_id)
    cu = get_current_user(request)
    assert cu is not None  # 刚校验过会话
    with Session(engine) as s:
        names = _tenant_names(s)
    return _me_payload(cu, names)


@router.get("/me")
def me(user: CurrentUser = Depends(require_user)):
    with Session(engine) as s:
        names = _tenant_names(s)
    return _me_payload(user, names)
