"""发布台 API（新契约：只做物料包 + article 忽略出口；台账/回流/sau 已移除，见 PRD R6）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..models import Dismissal
from ..publishing import service as pub_service

router = APIRouter(prefix="/api/publishing")

_SCOPES = ("article",)


class PackageIn(BaseModel):
    asset_type: str  # script | article
    asset_id: int


class DismissIn(BaseModel):
    scope: str  # article
    asset_id: int


@router.post("/packages")
def packages(body: PackageIn) -> dict:
    if body.asset_type not in ("script", "article"):
        raise HTTPException(400, detail="asset_type 仅支持 script/article")
    return pub_service.build_package(body.asset_type, body.asset_id)


@router.get("/dismissals")
def list_dismissals() -> dict:
    with Session(engine) as s:
        rows = s.exec(select(Dismissal).order_by(Dismissal.id.desc())).all()
        return {"items": [r.model_dump() for r in rows]}


@router.post("/dismissals", status_code=201)
def dismiss(body: DismissIn) -> dict:
    if body.scope not in _SCOPES:
        raise HTTPException(400, detail=f"scope 必须是 {'、'.join(_SCOPES)}（publish/metrics 已随台账移除）")
    with Session(engine) as s:
        exists = s.exec(select(Dismissal).where(
            Dismissal.scope == body.scope,
            Dismissal.asset_id == body.asset_id)).first()
        if exists is None:
            s.add(Dismissal(scope=body.scope, asset_id=body.asset_id))
            s.commit()
    return {"ok": True}


@router.delete("/dismissals/{scope}")
def restore(scope: str) -> dict:
    if scope not in _SCOPES:
        raise HTTPException(400, detail=f"scope 必须是 {'、'.join(_SCOPES)}")
    with Session(engine) as s:
        rows = s.exec(select(Dismissal).where(Dismissal.scope == scope)).all()
        for r in rows:
            s.delete(r)
        s.commit()
        return {"restored": len(rows)}
