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
        "pool_peer_window_hours": settings.pool_peer_window_hours,
        "comment_leadgen_route_min": settings.comment_leadgen_route_min,
        "backfill_window_days": settings.backfill_window_days,
        "hot_comment_topics": settings.hot_comment_topics,
        "comment_capture_cap": settings.comment_capture_cap,
        "comment_refresh_days": settings.comment_refresh_days,
        "managed_keys": sorted(MANAGED),  # 可在本页修改的键（前端按需展示）
    }
    from ..channels.visual import default_template

    st["xhs_cover_template"] = default_template(1)
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
    pool_peer_window_hours: int | None = None  # 池子线视频新鲜窗（小时，默认 30=只吃新视频）
    comment_leadgen_route_min: int | None = None  # 评论区分型：求学占比≥此%判获客矿（默认 60）
    backfill_window_days: int | None = None    # 存量回填追溯窗（天，默认 30）
    # 热门评论（2026-11-20 用户裁决 1+2+3）
    hot_comment_topics: str | None = None      # 热评来源话题白名单（逗号分隔，空=只出监控号）
    comment_capture_cap: int | None = None     # 每视频评论存档条数（按赞排序取前 N）
    comment_refresh_days: int | None = None    # 评论存档滚动刷新窗（天）
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
                "daily_card_limit", "jail_window_days",
                # 补 2026-11-15 发现：这两键此前只在 UpdateIn 声明、PUT 从未处理（静默忽略）
                "pool_peer_window_hours", "comment_leadgen_route_min",
                "backfill_window_days"):
        v = getattr(body, key)
        if v is not None:
            if not 1 <= v <= 100000:
                raise HTTPException(400, detail=f"{key} 必须是 1~100000 的整数")
            values[key] = v
    if body.hot_comment_topics is not None:
        values["hot_comment_topics"] = ",".join(
            x.strip() for x in body.hot_comment_topics.split(",") if x.strip())
    for key, lo, hi, label in (("comment_capture_cap", 5, 200, "每视频评论存档条数"),
                               ("comment_refresh_days", 1, 90, "评论刷新窗（天）")):
        v = getattr(body, key)
        if v is not None:
            if not lo <= v <= hi:
                raise HTTPException(400, detail=f"{label}必须是 {lo}~{hi} 的整数")
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


# ── 创作手册与判卷线（R2 2026-11-22）：AppSetting 直读（hot_comment_bar 同款模式） ──

# 三线系统规范速览（只读展示，与 prompts/*.md 同步维护——改提示词时同步这里）
_PLAYBOOK_BRIEF = {
    "script": [
        "前 3 秒甩判断或喊话，禁自我介绍与铺垫——对象+立场第一句落地",
        "全文只立一个核心判断，一条只讲一个故事；结尾停在最有力的判断上，不升华不喊口号",
        "篇幅 240~320 字（50~70 秒黄金档）；一处具体到可疑的细节",
        "黑话要么换大白话、要么首次出现就解释；数据国内源优先",
        "不编造经历与数字——素材没有就不写",
        "AI 味三禁：假对比默认全禁 / 名词减法 / 旁白腔；写完过六刀自查",
        "挂热点的稿子按爆款基因模式写（范本库 few-shot 自动注入）",
    ],
    "note": [
        "标题 ≤20 字=关键词+痛点，必含搜索词；5 个候选按点击力三板斧选主",
        "正文 300~800 字，首行一句含搜索词的自然句（搜索流量入口）",
        "收藏钩子必须真实存在（清单/步骤/避坑表）；站队点鲜明禁骑墙",
        "封面大字 ≤10 字痛点外放，且与标题不重复；副标 ≤14 字",
        "标签 3~8 个；禁「宝子们/小编」腔与元叙述",
        "标题承诺正文必须兑现；不编经历/反馈/数字",
    ],
    "article": [
        "文体=公众号深度长文 1500~2500 字；标题公式=人+困境，AI/FDE+动作+结果（≤24 字）",
        "事实只来自素材——素材没有的一个字不补；引原话冒号合规",
        "选题会先行：源文抽现场，人选定后只喂该现场证据包",
        "去AI味：禁口语口号腔（劝服祈使/自问自答/第二人称）；比喻转中性术语",
        "审校两轮（事实对源/读者/去AI味）；标题三次机检重拟不合规落提示",
        "增量而非拉长；数据国内源优先",
    ],
}


@router.get("/playbook")
def get_playbook() -> dict:
    """创作手册（R2-1）：brief=系统规范速览（只读），mine=号主附加手册（可编辑，生成时注入）。"""
    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    out = {}
    with Session(engine) as s:
        for kind in ("script", "note", "article"):
            row = s.get(AppSetting, f"playbook_{kind}")
            out[kind] = {"brief": _PLAYBOOK_BRIEF[kind],
                         "mine": (row.value or "") if row else ""}
    return out


class PlaybookIn(BaseModel):
    script: str | None = None
    note: str | None = None
    article: str | None = None


@router.put("/playbook")
def put_playbook(body: PlaybookIn) -> dict:
    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    with Session(engine) as s:
        for kind, v in (("script", body.script), ("note", body.note), ("article", body.article)):
            if v is None:
                continue
            row = s.get(AppSetting, f"playbook_{kind}")
            if row is None:
                s.add(AppSetting(key=f"playbook_{kind}", value=v.strip()[:4000]))
            else:
                row.value = v.strip()[:4000]
                s.add(row)
        s.commit()
    return get_playbook()


@router.get("/judgement-lines")
def get_judgement_lines() -> dict:
    """判卷线（R2-3）：current=当前生效值（含覆盖），defaults=调研假设常数。"""
    from ..analytics.judgement import get_lines

    return {"current": get_lines(),
            "defaults": {"viral_play": 30000, "pass_play": 10000,
                         "xhs_viral_rate": 0.03, "xhs_pass_rate": 0.02,
                         "wechat_viral_rate": 0.65, "wechat_pass_rate": 0.40,
                         "wechat_dead_rate": 0.30}}


class JudgementLinesIn(BaseModel):
    lines: dict[str, float]


@router.put("/judgement-lines")
def put_judgement_lines(body: JudgementLinesIn) -> dict:
    """覆盖判卷线：只收已知七键、只收正数；判卷/范本收割/晨报立即生效。"""
    import json as _json

    from sqlmodel import Session

    from ..analytics.judgement import _LINES_DEFAULT, reset_cache
    from ..db import engine
    from ..models import AppSetting

    clean: dict[str, float] = {}
    for k, v in body.lines.items():
        if k not in _LINES_DEFAULT:
            raise HTTPException(400, detail=f"未知判卷线「{k}」")
        if not isinstance(v, (int, float)) or v <= 0 or v > 10_000_000:
            raise HTTPException(400, detail=f"判卷线「{k}」必须是正数")
        clean[k] = float(v)
    with Session(engine) as s:
        row = s.get(AppSetting, "judgement_lines")
        merged: dict = {}
        if row is not None and (row.value or "").strip():
            try:
                merged = _json.loads(row.value) or {}
            except ValueError:
                merged = {}
        merged.update(clean)
        if row is None:
            s.add(AppSetting(key="judgement_lines", value=_json.dumps(merged, ensure_ascii=False)))
        else:
            row.value = _json.dumps(merged, ensure_ascii=False)
            s.add(row)
        s.commit()
    reset_cache()
    return get_judgement_lines()


@router.get("/publish-targets")
def get_publish_targets() -> dict:
    """发布节奏目标（R3-1）：每周各平台计划发布条数（0=不限）。"""
    import json as _json

    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    targets = {"douyin_week": 0, "wechat_week": 0, "xhs_week": 0}
    with Session(engine) as s:
        row = s.get(AppSetting, "publish_targets")
    if row is not None and (row.value or "").strip():
        try:
            for k, v in (_json.loads(row.value) or {}).items():
                if k in targets and isinstance(v, (int, float)) and v >= 0:
                    targets[k] = int(v)
        except ValueError:
            pass
    return {"targets": targets}


class PublishTargetsIn(BaseModel):
    targets: dict[str, int]


@router.put("/publish-targets")
def put_publish_targets(body: PublishTargetsIn) -> dict:
    import json as _json

    from sqlmodel import Session

    from ..db import engine
    from ..models import AppSetting

    clean: dict[str, int] = {}
    for k, v in body.targets.items():
        if k not in ("douyin_week", "wechat_week", "xhs_week"):
            raise HTTPException(400, detail=f"未知节奏键「{k}」")
        if not isinstance(v, int) or not 0 <= v <= 99:
            raise HTTPException(400, detail=f"「{k}」必须是 0~99 的整数（0=不限）")
        clean[k] = v
    with Session(engine) as s:
        row = s.get(AppSetting, "publish_targets")
        if row is None:
            s.add(AppSetting(key="publish_targets", value=_json.dumps(clean, ensure_ascii=False)))
        else:
            row.value = _json.dumps(clean, ensure_ascii=False)
            s.add(row)
        s.commit()
    return get_publish_targets()
