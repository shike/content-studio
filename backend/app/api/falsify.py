"""实测打假 API（PRD R1.4③）：清单 CRUD / 状态推进 / 结论回填 / 成稿。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..falsify import service as falsify_service
from ..models import FalsifyItem, User
from ._listing import paged_list_response

router = APIRouter(prefix="/api/falsify")


def _dict(f: FalsifyItem) -> dict:
    return f.model_dump()


@router.get("")
def list_items(status: str = "", limit: int = 100, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(FalsifyItem).order_by(FalsifyItem.id.desc())  # type: ignore[arg-type]
        if status:
            q = q.where(FalsifyItem.status == status)  # type: ignore[attr-defined]
        cq = select(FalsifyItem.status, func.count(FalsifyItem.id)).group_by(FalsifyItem.status)  # type: ignore[arg-type]
        return paged_list_response(s, q, offset=offset, limit=limit, counts_q=cq)


class FalsifyIn(BaseModel):
    claim: str        # 要打的主张一句话
    target: str = ""  # 实测对象
    evidence_note: str = ""  # 线索/链接


@router.post("", status_code=201)
def add_item(body: FalsifyIn, user: User = Depends(require_user)) -> dict:
    claim = body.claim.strip()
    if not claim:
        raise HTTPException(status_code=400, detail="主张不能为空")
    f = FalsifyItem(tenant_id=user.tenant_id, claim=claim[:60],
                    target=body.target.strip()[:40],
                    evidence_note=body.evidence_note.strip()[:300],
                    status="pending", auto_source=False)
    with Session(engine) as s:
        s.add(f)
        s.commit()
        s.refresh(f)
        return _dict(f)


class FalsifyUpdate(BaseModel):
    status: str | None = None   # pending/testing/tested/dismissed（scripted 由成稿动作写入）
    result_note: str | None = None  # 实测结论回填（自由文本，成稿论据来源）
    evidence_note: str | None = None  # 线索/链接补记


@router.put("/{item_id}")
def update_item(item_id: int, body: FalsifyUpdate,
                user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        f = s.get(FalsifyItem, item_id)
        if f is None:
            raise HTTPException(status_code=404, detail="falsify item not found")
        if body.evidence_note is not None:
            f.evidence_note = body.evidence_note.strip()[:300]
            s.add(f)
        if body.result_note is not None:
            f.result_note = body.result_note.strip()[:2000]
            s.add(f)
        if body.status is not None:
            if body.status not in falsify_service.FALSIFY_STATUS:
                raise HTTPException(status_code=400,
                                    detail=f"未知状态：{body.status}（可选：{'/'.join(falsify_service.FALSIFY_STATUS)}）")
            if body.status == "scripted":
                raise HTTPException(status_code=400,
                                    detail="「已成稿」由成稿动作写入（走 /compose），不可手改")
            if f.status == "scripted" and body.status != "dismissed":
                raise HTTPException(status_code=400, detail="已成稿条目不可回退状态（可不做=dismissed）")
            f.status = body.status
            s.add(f)
        s.commit()
        s.refresh(f)
        return _dict(f)


@router.post("/{item_id}/compose", status_code=202)
async def compose(item_id: int, user: User = Depends(require_user)) -> dict:
    try:
        return await falsify_service.compose_falsify(item_id=item_id, user=user)
    except KeyError:
        raise HTTPException(status_code=404, detail="falsify item not found") from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/{item_id}/dismiss")
def dismiss(item_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        f = s.get(FalsifyItem, item_id)
        if f is None:
            raise HTTPException(status_code=404, detail="falsify item not found")
        f.status = "dismissed"
        s.add(f)
        s.commit()
        return {"id": f.id, "status": f.status}
