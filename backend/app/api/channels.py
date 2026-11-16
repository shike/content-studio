"""内容域 API：渠道配置 / 统一稿件 / 发布登记（含公众号 API 发布，人工触发）。"""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels import xhs as xhs_line
from ..channels.engine import (channel_id_of, channel_key_of, note_out,
                               ensure_channels)
from ..db import engine
from ..integrations import wechat_publisher
from ..jobs.runner import runner
from ..models import Channel, Content, Idea, Publication, User
from ..articles.renderer import sanitize_html

router = APIRouter(prefix="/api/channels")


class ChannelConfigIn(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict | None = None


@router.get("")
def list_channels(user: User = Depends(require_user)) -> dict:
    """渠道清单（PRD R8.2）：四条内容线的注册态与配置。"""
    ensure_channels()
    with Session(engine) as s:
        rows = s.exec(select(Channel).order_by(Channel.id)).all()
        return {"items": [r.model_dump() for r in rows]}


@router.get("/{key}")
def get_channel(key: str, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        row = s.exec(select(Channel).where(Channel.key == key)).first()  # type: ignore[attr-defined]
        if row is None:
            raise HTTPException(status_code=404, detail="channel not found")
        return row.model_dump()


@router.put("/{key}")
def update_channel(key: str, body: ChannelConfigIn,
                   user: User = Depends(require_user)) -> dict:
    """渠道配置读写（仅管理员语义由路由依赖保证——settings 同款）。"""
    with Session(engine) as s:
        row = s.exec(select(Channel).where(Channel.key == key)).first()  # type: ignore[attr-defined]
        if row is None:
            raise HTTPException(status_code=404, detail="channel not found")
        if body.name is not None:
            row.name = body.name.strip()[:30]
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.config is not None:
            row.config = body.config
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.model_dump()


# ── 小红书线（PRD R5）：手动发起选题 → 笔记稿 → 发布登记 ──

class XhsIdeaIn(BaseModel):
    title: str
    angle: str = ""  # 切角/要求（可空）
    material_ids: list[int] = []  # 素材库引用（写作原料 + usages 追溯）


@router.post("/xhs/ideas", status_code=201)
def create_xhs_idea(body: XhsIdeaIn, user: User = Depends(require_user)) -> dict:
    """手动发起小红书选题（PRD R5.2：本期不做自动产线，宁缺勿滥）。"""
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="选题标题不能为空")
    with Session(engine) as s:
        idea = Idea(tenant_id=user.tenant_id, channel_id=channel_id_of(s, "xhs"),
                    source="manual", title_hint=title[:40], angle=body.angle.strip()[:120],
                    payload={"material_ids": body.material_ids[:5]}, status="draft")
        s.add(idea)
        s.commit()
        s.refresh(idea)
        return {"id": idea.id, "title": idea.title_hint, "status": idea.status}


class ComposeIn(BaseModel):
    pass


@router.post("/xhs/ideas/{idea_id}/compose", status_code=202)
async def compose_xhs_idea(idea_id: int, user: User = Depends(require_user)) -> dict:
    """生成笔记稿：note_generate（标题/正文/标签/封面）→ review 态。"""
    with Session(engine) as s:
        idea = s.get(Idea, idea_id)
        if idea is None or idea.channel_id != channel_id_of(s, "xhs"):
            raise HTTPException(status_code=404, detail="idea not found")
        if idea.content_id:
            dup = s.get(Content, idea.content_id)
            if dup is not None and dup.status in ("drafting", "review", "ready"):
                raise HTTPException(status_code=409,
                                    detail=f"该选题已有笔记稿（#{dup.id}），请先删除再重生成")
        c = Content(tenant_id=idea.tenant_id or 1, channel_id=idea.channel_id,
                    idea_id=idea.id, kind="note", title=idea.title_hint[:30],
                    body="", payload={"note_state": "drafting",
                                      "material_ids": (idea.payload or {}).get("material_ids") or [],
                                      "angle": idea.angle or ""})
        s.add(c)
        s.flush()
        idea.content_id = c.id
        s.add(idea)
        s.commit()
        content_id = c.id
    job = await runner.submit("note_generate", {"content_id": content_id},
                              dedup_key=f"note:{content_id}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"content_id": content_id, "job_id": job.id}


@router.get("/xhs/notes")
def list_xhs_notes(status: str = "", limit: int = 50, offset: int = 0,
                   user: User = Depends(require_user)) -> dict:
    """小红书笔记清单（kind=note）。"""
    with Session(engine) as s:
        q = select(Content).where(Content.kind == "note")  # type: ignore[attr-defined]
        if status:
            q = q.where(Content.status == status)  # type: ignore[attr-defined]
        total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
        rows = s.exec(q.order_by(Content.id.desc())  # type: ignore[attr-defined]
                      .offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()
        return {"items": [note_out(r) for r in rows], "total": total}


@router.get("/xhs/notes/{content_id}")
def get_xhs_note(content_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise HTTPException(status_code=404, detail="note not found")
        d = note_out(c)
        if d.get("cover"):
            d["cover_url"] = f"/api/contents/{c.id}/cover"
        return d


class NoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None


@router.put("/xhs/notes/{content_id}")
def update_xhs_note(content_id: int, body: NoteUpdate,
                    user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise HTTPException(status_code=404, detail="note not found")
        p = dict(c.payload or {})
        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(status_code=400, detail="标题不能为空")
            c.title = body.title.strip()[:30]
        if body.body is not None:
            c.body = body.body
        if body.tags is not None:
            p["tags"] = [t.strip().lstrip("#")[:16] for t in body.tags if t.strip()][:8]
        p["note_state"] = "review" if c.status in ("drafting", "failed") else p.get("note_state")
        c.payload = p
        if c.status in ("drafting", "failed"):
            c.status = "review"
        s.add(c)
        s.commit()
        s.refresh(c)
        return note_out(c)


@router.post("/xhs/notes/{content_id}/cover", status_code=202)
async def rerender_cover(content_id: int, body: dict | None = None,
                         user: User = Depends(require_user)) -> dict:
    """重渲染封面（可传 cover_title 覆盖大字）。"""
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise HTTPException(status_code=404, detail="note not found")
        big = str((body or {}).get("cover_title") or c.title or "")[:10]
        tenant_id = c.tenant_id or 1
    import asyncio

    try:
        path = await asyncio.to_thread(xhs_line.render_cover, big, tenant_id,
                                       f"xhs_{content_id}.png")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"封面渲染失败：{e}") from None
    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["cover"] = path
        c.payload = p
        s.add(c)
        s.commit()
    return {"cover": path, "cover_url": f"/api/contents/{content_id}/cover"}


@router.delete("/xhs/notes/{content_id}")
def delete_xhs_note(content_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise HTTPException(status_code=404, detail="note not found")
        if c.idea_id:
            idea = s.get(Idea, c.idea_id)
            if idea is not None and idea.content_id == c.id:
                idea.content_id = None
                idea.status = "draft"
                s.add(idea)
        s.delete(c)
        s.commit()
    return {"ok": True}


# ── 发布（人工登记 / 公众号 API，人工触发）──

class PublishIn(BaseModel):
    mode: str = "manual"  # manual|wechat_draft|wechat_submit
    url: str = ""  # manual 必填
    account_id: int | None = None


@router.post("/{key}/contents/{content_id}/publish", status_code=201)
async def publish_content(key: str, content_id: int, body: PublishIn,
                          user: User = Depends(require_user)) -> dict:
    """发布登记/API 发布统一入口。

    manual：人工发布后贴 URL 登记（全渠道）；
    wechat_draft / wechat_submit：公众号官方 API（渲染 HTML → 建草稿 → 可选群发），
    凭证未配置/开关关闭显式报错并回退人工路径。
    """
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None:
            raise HTTPException(status_code=404, detail="content not found")
        ch_key = channel_key_of(s, c.channel_id)
        if ch_key != key:
            raise HTTPException(status_code=400, detail=f"内容不属于渠道 {key}")
        if key == "wechat" and c.kind == "long_article":
            html = (c.payload or {}).get("html") or ""
        else:
            html = ""

    if body.mode == "manual":
        if not body.url.strip().startswith("http"):
            raise HTTPException(status_code=400, detail="manual 发布需要贴发布后链接")
        url, external, mode, platform = body.url.strip()[:300], "", "manual", {
            "persona": "douyin", "leadgen": "douyin", "wechat": "wechat", "xhs": "xiaohongshu"}[key]
    elif key == "wechat" and body.mode in ("wechat_draft", "wechat_submit"):
        if not html.strip():
            raise HTTPException(status_code=400, detail="先渲染公众号格式（render）再 API 发布")
        from ..articles.renderer import sanitize_html

        try:
            draft_id = wechat_publisher.add_draft(
                title=(c.title or "无题")[:64], html=sanitize_html(html),
                author="跃迁内容工作室")
            external, mode, url, platform = draft_id, body.mode, "", "wechat"
            if body.mode == "wechat_submit":
                wechat_publisher.submit_publish(draft_id)
        except wechat_publisher.WechatPublishError as e:
            raise HTTPException(status_code=409,
                                detail=f"{e}（可回退人工：复制 HTML 后到公众号后台发布）") from None
    else:
        raise HTTPException(status_code=400,
                            detail=f"渠道 {key} 不支持 {body.mode}（仅人工登记；公众号支持 API）")

    with Session(engine) as s:
        c = s.get(Content, content_id)
        now = datetime_now()
        p = dict(c.payload or {})
        state_key = ("script_state" if c.kind == "video_script"
                     else "article_state" if c.kind == "long_article" else "note_state")
        p[state_key] = "published"
        p["publication"] = {"url": url, "published_at": now, "mode": mode,
                            "external_id": external}
        c.payload = p
        c.status = "published"
        s.add(c)
        pub = Publication(tenant_id=c.tenant_id or 1, content_id=c.id,
                          platform=platform, url=url, external_id=external,
                          published_at=now, publish_mode=mode)
        s.add(pub)
        s.commit()
        s.refresh(pub)
        return {"publication_id": pub.id, "content_id": c.id, "mode": mode,
                "url": url, "external_id": external,
                "state": p[state_key]}


def datetime_now() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M")


@router.get("/{key}/publications")
def list_publications(key: str, limit: int = 50,
                      user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        ch = channel_id_of(s, key)
        rows = s.exec(
            select(Publication).where(Publication.tenant_id == user.tenant_id)  # type: ignore[attr-defined]
            .order_by(Publication.id.desc())).all()
        out = []
        for r in rows:
            c = s.get(Content, r.content_id)
            if c is not None and c.channel_id == ch:
                out.append({**r.model_dump(), "content_title": c.title,
                            "content_kind": c.kind})
        return {"items": out[:max(1, min(limit, 200))]}


@router.get("/{key}/publish-status")
def publish_status(key: str, user: User = Depends(require_user)) -> dict:
    """发布能力状态（前端按钮口径）：manual 恒可用；wechat 看 enabled+凭证。"""
    if key == "wechat":
        return {"manual": True, "api": wechat_publisher.publish_configured()}
    return {"manual": True, "api": False}
