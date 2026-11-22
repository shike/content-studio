"""母题与渠道派生 API（M9 2026-11-22）：一个母题派生四线成品（口播/长文/小红书/朋友圈）。

母题层管三件事：一键派生、追踪矩阵、跨线不重做。各成品独立走各线既有生命周期——
母题不替代任何产线，只做触发器与台账。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..channels.engine import channel_id_of
from ..db import engine
from ..jobs.runner import runner
from ..models import Content, Idea, Motif, User

router = APIRouter(prefix="/api/motifs")

_DERIVE_KINDS = ("script", "article", "note", "moments")


def _motif_out(m: Motif, counts: dict | None = None) -> dict:
    d = {"id": m.id, "title": m.title, "note": m.note, "status": m.status,
         "payload": m.payload or {}, "created_at": m.created_at, "updated_at": m.updated_at}
    if counts is not None:
        d["counts"] = counts
    return d


def _line_state(s: Session, motif_id: int, tid: int) -> dict:
    """母题×四线派生状态（每线：n=成品数, items=最近 3 条）。

    script/note 走 Idea.motif_id；article 走 口播稿→parent 长文链；moments 走 payload.motif_id。"""
    out: dict[str, dict] = {}
    idea_rows = s.exec(select(Idea).where(  # type: ignore[attr-defined]
        Idea.motif_id == motif_id,  # type: ignore[attr-defined]
        Idea.status != "discarded")).all()  # type: ignore[attr-defined]
    script_ideas = [i for i in idea_rows if i.channel_id == channel_id_of(s, "persona")]
    note_ideas = [i for i in idea_rows if i.channel_id == channel_id_of(s, "xhs")]
    out["script"] = {"n": len(script_ideas),
                     "items": [{"id": i.id, "title": i.title_hint, "status": i.status,
                                "content_id": i.content_id} for i in script_ideas[:3]]}
    out["note"] = {"n": len(note_ideas),
                   "items": [{"id": i.id, "title": i.title_hint, "status": i.status,
                              "content_id": i.content_id} for i in note_ideas[:3]]}
    script_content_ids = [i.content_id for i in script_ideas if i.content_id]
    articles: list[Content] = []
    if script_content_ids:
        articles = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.kind == "long_article",  # type: ignore[attr-defined]
            Content.parent_content_id.in_(script_content_ids))).all()  # type: ignore[attr-defined]
    out["article"] = {"n": len(articles),
                      "items": [{"id": a.id, "title": a.title, "status": a.status} for a in articles[:3]]}
    from sqlalchemy import text as _text

    moments = s.execute(_text(
        "SELECT * FROM contents WHERE kind='moments' AND tenant_id=:tid "
        "AND payload->>'motif_id'=:mid ORDER BY id DESC LIMIT 3"),
        {"tid": tid, "mid": str(motif_id)}).mappings().all()
    moments = [Content(**{k: v for k, v in dict(r).items() if k in Content.model_fields}) for r in moments]
    out["moments"] = {"n": len(moments),
                      "items": [{"id": c.id, "title": c.title, "status": c.status,
                                 "posts": (c.payload or {}).get("posts") or []} for c in moments[:3]]}
    return out


class MotifIn(BaseModel):
    title: str
    note: str = ""
    source_kind: str = ""  # 绑素材锚（如 kb_doc/douyin_video）
    source_id: int | None = None


@router.post("", status_code=201)
def create_motif(body: MotifIn, user: User = Depends(require_user)) -> dict:
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="母题标题不能为空")
    with Session(engine) as s:
        m = Motif(tenant_id=user.tenant_id, title=title[:80], note=body.note.strip()[:300],
                  payload={"source_kind": body.source_kind.strip()[:30],
                           "source_id": body.source_id or 0})
        s.add(m)
        s.commit()
        s.refresh(m)
        return _motif_out(m, {k: {"n": 0, "items": []} for k in _DERIVE_KINDS})


@router.get("")
def list_motifs(status: str = "active", limit: int = 50,
                user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        q = select(Motif)
        if status:
            q = q.where(Motif.status == status)  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(Motif.id.desc())  # type: ignore[attr-defined]
                      .limit(max(1, min(limit, 200)))).all()
        items = []
        for m in rows:
            state = _line_state(s, m.id, user.tenant_id)
            items.append(_motif_out(m, {k: {"n": state[k]["n"]} for k in _DERIVE_KINDS}))
        return {"items": items, "total": len(items)}


@router.get("/{motif_id}")
def motif_detail(motif_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        m = s.get(Motif, motif_id)
        if m is None:
            raise HTTPException(status_code=404, detail="motif not found")
        return _motif_out(m, _line_state(s, motif_id, user.tenant_id))


class MotifUpdate(BaseModel):
    title: str | None = None
    note: str | None = None
    status: str | None = None


@router.put("/{motif_id}")
def update_motif(motif_id: int, body: MotifUpdate, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        m = s.get(Motif, motif_id)
        if m is None:
            raise HTTPException(status_code=404, detail="motif not found")
        if body.title is not None and body.title.strip():
            m.title = body.title.strip()[:80]
        if body.note is not None:
            m.note = body.note.strip()[:300]
        if body.status is not None:
            if body.status not in ("active", "archived"):
                raise HTTPException(status_code=400, detail="status 只能是 active|archived")
            m.status = body.status
        s.add(m)
        s.commit()
        s.refresh(m)
        return _motif_out(m, _line_state(s, motif_id, user.tenant_id))


@router.delete("/{motif_id}")
def delete_motif(motif_id: int, user: User = Depends(require_user)) -> dict:
    """删除母题（无任何派生才可删——派生过的走归档）。"""
    with Session(engine) as s:
        m = s.get(Motif, motif_id)
        if m is None:
            raise HTTPException(status_code=404, detail="motif not found")
        state = _line_state(s, motif_id, user.tenant_id)
        if any(state[k]["n"] for k in _DERIVE_KINDS):
            raise HTTPException(status_code=400, detail="已有派生成品，改为归档不可删除")
        s.delete(m)
        s.commit()
    return {"ok": True}


class DeriveIn(BaseModel):
    kind: str  # script|article|note|moments


@router.post("/{motif_id}/derive")
async def derive(motif_id: int, body: DeriveIn, user: User = Depends(require_user)) -> dict:
    """派生到一条线：script/note=建该线选题（走既有备稿/生成流）；moments=生成朋友圈文案；
    article=从该母题的终稿口播稿派生长文（既有 script→article 链）。"""
    if body.kind not in _DERIVE_KINDS:
        raise HTTPException(status_code=400, detail=f"未知派生线「{body.kind}」")
    with Session(engine) as s:
        m = s.get(Motif, motif_id)
        if m is None:
            raise HTTPException(status_code=404, detail="motif not found")

        if body.kind == "script":
            idea = Idea(tenant_id=user.tenant_id, channel_id=channel_id_of(s, "persona"),
                        batch_date=datetime.now().strftime("%Y-%m-%d"), source="motif",
                        title_hint=m.title[:60], motif_id=motif_id,
                        payload={"angle_note": m.note[:200]})
            s.add(idea)
            s.commit()
            s.refresh(idea)
            return {"kind": "script", "idea_id": idea.id,
                    "next": "到「人设号 · 选题」对这张卡备稿"}

        if body.kind == "note":
            src = (m.payload or {}).get("source_id") or 0
            idea = Idea(tenant_id=user.tenant_id, channel_id=channel_id_of(s, "xhs"),
                        batch_date=datetime.now().strftime("%Y-%m-%d"), source="motif",
                        title_hint=m.title[:40], motif_id=motif_id, angle=m.note.strip()[:120],
                        payload={"material_ids": [int(src)] if src else []})
            s.add(idea)
            s.commit()
            s.refresh(idea)
            return {"kind": "note", "idea_id": idea.id,
                    "next": "到「小红书」点「发起并生成」（三元组可手填或走角度会）"}

        if body.kind == "article":
            script_ideas = s.exec(select(Idea).where(  # type: ignore[attr-defined]
                Idea.motif_id == motif_id,  # type: ignore[attr-defined]
                Idea.status != "discarded")).all()  # type: ignore[attr-defined]
            persona_ch = channel_id_of(s, "persona")
            script_ids = [i.content_id for i in script_ideas
                          if i.content_id and i.channel_id == persona_ch]
            script = None
            if script_ids:
                script = s.exec(select(Content).where(  # type: ignore[attr-defined]
                    Content.id.in_(script_ids),  # type: ignore[attr-defined]
                    Content.kind == "video_script",  # type: ignore[attr-defined]
                    Content.body != "")).first()  # type: ignore[attr-defined]
            if script is None:
                raise HTTPException(status_code=400,
                                    detail="该母题还没有可用的口播稿——先备稿生成，或绑 KB 素材走改编产线")
            dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.parent_content_id == script.id,  # type: ignore[attr-defined]
                Content.status == "drafting")).first()  # type: ignore[attr-defined]
            if dup is not None:
                raise HTTPException(status_code=409,
                                    detail=f"该口播稿正在生成长文（文章 #{dup.id}）")
            from ..channels.engine import new_article_content

            article = new_article_content(s, tenant_id=user.tenant_id, state="pending",
                                          parent_content_id=script.id)
            s.commit()
            article_id = article.id
        else:  # moments
            content = Content(tenant_id=user.tenant_id,
                              channel_id=channel_id_of(s, "persona"), kind="moments",
                              title=f"朋友圈｜{m.title[:30]}", body="", status="drafting",
                              payload={"motif_id": motif_id, "moments_state": "drafting"})
            s.add(content)
            s.commit()
            article_id = content.id  # 复用变量：提交任务
        job = await _submit(s, body.kind, article_id)
        return {"kind": body.kind, "content_id": article_id, "job_id": job.id}


async def _submit(s: Session, kind: str, content_id: int):
    """派生后的异步任务提交（article_generate / moments_generate）。"""
    if kind == "article":
        return await runner.submit("article_generate", {"article_id": content_id, "style": ""})
    return await runner.submit("moments_generate", {"content_id": content_id})


@router.post("/moments/{content_id}/published")
def moments_published(content_id: int, user: User = Depends(require_user)) -> dict:
    """朋友圈手动标记已发（复制发布后点一下；v1 不做指标回流）。"""
    from ..channels.moments import mark_moments_published

    with Session(engine) as s:
        mark_moments_published(s, content_id)
        s.commit()
    return {"ok": True}
