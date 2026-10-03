"""话题雷达 API：监控话题增删改查 + 手动巡检。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import runner
from ..models import RadarTopic
from ..radar.service import check_topic, ensure_seeded

router = APIRouter(prefix="/api/radar")


@router.get("")
def list_topics() -> dict:
    ensure_seeded()
    with Session(engine) as s:
        items = s.exec(select(RadarTopic).order_by(RadarTopic.id)).all()
        return {"items": [r.model_dump() for r in items]}


class ExtractIn(BaseModel):
    url: str


@router.post("/extract-hashtags", status_code=202)
async def extract_hashtags_ep(body: ExtractIn) -> dict:
    """贴视频链接 → 提取该视频携带的话题（ch_id/名称），供点选添加监控。"""
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url 不能为空")
    from ..benchmarks.downloader import is_douyin_url
    if not is_douyin_url(body.url.strip()):
        raise HTTPException(status_code=400, detail="仅支持抖音视频链接（防任意 URL 抓取）")
    job = await runner.submit("radar_extract", {"url": body.url.strip()})
    return {"job_id": job.id}


@router.post("/mine", status_code=202)
async def mine_topics() -> dict:
    """从拆解库挖掘可监控话题（探测带 # 标签的已批准视频）。"""
    from ..jobs.runner import runner
    from ..radar.service import ensure_seeded

    ensure_seeded()
    job = await runner.submit("radar_mine", {})
    return {"job_id": job.id}


class RadarIn(BaseModel):
    name: str
    ch_id: str


@router.post("", status_code=201)
def create_topic(body: RadarIn) -> dict:
    name, ch_id = body.name.strip(), body.ch_id.strip()
    if not name or not ch_id.isdigit():
        raise HTTPException(status_code=400, detail="话题名必填，ch_id 必须是纯数字")
    with Session(engine) as s:
        dup = s.exec(select(RadarTopic).where(RadarTopic.ch_id == ch_id)).first()
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"话题已在监控中：#{dup.name}")
        row = RadarTopic(ch_id=ch_id, name=name)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.model_dump()


@router.post("/check-all", status_code=202)
async def check_all() -> dict:
    """全部启用话题巡检一次（走 topic_radar 任务）。"""
    from ..jobs.runner import runner
    from ..radar.service import ensure_seeded

    ensure_seeded()
    job = await runner.submit("topic_radar", {"auto": True})
    return {"job_id": job.id}


@router.delete("/{topic_id}")
def delete_topic(topic_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(RadarTopic, topic_id)
        if row is None:
            raise HTTPException(status_code=404, detail="topic not found")
        s.delete(row)
        s.commit()
    return {"deleted": topic_id}


@router.post("/{topic_id}/toggle")
def toggle_topic(topic_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(RadarTopic, topic_id)
        if row is None:
            raise HTTPException(status_code=404, detail="topic not found")
        row.enabled = not row.enabled
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.model_dump()


@router.post("/{topic_id}/check", status_code=202)
async def check_now(topic_id: int) -> dict:
    with Session(engine) as s:
        row = s.get(RadarTopic, topic_id)
        if row is None:
            raise HTTPException(status_code=404, detail="topic not found")
    r = await check_topic(row)
    return r
