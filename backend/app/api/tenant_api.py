"""工作室自助配置接口：读写本工作室（单用户版唯一租户）的品牌与内容配置。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..auth import require_user
from ..db import engine
from ..models import Tenant, User
from ..tenant_brand import brand_of, save_brand

router = APIRouter(prefix="/api/tenant")


class BrandIn(BaseModel):
    label_line1: str = ""
    label_line2: str = ""
    signature: str = ""
    persona: str = ""
    primary: str = ""
    asr_vocab: str = ""
    cover_slogan: str = ""
    audience_note: str = ""


def _require_tenant_admin(user: User) -> None:
    if user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改品牌与内容配置")


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
