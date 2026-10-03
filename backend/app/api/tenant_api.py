"""租户自助配置接口：租户管理员读写**当前活跃租户**的品牌与内容配置。

与 /api/admin/tenants/{id}/brand 的分工：管理后台=平台管理员管所有租户；
本路由=租户管理员自助（读写会话的活跃租户；一人多租户下随侧栏切换而变）。
成员角色 403（品牌影响整租户的生成口径，不对成员开放）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from ..auth import require_user
from ..db import engine
from ..models import Tenant, User
from .admin import BrandIn
from ..tenant_brand import brand_of, save_brand

router = APIRouter(prefix="/api/tenant")


def _require_tenant_admin(user: User) -> None:
    if user.role not in ("platform_admin", "tenant_admin"):
        raise HTTPException(status_code=403, detail="仅租户管理员可修改品牌与内容配置")


def _tenant_name(tenant_id: int) -> str:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        return t.name if t else ""


@router.get("/brand")
def get_own_brand(user: User = Depends(require_user)) -> dict:
    """活跃租户品牌与内容配置（读=任意成员；写=租户管理员）。"""
    return {"tenant_id": user.tenant_id, "tenant_name": _tenant_name(user.tenant_id),
            "brand": brand_of(user.tenant_id)}


@router.put("/brand")
def put_own_brand(body: BrandIn, user: User = Depends(require_user)) -> dict:
    _require_tenant_admin(user)
    norm = save_brand(user.tenant_id, body.model_dump())
    return {"tenant_id": user.tenant_id, "tenant_name": _tenant_name(user.tenant_id),
            "brand": norm}
