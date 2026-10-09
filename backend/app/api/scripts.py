"""脚本工场 API。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..credits import job_points, require_points
from ..db import engine
from ..jobs.runner import runner
from ..models import AvatarVideo, Article, Script, ScriptTemplate, Topic, User
from ..scripts import service as script_service

router = APIRouter(prefix="/api/scripts")


class GenerateIn(BaseModel):
    topic_id: int
    use_style: bool = True  # 贴合我的风格（R9.4）
    length: str = "short"  # short=240~320字（50~70s 黄金档）| long=600~900字（PRD R2.1 两档，2026-10-09 定档）


class FinalizeIn(BaseModel):
    version_index: int = 0


class PolishIn(BaseModel):
    use_style: bool = True


class TemplateIn(BaseModel):
    name: str
    structure: list[dict]


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("script_generate"), "脚本生成")
    with Session(engine) as s:
        topic = s.get(Topic, body.topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        dup = s.exec(select(Script).where(
            Script.topic_id == body.topic_id,  # type: ignore[attr-defined]
            Script.status.in_(["generating", "polishing"]),  # type: ignore[attr-defined]
        )).first()  # type: ignore[attr-defined]
        if dup is not None:  # 实体级去重：同选题在途不重复生成
            raise HTTPException(status_code=409,
                                detail=f"该选题正在生成脚本（脚本 #{dup.id}），请等完成或删除后再试")
        script = Script(topic_id=body.topic_id, status="generating")
        use_style = body.use_style
        s.add(script)
        s.commit()
        s.refresh(script)
        script_id = script.id
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "length": body.length},
                              points=job_points("script_generate"))
    return {"script_id": script_id, "job_id": job.id}


@router.get("")
def list_scripts(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(Script).order_by(Script.id.desc())  # type: ignore[arg-type]
        if status:
            q = q.where(Script.status == status)
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()  # type: ignore[attr-defined]
        from sqlmodel import func
        counts = {st: n for st, n in s.exec(
            select(Script.status, func.count(Script.id)).group_by(Script.status)  # type: ignore[arg-type]
        ).all()}
        return {"items": [r.model_dump() for r in rows], "total": total, "counts": counts}


@router.get("/templates")
def list_templates() -> dict:
    with Session(engine) as s:
        rows = s.exec(select(ScriptTemplate).order_by(ScriptTemplate.id)).all()
        return {"items": [r.model_dump() for r in rows]}


@router.post("/templates", status_code=201)
def create_template(body: TemplateIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="模板名不能为空")
    with Session(engine) as s:
        tpl = ScriptTemplate(name=body.name.strip(), structure=body.structure)
        s.add(tpl)
        s.commit()
        s.refresh(tpl)
        return tpl.model_dump()


@router.get("/{script_id}")
def get_script(script_id: int) -> dict:
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail="script not found")
        return script.model_dump()


@router.post("/{script_id}/polish", status_code=202)
async def polish(script_id: int, body: PolishIn | None = None,
                 user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("script_polish"), "脚本打磨")
    use_style = body.use_style if body else True
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail="script not found")
        if not script.versions:
            raise HTTPException(status_code=400, detail="脚本还没有版本，先生成")
        script.status = "polishing"
        s.add(script)
        s.commit()
    job = await runner.submit("script_polish", {"script_id": script_id, "use_style": use_style},
                              points=job_points("script_polish"))
    return {"job_id": job.id}


@router.post("/{script_id}/finalize", status_code=202)
async def finalize(script_id: int, body: FinalizeIn | None = None,
                   user: User = Depends(require_user)) -> dict:
    require_points(user.tenant_id, job_points("script_finalize"), "脚本定稿")
    index = body.version_index if body else 0
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise HTTPException(status_code=404, detail="script not found")
        if not script.versions:
            raise HTTPException(status_code=400, detail="脚本还没有版本，先生成")
    job = await runner.submit("script_finalize",
                              {"script_id": script_id, "version_index": index},
                              dedup_key=f"finalize:{script_id}",
                              points=job_points("script_finalize"))
    return {"job_id": job.id}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int) -> dict:
    with Session(engine) as s:
        tpl = s.get(ScriptTemplate, template_id)
        if tpl is None:
            raise HTTPException(404, detail="template not found")
        remaining = s.exec(select(ScriptTemplate)).all()
        if len(remaining) <= 1:
            raise HTTPException(400, detail="至少保留一个结构模板")
        s.delete(tpl)
        s.commit()
    return {"ok": True}


@router.delete("/{script_id}")
def delete_script(script_id: int) -> dict:
    """删除脚本。引用检查：有关联视频项目或文章时先解绑（不级联删除）。"""
    with Session(engine) as s:
        script = s.get(Script, script_id)
        if script is None:
            raise HTTPException(404, detail="script not found")
        # 解绑引用（置空而非删除，保护下游产物）
        for vp in s.exec(select(AvatarVideo).where(AvatarVideo.script_id == script_id)).all():
            vp.script_id = None
            s.add(vp)
        for art in s.exec(select(Article).where(Article.script_id == script_id)).all():
            art.script_id = None
            s.add(art)
        # 若选题因该脚本被标为 produced，回退为 approved（可再生成）
        if script.topic_id:
            topic = s.get(Topic, script.topic_id)
            if topic is not None and topic.status == "produced":
                has_other = s.exec(select(Script).where(
                    Script.topic_id == script.topic_id,
                    Script.id != script_id)).first()
                if not has_other:
                    topic.status = "approved"
                    s.add(topic)
        s.delete(script)
        s.commit()
    return {"ok": True}
