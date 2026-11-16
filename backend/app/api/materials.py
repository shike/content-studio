"""素材库 API（PRD R1）：列表/详情/定夺/手动入库/贴链拆解/上传拆解/回填/全量扫描。

出参兼容：material_out() 提供「素材域字段 + 旧拆解库别名」超集——
拆解库页/获客选题页的旧字段（scan_status/comment_kind/quality_score/analysis…）
P2 期内继续可读，旧页退役后别名随迁。
"""
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import Material, MaterialAnalysis, User
from ..settings import settings
from ._listing import require_douyin_url
from .materials_helpers import material_list_query

router = APIRouter()

_MAX_UPLOAD = 2 * 1024 * 1024 * 1024  # 2GB


@router.get("/api/materials")
def list_materials(status: str = "", origin_type: str = "", ore: str = "",
                   q: str = "", author: str = "",
                   limit: int = 20, offset: int = 0,
                   user: User = Depends(require_user)) -> dict:
    """素材列表（分页 + 筛选）。

    status: inbox|ready|ignored|archived|all（缺省=all）；
    origin_type: douyin_video|kb_doc|test_record|manual_note|video...；
    ore: leadgen（获客矿，quality.comment_kind=leadgen_ore）；
    q: 标题/作者/转写模糊。
    """
    from ..materials import service

    with Session(engine) as s:
        rows, total, counts = material_list_query(
            s, status=status, origin_type=origin_type, ore=ore, q=q, author=author,
            limit=limit, offset=offset)
        amap = service.analyses_map(s, [r.id for r in rows])
        return {"items": [service.material_out(r, amap.get(r.id)) for r in rows],
                "total": total, "counts": counts}


@router.get("/api/materials/{material_id}")
def get_material(material_id: int, user: User = Depends(require_user)) -> dict:
    """素材详情：全字段 + 分析版本（breakdown/comments）+ 引用追溯。"""
    from ..materials import service

    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise HTTPException(status_code=404, detail="material not found")
        amap = service.analyses_map(s, [material_id])
        d = service.material_out(m, amap.get(material_id))
        d["analyses"] = [
            {"kind": a.kind, "version": a.version, "payload": a.payload,
             "text": a.text, "model": a.model, "created_at": a.created_at}
            for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                MaterialAnalysis.material_id == material_id)).all()  # type: ignore[attr-defined]
        ]
        d["usages"] = service.usages_of(s, material_id)
        return d


class MaterialIn(BaseModel):
    title: str
    body: str = ""
    origin_type: str = "manual_note"  # manual_note|image|audio|video|test_record...
    tags: list[str] = []
    note: str = ""


@router.post("/api/materials", status_code=201)
def create_material(body: MaterialIn, user: User = Depends(require_user)) -> dict:
    """手动入库（一手素材：实测记录/案例/口述/图片文字）。"""
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="标题不能为空")
    from ..materials import service

    with Session(engine) as s:
        m, _created = service.upsert_material(
            s, tenant_id=user.tenant_id, origin_type=body.origin_type,
            title=body.title.strip()[:120], body=body.body,
            tags=[t.strip()[:24] for t in body.tags if t.strip()][:8],
            note=body.note.strip()[:300],
            status="ready",  # 手动一手素材直接可用，不过收件箱
            quality={"auto": "manual"})
        return service.material_out(m)


class MaterialUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    note: str | None = None


@router.put("/api/materials/{material_id}")
def update_material(material_id: int, body: MaterialUpdate,
                    user: User = Depends(require_user)) -> dict:
    from ..materials import service

    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise HTTPException(status_code=404, detail="material not found")
        if body.title is not None:
            if not body.title.strip():
                raise HTTPException(status_code=400, detail="标题不能为空")
            m.title = body.title.strip()[:120]
        if body.body is not None:
            m.body = body.body
        if body.tags is not None:
            m.tags = [t.strip()[:24] for t in body.tags if t.strip()][:8]
        if body.note is not None:
            m.note = body.note.strip()[:500]
        s.add(m)
        s.commit()
        s.refresh(m)
        return service.material_out(m)


class StatusIn(BaseModel):
    status: str  # ready | ignored | archived | inbox


@router.post("/api/materials/{material_id}/status", status_code=202)
async def set_material_status(material_id: int, body: StatusIn,
                              user: User = Depends(require_user)) -> dict:
    """人工定夺：收下（ready）/忽略（ignored）/归档/退回收件箱。
    ready 且从未拆解的抖音素材 → 排队拆解（手动翻案强拆同入口）。"""
    if body.status not in ("ready", "ignored", "archived", "inbox"):
        raise HTTPException(status_code=400,
                            detail="status 必须是 ready / ignored / archived / inbox")
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise HTTPException(status_code=404, detail="material not found")
        m.status = body.status
        q = dict(m.quality or {})
        if body.status == "ready":
            q.pop("gate_reason", None)  # 人工翻案：清闸判定留痕
        m.quality = q
        s.add(m)
        s.commit()
    job_id = None
    if body.status == "ready":
        job = await runner.submit("material_analyze", {"material_id": material_id},
                                  dedup_key=f"mat:{material_id}")
        job_id = job.id
    return {"status": body.status, "job_id": job_id}


@router.post("/api/materials/inbox-resolve")
async def resolve_inbox(body: dict, user: User = Depends(require_user)) -> dict:
    """批量定夺收件箱（可按作者过滤）：approve=收下并排队拆解 / ignore=忽略。"""
    action = (body or {}).get("action") or ""
    author = (body or {}).get("author") or ""
    if action not in ("approve", "ignore"):
        raise HTTPException(status_code=400, detail="action 必须是 approve / ignore")

    with Session(engine) as s:
        q = select(Material).where(  # type: ignore[attr-defined]
            Material.status == "inbox")  # type: ignore[attr-defined]
        if author:
            q = q.where(Material.author == author)  # type: ignore[attr-defined]
        rows = s.exec(q).all()
        resolved = 0
        job_ids = []
        for m in rows:
            m.status = "ready" if action == "approve" else "ignored"
            if action == "ignore":
                qq = dict(m.quality or {})
                qq["gate_reason"] = "人工忽略"
                m.quality = qq
            else:
                qq = dict(m.quality or {})
                qq.pop("gate_reason", None)
                m.quality = qq
            s.add(m)
            resolved += 1
        s.commit()
        if action == "approve":
            for m in rows:
                job = await runner.submit("material_analyze", {"material_id": m.id},
                                          dedup_key=f"mat:{m.id}")
                job_ids.append(job.id)
    return {"resolved": resolved, "job_ids": job_ids}


class EnrichIn(BaseModel):
    pass


@router.post("/api/materials/{material_id}/enrich", status_code=202)
async def enrich_material(material_id: int, user: User = Depends(require_user)) -> dict:
    """手动（重）拆解：ready/ignored 均可强拆。"""
    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise HTTPException(status_code=404, detail="material not found")
        if m.status == "inbox":
            m.status = "ready"
            s.add(m)
            s.commit()
    job = await runner.submit("material_analyze", {"material_id": material_id},
                              dedup_key=f"mat:{material_id}")
    return {"material_id": material_id, "job_id": job.id}


class AnalyzeUrlIn(BaseModel):
    url: str


@router.post("/api/materials/analyze-url", status_code=202)
async def analyze_url(body: AnalyzeUrlIn, user: User = Depends(require_user)) -> dict:
    """贴链在线拆解（免登录）：分享口令全文可贴，自动提取。"""
    from ..materials.download import extract_douyin_url

    url = extract_douyin_url(body.url.strip())
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    require_douyin_url(url, "仅支持抖音视频链接（v.douyin.com / douyin.com）")
    from ..materials import service

    with Session(engine) as s:
        m, _created = service.upsert_material(
            s, tenant_id=user.tenant_id, origin_type="douyin_video",
            origin_url=url, status="ready")  # 实体去重：同一链接重复贴不建重复行
        material_id = m.id
    job = await runner.submit("material_analyze", {"material_id": material_id},
                              dedup_key=f"mat:{material_id}")
    return {"material_id": material_id, "job_id": job.id}


@router.post("/api/materials/upload", status_code=202)
async def analyze_upload(file: UploadFile, user: User = Depends(require_user)) -> dict:
    """本地视频/音频上传拆解（手工下载的视频、视频号内容走此口）。"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="缺少文件")
    suffix = Path(file.filename).suffix or ".mp4"
    out_dir = settings.data_dir / "materials"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"local_{int(time.time() * 1000)}{suffix}"
    size = 0
    with open(path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > _MAX_UPLOAD:
                f.close()
                path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="文件超过 2GB 上限")
            f.write(chunk)
    from ..materials import service

    with Session(engine) as s:
        m, _created = service.upsert_material(
            s, tenant_id=user.tenant_id, origin_type="video",
            title=Path(file.filename).stem[:100], status="ready",
            media={"path": str(path)})
        material_id = m.id
    job = await runner.submit("material_analyze", {"material_id": material_id},
                              dedup_key=f"mat:{material_id}")
    return {"material_id": material_id, "job_id": job.id}


@router.post("/api/materials/scan", status_code=202)
async def scan_all(user: User = Depends(require_user)) -> dict:
    """全量扫描同行采集源（与调度中心 material_watch_scan 同口径，防当日双跑）。"""
    from datetime import datetime

    job = await runner.submit("material_watch_scan", {"auto": True},
                              dedup_key=f"{user.tenant_id}:scan:all:{datetime.now():%Y%m%d}")
    return {"job_id": job.id}


class BackfillIn(BaseModel):
    scope: str = "peer"  # peer=同行账号 | topic=话题雷达


@router.post("/api/materials/backfill", status_code=202)
async def backfill(body: BackfillIn, user: User = Depends(require_user)) -> dict:
    """存量回填：同行账号 / 话题雷达两条线（追溯窗=backfill_window_days）。"""
    jtype = "material_backfill" if body.scope == "peer" else "material_topic_backfill"
    dedup = "1:material_backfill" if body.scope == "peer" else "1:material_topic_backfill"
    job = await runner.submit(jtype, {"auto": True}, dedup_key=dedup,
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


@router.delete("/api/materials/{material_id}")
def delete_material(material_id: int, user: User = Depends(require_user)) -> dict:
    """删除素材（连带删媒体文件与加工产物；引用记录随行删）。"""
    import os

    from ..models import MaterialUsage

    with Session(engine) as s:
        m = s.get(Material, material_id)
        if m is None:
            raise HTTPException(status_code=404, detail="material not found")
        media = m.media or {}
        for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                MaterialAnalysis.material_id == material_id)).all():  # type: ignore[attr-defined]
            s.delete(a)
        for u in s.exec(select(MaterialUsage).where(  # type: ignore[attr-defined]
                MaterialUsage.material_id == material_id)).all():  # type: ignore[attr-defined]
            s.delete(u)
        s.delete(m)
        s.commit()
    path = (media or {}).get("path") or ""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return {"deleted": material_id}
