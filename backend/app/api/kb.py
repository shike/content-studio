"""ima 知识库线管理 API（素材库「知识库」tab，2026-11-17）。

知识库=ima 源这条线的管理台：目录清单（读写 ima_topic_folders 设置，格式 `folder_id:名称` 逗号串）
+ 凭证状态（只写不读口径：只报已配置与否）+ 最近一次挖掘结果。
文档本体是素材（origin_type=kb_doc，quality.folder 记目录），全量进「推荐选题」可检索；挖掘触发复用 /api/articles/mine。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..articles.ima_kb import kb_config, topic_folders
from ..auth import require_user
from ..db import engine
from ..models import AppSetting, Job, Material, User

router = APIRouter(prefix="/api/kb")


def _effective_folders(s: Session) -> list[tuple[str, str]]:
    """当前生效目录（设置空值时回落内置默认——增删都以生效集合为基底写回）。"""
    return topic_folders(s)


def _write_folders(s: Session, folders: list[tuple[str, str]]) -> None:
    raw = ",".join(f"{fid}:{name}" for fid, name in folders)
    row = s.get(AppSetting, "ima_topic_folders")
    if row is None:
        s.add(AppSetting(key="ima_topic_folders", value=raw))
    else:
        row.value = raw
        s.add(row)
    s.commit()


@router.get("/overview")
def kb_overview(user: User = Depends(require_user)) -> dict:
    """知识库总览：凭证状态 + 目录清单（各目录入库数/最近入库）+ 最近挖掘 + 文档统计。"""
    with Session(engine) as s:
        cfg = kb_config(s)
        folders = _effective_folders(s)
        # 各目录入库统计（kb 文档 quality.folder 记目录名）
        rows = s.exec(
            select(Material.quality, func.count(Material.id), func.max(Material.created_at))  # type: ignore[arg-type]
            .where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
            .group_by(Material.quality)).all()  # type: ignore[attr-defined]
        per_folder: dict[str, dict] = {}
        stage = {"pending": 0, "low": 0}
        total_docs = 0
        for quality, n, latest in rows:
            q = quality or {}
            total_docs += n
            name = str(q.get("folder") or "（未知目录）")
            agg = per_folder.setdefault(name, {"docs": 0, "latest": None})
            agg["docs"] += n
            lt = str(latest or "")
            if lt > str(agg["latest"] or ""):
                agg["latest"] = lt
        for agg in per_folder.values():
            if agg["latest"]:
                agg["latest"] = str(agg["latest"]).replace(" ", "T")  # isoformat 口径（前端 relTime 补 Z 解析）
        # stage 统计与目录无关，单独查
        stage_rows = s.exec(
            select(Material.stage, func.count(Material.id))  # type: ignore[arg-type]
            .where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
            .group_by(Material.stage)).all()  # type: ignore[attr-defined]
        for st, n in stage_rows:
            if st in stage:
                stage[st] = n
        last = s.exec(
            select(Job).where(Job.type == "article_kb_miner")  # type: ignore[attr-defined]
            .order_by(Job.updated_at.desc())).first()  # type: ignore[attr-defined]
        return {
            "configured": bool(cfg),
            "folders": [{"folder_id": fid, "name": name,
                         "docs": (per_folder.get(name) or {}).get("docs", 0),
                         "latest": (per_folder.get(name) or {}).get("latest")}
                        for fid, name in folders],
            "unknown_folder_docs": (per_folder.get("（未知目录）") or {}).get("docs", 0),
            "docs_total": total_docs,
            "stage": stage,
            "last_mine": ({"status": last.status, "message": last.message,
                           "updated_at": str(last.updated_at or "").replace(" ", "T")} if last else None),
        }


class FolderIn(BaseModel):
    folder_id: str
    name: str


@router.post("/folders", status_code=201)
def add_folder(body: FolderIn, user: User = Depends(require_user)) -> dict:
    """加挖掘目录（写 ima_topic_folders；同名/同 id 幂等）。"""
    fid = body.folder_id.strip()
    if not fid:
        raise HTTPException(status_code=400, detail="folder_id 不能为空")
    name = body.name.strip() or fid[:16]
    with Session(engine) as s:
        folders = _effective_folders(s)
        if any(x[0] == fid for x in folders):
            raise HTTPException(status_code=409, detail="该目录已在清单")
        folders.append((fid, name[:30]))
        _write_folders(s, folders)
        return {"folders": [{"folder_id": a, "name": b} for a, b in folders]}


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: str, user: User = Depends(require_user)) -> dict:
    """移除挖掘目录（已入库文档不动，仍在推荐选题可检索）。"""
    with Session(engine) as s:
        folders = _effective_folders(s)
        kept = [x for x in folders if x[0] != folder_id]
        if len(kept) == len(folders):
            raise HTTPException(status_code=404, detail="目录不在清单")
        if not kept:
            raise HTTPException(status_code=400, detail="至少保留一个目录（清空会回落内置默认 12 个）")
        _write_folders(s, kept)
        return {"folders": [{"folder_id": a, "name": b} for a, b in kept]}
