"""Content Studio FastAPI 入口：路由装配 + 执行器注册 + 前端静态托管。"""
import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api import (admin, articles, auth_api as auth_routes, avatar, benchmarks, credits_api, crawl, health, jobs, llm, poster, tenant_api,
                  public_media, publishing, radar, schedules, scripts, settings as settings_api, style,
                  topics, watch)
from .db import engine, init_db
from sqlmodel import Session, select
from .jobs.runner import recover_interrupted_jobs, runner
from .version import APP_VERSION

# 导入服务模块以注册任务执行器
from .articles import service as _article_service  # noqa: F401
from .avatar import service as _avatar_service  # noqa: F401
from .benchmarks import service as _benchmark_service  # noqa: F401
from .publishing import service as _publishing_service  # noqa: F401
from .radar import service as _radar_service  # noqa: F401
from .scripts import service as _script_service  # noqa: F401
from .topics import service as _topic_service  # noqa: F401
from .watch import service as _watch_service  # noqa: F401

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_FALLBACK_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"/><title>跃迁内容工作室</title></head>
<body style="font-family:system-ui;padding:40px;line-height:1.8">
<h1>跃迁内容工作室</h1>
<p>前端尚未构建（frontend/dist 不存在）。后端 API 已就绪：
<a href="/api/health">/api/health</a></p>
<p>构建方法：cd frontend && npm install && npm run build</p>
</body></html>"""



def _bootstrap_admin() -> None:
    """首次启动引导：无任何用户时创建租户「示例工作室」+ 管理员账号。
    初始密码随机生成并打印到日志一次，首登强制改密；
    验收隔离实例可用 CS_BOOTSTRAP_PASSWORD 指定确定性口令。"""
    from .auth import hash_password
    from .models import Tenant, User

    # 安装参数（SaaS 产品化）：租户名/管理员可经 env 覆盖，默认给中性示例值
    bt_tenant = os.environ.get("CS_BOOTSTRAP_TENANT") or "示例工作室"
    bt_admin = os.environ.get("CS_BOOTSTRAP_ADMIN") or "admin"
    bt_credits = int(os.environ.get("CS_BOOTSTRAP_CREDITS") or 100000)

    with Session(engine) as s:
        if s.exec(select(User)).first() is not None:
            return
        tenant = Tenant(name=bt_tenant, credits=bt_credits)
        s.add(tenant)
        s.commit()
        s.refresh(tenant)
        password = os.environ.get("CS_BOOTSTRAP_PASSWORD") or secrets.token_urlsafe(8)
        s.add(User(tenant_id=tenant.id, username=bt_admin,
                   password_hash=hash_password(password), role="platform_admin",
                   display_name=bt_tenant, status="active", must_change_password=False))
        s.commit()
        if os.environ.get("CS_BOOTSTRAP_PASSWORD"):
            print(f"[bootstrap] 管理员已创建：用户名 {bt_admin} / 密码取 CS_BOOTSTRAP_PASSWORD（验收模式）")
        else:
            print(f"[bootstrap] 管理员已创建：用户名 {bt_admin} / 初始密码 {password}（首次登录请修改）")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    from .settings_store import load_overrides as _load_overrides

    _load_overrides()  # 数据库里的运行时配置覆盖 .env 默认值（服务器上无需改文件）
    _bootstrap_admin()
    from .tenant_brand import seed_founder_brand as _seed_brand

    _seed_brand()  # 创始租户（id=1）一次性写入示例品牌全套（幂等；brand 已有内容则跳过）
    _script_service.seed_default_template()
    runner.start()
    await recover_interrupted_jobs()

    yield


app = FastAPI(title="跃迁内容工作室", version=APP_VERSION, lifespan=lifespan)

from .auth import require_admin_dep, require_ready_user, require_user

@app.middleware("http")
async def _security_headers(request, call_next):
    """应用级安全响应头（纵深防御）：CSP 限制资源来源——即便未来出现未净化内容，
    脚本也无法在本源执行（wechat.html 端点在响应里自设更严的 CSP，此处不覆盖）。"""
    resp = await call_next(request)
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; media-src 'self' blob:; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'self'; base-uri 'self'",
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


app.include_router(health.router)
app.include_router(credits_api.router, dependencies=[Depends(require_ready_user)])
app.include_router(crawl.router, dependencies=[Depends(require_ready_user)])
app.include_router(tenant_api.router, dependencies=[Depends(require_ready_user)])
app.include_router(poster.router, dependencies=[Depends(require_ready_user)])
# 公开路由（无登录门禁）：签名配图——公众号编辑器跨域抓图带不了 Cookie，见 api/public_media.py
app.include_router(public_media.router)
app.include_router(jobs.router, dependencies=[Depends(require_ready_user)])
app.include_router(llm.router, dependencies=[Depends(require_ready_user)])
app.include_router(settings_api.router, dependencies=[Depends(require_admin_dep)])
app.include_router(topics.router, dependencies=[Depends(require_ready_user)])
app.include_router(scripts.router, dependencies=[Depends(require_ready_user)])
app.include_router(articles.router, dependencies=[Depends(require_ready_user)])
app.include_router(avatar.router, dependencies=[Depends(require_ready_user)])
app.include_router(benchmarks.router, dependencies=[Depends(require_ready_user)])
app.include_router(publishing.router, dependencies=[Depends(require_ready_user)])
app.include_router(radar.router, dependencies=[Depends(require_ready_user)])
app.include_router(watch.router, dependencies=[Depends(require_ready_user)])
app.include_router(style.router, dependencies=[Depends(require_ready_user)])
app.include_router(schedules.router, dependencies=[Depends(require_admin_dep)])
app.include_router(admin.router, dependencies=[Depends(require_ready_user)])
app.include_router(auth_routes.router)


@app.get("/", include_in_schema=False)
def index():
    index_file = _FRONTEND_DIST / "index.html"
    if index_file.exists():
        # HTML 必须每次回源校验：bundle 带内容哈希，index.html 若被缓存会指向已删除的旧 JS
        return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
    return HTMLResponse(_FALLBACK_HTML)


_assets_dir = _FRONTEND_DIST / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")


@app.api_route("/api/{rest_of_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
               include_in_schema=False)
def api_fallback(rest_of_path: str):
    raise HTTPException(status_code=404, detail=f"unknown api route: /api/{rest_of_path}")


# 前端根级静态文件（icon.svg / favicon-*.png / manifest 等 public/ 产物）：
# 只挂 /assets 时这些文件 404（2026-10-02 线上侧栏 logo 报错根因）。挂整个 dist 且
# 必须在全部 API 路由之后——先前注册的 /、/api/*、/docs 等路由优先匹配，本挂载只兜底
# 静态文件；"/" 的 no-cache index 由上方显式路由负责（注册序在先，不被此处覆盖）。
if _FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend-root")

