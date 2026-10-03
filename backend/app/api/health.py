"""GET /api/health：服务、数据库、LLM、本地依赖与外部容器健康。"""
import shutil

import httpx
from fastapi import APIRouter
from sqlmodel import Session, text

from ..db import engine
from ..llm import gateway
from ..settings import settings
from ..version import APP_VERSION

router = APIRouter()


def _probe(url: str):
    """任意 HTTP 响应（含 404）即视为服务在线。"""
    try:
        httpx.get(url, timeout=1.5)
        return "ok"
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/health")
def health() -> dict:
    try:
        with Session(engine) as s:
            s.exec(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    pw_ok = False
    try:  # playwright 浏览器二进制探测：清理工具清掉缓存目录时这里立刻可见（不启动浏览器，只查路径）
        import os

        from .. import browser as _browser  # 先经 browser.py 写入 PLAYWRIGHT_BROWSERS_PATH（Mac 浏览器在项目内）
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            pw_ok = os.path.exists(p.chromium.executable_path)
    except Exception:  # noqa: BLE001
        pw_ok = False

    return {
        "status": "ok",
        "app": "content-studio",
        "version": APP_VERSION,
        "db": db_ok,
        "llm": {
            "provider": gateway.active()["provider"],
            "model": gateway.active()["model"],
            "configured": gateway.is_configured(),
        },
        "deps": {
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "docker": shutil.which("docker") is not None,
            "playwright": pw_ok,
        },
    }
