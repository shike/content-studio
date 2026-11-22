"""统一稿件 API（内容域，PRD R0.8）：跨渠道稿件查询/封面服务/发布排期（R3-2）。

排期=Content.payload.publish_plan（"YYYY-MM-DD HH:MM"，人工设定）；发布登记后不可再排。"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels.engine import article_out, note_out, script_out
from ..db import engine
from ..models import Content, Publication, User

router = APIRouter(prefix="/api/contents")

_OUT_BY_KIND = {"video_script": script_out, "long_article": article_out, "note": note_out}


class ContentUpdate(BaseModel):
    title: str | None = None
    body: str | None = None


@router.get("")
def list_contents(channel: str = "", kind: str = "", status: str = "",
                  q: str = "", limit: int = 50, offset: int = 0,
                  user: User = Depends(require_user)) -> dict:
    """跨渠道稿件列表（数据看板/统一检索用；各渠道页走专属门面）。

    R4-1：q=标题模糊搜索；返回渠道 key 供台账跳转。"""

    with Session(engine) as s:
        q_ = select(Content)
        if q.strip():
            q_ = q_.where(Content.title.like(f"%{q.strip()[:60]}%"))  # type: ignore[attr-defined]
        if channel:
            from ..channels.engine import channel_id_of

            q_ = q_.where(Content.channel_id == channel_id_of(s, channel))  # type: ignore[attr-defined]
        if kind:
            q_ = q_.where(Content.kind == kind)  # type: ignore[attr-defined]
        if status:
            q_ = q_.where(Content.status == status)  # type: ignore[attr-defined]
        total = s.exec(select(func.count()).select_from(q_.subquery())).one() or 0
        rows = s.exec(q_.order_by(Content.id.desc())  # type: ignore[attr-defined]
                      .offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
        from ..models import Channel

        ch_map = {c.id: c.key for c in s.exec(select(Channel)).all()}  # type: ignore[attr-defined]
        return {"items": [{"id": r.id, "kind": r.kind, "title": r.title,
                           "status": r.status, "channel_id": r.channel_id,
                           "channel": ch_map.get(r.channel_id, ""),
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


class ScheduleIn(BaseModel):
    publish_at: str  # "YYYY-MM-DD HH:MM"；空串=清除排期


@router.put("/{content_id}/schedule")
def schedule_content(content_id: int, body: ScheduleIn,
                     user: User = Depends(require_user)) -> dict:
    """设定/清除发布排期（R3-2 2026-11-22）：人工决定哪天发，登记后作废。"""
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None:
            raise HTTPException(status_code=404, detail="内容不存在")
        pub = s.exec(select(Publication).where(  # type: ignore[attr-defined]
            Publication.content_id == content_id)).first()  # type: ignore[attr-defined]
        if pub is not None:
            raise HTTPException(status_code=400, detail="已发布登记的稿件不可再排期")
        v = (body.publish_at or "").strip()
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d %H:%M")
            except ValueError:
                raise HTTPException(status_code=400, detail="排期格式必须是 YYYY-MM-DD HH:MM") from None
        p = dict(c.payload or {})
        p["publish_plan"] = v
        c.payload = p
        s.add(c)
        s.commit()
        return {"ok": True, "publish_plan": v}
