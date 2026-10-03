"""实体复活（任务中台）：把失败实体拉回在途态 / 找出实体孤儿。

两个用途：
① retry-by-fingerprint 重排失败任务前，先把关联实体从 failed 拉回在途态（防二次幽灵）；
② 实体孤儿（实体停在 failed 且已无活跃/排期任务——任务记录可能已被归档清理）
   的聚类展示与整体重提，让"死在工作流里的产物"重新可见、可救。
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..db import engine
from ..models import Article, AvatarVideo, Job, Script, SelfVideo

# job_type → (实体模型, payload 主键字段, 在途状态)
ENTITY_TABLE: dict[str, tuple[type, str, str]] = {
    "script_generate": (Script, "script_id", "generating"),
    "script_polish": (Script, "script_id", "polishing"),
    "article_generate": (Article, "article_id", "generating"),
    "avatar_video": (AvatarVideo, "video_row_id", "generating"),
    # SelfVideo 无失败态（分析失败行保持 approved），coverage/孤儿判定需要它的映射
    "self_analyze": (SelfVideo, "video_id", "approved"),
}


def revive_entity(job_type: str, payload: dict) -> bool:
    """把 payload 指向的实体从 failed 拉回在途态。无实体关联的类型恒真。"""
    entry = ENTITY_TABLE.get(job_type)
    if entry is None:
        return True
    model, id_key, in_flight = entry
    entity_id = (payload or {}).get(id_key)
    if not entity_id:
        return True
    with Session(engine) as s:
        row = s.get(model, int(entity_id))
        if row is None:
            return False
        if getattr(row, "status", None) == "failed":
            row.status = in_flight
            s.add(row)
            s.commit()
    return True


def entity_orphans() -> list[dict]:
    """实体孤儿盘点：status=failed 且没有任何活跃/排期中的同源任务。

    任务记录可能已被清理，所以按实体表反查（活跃任务 payload 里含同 id 才算有主）。
    """
    ACTIVE = ("queued", "running", "parked")
    out: list[dict] = []
    with Session(engine) as s:
        active_payloads: dict[str, list[dict]] = {}
        for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.status.in_(ACTIVE))).all():  # type: ignore[attr-defined]
            active_payloads.setdefault(j.type, []).append(j.payload or {})
        for job_type, (model, id_key, _in_flight) in ENTITY_TABLE.items():
            live_ids = {p.get(id_key) for p in active_payloads.get(job_type, [])}
            for row in s.exec(select(model)).all():
                if getattr(row, "status", None) != "failed":
                    continue
                if row.id in live_ids:
                    continue  # 还有活跃任务在管它
                out.append({
                    "kind": "entity",
                    "job_type": job_type,
                    "entity_id": row.id,
                    "title": _entity_title(row),
                    "revive_payload": _revive_payload(job_type, row),
                })
    return out


def _entity_title(row) -> str:
    for attr in ("title", "name"):
        v = getattr(row, attr, None)
        if v:
            return str(v)[:60]
    if hasattr(row, "topic_id") and row.topic_id:  # 失败文章常无标题，回落选题标题
        from ..models import Topic
        with Session(engine) as s:
            t = s.get(Topic, row.topic_id)
            if t is not None and t.title:
                return f"#{row.id} {t.title[:50]}"
    return f"#{row.id}"


def _revive_payload(job_type: str, row) -> dict:
    if job_type == "article_generate":
        return {"article_id": row.id}
    if job_type in ("script_generate", "script_polish"):
        return {"script_id": row.id}

        return {"project_id": row.id}
    if job_type == "avatar_video":
        return {"video_row_id": row.id}
    return {}
