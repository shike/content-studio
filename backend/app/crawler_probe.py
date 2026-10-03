"""抖音采集链路探针：主动微型爬取，实时识别"爬虫是否正常"。

背景（2026-09-28）：拆解/挖话题连环失败才发现出口 IP 被抖音弹登录墙——任务层报错
"页面未吐出视频数据"与浏览器坏、隧道断难区分，且没任务跑的时候完全盲。

探针 = 用与线上完全相同的 launch() 打开一条真实视频页（轮换用拆解库最近的 approved
视频，更像真人行为），等 detail XHR / _ROUTER_DATA，按页面形态分类：
  ok            采集正常
  login_wall    IP 被要求登录（无登录配额用穿，等冷却或本地上传）
  captcha       验证码挑战
  proxy_down    出口隧道不通（Mac 关机 / home_proxy 挂了）
  browser_error 浏览器启动失败（内核被清等）
  no_data       页面加载了但没吐数据（风控其他形态/结构变化）

结果落 data/crawl_probe.json（最近一次 + 历史 20 条）；状态翻转发 macOS 通知
（服务器上无 osascript 静默跳过）。探测本身零豆，对 IP 配额消耗 ≈ 一次真实浏览。
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .settings import settings

_STATE_NAME = "crawl_probe.json"
HISTORY_MAX = 20
PROBE_WAIT_MS = 7000
FALLBACK_URL = "https://www.douyin.com/video/7654890394676743466"

CN_LABEL = {
    "ok": "采集正常",
    "login_wall": "IP 被要求登录（无登录配额用穿）",
    "captcha": "触发验证码",
    "proxy_down": "出口隧道不通",
    "browser_error": "浏览器启动失败",
    "no_data": "页面无数据（风控其他形态）",
}


def _state_path() -> Path:
    return settings.data_dir / _STATE_NAME


def read_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 无历史=从未探测
        return {"last": None, "history": []}


def _write_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _notify(text: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "跃迁内容工作室 数据采集"'],
            capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001 服务器无 osascript，静默
        pass


def _pick_probe_url() -> tuple[str, str]:
    """轮换选一条拆解库 approved 视频的 douyin 链接 → (url, 来源说明)；库空用兜底。"""
    from sqlmodel import Session, select

    from .db import engine
    from .models import BenchmarkVideo

    try:
        with Session(engine) as s:
            rows = s.exec(
                select(BenchmarkVideo)
                .where(BenchmarkVideo.scan_status == "approved")  # type: ignore[attr-defined]
                .order_by(BenchmarkVideo.id.desc())  # type: ignore[attr-defined]
            ).all()
        urls = [v.url for v in rows
                if v.url and "douyin.com" in v.url][:20]
    except Exception:  # noqa: BLE001 库不可读就用兜底
        urls = []
    if not urls:
        return FALLBACK_URL, "兜底探测页"
    state = read_state()
    last_url = (state.get("last") or {}).get("url")
    # 轮换：避开上次用过的那条
    pool = [u for u in urls if u != last_url] or urls
    return pool[0], f"拆解库视频（最近 20 条轮换）"


async def _proxy_alive(proxy: str) -> bool:
    """出口隧道 TCP 探测：5 秒内连不上 = 隧道断（Mac 关机 / home_proxy 挂）。"""
    import urllib.parse

    u = urllib.parse.urlparse(proxy)
    host, port = u.hostname or "127.0.0.1", u.port or 1080
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5)
        writer.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _classify_page(page) -> str:
    html = page.content()
    if "登录后免费畅享" in html or "请先登录" in html:
        return "login_wall"
    if "captcha_container" in html or "captcha_verify" in html:
        return "captcha"
    return "no_data"


async def probe() -> dict:
    """跑一次探针并落盘。返回本次结果 dict。"""
    proxy = (settings.douyin_proxy or "").strip()
    prev = (read_state().get("last") or {}).get("status")

    if proxy and not await _proxy_alive(proxy):
        result = {"status": "proxy_down", "detail": f"出口隧道不通（{proxy}）", "url": ""}
    else:
        url, src = _pick_probe_url()
        result = await asyncio.to_thread(_probe_browser, url, src, bool(proxy))
    result["at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m/%d %H:%M")

    state = read_state()
    state["last"] = result
    state["history"] = ([result] + state.get("history", []))[:HISTORY_MAX]
    _write_state(state)

    if prev and prev != result["status"]:  # 状态翻转才推送，避免每 15 分钟轰炸
        label = CN_LABEL.get(result["status"], result["status"])
        _notify(f"抖音采集{'恢复' if result['status'] == 'ok' else '异常'}：{label}")
    return result


def _probe_browser(url: str, src: str, via_proxy: bool) -> dict:
    from playwright.sync_api import sync_playwright

    from .browser import launch, ua

    detail: dict = {}
    try:
        with sync_playwright() as p:
            browser = launch(p)
            try:
                ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                          viewport={"width": 1280, "height": 900})
                page = ctx.new_page()

                def on_response(resp):
                    if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                        try:
                            detail.update(resp.json())
                        except Exception:  # noqa: BLE001
                            pass

                page.on("response", on_response)
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(PROBE_WAIT_MS)
                if not detail:
                    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*</script>",
                                  page.content(), re.S)
                    if m:
                        try:
                            detail = json.loads(m.group(1))
                        except json.JSONDecodeError:
                            pass
                if detail:
                    return {"status": "ok", "detail": f"视频页正常吐数据（{src}）", "url": url}
                return {"status": _classify_page(page), "detail": f"页面无数据（{src}）", "url": url}
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001 启动/导航失败按层归类
        msg = f"{type(e).__name__}: {str(e)[:100]}"
        if "Executable" in msg or "browserType.launch" in msg:
            return {"status": "browser_error", "detail": msg, "url": url}
        if "NET" in msg.upper() or "TIMEOUT" in msg.upper():
            return {"status": "network_error", "detail": msg, "url": url}
        return {"status": "browser_error", "detail": msg, "url": url}


def recent_failure_count(minutes: int = 60) -> int:
    """被动信号：最近 N 分钟爬取类任务的失败数（读库统计，零成本印证）。"""
    from sqlmodel import Session, select

    from .db import engine
    from .models import Job

    scrape_types = {"benchmark_analyze", "watch_scan", "watch_resolve",
                    "radar_extract", "radar_mine", "self_analyze"}
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=minutes)
    try:
        with Session(engine) as s:
            rows = s.exec(select(Job).where(
                Job.status == "failed",  # type: ignore[attr-defined]
                Job.updated_at >= since,  # type: ignore[attr-defined]
            )).all()
        return sum(1 for j in rows if j.type in scrape_types)
    except Exception:  # noqa: BLE001 统计失败不影响探针
        return 0
