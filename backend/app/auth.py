"""认证与租户上下文：口令哈希（scrypt）+ 服务端会话 + FastAPI 依赖。

- 密码：hashlib.scrypt（标准库，n=2^14 r=8 p=1），存储格式 scrypt$salt$hash
- 会话：随机 token 放 HttpOnly Cookie，库存 sha256(token) 与过期时间
- 依赖：require_user（任何活跃用户）/ require_admin（平台管理员）
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from contextvars import ContextVar
from typing import Optional
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from .db import engine
from .models import Tenant, User, UserSession
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


def create_session(user: User) -> tuple[str, datetime]:
    """创建服务端会话，返回 (明文 token, 带 UTC 时区的过期时间)。token 只存哈希。"""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        s.add(UserSession(token_hash=token_hash, user_id=user.id,
                          tenant_id=user.tenant_id,
                          expires_at=expires.replace(tzinfo=None)))
        s.commit()
    return token, expires


def _session_user(token: str) -> Optional[User]:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with Session(engine) as s:
        us = s.exec(select(UserSession).where(
            UserSession.token_hash == token_hash)).first()  # type: ignore[attr-defined]
        if us is None or us.expires_at < _now():
            return None
        return s.get(User, us.user_id)


def get_current_user(request: Request) -> Optional[User]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _session_user(token)


async def require_user(request: Request) -> User:
    user = get_current_user(request)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="未登录或会话失效")
    with Session(engine) as s:  # 纵深防御：停用租户的残留会话一律失效
        tenant = s.get(Tenant, user.tenant_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(status_code=403, detail="租户已停用，请联系管理员")
    ACTOR.set((user.tenant_id, user.id, user.role))
    return user


async def require_ready_user(request: Request) -> User:
    """业务路由依赖：在 require_user 之上强制完成首登改密。

    PRD R11.2「一次性密码首登强制改密」的服务端口径——否则直接调 API 可绕过前端拦截，
    一次性密码被转发后攻击者可长期使用而受害者的改密动作永不被触发。"""
    user = await require_user(request)
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="首次登录需先修改密码后再使用")
    return user


async def require_admin(request: Request) -> User:
    user = await require_user(request)
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return user


async def require_admin_dep(request: Request):
    """路由级依赖：要求平台管理员。"""
    return await require_admin(request)


async def require_tenant_admin(request: Request) -> User:
    user = await require_user(request)
    if user.role not in ("platform_admin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="需要租户管理员权限")
    return user
