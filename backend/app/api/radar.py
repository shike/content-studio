"""热点雷达·话题洞察只读接口（2026-10-30：话题雷达并入热点雷达后，仅保留查看）。"""
from fastapi import APIRouter, Depends

from ..auth import require_user
from ..db import engine
from ..models import User
from sqlmodel import Session, select

router = APIRouter(prefix="/api/radar")


@router.get("/topics")
def list_topics(user: User = Depends(require_user)) -> dict:
    """已监测话题与最近命中（每日 06:00 热点雷达自动检查写入）。"""
    from ..models import RadarTopic

    with Session(engine) as s:
        rows = s.exec(
            select(RadarTopic).order_by(RadarTopic.enabled.desc(), RadarTopic.name)  # type: ignore[attr-defined]
        ).all()
        return {"items": [{
            "id": r.id,
            "name": r.name,
            "enabled": bool(r.enabled),
            "hits": r.hits or [],
            "updated_at": r.updated_at,
        } for r in rows]}


@router.post("/topics/{topic_id}/check")
async def check_topic_now(topic_id: int, user: User = Depends(require_user)) -> dict:
    """立即拉取该话题下最新视频（免登录），刷新命中快照并按飙升逻辑自动拆解。"""
    import asyncio

    from ..models import RadarTopic
    from ..radar.service import check_topic

    with Session(engine) as s:
        row = s.get(RadarTopic, topic_id)
        if row is None:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="话题不存在")
        r = await check_topic(row)
        await asyncio.sleep(0)
    return {"name": r["name"], "new": r["new"], "spiked": r["spiked"]}


@router.post("/topics/extract")
async def extract_topics(body: dict, user: User = Depends(require_user)) -> dict:
    """贴抖音视频链接 → 免登录提取该视频的话题标签（name + ch_id），供添加监测。"""
    import asyncio
    import re as _re

    from fastapi import HTTPException

    from ..radar.service import _extract_hashtags

    url = (body or {}).get("url", "").strip()
    m = _re.search(r"/video/(\d+)", url) or _re.search(r"video/(\d+)", url)
    if not m:
        raise HTTPException(status_code=400, detail="请贴抖音视频链接（含 /video/数字）")
    items = await asyncio.to_thread(_extract_hashtags, m.group(1))
    return {"items": items[:12]}


@router.post("/topics")
def add_topic(body: dict, user: User = Depends(require_user)) -> dict:
    """添加监测话题。ch_id 来自提取结果（话题接口按 ch_id 拉视频列表）。"""
    import re as _re

    from fastapi import HTTPException

    from ..models import RadarTopic

    name = (body or {}).get("name", "").strip()
    ch_id = (body or {}).get("ch_id", "").strip()
    if not name or not _re.fullmatch(r"\d+", ch_id):
        raise HTTPException(status_code=400, detail="name 与数字 ch_id 必填")
    with Session(engine) as s:
        dup = s.exec(select(RadarTopic).where(RadarTopic.ch_id == ch_id)).first()  # type: ignore[attr-defined]
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"话题已存在：#{dup.name}")
        row = RadarTopic(name=name, ch_id=ch_id, enabled=True)
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"id": row.id, "name": row.name, "ch_id": row.ch_id}
