"""视觉工厂 API（M7 2026-11-22）：封面模板库（预览/默认模板）+ 图文卡片工厂（deck CRUD+页图服务）。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..channels.visual import (COVER_TEMPLATES, _TEMPLATE_NAMES, default_template,
                               render_cover_template, template_preview)
from ..db import engine
from ..jobs.runner import runner
from ..models import Content, Material, User

router = APIRouter(prefix="/api/visual")


@router.get("/templates")
def list_templates(user: User = Depends(require_user)) -> dict:
    """封面模板库：五套模板（key/名称/默认标记/预览图地址）。"""
    cur = default_template(user.tenant_id)
    return {"items": [{"key": k, "name": _TEMPLATE_NAMES[k], "is_default": k == cur,
                       "preview_url": f"/api/visual/templates/{k}/preview.png"}
                      for k in COVER_TEMPLATES],
            "current": cur}


@router.get("/templates/{key}/preview.png")
def template_preview_png(key: str, user: User = Depends(require_user)):
    if key not in COVER_TEMPLATES:
        raise HTTPException(status_code=404, detail="模板不存在")
    path = template_preview(key, user.tenant_id)
    return FileResponse(path, media_type="image/png")


@router.put("/templates/default")
def set_default_template(body: "TemplateIn", user: User = Depends(require_user)) -> dict:
    from sqlmodel import Session

    from ..models import AppSetting

    if body.key not in COVER_TEMPLATES:
        raise HTTPException(status_code=400, detail="未知模板")
    with Session(engine) as s:
        row = s.get(AppSetting, "xhs_cover_template")
        if row is None:
            s.add(AppSetting(key="xhs_cover_template", value=body.key))
        else:
            row.value = body.key
            s.add(row)
        s.commit()
    return {"ok": True, "current": body.key}


class TemplateIn(BaseModel):
    key: str


class DeckIn(BaseModel):
    source_kind: str  # note|material
    source_id: int
    page_count: int = 0  # 0=auto


@router.post("/decks", status_code=202)
async def create_deck(body: DeckIn, user: User = Depends(require_user)) -> dict:
    if body.source_kind not in ("note", "material"):
        raise HTTPException(status_code=400, detail="source_kind 只能是 note|material")
    with Session(engine) as s:
        if body.source_kind == "note":
            c = s.get(Content, body.source_id)
            if c is None or c.kind != "note" or not (c.body or "").strip():
                raise HTTPException(status_code=404, detail="笔记稿不存在或无正文")
            title = f"图文卡｜{(c.title or '')[:26]}"
        else:
            m = s.get(Material, body.source_id)
            if m is None or not (m.body or "").strip():
                raise HTTPException(status_code=404, detail="素材不存在或无正文")
            title = f"图文卡｜{(m.title or '')[:26]}"
        if body.source_kind == "note":
            ch_id = c.channel_id
        else:
            ch_id = m.channel_id
        deck = Content(tenant_id=user.tenant_id,
                       channel_id=ch_id,
                       kind="visual_deck", title=title, body="", status="drafting",
                       payload={"source_kind": body.source_kind, "source_id": body.source_id,
                                "page_count": max(0, min(body.page_count, 10)),
                                "deck_state": "drafting"})
        s.add(deck)
        s.commit()
        deck_id = deck.id
    job = await runner.submit("visual_deck", {"content_id": deck_id})
    return {"deck_id": deck_id, "job_id": job.id}


@router.get("/decks")
def list_decks(user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        rows = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.kind == "visual_deck")  # type: ignore[attr-defined]
            .order_by(Content.id.desc())).all()  # type: ignore[attr-defined]
        return {"items": [{"id": r.id, "title": r.title, "status": r.status,
                           "deck_state": (r.payload or {}).get("deck_state", ""),
                           "pages": len((r.payload or {}).get("image_paths") or []),
                           "source": {"kind": (r.payload or {}).get("source_kind"),
                                      "id": (r.payload or {}).get("source_id")},
                           "created_at": r.created_at} for r in rows]}


@router.get("/decks/{deck_id}")
def deck_detail(deck_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Content, deck_id)
        if c is None or c.kind != "visual_deck":
            raise HTTPException(status_code=404, detail="deck not found")
        p = c.payload or {}
        paths = p.get("image_paths") or []
        return {"id": c.id, "title": c.title, "status": c.status,
                "deck_state": p.get("deck_state", ""),
                "pages": [{"idx": i, "title": (p.get("pages") or [{}] * len(paths))[i].get("title", "") if i < len(p.get("pages") or []) else "",
                           "role": (p.get("pages") or [{}] * len(paths))[i].get("role", "") if i < len(p.get("pages") or []) else "",
                           "url": f"/api/visual/decks/{c.id}/pages/{i}.png"}
                          for i in range(len(paths))]}


@router.get("/decks/{deck_id}/pages/{idx}.png")
def deck_page_png(deck_id: int, idx: int, user: User = Depends(require_user)):
    with Session(engine) as s:
        c = s.get(Content, deck_id)
        if c is None or c.kind != "visual_deck":
            raise HTTPException(status_code=404, detail="deck not found")
        paths = (c.payload or {}).get("image_paths") or []
    if not 0 <= idx < len(paths):
        raise HTTPException(status_code=404, detail="页码不存在")
    import os

    if not os.path.exists(paths[idx]):
        raise HTTPException(status_code=404, detail="页图未生成")
    return FileResponse(paths[idx], media_type="image/png")


@router.delete("/decks/{deck_id}")
def delete_deck(deck_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Content, deck_id)
        if c is None or c.kind != "visual_deck":
            raise HTTPException(status_code=404, detail="deck not found")
        s.delete(c)
        s.commit()
    return {"ok": True}
