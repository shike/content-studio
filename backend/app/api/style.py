"""自我风格研究 API（R9）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import runner
from ..models import SelfVideo, StyleProfile, WatchAccount

router = APIRouter(prefix="/api/style")


def _self_account(s: Session) -> WatchAccount | None:
    return s.exec(
        select(WatchAccount).where(WatchAccount.kind == "self")  # type: ignore[attr-defined]
    ).first()


def _profile_dict(p: StyleProfile) -> dict:
    return {"version": p.version, "digest": p.digest, "traits": p.traits,
            "exemplars": p.exemplars, "based_on": p.based_on, "created_at": p.created_at}


class ResolveIn(BaseModel):
    action: str  # approve | ignore


@router.post("/videos/{video_id}/resolve", status_code=202)
async def resolve_video(video_id: int, body: ResolveIn) -> dict:
    """我的视频定夺：批准（排队风格拆解）或忽略。"""
    if body.action not in ("approve", "ignore"):
        raise HTTPException(status_code=400, detail="action 必须是 approve / ignore")
    with Session(engine) as s:
        v = s.get(SelfVideo, video_id)
        if v is None:
            raise HTTPException(status_code=404, detail="video not found")
        v.scan_status = "approved" if body.action == "approve" else "ignored"
        s.add(v)
        s.commit()
    if body.action == "approve":
        job = await runner.submit("self_analyze", {"video_id": video_id},
                                  dedup_key=f"selfanalyze:{video_id}")
        return {"status": "approved", "job_id": job.id}
    return {"status": "ignored"}


@router.get("/videos")
def list_videos(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    """风格视频列表（分页 + 标注筛选：done=已标注 / todo=未标注）。"""
    with Session(engine) as s:
        q = select(SelfVideo).order_by(SelfVideo.id.desc())  # type: ignore[attr-defined]
        if status == "done":
            q = q.where(SelfVideo.style_analysis.isnot(None))  # type: ignore[attr-defined]
        elif status == "todo":
            q = q.where(SelfVideo.style_analysis.is_(None))  # type: ignore[attr-defined]
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()  # type: ignore[attr-defined]
        items = []
        for v in rows:
            d = v.model_dump()
            d["analyzed"] = v.analyzed_at is not None  # 前端徽章依赖的派生字段
            items.append(d)
        return {"items": items, "total": total}


@router.get("/overview")
def overview() -> dict:
    with Session(engine) as s:
        acc = _self_account(s)
        videos = s.exec(
            select(SelfVideo).order_by(SelfVideo.id.desc()).limit(100)  # type: ignore[attr-defined]
        ).all()
        profile = s.exec(
            select(StyleProfile).order_by(StyleProfile.version.desc())  # type: ignore[attr-defined]
        ).first()
        history = s.exec(
            select(StyleProfile).order_by(StyleProfile.version.desc()).limit(20)  # type: ignore[attr-defined]
        ).all()
        total = len(videos)
        analyzed = sum(1 for v in videos if v.analyzed_at is not None)
        awaiting = sum(1 for v in videos if v.scan_status == "pending" and v.analyzed_at is None)
        return {
            "account": acc.model_dump() if acc else None,
            "profile": _profile_dict(profile) if profile else None,
            "profile_history": [_profile_dict(p) for p in history],
            "videos": [{
                "id": v.id, "title": v.title, "url": v.url,
                "analyzed": v.analyzed_at is not None,
                "analyzed_at": v.analyzed_at,
                "scan_status": v.scan_status,
                "style_analysis": v.style_analysis,
            } for v in videos],
            "stats": {"total": total, "analyzed": analyzed, "pending": awaiting},
        }


@router.post("/scan", status_code=202)
async def scan() -> dict:
    with Session(engine) as s:
        if _self_account(s) is None:
            raise HTTPException(status_code=400, detail="未添加我的账号：先在同行监测页添加并勾选「这是我的账号」")
    from datetime import datetime, timezone
    job = await runner.submit("self_scan", {"auto": True},
                              dedup_key=f"selfscan:all:{datetime.now(timezone.utc):%Y%m%d}")
    return {"job_id": job.id}


@router.post("/profile/update", status_code=202)
async def update_profile() -> dict:
    with Session(engine) as s:
        has = s.exec(
            select(SelfVideo).where(SelfVideo.analyzed_at != None)  # noqa: E712
        ).first()
    if has is None:
        raise HTTPException(status_code=400, detail="还没有已分析的视频，先扫描我的账号")
    job = await runner.submit("self_profile_update", {}, dedup_key="profile:update")
    return {"job_id": job.id}


@router.get("/profile/history")
def profile_history() -> dict:
    with Session(engine) as s:
        rows = s.exec(
            select(StyleProfile).order_by(StyleProfile.version.desc())  # type: ignore[attr-defined]
        ).all()
        return {"items": [_profile_dict(p) for p in rows]}
