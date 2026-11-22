"""自我风格研究 API（R9）。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import runner
from ..auth import require_user
from ..models import Account, ChannelStyle, SelfVideo, User

router = APIRouter(prefix="/api/style")


def _self_account(s: Session) -> Account | None:
    return s.exec(select(Account)).first()  # type: ignore[attr-defined]


def _profile_dict(p: ChannelStyle) -> dict:
    return {"version": p.version, "digest": p.digest, "traits": p.traits,
            "exemplars": p.exemplars, "based_on": p.based_on, "created_at": p.created_at}


def _latest_profile(s):
    """最新画像（P3 起读 channel_styles，渠道=persona；形状与旧 style_profiles 一致）。"""
    from ..channels.engine import channel_id_of

    ch = channel_id_of(s, "persona")
    return s.exec(select(ChannelStyle).where(  # type: ignore[attr-defined]
        ChannelStyle.channel_id == ch)  # type: ignore[attr-defined]
        .order_by(ChannelStyle.version.desc())).first()  # type: ignore[attr-defined]


class ResolveIn(BaseModel):
    action: str  # approve | ignore


@router.post("/videos/{video_id}/resolve", status_code=202)
async def resolve_video(video_id: int, body: ResolveIn,
                        user: User = Depends(require_user)) -> dict:
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
                                  dedup_key=f"{user.tenant_id}:selfanalyze:{video_id}")
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
        # 全库统计口径（此前在最新 100 条窗口上统计，self 视频>100 后 total 失真）
        from sqlmodel import func as _f
        total = s.exec(select(_f.count(SelfVideo.id))).one() or 0
        analyzed = s.exec(select(_f.count(SelfVideo.id)).where(
            SelfVideo.analyzed_at is not None)).one() or 0  # type: ignore[attr-defined]
        profile = _latest_profile(s)

        account = ({
            "id": acc.id, "name": acc.name, "enabled": bool(acc.enabled),
            "platform": acc.platform, "url": acc.profile_url,
            "has_cookie": bool((acc.auth or {}).get("creator_cookie")),
            "created_at": acc.created_at, "updated_at": acc.updated_at,
        } if acc else None)
        return {
            "account": account,
            "profile": _profile_dict(profile) if profile else None,
            "stats": {"total": total, "analyzed": analyzed},
        }


@router.get("/viral-samples")
def viral_samples(user: User = Depends(require_user)) -> dict:
    """爆款范本库（R2-2 2026-11-22）：self_scan 收割的 ≥收割线自有视频（结构化）。

    script_generate 热点模式以这些范本做 few-shot——今天跑出来的爆款就是明天的生成范本。"""
    from ..analytics.judgement import viral_play
    from ..selfstyle.service import viral_samples_items

    return {"items": viral_samples_items(), "line": viral_play()}
