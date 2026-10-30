"""话题雷达：监控泛 AI 类抖音话题，按日巡检话题下新视频测热度。

数据源=m.douyin.com challenge/aweme 接口（免登录 直连，实测无互动统计字段），
热度代理=话题下新增视频速度；单次巡检新增 ≥5 条判为飙升，macOS 通知推送。
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone

import httpx
import re
from playwright.sync_api import sync_playwright
from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..models import RadarTopic

_UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
SEEN_CAP = 300
SPIKE_NEW = 5  # 单次巡检新增视频数达到即判飙升
PROBE_PER_CHECK = 5  # 每次巡检每话题探测的最新视频条数（每条 ~10s，控制巡检时长）
LOW_FANS = 100_000   # 低粉线：粉丝 <10 万
RATIO_MIN = 2.0      # 高赞线：赞/粉比 ≥2

SEED_TOPICS: list[tuple[str, str]] = []  # 监控话题是运营资产不入库：部署者在雷达页添加
                                         # （贴一条带话题的视频链接可自动提取 ch_id，见 extract_hashtags）


def fetch_topic_videos(ch_id: str, count: int = 20) -> list[dict]:
    """拉话题下最新视频（免登录 直连）。失败抛 TransientError 走任务重试。"""
    resp = httpx.get(
        "https://m.douyin.com/web/api/v2/challenge/aweme/",
        params={"ch_id": ch_id, "count": count},
        headers={"User-Agent": _UA_M},
        timeout=15,
    )
    if resp.status_code != 200:
        raise TransientError(f"话题接口 http {resp.status_code}")
    data = resp.json()
    if data.get("status_code") not in (0, None):
        raise TransientError(f"话题接口 status_code={data.get('status_code')}")
    out = []
    for a in data.get("aweme_list") or []:
        aid = str(a.get("aweme_id") or "")
        if aid:
            out.append({"id": aid, "desc": (a.get("desc") or "")[:60]})
    return out


def _notify(text: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "跃迁内容工作室 话题雷达"'],
            capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001 通知失败不影响巡检
        pass


def _probe_video(vid: str) -> dict:
    """免登录 探测单视频：点赞数 + 作者粉丝数（PC web detail 双字段齐备）。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

    detail: dict = {}
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                        detail.update(resp.json())
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    a = ad.get("author") or {}
    st = ad.get("statistics") or {}
    out = {
        "digg": int(st.get("digg_count") or 0),
        "followers": int(a.get("follower_count") or 0),
        "author": (a.get("nickname") or "").strip()[:24],
    }
    return out


def _merge_hits(existing: list, candidates: list[dict], cap: int = 20) -> tuple[list, int]:
    """合并低粉高赞命中：去重按视频 id，赞粉比降序，保留 cap 条。返回 (新清单, 新增数)。"""
    merged = {h["id"]: h for h in (existing or [])}
    added = 0
    for c in candidates:
        if c["id"] in merged:
            continue
        merged[c["id"]] = c
        added += 1
    out = sorted(merged.values(), key=lambda h: -h.get("ratio", 0))[:cap]
    return out, added


def _extract_hashtags(vid: str) -> list[dict]:
    """免登录：从视频 detail 的 text_extra 提取话题（ch_id + 名称）。

    这是"添加监控话题免输 ch_id"的正解——贴一条带该话题的视频即可。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

    detail: dict = {}
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                        detail.update(resp.json())
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    out: list[dict] = []
    seen: set[str] = set()
    for t in ad.get("text_extra") or []:
        hid = str(t.get("hashtag_id") or "")
        hname = str(t.get("hashtag_name") or "").strip()
        if hid.isdigit() and hname and hid not in seen:
            seen.add(hid)
            out.append({"ch_id": hid, "name": hname})
    return out




