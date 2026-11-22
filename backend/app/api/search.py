"""统一资料检索（遗留收尾批 M11 v1）：一个搜索框跨素材/稿件/母题三实体。

各域已有专属搜索（KB 全文/台账/雷达）；这里做跨实体快速定位。收藏/集合 v1 裁掉
（单人工作台价值低，用户裁决口径）。"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from ..auth import require_user
from ..db import engine
from ..models import Content, Material, Motif, User

router = APIRouter(prefix="/api/search")


@router.get("")
def search_all(q: str, user: User = Depends(require_user)) -> dict:
    qq = q.strip()
    if not qq:
        return {"materials": [], "contents": [], "motifs": [], "q": ""}
    like = f"%{qq[:60]}%"
    with Session(engine) as s:
        materials = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
            (Material.title.like(like)) | (Material.body.like(like)))  # type: ignore[attr-defined]
            .order_by(Material.id.desc()).limit(10)).all()  # type: ignore[attr-defined]
        contents = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
            Content.title.like(like))  # type: ignore[attr-defined]
            .order_by(Content.id.desc()).limit(10)).all()  # type: ignore[attr-defined]
        motifs = s.exec(select(Motif).where(  # type: ignore[attr-defined]
            Motif.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
            (Motif.title.like(like)) | (Motif.note.like(like)))  # type: ignore[attr-defined]
            .order_by(Motif.id.desc()).limit(10)).all()  # type: ignore[attr-defined]
        return {
            "q": qq,
            "materials": [{"id": m.id, "title": m.title, "origin_type": m.origin_type,
                           "status": m.status} for m in materials],
            "contents": [{"id": c.id, "title": c.title, "kind": c.kind,
                          "status": c.status} for c in contents],
            "motifs": [{"id": m.id, "title": m.title, "status": m.status} for m in motifs],
        }
