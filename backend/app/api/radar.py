"""热点雷达·话题洞察只读接口（2026-10-30：话题雷达并入热点雷达后，仅保留查看）。"""
from fastapi import APIRouter, Depends

from ..auth import require_user
from ..db import engine
from ..models import User
from sqlmodel import Session, select

router = APIRouter(prefix="/api/radar")


@router.get("/topics")
def list_topics(user: User = Depends(require_user)) -> dict:
    """已监测话题与最近命中（每日 06:00 热点雷达自动检查写入）。"""
    from ..models import RadarTopic

    with Session(engine) as s:
        rows = s.exec(
            select(RadarTopic).order_by(RadarTopic.enabled.desc(), RadarTopic.name)  # type: ignore[attr-defined]
        ).all()
        return {"items": [{
            "id": r.id,
            "name": r.name,
            "enabled": bool(r.enabled),
            "hits": r.hits or [],
            "updated_at": r.updated_at,
        } for r in rows]}
