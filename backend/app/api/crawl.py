"""采集探针 API：GET 状态（状态文件+被动失败计数）、POST 手动立即探测。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import crawler_probe
from ..auth import require_user
from ..models import User

router = APIRouter(prefix="/api/crawl")


@router.get("/status")
def status(user: User = Depends(require_user)) -> dict:
    """最近一次探针结果 + 最近 1 小时爬取类任务失败数（被动印证）。"""
    state = crawler_probe.read_state()
    last = state.get("last") or {}
    return {
        "last": last,
        "history": state.get("history", [])[:10],
        "recent_failures": crawler_probe.recent_failure_count(60),
        "label": crawler_probe.CN_LABEL.get((last or {}).get("status", ""), "尚未探测"),
    }


@router.post("/probe")
async def probe_now(user: User = Depends(require_user)) -> dict:
    """手动立即探测（约 10~15 秒）：同时刷新状态文件，供「立即检测」按钮用。"""
    r = await crawler_probe.probe()
    return {"result": r, "label": crawler_probe.CN_LABEL.get(r["status"], r["status"])}
