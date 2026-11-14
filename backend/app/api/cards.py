"""选题工场 API（人设号靶子卡，PRD R1.4）：列表/备稿/不做/弃卡池/手动投喂。"""
import hashlib

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..cards import service as card_service
from ..db import engine
from ..jobs.runner import runner
from ..models import TopicCard, User
from ._listing import paged_list_response

router = APIRouter(prefix="/api/cards")


def _dict(c: TopicCard) -> dict:
    return c.model_dump()


@router.get("")
def list_cards(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(TopicCard).order_by(TopicCard.id.desc())  # type: ignore[arg-type]
        if status:
            q = q.where(TopicCard.status == status)  # type: ignore[attr-defined]
        cq = select(TopicCard.status, func.count(TopicCard.id)).group_by(TopicCard.status)  # type: ignore[arg-type]
        return paged_list_response(s, q, offset=offset, limit=limit, counts_q=cq)


@router.get("/jail")
def list_jail(limit: int = 50) -> dict:
    with Session(engine) as s:
        q = select(TopicCard).where(  # type: ignore[attr-defined]
            TopicCard.status == "jail").order_by(TopicCard.id.desc())  # type: ignore[attr-defined]
        rows = s.exec(q).all()  # type: ignore[call-overload]
    return {"items": [_dict(c) for c in rows[:max(1, min(limit, 100))]]}


class ComposeIn(BaseModel):
    hook: str = ""   # 可改写（空=用卡上钩子）
    spine: str = ""  # 可改写（空=用卡上主线）
    angle: str = ""  # 主打打法（六打法之一，必选）


@router.post("/{card_id}/compose", status_code=202)
async def compose(card_id: int, body: ComposeIn,
                  user: User = Depends(require_user)) -> dict:
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
        c = s.get(TopicCard, card_id)
        if c is None:
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
        c = s.get(TopicCard, card_id)
        if c is None:
            raise HTTPException(status_code=404, detail="card not found")
        if c.status != "jail":
            raise HTTPException(status_code=400, detail=f"状态 {c.status} 不在弃卡池")
        c.status = "draft"
        s.add(c)
        s.commit()
        return {"id": c.id, "status": c.status}


class SeedIn(BaseModel):
    text: str
    url: str = ""


@router.post("/seed", status_code=202)
async def seed(body: SeedIn, user: User = Depends(require_user)) -> dict:
    """手动投喂：一句话（可带链接）→ 池子卡立即走主线→钩子→法官（card_seed 任务）。"""
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="投喂内容不能为空")
    dedup = hashlib.md5(text.encode()).hexdigest()[:12]
    job = await runner.submit("card_seed", {"text": text[:300], "url": body.url.strip()[:200]},
                              dedup_key=f"1:cardseed:{dedup}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}
