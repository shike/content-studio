"""采集源 API（PRD R1.3/R1.8）：同行账号 / 话题雷达 / 账号候选的统一管理面。

- peer_account：增删改查+启停、单源/全量扫描、贴链识别、存量回填、扫描历史；
- radar_topic：添加（贴链提取 ch_id）/巡检/回填；
- 候选（status=candidate）：发现（关键词/同城矩阵）→ 定夺（activate/dismiss/清已处理）。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import Job, Material, Source, User
from ._listing import require_douyin_url

router = APIRouter(prefix="/api/sources")


def _sec_of(url: str) -> str:
    import re

    m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", url or "") or \
        re.search(r"sec_uid=([A-Za-z0-9_-]{20,})", url or "")
    return m.group(1) if m else ""


def _source_out(s: Session, r: Source, with_stats: bool = True) -> dict:
    cfg = r.config or {}
    d = r.model_dump()
    d["enabled"] = r.status == "active"
    d["account_type"] = cfg.get("account_type") or "persona"
    d["follower_count"] = int(cfg.get("follower_count") or 0)
    d["signature"] = cfg.get("signature") or ""
    d["dismissed"] = bool(cfg.get("dismissed"))
    if with_stats and r.kind == "peer_account":
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        total = s.exec(select(func.count(Material.id)).where(  # type: ignore[arg-type]
            Material.source_id == r.id)).one() or 0  # type: ignore[attr-defined]
        recent = s.exec(select(func.count(Material.id)).where(  # type: ignore[arg-type]
            Material.source_id == r.id,  # type: ignore[attr-defined]
            Material.created_at >= week_ago)).one() or 0  # type: ignore[attr-defined]
        d["video_total"] = total
        d["video_recent_7d"] = recent
    return d


@router.get("")
def list_sources(kind: str = "", status: str = "", q: str = "",
                 account_type: str = "", user: User = Depends(require_user)) -> dict:
    """采集源列表（kind: peer_account|radar_topic|...；status: candidate|active|disabled）。"""
    with Session(engine) as s:
        qry = select(Source)
        if kind:
            qry = qry.where(Source.kind == kind)  # type: ignore[attr-defined]
        if status:
            qry = qry.where(Source.status == status)  # type: ignore[attr-defined]
        if account_type in ("persona", "leadgen"):
            from sqlalchemy import text as _text

            qry = qry.where(_text("sources.config->>'account_type' = :at").bindparams(at=account_type))
        if q:
            like = f"%{q.strip()}%"
            from sqlalchemy import or_ as _or

            qry = qry.where(_or(Source.name.ilike(like), Source.handle.ilike(like)))  # type: ignore[attr-defined]
        rows = s.exec(qry.order_by(Source.id)).all()  # type: ignore[attr-defined]
        return {"items": [_source_out(s, r) for r in rows]}


class SourceIn(BaseModel):
    kind: str = "peer_account"  # peer_account | radar_topic
    platform: str = "douyin"
    name: str
    url: str = ""
    ext_id: str = ""  # radar_topic=ch_id
    note: str = ""
    account_type: str = "persona"  # 仅 peer_account
    creator_cookie: str = ""  # 兼容旧面（self 账号不走此端点，忽略）


@router.post("", status_code=201)
def create_source(body: SourceIn, user: User = Depends(require_user)) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    if body.kind not in ("peer_account", "radar_topic", "kb_folder", "hotlist", "manual"):
        raise HTTPException(status_code=400, detail="kind 不支持")
    with Session(engine) as s:
        if body.kind == "peer_account":
            if body.platform == "douyin" and not body.url.strip():
                raise HTTPException(status_code=400,
                                    detail="抖音账号需要主页链接（App 内分享主页 → 复制链接）")
            if body.url.strip():
                require_douyin_url(body.url.strip())
            sec = _sec_of(body.url)
            if sec:
                dup = s.exec(select(Source).where(  # type: ignore[attr-defined]
                    Source.kind == "peer_account",  # type: ignore[attr-defined]
                    Source.ext_id == sec)).first()  # type: ignore[attr-defined]
                if dup is not None:
                    raise HTTPException(status_code=409,
                                        detail=f"该账号已在清单：{dup.name}（状态 {dup.status}）")
            row = Source(tenant_id=user.tenant_id, kind="peer_account",
                         platform=body.platform, name=name[:60], url=body.url.strip(),
                         ext_id=sec, handle=name[:60], status="active",
                         config={"account_type": body.account_type if body.account_type in ("persona", "leadgen") else "persona"},
                         note=body.note.strip()[:300])
        elif body.kind == "radar_topic":
            import re as _re

            ch_id = body.ext_id.strip()
            if not _re.fullmatch(r"\d+", ch_id):
                raise HTTPException(status_code=400, detail="话题必须带数字 ch_id（贴视频链接可提取）")
            dup = s.exec(select(Source).where(  # type: ignore[attr-defined]
                Source.kind == "radar_topic",  # type: ignore[attr-defined]
                Source.ext_id == ch_id)).first()  # type: ignore[attr-defined]
            if dup is not None:
                raise HTTPException(status_code=409, detail=f"话题已存在：#{dup.name}")
            row = Source(tenant_id=user.tenant_id, kind="radar_topic", platform="douyin",
                         name=name[:40], ext_id=ch_id, handle=name[:40], status="active",
                         config={"seen_ids": [], "hits": [], "notify": True})
        else:
            row = Source(tenant_id=user.tenant_id, kind=body.kind,
                         platform=body.platform, name=name[:60], status="active",
                         note=body.note.strip()[:300])
        s.add(row)
        s.commit()
        s.refresh(row)
        return _source_out(s, row, with_stats=False)


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    note: str | None = None
    enabled: bool | None = None
    account_type: str | None = None


@router.put("/{source_id}")
def update_source(source_id: int, body: SourceUpdate,
                  user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        r = s.get(Source, source_id)
        if r is None:
            raise HTTPException(status_code=404, detail="source not found")
        if r.kind == "peer_account" and body.account_type is not None:
            if body.account_type not in ("persona", "leadgen"):
                raise HTTPException(status_code=400, detail="account_type 必须是 persona / leadgen")
            cfg = dict(r.config or {})
            cfg["account_type"] = body.account_type
            r.config = cfg
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="名称不能为空")
            r.name = body.name.strip()[:60]
        if body.url is not None:
            if body.url.strip():
                require_douyin_url(body.url.strip())
            r.url = body.url.strip()
            sec = _sec_of(r.url)
            if sec:
                r.ext_id = sec
        if body.note is not None:
            r.note = body.note.strip()[:300]
        if body.enabled is not None:
            if r.status != "candidate":
                r.status = "active" if body.enabled else "disabled"
        s.add(r)
        s.commit()
        s.refresh(r)
        return _source_out(s, r)


@router.delete("/{source_id}")
def delete_source(source_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        r = s.get(Source, source_id)
        if r is None:
            raise HTTPException(status_code=404, detail="source not found")
        n_mats = s.exec(select(func.count(Material.id)).where(  # type: ignore[arg-type]
            Material.source_id == source_id)).one() or 0  # type: ignore[attr-defined]
        if n_mats:
            raise HTTPException(status_code=409,
                                detail=f"该源已有 {n_mats} 条素材——先停用（enabled=false）而非删除")
        s.delete(r)
        s.commit()
    return {"deleted": source_id}


@router.post("/{source_id}/sync", status_code=202)
async def sync_source(source_id: int, user: User = Depends(require_user)) -> dict:
    """单源同步：peer_account=单号扫描；radar_topic=即时巡检。"""
    with Session(engine) as s:
        r = s.get(Source, source_id)
        if r is None:
            raise HTTPException(status_code=404, detail="source not found")
        kind = r.kind
    if kind == "radar_topic":
        job = await runner.submit("material_topic_check", {"source_id": source_id},
                                  dedup_key=f"1:topiccheck:{source_id}",
                                  actor=(user.tenant_id, user.id, user.role))
    else:
        job = await runner.submit(
            "material_watch_scan", {"source_id": source_id},
            dedup_key=f"{user.tenant_id}:scan:{source_id}:{datetime.now():%Y%m%d}")
    return {"job_id": job.id}


@router.post("/sync-all", status_code=202)
async def sync_all(user: User = Depends(require_user)) -> dict:
    job = await runner.submit("material_watch_scan", {"auto": True},
                              dedup_key=f"{user.tenant_id}:scan:all:{datetime.now():%Y%m%d}")
    return {"job_id": job.id}


class ResolveIn(BaseModel):
    url: str


@router.post("/resolve", status_code=202)
async def resolve(body: ResolveIn, user: User = Depends(require_user)) -> dict:
    """贴链识别账号：分享口令全文可贴，反查昵称+主页。"""
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url 不能为空")
    from ..materials.download import extract_douyin_url

    url = extract_douyin_url(body.url.strip())
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    require_douyin_url(url)
    job = await runner.submit("material_resolve", {"url": url}, dedup_key=url)
    return {"job_id": job.id}


@router.get("/scan-history")
def scan_history(limit: int = 8, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        rows = s.exec(
            select(Job).where(  # type: ignore[attr-defined]
                Job.type == "material_watch_scan",  # type: ignore[attr-defined]
                Job.archived == 0,  # type: ignore[attr-defined]
            )
            .order_by(Job.id.desc())  # type: ignore[attr-defined]
            .limit(max(1, min(limit, 30)))
        ).all()
        return {"items": [r.model_dump() for r in rows]}


# ---------- 账号候选 ----------

class DiscoverIn(BaseModel):
    keyword: str
    direction: str = ""


@router.post("/discover", status_code=202)
async def discover(body: DiscoverIn, user: User = Depends(require_user)) -> dict:
    kw, direction = body.keyword.strip(), body.direction.strip()
    if not kw:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    job = await runner.submit("material_discover", {"keyword": kw, "direction": direction},
                              dedup_key=f"{user.tenant_id}:discover:{kw}:{direction}")
    return {"job_id": job.id}


@router.post("/hl-search", status_code=202)
async def hl_search(user: User = Depends(require_user)) -> dict:
    """同城组局号搜索矩阵挖掘（调度每晚 2 点自动跑；此口手动补跑）。"""
    job = await runner.submit("material_hl_search", {"auto": True},
                              dedup_key=f"1:hlsearch:{datetime.now():%Y%m%d}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


@router.post("/{source_id}/activate")
def activate_candidate(source_id: int, user: User = Depends(require_user)) -> dict:
    """候选定夺：加入采集清单（candidate → active）。"""
    with Session(engine) as s:
        r = s.get(Source, source_id)
        if r is None:
            raise HTTPException(status_code=404, detail="source not found")
        if r.status != "candidate":
            raise HTTPException(status_code=400, detail="只有候选可以加入")
        if (r.config or {}).get("dismissed"):
            raise HTTPException(status_code=400, detail="已忽略的候选不能加入，请先重新发现")
        if r.ext_id:
            dup = s.exec(select(Source).where(  # type: ignore[attr-defined]
                Source.kind == "peer_account",  # type: ignore[attr-defined]
                Source.status == "active",  # type: ignore[attr-defined]
                Source.ext_id == r.ext_id)).first()  # type: ignore[attr-defined]
            if dup is not None:
                cfg = dict(r.config or {})
                cfg["dismissed"] = True
                r.config = cfg
                s.add(r)
                s.commit()
                return {"source": _source_out(s, r), "duplicate": True}
        r.status = "active"
        cfg = dict(r.config or {})
        cfg["added_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        note_bits = []
        if cfg.get("keyword"):
            note_bits.append(f"发现自关键词「{cfg['keyword']}」")
        if cfg.get("follower_count"):
            from ..materials.executors import _fmt_follow

            note_bits.append(f"粉丝 {_fmt_follow(int(cfg['follower_count']))}")
        if cfg.get("leadgen_density"):
            note_bits.append(f"求带密度 {float(cfg['leadgen_density']):.0%}")
        if note_bits:
            r.note = (" · ".join(note_bits))[:300]
        s.add(r)
        s.commit()
        s.refresh(r)
        return {"source": _source_out(s, r), "duplicate": False}


@router.post("/{source_id}/dismiss")
def dismiss_candidate(source_id: int, user: User = Depends(require_user)) -> dict:
    """候选永久忽略（重新发现不复活）。"""
    with Session(engine) as s:
        r = s.get(Source, source_id)
        if r is None:
            raise HTTPException(status_code=404, detail="source not found")
        cfg = dict(r.config or {})
        cfg["dismissed"] = True
        r.config = cfg
        s.add(r)
        s.commit()
        s.refresh(r)
        return _source_out(s, r)


@router.delete("")
def clear_processed(user: User = Depends(require_user)) -> dict:
    """清掉已处理的候选（已忽略/已加入转正后不再需要的记录）；待定夺保留。"""
    with Session(engine) as s:
        rows = s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.status == "candidate")).all()  # type: ignore[attr-defined]
        gone = 0
        for r in rows:
            if (r.config or {}).get("dismissed"):
                s.delete(r)
                gone += 1
        s.commit()
        return {"deleted": gone}


# ---------- 话题雷达特有 ----------

class ExtractIn(BaseModel):
    url: str


@router.post("/radar-extract")
async def radar_extract(body: ExtractIn, user: User = Depends(require_user)) -> dict:
    """贴抖音视频链接 → 免登录提取话题标签（name + ch_id），供添加监测。"""
    import asyncio
    import re as _re

    url = (body.url or "").strip()
    m = _re.search(r"/video/(\d+)", url) or _re.search(r"video/(\d+)", url)
    if not m:
        raise HTTPException(status_code=400, detail="请贴抖音视频链接（含 /video/数字）")
    from ..materials.collectors.radar_patrol import _extract_hashtags

    items = await asyncio.to_thread(_extract_hashtags, m.group(1))
    return {"items": items[:12]}


@router.post("/backfill-topics", status_code=202)
async def backfill_topics(user: User = Depends(require_user)) -> dict:
    """话题存量回填：精选集全部探测→过闸建行→自动拆解。在途去重。"""
    from datetime import datetime as _dt

    job = await runner.submit("material_topic_backfill", {"auto": True},
                              dedup_key="1:material_topic_backfill",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id, "submitted_at": _dt.now().isoformat(timespec="seconds")}
