"""选题工场 API（人设号靶子卡，PRD R2.2）：列表/备稿/不做/弃卡池。

P3 内容域：数据在 ideas 表（channel=persona）；本路由是旧契约门面——
输出字段与旧 topic_cards 完全一致（pool/script_id 经 payload/content_id 映射），
HotTargets 前端零改动。P5 旧前端退役时本门面随迁。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels.engine import channel_id_of
from ..db import engine
from ..models import Idea, User
from ._listing import paged_list_response

router = APIRouter(prefix="/api/cards")


def _dict(c: Idea) -> dict:
    """Idea 行 → 旧靶子卡形状。"""
    p = c.payload or {}
    return {
        "id": c.id, "batch_date": c.batch_date, "source": c.source,
        "pool": p.get("pool") or {}, "spine": c.spine or "", "hook": c.hook or "",
        "spice": c.spice or "", "takeaways": c.takeaways or [],
        "judge": c.judge or {}, "gates": c.gates or [], "status": c.status,
        "superseded_by": c.superseded_by, "script_id": c.content_id,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }


def _persona_q(s: Session):
    ch = channel_id_of(s, "persona")
    return select(Idea).where(Idea.channel_id == ch)  # type: ignore[attr-defined]


@router.get("")
def list_cards(status: str = "", limit: int = 50, offset: int = 0,
               user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        q = _persona_q(s).order_by(Idea.id.desc())  # type: ignore[attr-defined]
        if status:
            q = q.where(Idea.status == status)  # type: ignore[attr-defined]
        cq = select(Idea.status, func.count(Idea.id)).group_by(Idea.status)  # type: ignore[arg-type]
        cq = cq.where(Idea.channel_id == channel_id_of(s, "persona"))  # type: ignore[attr-defined]
        return paged_list_response(s, q, offset=offset, limit=limit, counts_q=cq,
                                   serialize=_dict)


@router.get("/jail")
def list_jail(limit: int = 50, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        q = _persona_q(s).where(Idea.status == "jail")  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(Idea.id.desc())).all()  # type: ignore[attr-defined]
    return {"items": [_dict(c) for c in rows[:max(1, min(limit, 100))]]}


class ComposeIn(BaseModel):
    hook: str = ""   # 可改写（空=用卡上钩子）
    spine: str = ""  # 可改写（空=用卡上主线）
    angle: str = ""  # 主打打法（六打法之一，必选）


@router.post("/{card_id}/compose", status_code=202)
async def compose(card_id: int, body: ComposeIn,
                  user: User = Depends(require_user)) -> dict:
    from ..cards import service as card_service

    try:
        return await card_service.compose_card(card_id=card_id, hook=body.hook,
                                               spine=body.spine, angle=body.angle,
                                               user=user)
    except KeyError:
        raise HTTPException(status_code=404, detail="card not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/{card_id}/discard")
def discard(card_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        c = s.get(Idea, card_id)
        if c is None or c.channel_id != channel_id_of(s, "persona"):
            raise HTTPException(status_code=404, detail="card not found")
        if c.status not in ("draft", "jail"):
            raise HTTPException(status_code=400,
                                detail=f"状态 {c.status} 不可不做（仅 draft/jail）")
        c.status = "discarded"
        s.add(c)
        s.commit()
        return {"id": c.id, "status": c.status}


@router.post("/{card_id}/revive")
def revive(card_id: int, user: User = Depends(require_user)) -> dict:
    """弃卡池捞回（翻案）：jail → draft。用户的判断盖过法官。"""
    with Session(engine) as s:
        c = s.get(Idea, card_id)
        if c is None or c.channel_id != channel_id_of(s, "persona"):
            raise HTTPException(status_code=404, detail="card not found")
        if c.status != "jail":
            raise HTTPException(status_code=400, detail=f"状态 {c.status} 不在弃卡池")
        c.status = "draft"
        s.add(c)
        s.commit()
        return {"id": c.id, "status": c.status}
