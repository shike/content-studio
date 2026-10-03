"""管理后台 API：租户管理 + 成员管理（产品化 P1）。

权限分层：
- platform_admin：全部租户与全部用户（建租户/充值/启停/建账号/改角色）
- tenant_admin：仅本租户成员（建账号/重置密码/启停；不可触碰 platform_admin）
- member：无入口（路由层 403）

一次性密码规则：新建账号与重置密码均生成随机口令、仅本次响应返回一次，
明文不落库；首登强制改密（must_change_password）。停用/重置密码即清除该用户全部会话。
"""
from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import hash_password, require_admin_dep, require_user
from datetime import datetime
from pathlib import Path

from ..credits import topup
from ..db import engine
from ..models import (BeanLedger, Article, AvatarConfig, AvatarVideo, BenchmarkVideo,
                      CreditTransaction, Dismissal, Job, LLMCall, RadarTopic, Topic,
                      ResearchReport, Script, SelfVideo, StyleProfile, Tenant, User,
                      UserSession, WatchAccount, WatchCandidate)
from ..settings import settings

router = APIRouter(prefix="/api/admin")

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")
_ROLES = ("platform_admin", "tenant_admin", "member")


async def _require_platform(request: Request) -> User:
    user = await require_user(request)
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return user


async def _require_tenant_admin(request: Request) -> User:
    user = await require_user(request)
    if user.role not in ("platform_admin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="需要租户管理员权限")
    return user


def _gen_password() -> str:
    return secrets.token_urlsafe(8)


def _user_view(u: User, tenant_name: str = "") -> dict:
    return {"id": u.id, "tenant_id": u.tenant_id, "tenant_name": tenant_name,
            "username": u.username, "role": u.role,
            "display_name": u.display_name or u.username,
            "status": u.status, "must_change_password": u.must_change_password,
            "created_at": u.created_at.isoformat() if u.created_at else None}


def _kill_sessions(user_id: int) -> None:
    with Session(engine) as s:
        for us in s.exec(select(UserSession).where(UserSession.user_id == user_id)):
            s.delete(us)
        s.commit()


# ---------- 租户管理（platform_admin） ----------

@router.get("/tenants")
async def list_tenants(request: Request):
    await _require_platform(request)
    cutoff = _usage_cutoff(30)
    with Session(engine) as s:
        tenants = s.exec(select(Tenant).order_by(Tenant.id)).all()
        users = s.exec(select(User)).all()
        counts: dict[int, int] = {}
        for u in users:
            counts[u.tenant_id] = counts.get(u.tenant_id, 0) + 1
        # 列表一眼列：近 30 天 LLM 成本估算与活跃成员数
        cost30: dict[int, float] = {}
        for r in s.exec(select(LLMCall).where(LLMCall.created_at >= cutoff)).all():  # type: ignore[attr-defined]
            cost30[r.tenant_id] = cost30.get(r.tenant_id, 0.0) + r.cost_est
        active30: dict[int, set] = {}
        for j in s.exec(select(Job).where(Job.created_at >= cutoff,
                                          Job.status != "superseded")).all():  # type: ignore[attr-defined]
            if j.tenant_id and j.user_id:
                active30.setdefault(j.tenant_id, set()).add(j.user_id)
        return {"tenants": [{
            "id": t.id, "name": t.name, "credits": t.credits, "status": t.status,
            "member_count": counts.get(t.id, 0),
            "cost30": round(cost30.get(t.id, 0.0), 2),
            "active_members30": len(active30.get(t.id, set())),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            # 品牌/内容配置（品牌编辑卡的数据源；缺失=中性默认，卡片留空即按默认渲染）
            "brand": t.brand or {},
        } for t in tenants]}


class TenantIn(BaseModel):
    name: str
    credits: int = 0
    admin_username: str = ""  # 可选：同时创建首个 tenant_admin
    admin_display_name: str = ""


@router.post("/tenants")
async def create_tenant(body: TenantIn, request: Request):
    await _require_platform(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="租户名称不能为空")
    if body.credits < 0:
        raise HTTPException(status_code=400, detail="初始积分不能为负")
    uname = body.admin_username.strip()
    if uname and not _USERNAME_RE.match(uname):
        raise HTTPException(status_code=400,
                            detail="管理员用户名需 3-32 位字母/数字/下划线")
    one_time_password = ""
    with Session(engine) as s:
        if s.exec(select(Tenant).where(Tenant.name == name)).first():
            raise HTTPException(status_code=400, detail=f"租户「{name}」已存在")
        if uname and s.exec(select(User).where(User.username == uname)).first():
            raise HTTPException(status_code=400, detail=f"用户名 {uname} 已被占用")
        t = Tenant(name=name, credits=body.credits)
        s.add(t)
        s.commit()
        s.refresh(t)
        s.add(CreditTransaction(tenant_id=t.id, delta=body.credits,
                                balance_after=body.credits, reason="建租户初始积分",
                                kind="topup"))
        admin_pwd = ""
        if uname:
            admin_pwd = _gen_password()
            s.add(User(tenant_id=t.id, username=uname,
                       password_hash=hash_password(admin_pwd), role="tenant_admin",
                       display_name=body.admin_display_name.strip() or uname,
                       status="active", must_change_password=True))
        s.commit()
        new_tenant_id = t.id
        one_time_password = admin_pwd
    out = {"ok": True, "tenant_id": new_tenant_id}
    if one_time_password:
        out["one_time_password"] = one_time_password
        out["admin_username"] = uname
    return out


class TopupIn(BaseModel):
    points: int
    reason: str = ""


class BrandIn(BaseModel):
    label_line1: str = ""
    label_line2: str = ""
    signature: str = ""
    persona: str = ""
    accent: str = ""
    primary: str = ""
    asr_vocab: str = ""
    cover_slogan: str = ""
    audience_note: str = ""


@router.get("/tenants/{tenant_id}/brand")
def get_tenant_brand(tenant_id: int, user: User = Depends(require_admin_dep)) -> dict:
    """租户品牌/人设配置（角标栏目名/头图署名/内容人设）。"""
    from ..tenant_brand import brand_of

    return {"tenant_id": tenant_id, "brand": brand_of(tenant_id)}


@router.put("/tenants/{tenant_id}/brand")
def put_tenant_brand(tenant_id: int, body: BrandIn,
                     user: User = Depends(require_admin_dep)) -> dict:
    """保存租户品牌/人设（保存后角标字体子集自动重生成）。"""
    from ..tenant_brand import save_brand

    try:
        norm = save_brand(tenant_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"tenant_id": tenant_id, "brand": norm}


@router.post("/tenants/{tenant_id}/topup")
async def tenant_topup(tenant_id: int, body: TopupIn, request: Request):
    await _require_platform(request)
    if body.points == 0:
        raise HTTPException(status_code=400, detail="积分数额不能为 0")
    with Session(engine) as s:
        if s.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail="租户不存在")
    if body.points > 0:
        reason = body.reason.strip() or "管理员充值（管理后台）"
    else:
        reason = body.reason.strip() or "管理员调整（管理后台）"
    balance = topup(tenant_id, body.points, reason)
    return {"ok": True, "balance": balance}


class TenantStatusIn(BaseModel):
    status: str  # active | disabled


@router.post("/tenants/{tenant_id}/status")
async def tenant_status(tenant_id: int, body: TenantStatusIn, request: Request):
    me = await _require_platform(request)
    if body.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status 仅支持 active/disabled")
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(status_code=404, detail="租户不存在")
        if t.id == me.tenant_id and body.status == "disabled":
            raise HTTPException(status_code=400, detail="不能停用自己所在租户")
        t.status = body.status
        s.add(t)
        s.commit()
        if body.status == "disabled":  # 停用租户即清全部成员会话
            for u in s.exec(select(User).where(User.tenant_id == tenant_id)):
                _kill_sessions(u.id)
    return {"ok": True, "status": body.status}


@router.get("/tenants/{tenant_id}/transactions")
async def tenant_transactions(tenant_id: int, request: Request):
    me = await _require_tenant_admin(request)
    if me.role != "platform_admin" and me.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="只能查看本租户积分流水")
    with Session(engine) as s:
        rows = s.exec(select(CreditTransaction).where(
            CreditTransaction.tenant_id == tenant_id).order_by(
            CreditTransaction.id.desc()).limit(50)).all()
        return {"transactions": [{
            "id": r.id, "delta": r.delta, "balance_after": r.balance_after,
            "reason": r.reason, "kind": r.kind,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows]}


# ---------- 成员管理 ----------

@router.get("/users")
async def list_users(request: Request):
    me = await _require_tenant_admin(request)
    with Session(engine) as s:
        q = select(User)
        if me.role != "platform_admin":
            q = q.where(User.tenant_id == me.tenant_id)  # type: ignore[attr-defined]
        users = s.exec(q.order_by(User.id)).all()
        tenants = {t.id: t.name for t in s.exec(select(Tenant)).all()}
    return {"users": [_user_view(u, tenants.get(u.tenant_id, "")) for u in users]}


class UserIn(BaseModel):
    username: str
    display_name: str = ""
    role: str = "member"
    tenant_id: int | None = None  # platform_admin 可指定租户；tenant_admin 忽略


@router.post("/users")
async def create_user(body: UserIn, request: Request):
    me = await _require_tenant_admin(request)
    uname = body.username.strip()
    if not _USERNAME_RE.match(uname):
        raise HTTPException(status_code=400, detail="用户名需 3-32 位字母/数字/下划线")
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail="角色不合法")
    tenant_id = me.tenant_id
    if me.role == "platform_admin":
        if body.role == "platform_admin" and body.tenant_id is None:
            raise HTTPException(status_code=400, detail="平台管理员账号必须指定所属租户")
        if body.tenant_id is not None:
            tenant_id = body.tenant_id
    elif body.role == "platform_admin":
        raise HTTPException(status_code=403, detail="租户管理员不能创建平台管理员")
    with Session(engine) as s:
        if s.exec(select(User).where(User.username == uname)).first():
            raise HTTPException(status_code=400, detail=f"用户名 {uname} 已被占用")
        if s.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail="目标租户不存在")
        pwd = _gen_password()
        u = User(tenant_id=tenant_id, username=uname,
                 password_hash=hash_password(pwd), role=body.role,
                 display_name=body.display_name.strip() or uname,
                 status="active", must_change_password=True)
        s.add(u)
        s.commit()
        s.refresh(u)
    return {"ok": True, "user": _user_view(u), "one_time_password": pwd}


class RoleIn(BaseModel):
    role: str


@router.post("/users/{user_id}/role")
async def change_role(user_id: int, body: RoleIn, request: Request):
    me = await _require_platform(request)
    if body.role not in _ROLES:
        raise HTTPException(status_code=400, detail="角色不合法")
    with Session(engine) as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if u.id == me.id and body.role != "platform_admin":
            raise HTTPException(status_code=400, detail="不能降级自己的平台管理员角色")
        u.role = body.role
        s.add(u)
        s.commit()
    return {"ok": True}


class StatusIn(BaseModel):
    status: str  # active | disabled


@router.post("/users/{user_id}/status")
async def change_user_status(user_id: int, body: StatusIn, request: Request):
    me = await _require_tenant_admin(request)
    if body.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status 仅支持 active/disabled")
    with Session(engine) as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if me.role != "platform_admin":
            if u.tenant_id != me.tenant_id:
                raise HTTPException(status_code=403, detail="只能管理本租户成员")
            if u.role == "platform_admin":
                raise HTTPException(status_code=403, detail="不能操作平台管理员账号")
        if u.id == me.id:
            raise HTTPException(status_code=400, detail="不能停用自己的账号")
        u.status = body.status
        s.add(u)
        s.commit()
    if body.status == "disabled":
        _kill_sessions(user_id)
    return {"ok": True, "status": body.status}


@router.post("/users/{user_id}/reset-password")
async def reset_password(user_id: int, request: Request):
    me = await _require_tenant_admin(request)
    with Session(engine) as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if me.role != "platform_admin":
            if u.tenant_id != me.tenant_id:
                raise HTTPException(status_code=403, detail="只能管理本租户成员")
            if u.role == "platform_admin":
                raise HTTPException(status_code=403, detail="不能操作平台管理员账号")
        pwd = _gen_password()
        u.password_hash = hash_password(pwd)
        u.must_change_password = True
        s.add(u)
        s.commit()
    _kill_sessions(user_id)  # 重置密码即踢下线
    return {"ok": True, "one_time_password": pwd}


# ---------- 用量统计（用户使用情况看板） ----------

def _usage_cutoff(days: int):
    from datetime import datetime, timedelta
    return datetime.now() - timedelta(days=max(1, min(days, 365)))


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


@router.get("/tenants/{tenant_id}/usage")
async def tenant_usage(tenant_id: int, request: Request, days: int = 30):
    """租户用量看板：LLM 真实台账 / 任务统计 / 蝉豆 / 积分 / 成员排行。"""
    me = await _require_tenant_admin(request)
    if me.role != "platform_admin" and me.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="只能查看本租户用量")
    cutoff = _usage_cutoff(days)
    from ..credits import job_points

    with Session(engine) as s:
        if s.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail="租户不存在")
        # LLM 真实消耗台账（按用途分账）
        llm_rows = s.exec(select(LLMCall).where(
            LLMCall.tenant_id == tenant_id,
            LLMCall.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        by_purpose: dict[str, dict] = {}
        for r in llm_rows:
            p = by_purpose.setdefault(r.purpose, {"purpose": r.purpose, "calls": 0,
                                                  "tokens_in": 0, "tokens_out": 0, "cost_est": 0.0})
            p["calls"] += 1
            p["tokens_in"] += r.tokens_in
            p["tokens_out"] += r.tokens_out
            p["cost_est"] += r.cost_est
        llm = {"calls": len(llm_rows),
               "tokens_in": sum(r.tokens_in for r in llm_rows),
               "tokens_out": sum(r.tokens_out for r in llm_rows),
               "cost_est": round(sum(r.cost_est for r in llm_rows), 2),
               "by_purpose": sorted(by_purpose.values(),
                                    key=lambda x: -x["cost_est"])}
        # 任务统计（归属落库后的新任务才有租户/成员信息）
        job_rows = s.exec(select(Job).where(
            Job.tenant_id == tenant_id,
            Job.created_at >= cutoff,
            Job.status != "superseded")).all()  # type: ignore[attr-defined]
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        job_by_day: dict[str, dict] = {}
        for j in job_rows:
            by_status[j.status] = by_status.get(j.status, 0) + 1
            by_type[j.type] = by_type.get(j.type, 0) + 1
            day = (j.created_at or cutoff).strftime("%m-%d")
            d = job_by_day.setdefault(day, {"day": day, "count": 0, "failed": 0, "cost": 0.0})
            d["count"] += 1
            if j.status == "failed":
                d["failed"] += 1
        llm_by_day: dict[str, float] = {}
        for r in llm_rows:
            if r.created_at:
                llm_by_day[r.created_at.strftime("%m-%d")] = \
                    llm_by_day.get(r.created_at.strftime("%m-%d"), 0.0) + r.cost_est
        # 逐日补零（全窗口都有刻度，看趋势不缺日）
        from datetime import datetime, timedelta
        by_day: list[dict] = []
        for i in range(days - 1, -1, -1):
            key = (datetime.now() - timedelta(days=i)).strftime("%m-%d")
            jd = job_by_day.get(key, {"count": 0, "failed": 0})
            by_day.append({"day": key, "count": jd["count"], "failed": jd["failed"],
                           "cost": round(llm_by_day.get(key, 0.0), 2)})
        ok = by_status.get("succeeded", 0)
        done = ok + by_status.get("failed", 0)
        jobs_stat = {"total": len(job_rows), "by_status": by_status,
                     "success_rate": round(ok / done, 3) if done else None,
                     "by_type": sorted(by_type.items(), key=lambda x: -x[1]),
                     "by_day": by_day}
        # 成员排行（口径：任务数 × JOB_POINTS 预扣积分；LLM 成本精确不到人）
        users = s.exec(select(User).where(User.tenant_id == tenant_id)).all()  # type: ignore[attr-defined]
        members = []
        for u in users:
            ujobs = [j for j in job_rows if j.user_id == u.id]
            ujobs.sort(key=lambda j: j.created_at or cutoff, reverse=True)
            members.append({
                "user_id": u.id, "username": u.username,
                "display_name": u.display_name or u.username, "role": u.role,
                "jobs": len(ujobs),
                "points": sum(job_points(j.type) for j in ujobs),
                "last_active_at": _iso(ujobs[0].created_at) if ujobs else None,
            })
        members.sort(key=lambda m: -m["jobs"])
        # 蝉豆与积分流水
        beans_rows = s.exec(select(BeanLedger).where(
            BeanLedger.tenant_id == tenant_id,
            BeanLedger.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        beans = {"beans": sum(b.beans for b in beans_rows),
                 "seconds": round(sum(b.seconds or 0 for b in beans_rows), 1)}
        txn_rows = s.exec(select(CreditTransaction).where(
            CreditTransaction.tenant_id == tenant_id,
            CreditTransaction.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        credits_stat = {"topup": sum(t.delta for t in txn_rows if t.delta > 0),
                        "consume": -sum(t.delta for t in txn_rows if t.delta < 0)}
        balance = getattr(s.get(Tenant, tenant_id), "credits", 0)
    if me.role != "platform_admin":
        # 租户管理员只看用量与积分，金额是平台内部口径
        llm.pop("cost_est", None)
        for p in llm.get("by_purpose", []):
            p.pop("cost_est", None)
        jobs_stat["by_day"] = [{k: v for k, v in d.items() if k != "cost"}
                               for d in jobs_stat["by_day"]]
    return {"days": days, "llm": llm, "jobs": jobs_stat, "beans": beans,
            "credits": credits_stat, "balance": balance, "members": members}


@router.get("/users/{user_id}/usage")
async def user_usage(user_id: int, request: Request, days: int = 30):
    """成员个人用量：任务数按类型分布 / 预扣积分 / 最近活跃 / 最近任务。"""
    me = await _require_tenant_admin(request)
    cutoff = _usage_cutoff(days)
    from ..credits import job_points
    with Session(engine) as s:
        u = s.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if me.role != "platform_admin" and me.tenant_id != u.tenant_id:
            raise HTTPException(status_code=403, detail="只能查看本租户成员用量")
        rows = s.exec(select(Job).where(
            Job.user_id == user_id,
            Job.created_at >= cutoff,
            Job.status != "superseded").order_by(Job.created_at.desc())).all()  # type: ignore[attr-defined]
        by_type: dict[str, int] = {}
        for j in rows:
            by_type[j.type] = by_type.get(j.type, 0) + 1
        tenant_name = ""
        t = s.get(Tenant, u.tenant_id)
        if t is not None:
            tenant_name = t.name
    return {"user_id": user_id, "username": u.username, "tenant_name": tenant_name,
            "days": days, "jobs_total": len(rows), "by_type": by_type,
            "points": sum(job_points(j.type) for j in rows),
            "last_active_at": _iso(rows[0].created_at) if rows else None,
            "recent": [{"id": j.id, "type": j.type, "status": j.status,
                        "created_at": _iso(j.created_at)} for j in rows[:10]]}


@router.get("/overview")
async def admin_overview(request: Request):
    """平台总览仪表盘：KPI + 14 天趋势 + 租户成本排行 + 最近动态（platform_admin）。"""
    await _require_platform(request)
    from datetime import datetime, timedelta
    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    c14 = day_start - timedelta(days=13)
    c30 = _usage_cutoff(30)
    with Session(engine) as s:
        tenants = s.exec(select(Tenant)).all()
        users = s.exec(select(User)).all()
        user_map = {u.id: (u.display_name or u.username) for u in users}
        tenant_map = {t.id: t.name for t in tenants}
        llm14 = s.exec(select(LLMCall).where(LLMCall.created_at >= c14)).all()  # type: ignore[attr-defined]
        llm30 = s.exec(select(LLMCall).where(LLMCall.created_at >= c30)).all()  # type: ignore[attr-defined]
        jobs14 = s.exec(select(Job).where(Job.created_at >= c14,
                                          Job.status != "superseded")).all()  # type: ignore[attr-defined]
        today_llm = [r for r in llm14 if r.created_at and r.created_at >= day_start]
        today_jobs = [j for j in jobs14 if j.created_at and j.created_at >= day_start]
        tj_done = [j for j in today_jobs if j.status in ("succeeded", "failed")]
        members_active30 = {j.user_id for j in jobs14
                            if j.user_id and j.created_at and j.created_at >= c30}
        # 近 14 天逐日趋势（补零）
        trend = []
        for i in range(13, -1, -1):
            d0 = day_start - timedelta(days=i)
            d1 = d0 + timedelta(days=1)
            day_llm = [r for r in llm14 if r.created_at and d0 <= r.created_at < d1]
            day_jobs = [j for j in jobs14 if j.created_at and d0 <= j.created_at < d1]
            trend.append({"day": d0.strftime("%m-%d"),
                          "cost": round(sum(r.cost_est for r in day_llm), 2),
                          "jobs": len(day_jobs),
                          "failed": sum(1 for j in day_jobs if j.status == "failed")})
        # 租户成本排行（30 天）
        cost_by_tenant: dict[int, float] = {}
        for r in llm30:
            cost_by_tenant[r.tenant_id] = cost_by_tenant.get(r.tenant_id, 0.0) + r.cost_est
        cost_by_tenant.pop(0, None)  # 系统调用（调度任务/审计 ping）不计租户排行
        top = sorted(cost_by_tenant.items(), key=lambda x: -x[1])[:5]
        # 最近动态：全平台最新任务事件（含系统任务）
        recent = s.exec(select(Job).where(Job.status != "superseded")
                        .order_by(Job.id.desc()).limit(15)).all()  # type: ignore[attr-defined]
        feed = [{"job_id": j.id, "type": j.type, "status": j.status,
                 "user": user_map.get(j.user_id) if j.user_id else "系统",
                 "tenant": tenant_map.get(j.tenant_id) if j.tenant_id else "系统",
                 "created_at": _iso(j.created_at)} for j in recent]
        credits_total = sum(t.credits or 0 for t in tenants)
    return {
        "tenants": {"total": len(tenants),
                    "active": sum(1 for t in tenants if t.status == "active")},
        "members": {"total": len(users), "active30": len(members_active30)},
        "llm": {"today": round(sum(r.cost_est for r in today_llm), 2),
                "d30": round(sum(r.cost_est for r in llm30), 2),
                "calls_today": len(today_llm)},
        "jobs": {"today": len(today_jobs),
                 "success_rate": (round(sum(1 for j in tj_done if j.status == "succeeded")
                                        / len(tj_done), 3) if tj_done else None)},
        "credits_total": credits_total,
        "trend": trend,
        "top_tenants": [{"id": tid, "name": tenant_map.get(tid, f"租户{tid}"),
                         "cost": round(cst, 2)} for tid, cst in top],
        "feed": feed,
    }


# ---------- 租户删除 / 数据导出（危险区，platform_admin） ----------

_EXPORT_TABLES = [
    ("topics", Topic), ("research_reports", ResearchReport), ("scripts", Script),
    ("articles", Article), ("benchmark_videos", BenchmarkVideo),
    ("watch_accounts", WatchAccount), ("watch_candidates", WatchCandidate),
    ("self_videos", SelfVideo), ("style_profiles", StyleProfile),
    ("avatar_configs", AvatarConfig), ("avatar_videos", AvatarVideo),
    ("radar_topics", RadarTopic), ("dismissals", Dismissal),
    ("bean_ledger", BeanLedger), ("credit_transactions", CreditTransaction),
]


def _delete_tenant_files(tenant_id: int) -> int:
    """删除租户磁盘文件（视频/音频/成片/配图目录），返回删除数。"""
    import shutil as _shutil
    removed = 0
    with Session(engine) as s:
        paths: list[str] = []
        for r in s.exec(select(BenchmarkVideo).where(BenchmarkVideo.tenant_id == tenant_id)):  # type: ignore[attr-defined]
            if r.media_path:
                paths.append(r.media_path)
            if r.path:
                paths.append(r.path)
            paths += [p for p in (r.srt_path, r.draft_path) if p]
        for r in s.exec(select(AvatarVideo).where(AvatarVideo.tenant_id == tenant_id)):  # type: ignore[attr-defined]
            paths += [p for p in (getattr(r, "audio_path", ""), getattr(r, "video_path", "")) if p]
    for p in paths:
        f = Path(p)
        if f.is_file():
            f.unlink(missing_ok=True)
            removed += 1
    img_dir = settings.data_dir / "article_images" / str(tenant_id)
    if img_dir.is_dir():
        _shutil.rmtree(img_dir, ignore_errors=True)
        removed += 1
    return removed


@router.get("/tenants/{tenant_id}/export")
async def tenant_export(tenant_id: int, request: Request):
    """租户数据导出：全业务表 JSON 下载（交接/备份用）。"""
    me = await _require_platform(request)
    from fastapi.responses import JSONResponse
    with Session(engine) as s:
        if s.get(Tenant, tenant_id) is None:
            raise HTTPException(status_code=404, detail="租户不存在")
        payload = {"tenant_id": tenant_id, "exported_at": datetime.now().isoformat(), "tables": {}}
        for name, model in _EXPORT_TABLES:
            rows = s.exec(select(model).where(model.tenant_id == tenant_id)).all()  # type: ignore[attr-defined]
            payload["tables"][name] = [r.model_dump(mode="json") for r in rows]
        users = s.exec(select(User).where(User.tenant_id == tenant_id)).all()  # type: ignore[attr-defined]
        payload["tables"]["users"] = [{"id": u.id, "username": u.username, "role": u.role,
                                       "display_name": u.display_name, "status": u.status} for u in users]
    return JSONResponse(payload, headers={
        "Content-Disposition": f'attachment; filename="tenant-{tenant_id}-export.json"'})


@router.delete("/tenants/{tenant_id}")
async def tenant_delete(tenant_id: int, request: Request, confirm_name: str = ""):
    """物理删除租户：先自动备份，再清磁盘文件与库内全部归属行。不可撤销。

    confirm_name 必须与租户名完全一致（二次确认）；有活跃任务的租户拒绝删除。"""
    me = await _require_platform(request)
    if tenant_id == me.tenant_id:
        raise HTTPException(status_code=400, detail="不能删除自己所在的租户")
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            raise HTTPException(status_code=404, detail="租户不存在")
        if t.name != confirm_name:
            raise HTTPException(status_code=400, detail="确认名称与租户名不一致")
        active = s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.tenant_id == tenant_id,  # type: ignore[attr-defined]
            Job.status.in_(["queued", "running", "parked"]),  # type: ignore[attr-defined]
        )).first()
        if active is not None:
            raise HTTPException(status_code=400,
                                detail=f"租户还有活跃任务（#{active.id} 在 {active.status}），等结束或清理后再删除")
    from ..db import backup_db
    backup_db("pre-tenant-delete", keep=10)  # 删库先备份（数据安全网约定）
    files = _delete_tenant_files(tenant_id)
    removed = {}
    with Session(engine) as s:
        for name, model in _EXPORT_TABLES:
            rows = s.exec(select(model).where(model.tenant_id == tenant_id)).all()  # type: ignore[attr-defined]
            removed[name] = len(rows)
            for r in rows:
                s.delete(r)
        jobs = s.exec(select(Job).where(Job.tenant_id == tenant_id)).all()  # type: ignore[attr-defined]
        removed["jobs"] = len(jobs)
        for j in jobs:
            s.delete(j)
        for u in s.exec(select(User).where(User.tenant_id == tenant_id)).all():  # type: ignore[attr-defined]
            for us in s.exec(select(UserSession).where(UserSession.user_id == u.id)):  # type: ignore[attr-defined]
                s.delete(us)
            s.delete(u)
        t = s.get(Tenant, tenant_id)
        if t is not None:
            s.delete(t)
        s.commit()
    return {"ok": True, "files_removed": files, "rows_removed": removed}
