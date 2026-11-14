"""设置接口：平台运行时配置的读取与修改（存数据库 `app_settings`，非 .env）。

- `.env` 只留引导/部署项（data_dir、Cookie Secure、登录限流阈值、CS_* 环境变量）；
- 运行时可改项（LLM 通道与密钥、检索通道、采集出口、ASR 模型、扫描计划、拆解闸线）存数据库：
  随每日备份进快照，服务器上无需改文件；写入即生效（内存同步刷新）。
- 密钥类只写不读：GET 只回报「是否已配置」。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..llm import gateway
from ..settings import settings
from ..settings_store import MANAGED, save_overrides, stored_secret_state

router = APIRouter(prefix="/api/settings")

_MODELS = ["glm-5.3-flash", "glm-5.3"]
_SEARCH_PROVIDERS = ["auto", "glm", "ddg"]


def _status() -> dict:
    st = {
        "llm_model": settings.llm_model,
        "llm_configured": gateway.is_configured(),
        "zhipu_base_url": settings.zhipu_base_url,
        "coding_plan": "/api/coding/" in settings.zhipu_base_url,
        "search_provider": settings.search_provider,
        "search_enabled": settings.search_enabled,
        "zhipu_search_base_url": settings.zhipu_search_base_url,
        "asr_model": settings.asr_model,
        "douyin_proxy": settings.douyin_proxy,
        "scrape_proxy": settings.scrape_proxy,
        "watch_scan_enabled": settings.watch_scan_enabled,
        "watch_scan_hour": settings.watch_scan_hour,
        "dissect_comment_min": settings.dissect_comment_min,
        "dissect_digg_min": settings.dissect_digg_min,
        "pool_peer_min_comments": settings.pool_peer_min_comments,
        "hook_speed_cps": settings.hook_speed_cps,
        "hook_ban_prefixes": settings.hook_ban_prefixes,
        "coverage_min": settings.coverage_min,
        "judge_pass_line": settings.judge_pass_line,
        "daily_card_limit": settings.daily_card_limit,
        "jail_window_days": settings.jail_window_days,
        "pool_pick_max": settings.pool_pick_max,
        "managed_keys": sorted(MANAGED),  # 可在本页修改的键（前端按需展示）
    }
    st.update(stored_secret_state())  # *_set 状态（密钥明文绝不回传）
    return st


@router.get("")
def get_settings() -> dict:
    return _status()


class UpdateIn(BaseModel):
    # 非密钥项
    llm_model: str | None = None
    zhipu_base_url: str | None = None
    search_provider: str | None = None
    search_enabled: bool | None = None
    zhipu_search_base_url: str | None = None
    asr_model: str | None = None
    douyin_proxy: str | None = None
    scrape_proxy: str | None = None
    watch_scan_enabled: bool | None = None
    watch_scan_hour: int | None = None
    dissect_comment_min: int | None = None  # 拆解闸：评论≥N 自动拆（2026-11-11）
    dissect_digg_min: int | None = None     # 拆解闸：赞≥N 自动拆
    # 选题工场参数（R1.4，2026-11-14）
    pool_peer_min_comments: int | None = None  # 一手池子线：监控号评论≥N
    hook_speed_cps: int | None = None          # 三秒口径语速（字/秒），字数线=3×此值
    hook_ban_prefixes: str | None = None       # 钩子/主线开头词根黑名单（逗号分隔）
    coverage_min: str | None = None            # 覆盖面最低档 boss/middle/job_wide
    judge_pass_line: int | None = None         # 法官三维过线值（1-10）
    daily_card_limit: int | None = None        # 每日进靶子箱上限
    jail_window_days: int | None = None        # 弃卡池可捞窗口（天）
    pool_pick_max: int | None = None           # 二手外站精选上限
    pool_peer_window_hours: int | None = None  # 池子线视频新鲜窗（小时，默认 30=只吃新视频）
    comment_leadgen_route_min: int | None = None  # 评论区分型：求学占比≥此%判获客矿（默认 60）
    # 密钥项（只写不读）
    zhipu_api_key: str | None = None


@router.put("")
def update_settings(body: UpdateIn) -> dict:
    values: dict = {}

    if body.llm_model is not None:
        if body.llm_model not in _MODELS:
            raise HTTPException(400, detail=f"未知模型，可选：{'、'.join(_MODELS)}")
        values["llm_model"] = body.llm_model
    if body.zhipu_base_url is not None:
        v = body.zhipu_base_url.strip()
        if v and not v.startswith(("http://", "https://")):
            raise HTTPException(400, detail="端点必须是 http(s) URL")
        values["zhipu_base_url"] = v
    if body.search_provider is not None:
        if body.search_provider not in _SEARCH_PROVIDERS:
            raise HTTPException(400, detail=f"未知通道，可选：{'、'.join(_SEARCH_PROVIDERS)}")
        values["search_provider"] = body.search_provider
    if body.search_enabled is not None:
        values["search_enabled"] = body.search_enabled
    if body.zhipu_search_base_url is not None:
        v = body.zhipu_search_base_url.strip()
        if v and not v.startswith(("http://", "https://")):
            raise HTTPException(400, detail="检索端点必须是 http(s) URL")
        values["zhipu_search_base_url"] = v
    if body.asr_model is not None:
        if not body.asr_model.strip():
            raise HTTPException(400, detail="ASR 模型名不能为空")
        values["asr_model"] = body.asr_model.strip()
    if body.douyin_proxy is not None:
        v = body.douyin_proxy.strip()
        if v and not v.startswith(("socks5://", "socks5h://", "http://", "https://")):
            raise HTTPException(400, detail="代理需为 socks5:// 或 http(s):// 地址（空=直连）")
        values["douyin_proxy"] = v
    if body.scrape_proxy is not None:
        v = body.scrape_proxy.strip()
        if v and not v.startswith(("socks5://", "socks5h://", "http://", "https://")):
            raise HTTPException(400, detail="采集代理需为 socks5:// 或 http(s):// 地址（空=直连）")
        values["scrape_proxy"] = v
    if body.watch_scan_enabled is not None:
        values["watch_scan_enabled"] = body.watch_scan_enabled
    if body.watch_scan_hour is not None:
        if not 0 <= body.watch_scan_hour <= 23:
            raise HTTPException(400, detail="扫描时间必须是 0~23 的整数（小时）")
        values["watch_scan_hour"] = body.watch_scan_hour
    for key, label in (("dissect_comment_min", "评论线"), ("dissect_digg_min", "赞线")):
        v = getattr(body, key)
        if v is not None:
            if not 0 <= v <= 100000:
                raise HTTPException(400, detail=f"拆解闸{label}必须是 0~100000 的整数")
            values[key] = v
    for key in ("pool_peer_min_comments", "hook_speed_cps", "judge_pass_line",
                "daily_card_limit", "jail_window_days", "pool_pick_max"):
        v = getattr(body, key)
        if v is not None:
            if not 1 <= v <= 100000:
                raise HTTPException(400, detail=f"{key} 必须是 1~100000 的整数")
            values[key] = v
    if body.coverage_min is not None:
        if body.coverage_min not in ("boss", "middle", "job_wide"):
            raise HTTPException(400, detail="coverage_min 可选：boss/middle/job_wide（job_narrow 恒拦）")
        values["coverage_min"] = body.coverage_min
    if body.hook_ban_prefixes is not None:
        values["hook_ban_prefixes"] = body.hook_ban_prefixes.strip()
    # 密钥项：空串=清除
    for key in ("zhipu_api_key",):
        v = getattr(body, key)
        if v is not None:
            values[key] = v.strip()

    if values:
        save_overrides(values)  # 落库 + 立即生效（内存同步刷新）
    return _status()
