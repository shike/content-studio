"""统一稿件 API（内容域，PRD R0.8）：跨渠道稿件查询/封面服务。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels.engine import article_out, note_out, script_out
from ..db import engine
from ..models import Content, User

router = APIRouter(prefix="/api/contents")

_OUT_BY_KIND = {"video_script": script_out, "long_article": article_out, "note": note_out}


class ContentUpdate(BaseModel):
    title: str | None = None
    body: str | None = None


@router.get("")
def list_contents(channel: str = "", kind: str = "", status: str = "",
                 limit: int = 50, offset: int = 0,
                 user: User = Depends(require_user)) -> dict:
    """跨渠道稿件列表（数据看板/统一检索用；各渠道页走专属门面）。"""
    from sqlalchemy import text as _text

    with Session(engine) as s:
        q = select(Content)
        if channel:
            from ..channels.engine import channel_id_of

            q = q.where(Content.channel_id == channel_id_of(s, channel))  # type: ignore[attr-defined]
        if kind:
            q = q.where(Content.kind == kind)  # type: ignore[attr-defined]
        if status:
            q = q.where(Content.status == status)  # type: ignore[attr-defined]
        total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
        rows = s.exec(q.order_by(Content.id.desc())  # type: ignore[attr-defined]
                      .offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
        return {"items": [{"id": r.id, "kind": r.kind, "title": r.title,
                           "status": r.status, "channel_id": r.channel_id,
                           "created_at": r.created_at} for r in rows],
                "total": total}


@router.get("/{content_id}/cover")
def note_cover(content_id: int):
    """小红书封面图服务（PNG；本地文件即用即存，可重复渲染覆盖）。"""
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise HTTPException(status_code=404, detail="note not found")
        path = (c.payload or {}).get("cover") or ""
    import os

    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="封面未生成")
    return FileResponse(path, media_type="image/png")
