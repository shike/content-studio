"""认证与租户上下文：口令哈希（scrypt）+ 服务端会话 + FastAPI 依赖。

- 密码：hashlib.scrypt（标准库，n=2^14 r=8 p=1），存储格式 scrypt$salt$hash
- 会话：随机 token 放 HttpOnly Cookie，库存 sha256(token) 与过期时间；
  会话行的 tenant_id = 当前活跃租户（一人多租户切换的落点）
- 依赖：require_user（任何活跃用户）/ require_admin（平台管理员）
- 租户归属：TenantMembership 多对多；users.tenant_id 仅为默认租户，
  users.role 仅承载 platform_admin 全局位——租户内角色看 membership
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .db import engine
from .models import Tenant, TenantMembership, User, UserSession
from .settings import settings

SESSION_COOKIE = "cs_session"
SESSION_DAYS = 7

# 任务归属链：require_user（async 依赖）设值 → runner.submit/gateway/db 事件读取。
# 必须在 async 上下文 set（sync 依赖跑线程池，写入不回传请求上下文）。
# (tenant_id, user_id, role)：role=platform_admin 时查询豁免租户过滤（平台视角）；
# (0, 0, "") = 系统上下文（调度/启动），查询不隔离但新行归属保持 0。
ACTOR: ContextVar[tuple[int, int, str]] = ContextVar("cs_actor", default=(0, 0, ""))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


# 时序均匀化用假哈希：用户不存在时也执行一次 scrypt（防按响应耗时枚举用户名）
_DUMMY_HASH = hash_password("timing-equalizer")


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, hash_hex = stored.split("$")
        h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                           n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h.hex(), hash_hex)
    except Exception:  # noqa: BLE001 格式错误一律视为不匹配
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class LoginGuard:
    """登录失败限流（防爆破/撞库）：账号与来源 IP 双维度滑动窗口计数，成功即清零。

    进程内内存态（单进程部署足够；重启清零，属可接受的轻量方案）。
    窗口/阈值经 .env 配置（LOGIN_MAX_FAILS / LOGIN_WINDOW_SEC / LOGIN_IP_MAX）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_user: dict[str, list[float]] = {}
        self._by_ip: dict[str, list[float]] = {}

    def _prune(self, bucket: dict[str, list[float]], key: str, now: float) -> list[float]:
        arr = [t for t in bucket.get(key, []) if now - t < settings.login_window_sec]
        if arr:
            bucket[key] = arr
        else:
            bucket.pop(key, None)
        return arr

    def retry_after(self, username: str, ip: str) -> int:
        """0 = 允许尝试；>0 = 需等待的秒数（账号或 IP 任一触顶即锁）。"""
        now = time.time()
        with self._lock:
            u = self._prune(self._by_user, username.lower(), now)
            p = self._prune(self._by_ip, ip, now)
            wait = 0
            if len(u) >= settings.login_max_fails and u:
                wait = max(wait, int(settings.login_window_sec - (now - u[0])) + 1)
            if len(p) >= settings.login_ip_max and p:
                wait = max(wait, int(settings.login_window_sec - (now - p[0])) + 1)
            return max(0, wait)

    def record_fail(self, username: str, ip: str) -> None:
        now = time.time()
        key = username.lower()
        with self._lock:
            u = self._prune(self._by_user, key, now)
            u.append(now)
            self._by_user[key] = u
            p = self._prune(self._by_ip, ip, now)
            p.append(now)
            self._by_ip[ip] = p

    def clear(self, username: str, ip: str) -> None:
        with self._lock:
            self._by_user.pop(username.lower(), None)
            self._by_ip.pop(ip, None)


LOGIN_GUARD = LoginGuard()


def client_ip(request: Request) -> str:
    """真实来源 IP。仅当直连方是回环地址（本机 Nginx 反代）时信任 X-Forwarded-For 首段。"""
    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "")
    if peer in ("127.0.0.1", "::1") and xff:
        return xff.split(",")[0].strip()
    return peer or "unknown"


def create_session(user: User, tenant_id: int = 0) -> tuple[str, datetime]:
    """创建服务端会话，返回 (明文 token, 带 UTC 时区的过期时间)。token 只存哈希。

    tenant_id=会话的活跃租户；0 取用户默认租户（改密重建等场景应显式传原活跃租户，
    否则用户会被切回默认租户）。"""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        s.add(UserSession(token_hash=token_hash, user_id=user.id,
                          tenant_id=tenant_id or user.tenant_id,
                          expires_at=expires.replace(tzinfo=None)))
        s.commit()
    return token, expires


@dataclass
class CurrentUser:
    """请求级用户上下文：User 实时字段 + 活跃租户 + 租户内有效角色。

    tenant_id 是会话携带的活跃租户（≠默认租户）；role 在 platform_admin 时保持
    全局位（跨租户豁免），否则等于活跃租户的 membership.role。端点按属性访问
    user.tenant_id / user.role / user.id 等，与旧 User 形态兼容。"""
    id: int
    tenant_id: int
    role: str
    username: str = ""
    display_name: str = ""
    status: str = "active"
    must_change_password: bool = False
    password_hash: str = ""
    memberships: list = field(default_factory=list)  # [{tenant_id, role}]


def _memberships_of(s: Session, user_id: int) -> dict[int, str]:
    rows = s.exec(select(TenantMembership).where(
        TenantMembership.user_id == user_id)).all()  # type: ignore[attr-defined]
    return {m.tenant_id: m.role for m in rows}


def _tenant_active(s: Session, tenant_id: int) -> bool:
    t = s.get(Tenant, tenant_id)
    return t is not None and t.status == "active"


def pick_active_tenant(user: User, mems: dict[int, str], s: Session) -> Optional[int]:
    """登录落点：默认租户优先（active 且有归属），否则第一个 active 归属；全不可用返回 None。"""
    if user.tenant_id in mems and _tenant_active(s, user.tenant_id):
        return user.tenant_id
    for tid in mems:
        if _tenant_active(s, tid):
            return tid
    return None


def _resolve_user(token: str) -> Optional[CurrentUser]:
    """会话 → 用户上下文。活跃租户失效（归属被移除/租户停用）时自动回落到
    可用归属并回写会话；无可回落抛 403（正常情况下停用/删除租户已清会话，
    这里是数据被手工改动时的防御）。"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        us = s.exec(select(UserSession).where(
            UserSession.token_hash == token_hash)).first()  # type: ignore[attr-defined]
        if us is None or us.expires_at < _now():
            return None
        user = s.get(User, us.user_id)
        if user is None or user.status != "active":
            return None
        mems = _memberships_of(s, user.id)
        active = us.tenant_id
        if active not in mems or not _tenant_active(s, active):
            cand = pick_active_tenant(user, mems, s)
            if cand is None:
                raise HTTPException(status_code=403,
                                    detail="归属租户均已停用或移除，请联系管理员")
            active = cand
            us.tenant_id = active
            s.add(us)
            s.commit()
        role = "platform_admin" if user.role == "platform_admin" else mems[active]
        return CurrentUser(id=user.id, tenant_id=active, role=role,
                           username=user.username, display_name=user.display_name,
                           status=user.status, must_change_password=user.must_change_password,
                           password_hash=user.password_hash,
                           memberships=[{"tenant_id": t, "role": r} for t, r in mems.items()])


def get_current_user(request: Request) -> Optional[CurrentUser]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _resolve_user(token)


def session_tenant_of(request: Request) -> Optional[int]:
    """按 cookie 找会话行当前活跃租户（切换租户时校验/回写用）。"""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        us = s.exec(select(UserSession).where(
            UserSession.token_hash == token_hash)).first()  # type: ignore[attr-defined]
        return us.tenant_id if us else None


def switch_session_tenant(request: Request, user: CurrentUser, tenant_id: int) -> None:
    """切换会话活跃租户（校验归属 + 租户 active，改会话行，cookie 不变）。"""
    if tenant_id not in {m["tenant_id"] for m in user.memberships}:
        raise HTTPException(status_code=403, detail="未归属该租户")
    if not _tenant_active_by_id(tenant_id):
        raise HTTPException(status_code=403, detail="目标租户已停用")
    token = request.cookies.get(SESSION_COOKIE) or ""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        us = s.exec(select(UserSession).where(
            UserSession.token_hash == token_hash)).first()  # type: ignore[attr-defined]
        if us is None:
            raise HTTPException(status_code=401, detail="会话失效，请重新登录")
        us.tenant_id = tenant_id
        s.add(us)
        s.commit()


def _tenant_active_by_id(tenant_id: int) -> bool:
    with Session(engine) as s:
        return _tenant_active(s, tenant_id)


async def require_user(request: Request) -> CurrentUser:
    cu = get_current_user(request)
    if cu is None:
        raise HTTPException(status_code=401, detail="未登录或会话失效")
    ACTOR.set((cu.tenant_id, cu.id, cu.role))
    return cu


async def require_ready_user(request: Request) -> CurrentUser:
    """业务路由依赖：在 require_user 之上强制完成首登改密。

    PRD R11.2「一次性密码首登强制改密」的服务端口径——否则直接调 API 可绕过前端拦截，
    一次性密码被转发后攻击者可长期使用而受害者的改密动作永不被触发。"""
    user = await require_user(request)
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="首次登录需先修改密码后再使用")
    return user


async def require_admin(request: Request) -> CurrentUser:
    user = await require_user(request)
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return user


async def require_admin_dep(request: Request):
    """路由级依赖：要求平台管理员。"""
    return await require_admin(request)


async def require_tenant_admin(request: Request) -> CurrentUser:
    """要求平台管理员，或活跃租户内的租户管理员（membership.role）。"""
    user = await require_user(request)
    if user.role not in ("platform_admin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="需要租户管理员权限")
    return user
