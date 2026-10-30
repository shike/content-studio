"""抖音采集链路探针：主动微型爬取，多样本表决识别"爬虫是否正常"。

背景（2026-09-28）：拆解/挖话题连环失败才发现出口 IP 被抖音弹登录墙——任务层报错
"页面未吐出视频数据"与浏览器坏、隧道断难区分，且没任务跑的时候完全盲。

探针 = 用与线上完全相同的 launch() 连测 SAMPLES 条真实视频页（轮换拆解库 approved，
毒视频自动冷却），等 detail XHR / _ROUTER_DATA，按页面形态分类，多样本表决：
  ok            采集正常（≥1 条通过即视为可用；detail 带"过 N/M"比例）
  captcha       全部样本撞验证码
  video_gone    全部样本指向已删除/不可见视频
  login_wall    IP 被要求登录（无登录配额用穿，等冷却或本地上传）
  proxy_down    出口隧道不通（Mac 关机 / home_proxy 挂了）
  browser_error 浏览器启动失败（内核被清等）
  no_data       页面加载了但没吐数据（风控其他形态/结构变化）

毒视频冷却：同 URL 连续 2 次探不通 → 冷却 24h（移出探测池），防止个别被平台
特殊标记的视频让探针在解封后仍持续误报（2026-10-12 单样本误报教训）。
结果落 data/crawl_probe.json，附 recent_failures（近 1h 爬取任务真实失败数，
与主动探针交叉印证）。状态翻转发 macOS 通知（服务器上静默跳过）。
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
PROBE_DEADLINE_SEC = 60  # detail 等待上限（与下载器同口径；2026-10-29 实测 XHR 可晚至 20s+）
FALLBACK_URL = "https://www.douyin.com/video/7654890394676743466"
SAMPLES = 3            # 每次探测的样本数（多样本表决，单条毒视频不再一票否决）
COOLDOWN_FAILS = 2     # 连续探不通 N 次 → 冷却
COOLDOWN_HOURS = 24    # 冷却时长

CN_LABEL = {
    "ok": "采集正常",
    "login_wall": "IP 被要求登录（无登录配额用穿）",
    "captcha": "触发验证码",
    "proxy_down": "出口隧道不通",
    "browser_error": "浏览器启动失败",
    "no_data": "页面无数据（风控其他形态）",
    "video_gone": "视频已删除或不可见",
    "network_error": "网络错误",
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


def _cooldown_map(state: dict) -> dict:
    """URL 冷却表：{url: {"n": 连续失败数, "until": 冷却截止 ISO}}。"""
    return state.get("url_cooldown") or {}


def _in_cooldown(url: str, cooldown: dict, now: datetime) -> bool:
    ent = cooldown.get(url) or {}
    until = ent.get("until") or ""
    return bool(until) and until > now.strftime("%Y-%m-%d %H:%M")


def _pick_probe_urls(count: int = SAMPLES) -> tuple[list[str], str]:
    """选 N 条拆解库 approved 视频链接（轮换+跳过冷却中的毒视频）；不足补兜底。"""
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
    state = read_state()
    cooldown = _cooldown_map(state)
    now = datetime.now(timezone(timedelta(hours=8)))
    recent = [(h.get("url")) for h in (state.get("history") or [])[:count]]
    pool = [u for u in urls if u not in recent and not _in_cooldown(u, cooldown, now)]
    if len(pool) < count:  # 轮换不够时放开"最近用过"，仍避开冷却
        pool += [u for u in urls if u not in pool and not _in_cooldown(u, cooldown, now)]
    if not pool:
        return [FALLBACK_URL] * count, "兜底探测页"
    picks = (pool * count)[:count]
    return picks, f"拆解库视频（最近 20 条轮换）"


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
    """只认可观察事实（2026-10-29 实证：captcha_container 容器在正常页 HTML 常驻，
    按 DOM 判必误报——与下载器同口径，captcha 仅标题命中才算）。"""
    title = page.title() or ""
    if re.search(r"安全验证|验证码", title):
        return "captcha"
    html = page.content()
    if "视频不存在" in html or "视频不见了" in html or "作品不见" in html:
        return "video_gone"
    return "no_data"


async def probe() -> dict:
    """跑一次探针（多样本表决）并落盘。返回本次结果 dict。"""
    proxy = (settings.scrape_proxy or "").strip()  # 匿名探针=采集链路（空=直连）
    prev = (read_state().get("last") or {}).get("status")

    if proxy and not await _proxy_alive(proxy):
        result = {"status": "proxy_down", "detail": f"出口隧道不通（{proxy}）", "url": ""}
    else:
        urls, src = _pick_probe_urls(SAMPLES)
        results = []
        for u in urls:  # 顺序轻探测（每条 ~10s，与真人浏览密度相当）
            r = await asyncio.to_thread(_probe_browser, u, src, bool(proxy))
            r["url"] = u
            results.append(r)
        ok_n = sum(1 for r in results if r["status"] == "ok")
        # 表决：≥1 条通过 = 采集可用（detail 带比例）；0 通过按主要失败形态定性
        if ok_n >= 1:
            result = {"status": "ok",
                      "detail": f"样本通过 {ok_n}/{len(results)}（{src}）", "url": urls[0]}
        else:
            bad = results[0]["status"]
            result = {"status": bad if bad != "ok" else "no_data",
                      "detail": f"全部 {len(results)} 条样本未通过（{src}）", "url": urls[0] if urls else ""}
        result["samples"] = [
            {"url": r.get("url", ""), "status": r["status"]} for r in results]

    result["at"] = datetime.now(timezone(timedelta(hours=8))).strftime("%Y/%m/%d %H:%M")
    result["recent_failures"] = recent_failure_count(60)  # 被动信号交叉印证

    # 毒视频冷却：非 ok 的 URL 连续 COOLDOWN_FAILS 次 → 冷却 24h；ok 的清零
    state = read_state()
    cooldown = _cooldown_map(state)
    now_iso = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    for smp in (result.get("samples") or []):
        u, st = smp.get("url") or "", smp.get("status")
        if not u:
            continue
        ent = cooldown.get(u) or {"n": 0}
        if st == "ok":
            cooldown.pop(u, None)
            continue
        ent["n"] = int(ent.get("n") or 0) + 1
        if ent["n"] >= COOLDOWN_FAILS:
            until = (datetime.now(timezone(timedelta(hours=8)))
                     + timedelta(hours=COOLDOWN_HOURS)).strftime("%Y-%m-%d %H:%M")
            ent["until"] = until
        cooldown[u] = ent
    state["url_cooldown"] = cooldown

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
                # 平台一致指纹（2026-10-27 定论）：Mac UA 跑 Linux=不一致指纹，
                # 正是数据被扣的原因——探针必须与任务同款平台原生身份
                ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                          viewport={"width": 1280, "height": 800})
                page = ctx.new_page()

                def on_response(resp):
                    if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                        try:
                            detail.update(resp.json())
                        except Exception:  # noqa: BLE001
                            pass

                page.on("response", on_response)
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                _t = __import__("time").time()
                while not detail and __import__("time").time() - _t < PROBE_DEADLINE_SEC:
                    page.wait_for_timeout(2000)
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
                    "self_analyze"}
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
