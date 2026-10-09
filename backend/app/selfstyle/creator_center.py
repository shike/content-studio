"""创作者中心登录态抓取（R9 增强）：全部视频的播放/点赞/评论/转发。

前提：用户在同行监测「我的账号」上配置 creator_cookie（creator.douyin.com 的
登录态，浏览器 F12 → Network → 复制整串 Cookie）。

路线：Playwright 注入 cookie → 打开内容管理页 → 拦页面自己的内容列表 XHR →
拿到首个请求后在页面上下文内同源重放改 cursor 翻页（同源 fetch 带站点自己的
凭据与签名，不怕接口参数变化）。cookie 失效明确报错，绝不静默回落公开路线
——两条路线数据口径不同（播放量全量 vs 点赞快照），混着刷会把新数据刷没。
"""
from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl


class CreatorCookieError(RuntimeError):
    """cookie 失效/未登录（用户需要重新导出）。"""


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


def _walk_items(o, out: list) -> None:
    """防御式挖列表：兼容 item_list / aweme_list / data.item_list 等包法。"""
    if isinstance(o, dict):
        for key in ("item_list", "aweme_list", "items"):
            v = o.get(key)
            if isinstance(v, list) and v:
                out.extend(x for x in v if isinstance(x, dict))
        for v in o.values():
            _walk_items(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_items(v, out)


def _entry_of(raw: dict) -> Optional[dict]:
    aweme_id = str(raw.get("aweme_id") or raw.get("item_id") or raw.get("id") or "").strip()
    if not aweme_id or not aweme_id.isdigit():
        return None
    stat = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else raw

    def _num(*keys: str) -> int:
        for k in keys:
            v = stat.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    title = re.sub(r"\s+", " ", str(raw.get("title") or raw.get("desc") or "")).strip()
    return {"id": aweme_id, "title": title[:120],
            "stats": {"play": _num("play_count", "watch_count"),
                      "digg": _num("digg_count", "like_count"),
                      "comment": _num("comment_count"),
                      "share": _num("share_count"),
                      "collect": _num("collect_count")}}


def _bump_cursor(source: str, cursor: int) -> str:
    """URL（GET）或表单体（POST）里的 cursor 参数替换为指定页。"""
    if "=" in source and source.lstrip().startswith(("http", "/")):
        parts = urlsplit(source)
        q = [(k, str(cursor) if k == "cursor" else v) for k, v in parse_qsl(parts.query)]
        return urlunsplit(parts._replace(query=urlencode(q)))
    q = [(k, str(cursor) if k == "cursor" else v) for k, v in parse_qsl(source)]
    return urlencode(q)


_REPLAY_JS = """
async (args) => {
  const opt = {method: args.method, headers: Object.assign({},
    args.content_type ? {"content-type": args.content_type} : {}),
    credentials: "include"};
  if (args.method === "POST" && args.body) opt.body = args.body;
  const r = await fetch(args.url, opt);
  return await r.text();
}
"""


def fetch_all_videos(cookie: str, max_pages: int = 40) -> tuple[Optional[list[dict]], Optional[str]]:
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
        return None, ("cookie 里没有 sessionid——复制的可能不是创作者中心的登录态"
                      "（要在 creator.douyin.com 登录状态下从请求头里复制）")

    captured: dict = {}      # 首个内容列表 XHR：url/method/post_data/content_type
    raw_items: list[dict] = []   # 首屏 + 翻页的全部原始条目（出口统一去重映射）
    login_wall = {"hit": False}

    def _on_response(resp):
        url = resp.url
        if "passport" in url and "login" in url:
            login_wall["hit"] = True
        if captured or "item/list" not in url or "creator" not in url:
            return
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001 非 JSON 响应忽略
            return
        probe: list = []
        _walk_items(body, probe)
        if not any(_entry_of(r) for r in probe):
            return
        captured.update({"url": url, "method": resp.request.method,
                         "post_data": resp.request.post_data or "",
                         "content_type": resp.request.headers.get("content-type", "")})
        raw_items.extend(probe)

    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
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
                    "创作者中心 cookie 失效（跳到了登录页）——请重新扫码登录后导出新 cookie")
            if not captured:
                raise CreatorCookieError(
                    "内容管理页没有吐出作品列表接口（页面结构可能变了，或 cookie 无效）")

            # 页面上下文内同源重放翻页：改 cursor 逐页拉，直到没有新条目
            method = captured["method"]
            ctype = (captured["content_type"] or "").split(";")[0]
            per_page = 20
            try:  # 从首个请求里学每页条数
                src = captured["post_data"] if method == "POST" else captured["url"]
                for k, v in parse_qsl(src):
                    if k == "count" and v.isdigit():
                        per_page = max(5, int(v))
            except Exception:  # noqa: BLE001 学不到就用 20
                pass
            known_ids = {e["id"] for e in map(_entry_of, raw_items) if e}
            for page_no in range(1, max_pages):
                cursor = page_no * per_page
                url2 = _bump_cursor(captured["url"], cursor)
                body2 = _bump_cursor(captured["post_data"], cursor) if method == "POST" else ""
                try:
                    text = page.evaluate(
                        _REPLAY_JS, {"url": url2, "method": method,
                                     "content_type": ctype or None, "body": body2})
                    batch: list = []
                    _walk_items(json.loads(text), batch)
                except Exception:  # noqa: BLE001 翻页失败以已到手的收尾
                    break
                fresh_ids = {e["id"] for e in map(_entry_of, batch) if e} - known_ids
                raw_items.extend(batch)
                if not fresh_ids:
                    break
                known_ids |= fresh_ids
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
