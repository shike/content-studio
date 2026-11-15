"""GET /api/health：服务、数据库、LLM 与本地浏览器依赖健康。"""
import traceback

from fastapi import APIRouter, Depends
from sqlmodel import Session, text

from ..auth import require_user
from ..db import engine
from ..llm import gateway
from ..models import User
from ..version import APP_VERSION

router = APIRouter()


@router.get("/api/debug/tasks")
async def debug_tasks(user: User = Depends(require_user)) -> dict:
    """诊断端点（管理员）：转储全部 asyncio 任务栈——定位挂死任务的卡点。"""
    import asyncio

    def _fmt(frames) -> str:
        lines = []
        for f in frames[-8:]:
            try:
                lines.append(f"{f.f_code.co_filename}:{f.f_lineno} in {f.f_code.co_name}")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(lines)

    out = []
    for t in asyncio.all_tasks():
        try:
            stack = _fmt(t.get_stack(limit=8)) if not t.done() else ""
        except Exception:  # noqa: BLE001 单任务取栈失败不炸端点
            stack = "?"
        coro = getattr(t.get_coro(), "__qualname__", "?")
        out.append({"name": t.get_name(), "coro": coro, "done": t.done(),
                    "stack": stack[-1200:]})
    return {"tasks": out}


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

        from .. import browser as _browser  # noqa: F401 副作用导入：先经 browser.py 写入 PLAYWRIGHT_BROWSERS_PATH（Mac 浏览器在项目内）
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
            "playwright": pw_ok,
        },
    }
