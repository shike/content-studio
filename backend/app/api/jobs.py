"""任务查询/删除接口（列表 + 单任务 + 终态清理 + 失败中台）。"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import get_current_user
from ..credits import JOB_POINTS
from ..db import engine
from ..jobs.errors import fingerprint
from ..jobs.revive import entity_orphans, revive_entity
from ..jobs.runner import runner
from ..models import FailurePattern, Job

router = APIRouter(prefix="/api/jobs")

_CLEARABLE = {"failed", "superseded", "succeeded"}

_WEIGHT = {"running": 0, "queued": 1, "parked": 2, "superseded": 3, "failed": 4, "succeeded": 5}


def _actor_of(j: Job):
    """人工重试继承原任务归属；历史行/系统任务（tenant_id=0）回落当前登录者。
    第三位 role 恒空串：重试执行期不做平台豁免，按租户作用域跑。"""
    if getattr(j, "tenant_id", 0):
        return (j.tenant_id, j.user_id, "")
    return None


@router.get("")
def list_jobs(request: Request, status: str = "", type: str = "", limit: int = 50,
              offset: int = 0, tenant_id: int = 0) -> dict:
    """管道内任务列表：运行中置顶、排队按 FIFO、挂起次之、其余按新→旧。

    counts 为全库口径；queue_position 仅排队任务有（近似 FIFO 位次）。
    tenant_id 过滤仅 platform_admin 可用（管理后台租户详情用）。
    """
    user = get_current_user(request)
    if user is None or user.status != "active":
        raise HTTPException(status_code=401, detail="未登录或会话失效")
    # 租户隔离：平台管理员看全平台，其余成员只看本租户任务
    my_tenant = None if user.role == "platform_admin" else user.tenant_id
    with Session(engine) as s:
        counts = {k: 0 for k in _WEIGHT}
        cnt_q = select(Job.status, Job.id).where(Job.archived == 0)  # type: ignore[attr-defined]
        if my_tenant:
            cnt_q = cnt_q.where(Job.tenant_id == my_tenant)  # type: ignore[attr-defined]
        for st, n in s.exec(cnt_q).all():
            if st in counts:
                counts[st] += 1
        q = select(Job).where(Job.archived == 0)  # type: ignore[attr-defined]
        if my_tenant:
            q = q.where(Job.tenant_id == my_tenant)  # type: ignore[attr-defined]
        if status in _WEIGHT:
            q = q.where(Job.status == status)  # type: ignore[attr-defined]
        if type:
            q = q.where(Job.type == type)  # type: ignore[attr-defined]
        if tenant_id:
            if user.role != "platform_admin":
                raise HTTPException(status_code=403, detail="需要平台管理员权限")
            q = q.where(Job.tenant_id == tenant_id)  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(Job.id.desc()).limit(500)).all()  # type: ignore[attr-defined]
        rows = sorted(rows, key=lambda j: (_WEIGHT.get(j.status, 9),
                                           j.id if j.status == "queued" else -j.id))
        queued_ahead = sorted(j.id for j in rows if j.status == "queued")
        total = len(rows)
        page = rows[max(0, offset): max(0, offset) + max(1, min(limit, 200))]
        cov = _coverage(s) if any(j.status == "failed" for j in page) else {}
        items = []
        for j in page:
            d = j.model_dump()
            d.pop("result", None)  # 列表不背结果体（notes 可能很长），详情走 /{id}
            if j.status == "queued":
                d["queue_position"] = queued_ahead.index(j.id) + 1
            if j.status == "failed" and j.id in cov:
                d["covered_by"] = cov[j.id]  # 已闭环：后续任务成功重跑了同一工作
            items.append(d)
        return {"items": items, "total": total, "counts": counts}


class ClearIn(BaseModel):
    status: str  # failed | superseded | succeeded


class RetryFailedIn(BaseModel):
    type: str | None = None


@router.post("/clear")
def clear_jobs(body: ClearIn) -> dict:
    """批量归档终态任务（运行中/排队不可清）。归档制：不物理删，保全故障证据。"""
    if body.status not in _CLEARABLE:
        raise HTTPException(status_code=400, detail=f"status 必须是 {'、'.join(_CLEARABLE)}")
    with Session(engine) as s:
        rows = s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.status == body.status,  # type: ignore[attr-defined]
            Job.archived == 0,  # type: ignore[attr-defined]
        )).all()
        for j in rows:
            j.archived = 1
            s.add(j)
        s.commit()
        return {"archived": len(rows)}


@router.post("/{job_id}/retry", status_code=202)
async def retry_job(job_id: int) -> dict:
    """失败任务重试：按原 type/payload 重新入队，原记录标记已续跑（人工重试清零自动重试计数）。"""
    from ..jobs.runner import runner as _runner

    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status not in ("failed", "parked"):
            raise HTTPException(status_code=400, detail="只有失败/挂起的任务可以重试")
        job.status = "superseded"
        job.error = "已手动重试，重新入队"
        s.add(job)
        new_payload = dict(job.payload)
        new_type = job.type
    from ..jobs.revive import revive_entity
    try:
        revive_entity(new_type, new_payload)  # 实体先回在途态，重试期间不显示失败
    except Exception as e:  # noqa: BLE001
        print(f"[jobs] 手动重试实体回退失败: {type(e).__name__}: {str(e)[:100]}")
    _runner.manual_retry_reset(job_id)
    new_job = await _runner.submit(new_type, new_payload, retry_of=job_id,
                                   actor=_actor_of(job),
                                   points=JOB_POINTS.get(new_type, 0))
    return {"job_id": new_job.id}


@router.post("/retry-failed", status_code=202)
def retry_failed(body: RetryFailedIn | None = None) -> dict:
    """批量重试失败任务（可按 type 过滤），原记录全部标记已续跑。默认跳过确定性失败。"""
    from ..jobs.errors import classify
    from ..jobs.runner import runner as _runner

    wanted = body.type if body and body.type else None
    requeued_ids: list[int] = []
    with Session(engine) as s:
        q = select(Job).where(  # type: ignore[attr-defined]
            Job.status == "failed",  # type: ignore[attr-defined]
            Job.archived == 0,  # type: ignore[attr-defined]
        )
        if wanted:
            q = q.where(Job.type == wanted)  # type: ignore[attr-defined]
        for j in s.exec(q).all():
            if classify(RuntimeError(j.error or "")) == "deterministic":
                continue  # 确定性失败重试也是白烧，跳过（单条人工重试不受限）
            j.status = "superseded"
            j.error = "已批量重试，重新入队"
            s.add(j)
            requeued_ids.append(j.id)
        s.commit()
    from ..jobs.revive import revive_entity
    for jid in requeued_ids:
        _runner.manual_retry_reset(jid)
        with Session(engine) as s:
            j = s.get(Job, jid)
            if j is not None:
                try:
                    revive_entity(j.type, dict(j.payload))
                except Exception:  # noqa: BLE001
                    pass
                _runner.submit(j.type, dict(j.payload), retry_of=j.id,
                               actor=_actor_of(j),
                               points=JOB_POINTS.get(j.type, 0))
    return {"requeued": len(requeued_ids)}


@router.delete("/{job_id}")
def delete_job(job_id: int) -> dict:
    """单条任务归档（不物理删，保全证据）；运行中不可动。"""
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status == "running":
            raise HTTPException(status_code=400, detail="运行中的任务不能归档，等它结束再清理")
        job.archived = 1
        s.add(job)
        s.commit()
    return {"archived": job_id}


# ---------- 失败中台：错误指纹聚类 / 按指纹复活 / 待处置 ----------
# 路由顺序：GET /failures 与 GET /{job_id} 同为单段路径，FastAPI 按声明顺序匹配，
# 固定路径必须在 /{job_id} 之前声明才不会被截胡（文末的 get_job 是有意放最后的）。

@router.get("/failures")
def failure_groups() -> dict:
    """待处置盘点：失败且无自动重试排期且未归档的任务，按错误指纹聚类；
    附实体孤儿组（实体 failed 但已无活跃任务，如任务记录被清理后的失败文章）。
    """
    from ..jobs.errors import fingerprint_label

    with Session(engine) as s:
        patterns = {p.fp: p for p in s.exec(select(FailurePattern)).all()}
        groups: dict[str, dict] = {}
        rows = s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.status == "failed",  # type: ignore[attr-defined]
            Job.next_retry_at == None,  # noqa: E711  # type: ignore[attr-defined]
            Job.archived == 0,  # type: ignore[attr-defined]
        )).all()
        cov = _coverage(s)
        for j in rows:
            if j.id in cov:
                continue  # 已闭环（后续任务成功重跑），不算待处置
            # 老失败任务没有指纹字段：按错误消息懒归类（如 DDG 限流消息 → ddg:rate-limited）
            fp = j.error_fp or fingerprint(RuntimeError(j.error or ""))
            g = groups.setdefault(fp, {
                "fp": fp, "kind": "job", "count": 0, "jobs": [],
                "sample_error": "", "sample_job_id": 0, "types": set(),
                "fail_class": j.fail_class,
            })
            g["count"] += 1
            g["types"].add(j.type)
            if not g["sample_error"]:
                g["sample_error"] = (j.error or "")[:400]
                g["sample_job_id"] = j.id
        for fp, g in groups.items():
            p = patterns.get(fp)
            g["label"] = (p.label if p else "") or fingerprint_label(fp)
            g["root_cause"] = p.root_cause if p else ""
            g["pattern_status"] = p.status if p else "open"
            g["last_type"] = "、".join(sorted(g.pop("types")))
        items = [g for g in groups.values() if (p := patterns.get(g["fp"])) is None
                 or p.status == "open"]
        for g in items:
            g["count"] = sum(1 for j in rows if j.id not in cov
                             and (j.error_fp or fingerprint(RuntimeError(j.error or ""))) == g["fp"])
        items = [g for g in items if g["count"] > 0]
        # 实体孤儿组（合成指纹 entity:{type}，无指纹记录也可复活）
        for o in entity_orphans():
            fp = f"entity:{o['job_type']}"
            g = groups.setdefault(fp, {
                "fp": fp, "kind": "entity", "count": 0, "jobs": [],
                "sample_error": "", "sample_job_id": 0, "types": {o["job_type"]},
                "fail_class": "deterministic", "last_type": o["job_type"],
                "label": _ENTITY_LABELS.get(o["job_type"], o["job_type"]),
                "root_cause": "", "pattern_status": "open",
            })
            g["count"] += 1
            g["orphans"] = g.get("orphans", []) + [
                {"entity_id": o["entity_id"], "title": o["title"]}
            ]
            if not g["sample_error"]:
                g["sample_error"] = f"实体 #{o['entity_id']}「{o['title']}」停在失败态，且已无在途任务"
        items += [g for g in groups.values() if g["kind"] == "entity"]
        items.sort(key=lambda g: -g["count"])
        return {"items": items, "disposal": sum(g["count"] for g in items)}


_ENTITY_LABELS = {
    "article_generate": "长文生成失败（无在途任务）",
    "script_generate": "脚本生成失败（无在途任务）",
    "script_polish": "脚本打磨失败（无在途任务）",
    "media_align": "字幕对齐失败（无在途任务）",
    "draft_generate": "剪映草稿失败（无在途任务）",
    "avatar_video": "数字人合成失败（无在途任务）",
}


def _coverage(s: Session) -> dict[int, int]:
    """闭环判定：failed job id → 覆盖它的后续成功任务 id。

    判定 = 同 type 且（同 dedup_key 或同实体 id）且 id 更大的 succeeded 任务存在。
    有覆盖 = 该失败已被"重新执行成功"闭环，不再算待处置。"""
    # 注意：succeeded 侧不过滤 archived——归档只是隐藏显示，
    # "这份工作已被成功完成"的事实仍然作为闭环依据
    succ = s.exec(select(Job).where(  # type: ignore[attr-defined]
        Job.status == "succeeded",  # type: ignore[attr-defined]
    ).order_by(Job.id)).all()  # type: ignore[attr-defined]
    best_dedup: dict[tuple, int] = {}
    best_entity: dict[tuple, int] = {}
    for j in succ:
        if j.dedup_key:
            best_dedup[(j.type, j.dedup_key)] = j.id
        ek = _id_key_of(j.type)
        eid = (j.payload or {}).get(ek) if ek else None
        if ek and eid is not None:
            best_entity[(j.type, eid)] = j.id
    cov: dict[int, int] = {}
    for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.status == "failed",  # type: ignore[attr-defined]
            Job.archived == 0,  # type: ignore[attr-defined]
    )).all():
        later = best_dedup.get((j.type, j.dedup_key)) if j.dedup_key else None
        if later is None:
            ek = _id_key_of(j.type)
            eid = (j.payload or {}).get(ek) if ek else None
            if ek and eid is not None:
                later = best_entity.get((j.type, eid))
        if later is not None and later > j.id:
            cov[j.id] = later
    return cov


def _id_key_of(job_type: str) -> str:
    return {"article_generate": "article_id", "script_generate": "script_id",
            "script_polish": "script_id", "media_align": "project_id",
            "draft_generate": "project_id", "avatar_video": "video_row_id",
            "self_analyze": "video_id"}.get(job_type, "")


class RetryByFpIn(BaseModel):
    fp: str


@router.post("/retry-by-fingerprint", status_code=202)
async def retry_by_fingerprint(body: RetryByFpIn) -> dict:
    """按指纹整批复活：实体孤儿 → 重提生成任务；失败任务 → 实体回在途态后重新入队。"""
    if body.fp.startswith("entity:"):
        job_type = body.fp.removeprefix("entity:")
        requeued = 0
        for o in entity_orphans():
            if o["job_type"] != job_type:
                continue
            revive_entity(job_type, o["revive_payload"])  # 实体先回在途态再重提
            await runner.submit(job_type, o["revive_payload"])
            requeued += 1
        if requeued == 0:
            raise HTTPException(status_code=404, detail="该类实体孤儿已清零（可能刚被处理）")
        return {"requeued": requeued}
    requeued_ids: list[int] = []
    with Session(engine) as s:
        rows = s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.status == "failed",  # type: ignore[attr-defined]
            Job.error_fp == body.fp,  # type: ignore[attr-defined]
            Job.archived == 0,  # type: ignore[attr-defined]
        )).all()
        for j in rows:
            if not revive_entity(j.type, j.payload):
                continue  # 实体已被删除，任务无法复活
            j.status = "superseded"
            j.error = "已按指纹批量复活，重新入队"
            s.add(j)
            requeued_ids.append(j.id)
        s.commit()
    for jid in requeued_ids:
        runner.manual_retry_reset(jid)
        with Session(engine) as s:
            j = s.get(Job, jid)
            if j is not None:
                await runner.submit(j.type, dict(j.payload), retry_of=jid,
                                    actor=_actor_of(j),
                                    points=JOB_POINTS.get(j.type, 0))
    if not requeued_ids:
        raise HTTPException(status_code=404, detail="该指纹下没有可复活的任务")
    return {"requeued": len(requeued_ids)}


class ResolveIn(BaseModel):
    status: str  # fixed | ignored | open
    note: str = ""


@router.post("/failures/{fp}/resolve")
def resolve_failure(fp: str, body: ResolveIn) -> dict:
    """指纹处置：修完根因标 fixed（可再按指纹复活历史失败）；已知不修标 ignored。"""
    if body.status not in ("fixed", "ignored", "open"):
        raise HTTPException(status_code=400, detail="status 必须是 fixed / ignored / open")
    with Session(engine) as s:
        p = s.exec(select(FailurePattern).where(  # type: ignore[attr-defined]
            FailurePattern.fp == fp)).first()
        if p is None:
            raise HTTPException(status_code=404, detail="该指纹还没有失败记录")
        p.status = body.status
        if body.note:
            p.root_cause = body.note
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.model_dump()


@router.get("/{job_id}")
def get_job(job_id: int) -> Job:
    """单任务详情（含 result）。声明在最后，避免截胡 /failures 等固定路径。"""
    with Session(engine) as s:
        job = s.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job
