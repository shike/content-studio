"""平台运行时配置的数据库层：`.env` 提供引导默认值，数据库覆盖运行时可改项。

设计要点：
- 只在 DB 有该键时才覆盖内存 settings（无行 = 沿用 .env/默认值）——存量部署零迁移成本；
- 写入即落库 + 同步刷新内存（读取方全是调用时动态读 `settings.x`，无需重启）；
- 密钥类值只写不读（GET 只回报"是否已配置"），值存 data/studio.db（每日备份覆盖）。
"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from .db import engine
from .models import AppSetting
from .settings import settings

# 受管键 → 类型（str/int/bool/secret；secret 只写不读回）
MANAGED: dict[str, str] = {
    "llm_model": "str",
    "zhipu_api_key": "secret",
    "zhipu_base_url": "str",
    "search_provider": "str",
    "search_enabled": "bool",
    "zhipu_search_base_url": "str",
    "asr_model": "str",
    "douyin_proxy": "str",
    "scrape_proxy": "str",
    "douyin_ua": "str",
    "watch_scan_enabled": "bool",
    "creator_data_enabled": "bool",
    "ima_kb_enabled": "bool",
    "watch_scan_hour": "int",
    "dissect_comment_min": "int",  # 拆解闸：评论≥20 自动拆（2026-11-11 用户定版）
    "dissect_digg_min": "int",     # 拆解闸：赞≥300 自动拆（与评论线任一满足即可）
    # 选题工场（PRD R1.4，2026-11-14）
    "pool_peer_min_comments": "int",  # 一手池子线：监控号新视频评论≥此值直产池子卡
    "hook_speed_cps": "int",          # 三秒口径语速（字/秒）；字数线=3×此值
    "hook_ban_prefixes": "str",       # 钩子/主线开头词根黑名单（逗号分隔）
    "coverage_min": "str",            # 覆盖面最低档（job_narrow 恒拦）
    "judge_pass_line": "int",         # 总法官三维过线值
    "daily_card_limit": "int",        # 每日进靶子箱上限
    "jail_window_days": "int",        # 弃卡池可捞窗口（天）
    "pool_pick_max": "int",           # 二手外站精选上限
    "pool_peer_window_hours": "int",  # 池子线视频新鲜窗（小时，默认 30=只吃新视频）
}


def _coerce(kind: str, raw: str):
    v = json.loads(raw)
    if kind == "bool":
        return bool(v)
    if kind == "int":
        return int(v)
    return str(v)


def _apply(key: str, value) -> None:
    setattr(settings, key, value)
    if key == "asr_model":  # ASR 模型有进程级缓存，换模型需清缓存
        try:
            from . import asr

            asr._get_model.cache_clear()
            asr._get_small_model.cache_clear()
        except Exception as e:  # noqa: BLE001 清缓存失败不拦配置写入
            print(f"[settings] ASR 缓存清理失败: {type(e).__name__}: {e}")


def load_overrides() -> int:
    """启动时载入 DB 覆盖值（在 init_db 之后调用）。返回载入条数。"""
    n = 0
    with Session(engine) as s:
        for row in s.exec(select(AppSetting)).all():
            kind = MANAGED.get(row.key)
            if kind is None:
                continue  # 未知键（旧版本留下）忽略
            try:
                _apply(row.key, _coerce(kind, row.value))
                n += 1
            except Exception as e:  # noqa: BLE001 坏值不拦启动
                print(f"[settings] 载入 {row.key} 失败: {type(e).__name__}: {e}")
    if n:
        print(f"[settings] 已从数据库载入 {n} 项运行时配置")
    return n


def save_overrides(values: dict) -> None:
    """写入并立即生效（校验在 API 层完成）。"""
    with Session(engine) as s:
        for key, value in values.items():
            kind = MANAGED.get(key)
            if kind is None:
                continue
            raw = json.dumps(value, ensure_ascii=False)
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=raw))
            else:
                row.value = raw
                s.add(row)
        s.commit()
    for key, value in values.items():
        if key in MANAGED:
            _apply(key, value)


def stored_secret_state() -> dict:
    """密钥/凭证的'是否已配置'（不回传明文）。"""
    with Session(engine) as s:
        keys = {r.key for r in s.exec(select(AppSetting)).all()}
    return {
        "zhipu_api_key_set": bool(settings.zhipu_api_key),
        "in_db": sorted(k for k in keys if k in MANAGED),
    }
