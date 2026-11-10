"""选题中心 API。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import Article, User, Job, ResearchReport, Script, Topic
from ..topics import service as topic_service
from ._listing import paged_list_response

router = APIRouter(prefix="/api/topics")


class IdeaIn(BaseModel):
    text: str
    line: str = "persona"  # 选题线：persona | leadgen（三库独立，2026-11-02）


class QuickIn(BaseModel):
    title: str
    audience: str = "both"  # boss | fde | both
    line: str = "persona"  # 快速记选题默认人设库，可手动选获客


def _validate_line(line: str) -> str:
    if line not in ("persona", "leadgen"):
        raise HTTPException(400, detail="line 必须是 persona / leadgen")
    return line


@router.post("/trending", status_code=202)
async def trending_topics(user: User = Depends(require_user)) -> dict:
    """今日热点选题提炼（R1.4）：站内对标议题+联网今日热点 × 租户画像 → 3 个切角落选题库。

    轻活走队列（LLM 一次调用），产出 source_type=trending 的 draft 选题，前端轮询展示。"""
    job = await runner.submit("trending_topics", {"tenant_id": user.tenant_id},
                              dedup_key=f"{user.tenant_id}:trending_topics:daily")
    return {"job_id": job.id}


@router.post("/quick", status_code=201)
def quick_topic(body: QuickIn) -> dict:
    """快速记选题（R1.1b）：不触发 LLM，直接入库待审。默认进人设库，可指定获客库。"""
    title = body.title.strip()[:60]
    if not title:
        raise HTTPException(400, detail="标题不能为空")
    if body.audience not in ("boss", "fde", "both"):
        raise HTTPException(400, detail="audience 必须是 boss/fde/both")
    line = _validate_line(body.line)
    with Session(engine) as s:
        topic = Topic(title=title, source_type="manual", audience=body.audience, line=line)
        s.add(topic)
        s.flush()  # flush 拿 id（同 benchmarks：消灭 commit+refresh 的偶发失败面）
        topic_service.mark_similar(title, topic.id, s, line=line)
        s.commit()
        s.refresh(topic)
        return topic.model_dump()


def _latest_report(s: Session, topic_id: int) -> str:
    report = s.exec(
        select(ResearchReport)
        .where(ResearchReport.topic_id == topic_id)
        .order_by(ResearchReport.id.desc())  # type: ignore[union-attr]
    ).first()
    return report.content if report else ""


def _topic_out(s: Session, topic: Topic, with_report: bool = False) -> dict:
    data = topic.model_dump()
    if with_report:
        data["research_report"] = _latest_report(s, topic.id)
    # 生命周期（下游视图）：该选题的脚本与文章（列表与详情都带）
    from ..models import Article as _A, Script as _S
    scripts = s.exec(select(_S).where(_S.topic_id == topic.id)).all()
    data["scripts"] = [{"id": x.id, "status": x.status} for x in scripts]
    data["articles"] = [{"id": x.id, "status": x.status}
                        for x in s.exec(select(_A).where(_A.topic_id == topic.id)).all()]
    return data


@router.post("/ideas", status_code=202)
async def create_idea(body: IdeaIn, user: User = Depends(require_user)) -> dict:
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="idea 不能为空")
    line = _validate_line(body.line)
    with Session(engine) as s:
        topic = Topic(title=text[:60], source_type="idea", source_ref=text, line=line)
        s.add(topic)
        s.commit()
        s.refresh(topic)
        topic_id = topic.id
    active = s.exec(select(Job).where(
        Job.type == "idea_research",  # type: ignore[attr-defined]
        Job.status.in_(["queued", "running"]),  # type: ignore[attr-defined]
    )).all()  # type: ignore[attr-defined]
    for j in active:  # 实体级去重：相同原文正在深研不重复提交
        if (j.payload or {}).get("text") == text:
            raise HTTPException(status_code=409, detail="相同选题正在深研中，请等完成")
    job = await runner.submit("idea_research",
                              {"topic_id": topic_id, "text": text})
    return {"topic_id": topic_id, "job_id": job.id}



@router.get("")
def list_topics(status: Optional[str] = None, source: Optional[str] = None,
                audience: Optional[str] = None, min_score: Optional[float] = None,
                line: Optional[str] = None,
                order: str = "new", limit: int = 50, offset: int = 0) -> dict:
    """三库独立（2026-11-02）：line 必传 persona/leadgen，两库互不可见。"""
    with Session(engine) as s:
        if line not in ("persona", "leadgen"):
            raise HTTPException(400, detail="line 必传 persona / leadgen")
        # order=new 按入库时间倒序；score 按分数从高到低（无分排最后，仍按新→旧）
        q = select(Topic).where(Topic.line == line)  # type: ignore[attr-defined]
        if order == "score":
            q = q.order_by(Topic.score.desc().nulls_last(), Topic.id.desc())  # type: ignore[attr-defined]
        else:
            q = q.order_by(Topic.id.desc())  # type: ignore[arg-type]
        if status:
            q = q.where(Topic.status == status)
        if source:
            q = q.where(Topic.source_type == source)
        if audience:
            q = q.where(Topic.audience == audience)
        if min_score is not None:
            q = q.where(Topic.score >= min_score)  # type: ignore[attr-defined]
        cq = select(Topic.status, func.count(Topic.id)).group_by(Topic.status).where(  # type: ignore[arg-type]
            Topic.line == line)  # type: ignore[attr-defined]
        return paged_list_response(s, q, offset=offset, limit=limit, counts_q=cq,
                                   serialize=lambda t: _topic_out(s, t))


@router.get("/{topic_id}")
def get_topic(topic_id: int) -> dict:
    with Session(engine) as s:
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        return _topic_out(s, topic, with_report=True)


@router.post("/{topic_id}/approve")
def approve_topic(topic_id: int) -> dict:
    return _set_status(topic_id, "approved")


@router.post("/{topic_id}/set-type")
def set_topic_type(topic_id: int, body: dict,
                   user: User = Depends(require_user)) -> dict:
    """手动修正选题的两类号归属（获客/人设），evidence.account_type 即口径；
    三库独立后同步搬库（line = account_type）。"""
    t = str((body or {}).get("account_type") or "").strip()
    if t not in ("persona", "leadgen"):
        raise HTTPException(status_code=400, detail="account_type 必须是 persona / leadgen")
    with Session(engine) as s:
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        ev = dict(topic.evidence or {})
        ev["account_type"] = t
        ev["account_type_manual"] = True
        topic.evidence = ev
        topic.line = t
        s.add(topic)
        s.commit()
    return {"id": topic_id, "account_type": t}


@router.post("/{topic_id}/reject")
def reject_topic(topic_id: int) -> dict:
    return _set_status(topic_id, "rejected")


def _set_status(topic_id: int, status: str) -> dict:
    with Session(engine) as s:
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        topic.status = status
        s.add(topic)
        s.commit()
        s.refresh(topic)
        return _topic_out(s, topic)




def _delete_topic_cascade(s: Session, topic: Topic) -> None:
    """删选题连带（delete_topic 与 cleanup_rejected 单点共用）：
    研究报告一并删；文章与脚本保留、仅解绑（FK 约束要求先解绑才能删选题）。"""
    for rep in s.exec(select(ResearchReport).where(ResearchReport.topic_id == topic.id)).all():
        s.delete(rep)
    for art in s.exec(select(Article).where(Article.topic_id == topic.id)).all():
        art.topic_id = None
        s.add(art)
    for sc in s.exec(select(Script).where(Script.topic_id == topic.id)).all():
        sc.topic_id = None
        s.add(sc)
    s.delete(topic)


@router.delete("/{topic_id}")
def delete_topic(topic_id: int) -> dict:
    """删除选题（含研究报告），不可恢复。文章与脚本保留，仅解绑。"""
    with Session(engine) as s:
        topic = s.get(Topic, topic_id)
        if topic is None:
            raise HTTPException(404, detail="topic not found")
        _delete_topic_cascade(s, topic)
        s.commit()
    return {"ok": True}


@router.post("/cleanup-rejected")
def cleanup_rejected(body: dict) -> dict:
    """清理 N 天前（默认 30）的已否决选题，防列表被自动拒堆积淹没。"""
    days = int((body or {}).get("days") or 30)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with Session(engine) as s:
        rows = s.exec(
            select(Topic).where(
                Topic.status == "rejected",  # type: ignore[attr-defined]
                Topic.updated_at < cutoff,  # type: ignore[attr-defined]
            )  # type: ignore[attr-defined]
        ).all()
        for topic in rows:
            _delete_topic_cascade(s, topic)
        s.commit()
        return {"deleted": len(rows)}


