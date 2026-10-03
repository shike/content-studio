"""设置接口：平台运行时配置的读取与修改（存数据库 `app_settings`，非 .env）。

- `.env` 只留引导/部署项（host/port/data_dir、Cookie Secure、登录限流阈值、CS_* 环境变量）；
- 运行时可改项（LLM 通道与密钥、检索通道、ASR 模型、数字人凭证、扫描计划）存数据库：
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

_MODELS = ["glm-5", "glm-5-turbo", "glm-4.7", "glm-5.3", "glm-5.3-flash",
           "deepseek-chat", "deepseek-reasoner"]
_SEARCH_PROVIDERS = ["auto", "glm", "ddg"]
_LLM_PROVIDERS = ["glm", "deepseek"]


def _status() -> dict:
    st = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_configured": gateway.is_configured(),
        "zhipu_base_url": settings.zhipu_base_url,
        "coding_plan": "/api/coding/" in settings.zhipu_base_url,
        "search_provider": settings.search_provider,
        "search_enabled": settings.search_enabled,
        "zhipu_search_base_url": settings.zhipu_search_base_url,
        "zhipu_search_model": settings.zhipu_search_model,
        "asr_model": settings.asr_model,
        "douyin_proxy": settings.douyin_proxy,
        "watch_scan_enabled": settings.watch_scan_enabled,
        "watch_scan_hour": settings.watch_scan_hour,
        "chanjing_configured": bool(settings.chanjing_app_id and settings.chanjing_secret_key),
        "managed_keys": sorted(MANAGED),  # 可在本页修改的键（前端按需展示）
    }
    st.update(stored_secret_state())  # *_set 状态（密钥明文绝不回传）
    return st


@router.get("")
def get_settings() -> dict:
    return _status()


class UpdateIn(BaseModel):
    # 非密钥项
    llm_provider: str | None = None
    llm_model: str | None = None
    zhipu_base_url: str | None = None
    search_provider: str | None = None
    search_enabled: bool | None = None
    zhipu_search_model: str | None = None
    zhipu_search_base_url: str | None = None
    asr_model: str | None = None
    douyin_proxy: str | None = None
    watch_scan_enabled: bool | None = None
    watch_scan_hour: int | None = None
    # 密钥项（只写不读）
    zhipu_api_key: str | None = None
    deepseek_api_key: str | None = None
    chanjing_app_id: str | None = None
    chanjing_secret_key: str | None = None


@router.put("")
def update_settings(body: UpdateIn) -> dict:
    values: dict = {}

    if body.llm_provider is not None:
        if body.llm_provider not in _LLM_PROVIDERS:
            raise HTTPException(400, detail=f"未知 LLM 通道，可选：{'、'.join(_LLM_PROVIDERS)}")
        values["llm_provider"] = body.llm_provider
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
    if body.zhipu_search_model is not None:
        values["zhipu_search_model"] = body.zhipu_search_model.strip()
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
    if body.watch_scan_enabled is not None:
        values["watch_scan_enabled"] = body.watch_scan_enabled
    if body.watch_scan_hour is not None:
        if not 0 <= body.watch_scan_hour <= 23:
            raise HTTPException(400, detail="扫描时间必须是 0~23 的整数（小时）")
        values["watch_scan_hour"] = body.watch_scan_hour
    # 密钥项：空串=清除
    for key in ("zhipu_api_key", "deepseek_api_key", "chanjing_app_id", "chanjing_secret_key"):
        v = getattr(body, key)
        if v is not None:
            values[key] = v.strip()

    if values:
        save_overrides(values)  # 落库 + 立即生效（内存同步刷新）
    return _status()
