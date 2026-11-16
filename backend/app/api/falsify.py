"""实测打假 API（PRD R1.4③ / R2.3）：清单 CRUD / 状态推进 / 结论回填 / 成稿。

P2 起是素材库 test_record 子集的领域门面（/api/falsify 契约不变，HotTargets 不改）；
存储映射见 falsify/service.py 模块注释。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..falsify import service as falsify_service
from ..models import Material, User

router = APIRouter(prefix="/api/falsify")

_ORIGIN = "test_record"


@router.get("")
def list_items(status: str = "", limit: int = 100, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(Material).where(Material.origin_type == _ORIGIN)  # type: ignore[attr-defined]
        cq = select(Material.stage, func.count(Material.id)).group_by(Material.stage)  # type: ignore[arg-type]
        if status:
            q = q.where(Material.stage == status)  # type: ignore[attr-defined]
        total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
        rows = s.exec(q.order_by(Material.id.desc())  # type: ignore[attr-defined]
                      .offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
        counts = {st: n for st, n in s.exec(cq).all()}
        return {"items": [falsify_service._item_out(r) for r in rows],
                "total": total, "counts": counts}


class FalsifyIn(BaseModel):
    claim: str        # 要打的主张一句话
    target: str = ""  # 实测对象
    evidence_note: str = ""  # 线索/链接


@router.post("", status_code=201)
def add_item(body: FalsifyIn, user: User = Depends(require_user)) -> dict:
    claim = body.claim.strip()
    if not claim:
        raise HTTPException(status_code=400, detail="主张不能为空")
    from ..materials.service import upsert_material

    with Session(engine) as s:
        m, _created = upsert_material(
            s, tenant_id=user.tenant_id, origin_type=_ORIGIN,
            title=claim[:60],
            note=body.evidence_note.strip()[:300],
            quality={"target": body.target.strip()[:40], "auto_source": False},
            stage="pending", status="ready")
        return falsify_service._item_out(m)


class FalsifyUpdate(BaseModel):
    status: str | None = None   # pending/testing/tested/dismissed（scripted 由成稿动作写入）
    result_note: str | None = None  # 实测结论回填（自由文本，成稿论据来源）
    evidence_note: str | None = None  # 线索/链接补记


@router.put("/{item_id}")
def update_item(item_id: int, body: FalsifyUpdate,
                user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        m = s.get(Material, item_id)
        if m is None or m.origin_type != _ORIGIN:
            raise HTTPException(status_code=404, detail="falsify item not found")
        if body.evidence_note is not None:
            m.note = body.evidence_note.strip()[:300]
            s.add(m)
        if body.result_note is not None:
            m.body = body.result_note.strip()[:2000]
            s.add(m)
        if body.status is not None:
            if body.status not in falsify_service.FALSIFY_STATUS:
                raise HTTPException(status_code=400,
                                    detail=f"未知状态：{body.status}（可选：{'/'.join(falsify_service.FALSIFY_STATUS)}）")
            if body.status == "scripted":
                raise HTTPException(status_code=400,
                                    detail="「已成稿」由成稿动作写入（走 /compose），不可手改")
            if (m.stage or "") == "scripted" and body.status != "dismissed":
                raise HTTPException(status_code=400, detail="已成稿条目不可回退状态（可不做=dismissed）")
            m.stage = body.status
            s.add(m)
        s.commit()
        s.refresh(m)
        return falsify_service._item_out(m)


@router.post("/{item_id}/compose", status_code=202)
async def compose(item_id: int, user: User = Depends(require_user)) -> dict:
    try:
        return await falsify_service.compose_falsify(item_id=item_id, user=user)
    except KeyError:
        raise HTTPException(status_code=404, detail="falsify item not found") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None


@router.post("/{item_id}/dismiss")
def dismiss(item_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        m = s.get(Material, item_id)
        if m is None or m.origin_type != _ORIGIN:
            raise HTTPException(status_code=404, detail="falsify item not found")
        m.stage = "dismissed"
        s.add(m)
        s.commit()
        return {"id": m.id, "status": m.stage}
