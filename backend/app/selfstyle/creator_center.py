"""创作者中心登录态抓取（R9 增强）：全部视频的播放/点赞/评论/转发。

前提：用户在同行监测「我的账号」上配置 creator_cookie（creator.douyin.com 的
登录态，扫码或 F12 导出均可）。

路线（2026-10-09 真实接口校准）：Playwright 注入 cookie → 打开内容管理页 →
拦页面自己的 work_list XHR（janus/douyin/creator/pc/work_list，GET 分页，键是
max_cursor/has_more）→ 在页面上下文内同源重放改 max_cursor 翻页（同源 fetch
带站点自己的凭据与签名，不怕参数变化）。出口随 browser.launch 统一走
douyin_proxy 住宅出口（出口 IP=家宽，与账号日常登录环境一致）。
cookie 失效明确报错，绝不静默回落公开路线——两条路线数据口径不同（播放量
全量 vs 点赞快照），混着刷会把新数据刷没。
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode


class CreatorCookieError(RuntimeError):
    """cookie 失效/未登录（用户需要重新导出）。"""

_WORK_LIST_PATH = "janus/douyin/creator/pc/work_list"


def _parse_cookie(cookie_str: str) -> list[dict]:
    """整串 Cookie 头 → playwright cookie 列表（.douyin.com 全域）。"""
    out: list[dict] = []
    for part in cookie_str.replace("\n", "").split(";"):
        if "=" not in part:
            continue
        k, _, v = part.strip().partition("=")
        if not k.strip():
            continue
        out.append({"name": k.strip(), "value": v.strip(),
                    "domain": ".douyin.com", "path": "/"})
    return out


def _entry_of(raw: dict) -> Optional[dict]:
    aweme_id = str(raw.get("aweme_id") or raw.get("item_id") or "").strip()
    if not aweme_id or not aweme_id.isdigit():
        return None
    stat = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}

    def _num(*keys: str) -> int:
        for k in keys:
            v = stat.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    title = re.sub(r"\s+", " ", str(raw.get("item_title") or raw.get("desc")
                                   or raw.get("caption") or "")).strip()
    return {"id": aweme_id, "title": title[:120],
            "stats": {"play": _num("play_count", "watch_count"),
                      "digg": _num("digg_count", "like_count"),
                      "comment": _num("comment_count"),
                      "share": _num("share_count", "forward_count"),
                      "collect": _num("collect_count")}}


def _qget(query: dict, key: str, default: str = "") -> str:
    return str(query.get(key, default))


_REPLAY_JS = """
async (args) => {
  const r = await fetch(args.url, {method: "GET", credentials: "include"});
  return await r.text();
}
"""


def fetch_all_videos(cookie: str, max_pages: int = 60) -> tuple[Optional[list[dict]], Optional[str]]:
    """拉登录账号的全部作品（含播放/点赞/评论/转发）。

    返回 (entries, error)；entries=None 时 error 给原因。cookie 失效抛
    CreatorCookieError 消息（调用方原样透出给用户，提示重新导出）。"""
    if not cookie.strip():
        return None, "未配置创作者中心 cookie"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"
    from ..browser import launch, ua

    jar = _parse_cookie(cookie)
    if not any(c["name"] in ("sessionid", "sessionid_ss") for c in jar):
        return None, ("cookie 里没有 sessionid——拿到的不像是创作者中心的登录态"
                      "（要在 creator.douyin.com 登录状态下取）")

    captured: dict = {}   # work_list 模板：base_url + query（取 count 最大的那个请求）
    state = {"max_cursor": None, "has_more": False}
    raw_items: list[dict] = []
    login_wall = {"hit": False}

    def _on_response(resp):
        url = resp.url
        if "passport" in url and "login" in url:
            login_wall["hit"] = True
        if _WORK_LIST_PATH not in url:
            return
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 非 JSON 响应忽略
            return
        query = dict(parse_qsl(url.split("?", 1)[1])) if "?" in url else {}
        items = [x for x in (body.get("aweme_list") or body.get("items") or [])
                 if isinstance(x, dict)]
        if not items and not query:
            return
        raw_items.extend(items)
        # 模板取 count 最大的请求（首屏可能先发 count=1 的探测，别拿它当翻页模板）
        try:
            cur_count = int(_qget(query, "count", "0"))
        except ValueError:
            cur_count = 0
        try:
            old_query = captured.get("query") or {}
            old_count = int(_qget(old_query, "count", "0"))
        except ValueError:
            old_count = 0
        if not captured or cur_count > old_count:
            captured.clear()
            captured.update({"base": url.split("?", 1)[0], "query": query})
            # 游标状态只从模板响应取——count=1 的探测请求会带偏翻页起点
            state["max_cursor"] = body.get("max_cursor")
            state["has_more"] = bool(body.get("has_more"))

    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理=住宅出口）
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1440, "height": 900})
            ctx.add_cookies(jar)
            page = ctx.new_page()
            page.on("response", _on_response)
            page.goto("https://creator.douyin.com/creator-micro/content/manage",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6500)
            if login_wall["hit"] and not captured:
                raise CreatorCookieError(
                    "创作者中心 cookie 失效（跳到了登录页）——请重新扫码登录再取一次")
            if not captured:
                raise CreatorCookieError(
                    "内容管理页没有吐出作品列表接口（页面结构可能变了，或 cookie 无效）")

            # 页面上下文内同源重放翻页：接口用 max_cursor 游标（响应回传下一个游标）
            query = dict(captured["query"])
            for page_no in range(max_pages):
                if not state["has_more"] or state["max_cursor"] in (None, "", 0, "0"):
                    break
                # 人味节流：每页间隔 1.2~2.0s，读自己数据也要像人翻页（风控友好）
                page.wait_for_timeout(1200 + (page_no * 797) % 800)
                query["max_cursor"] = str(state["max_cursor"])
                url2 = captured["base"] + "?" + urlencode(query)
                try:
                    text = page.evaluate(_REPLAY_JS, {"url": url2})
                    body = json.loads(text)
                except Exception:  # noqa: BLE001 翻页失败以已到手的收尾
                    break
                batch = [x for x in (body.get("aweme_list") or body.get("items") or [])
                         if isinstance(x, dict)]
                fresh = {e["id"] for e in map(_entry_of, batch) if e}
                before = len({e["id"] for e in map(_entry_of, raw_items) if e})
                raw_items.extend(batch)
                after = len({e["id"] for e in map(_entry_of, raw_items) if e})
                state["max_cursor"] = body.get("max_cursor")
                state["has_more"] = bool(body.get("has_more"))
                if after <= before:
                    break
        except CreatorCookieError:
            raise
        except Exception as e:  # noqa: BLE001
            return None, f"创作者中心路线失败: {type(e).__name__}: {str(e)[:140]}"
        finally:
            browser.close()

    uniq: dict[str, dict] = {}
    for e in map(_entry_of, raw_items):
        if e:
            uniq.setdefault(e["id"], e)
    if not uniq:
        return None, "创作者中心没有吐出任何作品（账号可能没有发布过视频，或接口结构变了）"
    return list(uniq.values()), None
