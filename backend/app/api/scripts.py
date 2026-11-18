"""脚本工场 API（P3 内容域门面）：数据在 contents(kind=video_script)。

输出/入参契约与旧 scripts 表完全一致（versions/teleprompter_text/status 等
经 channels.engine.script_out 整形），Scripts.tsx 前端零改动。P5 随旧前端退役。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..channels.engine import channel_key_of, new_script_content, script_out
from ..db import engine
from ..jobs.runner import runner
from ..models import Content, Publication, User
from ..channels import scripts as script_service
from ._listing import paged_list_response

router = APIRouter(prefix="/api/scripts")

_SCRIPT_STATE_BY_STATUS = {
    "generating": ("drafting", "generating"),
    "polishing": ("drafting", "polishing"),
    "generated": ("review", "generated"),
    "final": ("ready", "final"),
    "failed": ("failed", "failed"),
}


def _script_q(s: Session):
    return select(Content).where(Content.kind == "video_script")  # type: ignore[attr-defined]


def _state_filter(q, status: str):
    """旧状态串 → (canonical_status, payload state) 过滤（JSONB 表达式）。"""
    mapping = _SCRIPT_STATE_BY_STATUS.get(status)
    if not mapping:
        return q
    canonical, state = mapping
    q = q.where(Content.status == canonical)  # type: ignore[attr-defined]
    if canonical == "drafting" and status in ("generating", "polishing"):
        q = q.where(Content.payload.op('->>')('script_state') == state)
    return q


def _account_filter(q, account_type: str):
    if account_type in ("persona", "leadgen"):
        q = q.where(Content.payload.op('->>')('account_type') == account_type)
    return q


class GenerateIn(BaseModel):
    card_id: int = 0  # 靶子卡备稿（选题工场 R2.2）
    ore_id: int = 0  # 获客矿建稿（分型路由消费面；与 card_id 二选一）
    kb_id: int = 0  # 知识库文档建稿（素材库知识库 2026-11-17；三者选一）
    hook: str = ""
    spine: str = ""
    use_style: bool = True
    length: str = "short"
    angles: list[str] = []
    hotspot: str = ""  # 挂靠热点（2026-11-18 爆款基因模式：填了=热点×立场×一线视角写法）
    account_type: str = ""


class FinalizeIn(BaseModel):
    version_index: int = 0


class PolishIn(BaseModel):
    use_style: bool = True


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, user: User = Depends(require_user)) -> dict:
    if body.card_id:  # 靶子卡备稿：compose 单点在 channels/persona（cards.service 沿用）
        from ..channels.persona.service import compose_card

        angle = (body.angles[0].strip() if body.angles else "")
        try:
            return await compose_card(card_id=body.card_id, hook=body.hook,
                                      spine=body.spine, angle=angle, user=user)
        except KeyError:
            raise HTTPException(status_code=404, detail="card not found") from None
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from None
    if body.ore_id:  # 获客矿建稿：获客矿素材+求学需求评论直接进获客脚本生成
        with Session(engine) as s:
            from ..materials.service import record_usage
            from ..models import Material

            ore = s.get(Material, body.ore_id)
            if ore is None or (ore.quality or {}).get("comment_kind") != "leadgen_ore":
                raise HTTPException(status_code=404, detail="获客矿素材不存在")
            dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.payload.op('->>')('ore_id') == str(body.ore_id),  # type: ignore[attr-defined]
                Content.status == "drafting")).first()  # type: ignore[attr-defined]
            if dup is not None:
                raise HTTPException(status_code=409,
                                    detail=f"该获客矿正在生成脚本（脚本 #{dup.id}），请等完成或删除后再试")
            script = new_script_content(
                s, tenant_id=user.tenant_id, channel_key="leadgen",
                title_hint=f"获客：{(ore.title or '')[:50]}",
                payload={"script_state": "generating", "ore_id": body.ore_id,
                         "account_type": "leadgen"})
            # 引用追溯（PRD R1.6）：获客稿 ← 获客矿素材
            record_usage(s, tenant_id=user.tenant_id, material_id=body.ore_id,
                         target_type="content", target_id=script.id, role="source")
            s.commit()  # with-Session 退出不 commit（SQLA2）——漏了=脚本行回滚
            script_id = script.id
        angles = [a.strip() for a in body.angles if a.strip()][:3]
        known = script_service.SIX_ANGLES
        bad = [a for a in angles if a not in known]
        if bad:
            raise HTTPException(status_code=400, detail=f"未知打法：{'、'.join(bad)}")
        job = await runner.submit("script_generate",
                                  {"script_id": script_id, "ore_id": body.ore_id,
                                   "length": body.length, "use_style": body.use_style,
                                   "angles": angles},
                                  dedup_key=f"ore:{body.ore_id}", actor=(user.tenant_id, user.id, user.role))
        return {"script_id": script_id, "job_id": job.id}
    if body.kb_id:  # 知识库文档建稿：挖掘器的改编角度/金句直接做切角与证据，原文做事实底
        with Session(engine) as s:
            from ..materials.service import record_usage
            from ..models import Material

            kb = s.get(Material, body.kb_id)
            if kb is None or kb.origin_type != "kb_doc":
                raise HTTPException(status_code=404, detail="知识库文档不存在")
            dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.payload.op('->>')('kb_id') == str(body.kb_id),  # type: ignore[attr-defined]
                Content.status == "drafting")).first()  # type: ignore[attr-defined]
            if dup is not None:
                raise HTTPException(status_code=409,
                                    detail=f"该文档正在生成脚本（脚本 #{dup.id}），请等完成或删除后再试")
            script = new_script_content(
                s, tenant_id=user.tenant_id, channel_key="persona",
                title_hint=f"知识库：{(kb.title or '')[:50]}",
                payload={"script_state": "generating", "kb_id": body.kb_id,
                         "account_type": "persona"})
            # 引用追溯（PRD R1.6）：脚本 ← 知识库文档
            record_usage(s, tenant_id=user.tenant_id, material_id=body.kb_id,
                         target_type="content", target_id=script.id, role="source")
            s.commit()  # with-Session 退出不 commit（SQLA2）——漏了=脚本行回滚
            script_id = script.id
        angles = [a.strip() for a in body.angles if a.strip()][:3]
        known = script_service.SIX_ANGLES
        bad = [a for a in angles if a not in known]
        if bad:
            raise HTTPException(status_code=400, detail=f"未知打法：{'、'.join(bad)}")
        job = await runner.submit("script_generate",
                                  {"script_id": script_id, "kb_id": body.kb_id,
                                   "length": body.length, "use_style": body.use_style,
                                   "angles": angles, "hotspot": body.hotspot},
                                  dedup_key=f"kb:{body.kb_id}", actor=(user.tenant_id, user.id, user.role))
        return {"script_id": script_id, "job_id": job.id}
    if not (body.card_id or body.ore_id or body.kb_id):
        raise HTTPException(status_code=400, detail="需要 card_id / ore_id / kb_id")
    angles = [a.strip() for a in body.angles if a.strip()][:3]
    known = script_service.SIX_ANGLES
    bad = [a for a in angles if a not in known]
    if bad:
        raise HTTPException(status_code=400, detail=f"未知打法：{'、'.join(bad)}")
    with Session(engine) as s:
        script = new_script_content(
            s, tenant_id=user.tenant_id,
            channel_key="persona" if (body.account_type or "persona") != "leadgen" else "leadgen",
            payload={"script_state": "generating",
                     "account_type": body.account_type or "persona"})
        s.commit()  # with-Session 退出不 commit（SQLA2）
        script_id = script.id
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "length": body.length,
                               "account_type": body.account_type,
                               "use_style": body.use_style, "angles": angles})
    return {"script_id": script_id, "job_id": job.id}


@router.get("")
def list_scripts(status: str = "", limit: int = 50, offset: int = 0,
                 account_type: str = "", user: User = Depends(require_user)) -> dict:
    """状态入参=旧状态串（generating/polishing/generated/final/failed），JSONB 状态串过滤。"""
    with Session(engine) as s:
        q = _script_q(s)
        cq = select(Content.payload.op('->>')('script_state').label('st'),
                    func.count(Content.id))  # type: ignore[arg-type]
        cq = cq.where(Content.kind == "video_script")  # type: ignore[attr-defined]
        if status:
            q = _state_filter(q, status)
        if account_type in ("persona", "leadgen"):
            q = _account_filter(q, account_type)
            cq = cq.where(Content.payload.op('->>')('account_type') == account_type)
        resp = paged_list_response(s, q.order_by(Content.id.desc()),  # type: ignore[attr-defined]
                                   offset=offset, limit=limit,
                                   serialize=lambda r: script_out(r, channel_key_of(s, r.channel_id)))
        raw_counts = {st or "": n for st, n in s.exec(cq.group_by('st')).all()}
        # counts 按旧状态串口径聚合计数（生成中=generating+polishing 两个 state 分开列）
        resp["counts"] = {k: v for k, v in raw_counts.items() if k}
        return resp


@router.get("/{script_id}")
def get_script(script_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise HTTPException(status_code=404, detail="script not found")
        return script_out(script, channel_key_of(s, script.channel_id))


class HotCommentIn(BaseModel):
    material_id: int
    comment_idx: int = 0


@router.post("/hotcomment", status_code=202)
async def hotcomment_script(body: HotCommentIn,
                            user: User = Depends(require_user)) -> dict:
    """热评→口播稿（2026-11-18 车道二）：高赞热评开场=受众认领，知识库弹药+形态读稿闸。"""
    job = await runner.submit("script_hotcomment",
                              {"material_id": body.material_id, "comment_idx": body.comment_idx},
                              dedup_key=f"1:hotc:{body.material_id}:{body.comment_idx}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


class ReviseIn(BaseModel):
    feedback: str


@router.post("/{script_id}/revise", status_code=202)
async def revise_script(script_id: int, body: ReviseIn,
                        user: User = Depends(require_user)) -> dict:
    """对话框内迭代（2026-11-18 用户裁决）：按号主反馈重写当前版本，追加新版本。"""
    feedback = body.feedback.strip()
    if not feedback:
        raise HTTPException(status_code=400, detail="反馈不能为空")
    job = await runner.submit("script_revise",
                              {"script_id": script_id, "feedback": feedback[:300]},
                              dedup_key=f"1:srev:{script_id}", actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


@router.post("/{script_id}/polish", status_code=202)
async def polish(script_id: int, body: PolishIn | None = None,
                 user: User = Depends(require_user)) -> dict:
    use_style = body.use_style if body else True
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise HTTPException(status_code=404, detail="script not found")
        if not script.versions:
            raise HTTPException(status_code=400, detail="脚本还没有版本，先生成")
        p = dict(script.payload or {})
        p["script_state"] = "polishing"
        script.payload = p
        script.status = "drafting"
        s.add(script)
        s.commit()
    job = await runner.submit("script_polish", {"script_id": script_id, "use_style": use_style})
    return {"job_id": job.id}


@router.post("/{script_id}/finalize", status_code=202)
async def finalize(script_id: int, body: FinalizeIn | None = None,
                   user: User = Depends(require_user)) -> dict:
    index = body.version_index if body else 0
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise HTTPException(status_code=404, detail="script not found")
        if not script.versions:
            raise HTTPException(status_code=400, detail="脚本还没有版本，先生成")
    job = await runner.submit("script_finalize",
                              {"script_id": script_id, "version_index": index},
                              dedup_key=f"finalize:{script_id}")
    return {"job_id": job.id}


@router.delete("/{script_id}")
def delete_script(script_id: int, user: User = Depends(require_user)) -> dict:
    """删除脚本。引用检查：派生长文先解绑（parent_content_id 置空，不级联删除）。"""
    with Session(engine) as s:
        script = s.get(Content, script_id)
        if script is None or script.kind != "video_script":
            raise HTTPException(404, detail="script not found")
        if s.exec(select(Publication).where(Publication.content_id == script_id)).first():  # type: ignore[attr-defined]
            raise HTTPException(409, detail="已发布，不能删除（保留发布记录与数据对照）")
        # 解绑引用（置空而非删除，保护下游产物）
        for art in s.exec(select(Content).where(  # type: ignore[attr-defined]
                Content.parent_content_id == script_id)).all():  # type: ignore[attr-defined]
            art.parent_content_id = None
            s.add(art)
        s.delete(script)
        s.commit()
    return {"ok": True}
