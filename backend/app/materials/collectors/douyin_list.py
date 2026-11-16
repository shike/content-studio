"""抖音抓取层（零业务）：账号视频列表三条路线、作者反查、单视频作者探测。

2026-11-16 C 阶段从 service.py 拆出（只挪不改行为）。路线降级与拦截事实见 AGENTS.md
「抖音抓取事实」——改前先实测。播放数匿名恒拿不到（只有作者后台可见）。
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from ...browser import launch, ua

_LIST_LIMIT = 20          # 每账号最多看最近 N 条


def _extract_stats(a: dict) -> dict:
    """从 aweme 对象提取互动数（字段缺失容忍，全 None 时返回空 dict）。"""
    st = a.get("statistics") or a.get("stats") or {}
    if not isinstance(st, dict):
        st = {}
    out = {}
    for key, src_key in (("digg", "digg_count"), ("comment", "comment_count"),
                         ("share", "share_count"), ("collect", "collect_count")):
        v = st.get(src_key)
        if isinstance(v, int):
            out[key] = v
    return out


def _walk_awemes(obj, out: list) -> None:
    """递归收集 aweme 对象（兼容 post 接口响应与 _ROUTER_DATA 内嵌两种形态）。"""
    if isinstance(obj, dict):
        aweme_id = obj.get("aweme_id") or obj.get("id")
        author = obj.get("author")
        if isinstance(aweme_id, (str, int)) and str(aweme_id).isdigit() and (
            "desc" in obj or "statistics" in obj or "video" in obj
        ):
            sec = author.get("sec_uid") if isinstance(author, dict) else None
            out.append({"id": str(aweme_id),
                        "title": str(obj.get("desc") or "")[:80],
                        "sec_uid": sec,
                        "stats": _extract_stats(obj),
                        "ctime": obj.get("create_time") or 0})
        for v in obj.values():
            _walk_awemes(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_awemes(v, out)


def _list_author_videos_via_anchor(url: str, limit: int = _LIST_LIMIT) -> tuple[Optional[list], Optional[str], bool]:
    """锚点视频模式：贴作者任意一条视频链接，从视频页反推作者近期作品。

    免登录：detail 给作者身份，mix/aweme（合集流）吐同作者视频（实测 6+ 条）。
    返回 (entries, error, is_anchor)；is_anchor=False 表示链接不是视频链接。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright", True

    from ..download import resolve_aweme_id

    rid = resolve_aweme_id(url)
    if not rid:
        return None, None, False

    raw_mix: list = []
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
                    elif "mix/aweme" in resp.url:
                        _walk_awemes(resp.json(), raw_mix)
                except Exception:  # noqa: BLE001
                    pass

            page.on("response", on_response)
            page.goto(f"https://www.douyin.com/video/{rid}",
                      timeout=45000, wait_until="domcontentloaded")
            # 先等 detail 到手（隧道下 XHR 实测 22~23s，固定短窗=连环误判"未吐出"）
            deadline = time.time() + 60
            while not detail and time.time() < deadline:
                page.wait_for_timeout(2000)
            # 右栏合集流下滑翻页，多拿几批同作者视频（深翻页模式按 limit 加滚动次数）
            for _ in range(3 if limit <= _LIST_LIMIT else 10):
                page.mouse.wheel(0, 3200)
                page.wait_for_timeout(2200)
        except Exception as e:  # noqa: BLE001
            return None, f"浏览器路线失败: {str(e)[:120]}", True
        finally:
            browser.close()

    def _find_sec_uids(o, out: list) -> None:
        if isinstance(o, dict):
            a = o.get("author")
            if isinstance(a, dict) and a.get("sec_uid"):
                out.append(a["sec_uid"])
            for v in o.values():
                _find_sec_uids(v, out)
        elif isinstance(o, list):
            for v in o:
                _find_sec_uids(v, out)

    anchor_uids: list = []
    _find_sec_uids(detail, anchor_uids)
    sec_uid = anchor_uids[0] if anchor_uids else None
    if not sec_uid:
        return None, "视频页未吐出作者信息（可能触发验证码，可重试）", True

    # 锚点视频本身 + 合集流里同作者的视频（mix 可能混入他人，按 sec_uid 过滤；
    # stats/ctime 贯穿——回填线的拆解闸要直接吃列表数据，丢了就得逐条探测）
    entries: list[dict] = [{"id": str(rid), "title": ""}]
    for it in raw_mix:
        if it.get("sec_uid") in (None, sec_uid):
            entries.append({"id": it["id"], "title": it["title"],
                            "stats": it.get("stats") or {}, "ctime": it.get("ctime") or 0})
    seen: set = set()
    uniq = []
    for it in entries:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)
    uniq = uniq[:limit]
    if len(uniq) <= 1:
        return None, "合集流未吐出同作者视频（该作者可能无合集，建议贴主页链接）", True
    return uniq, None, True


def _list_author_videos_via_share_page(url: str, limit: int = _LIST_LIMIT) -> tuple[Optional[list], Optional[str], bool]:
    """移动分享主页路线：iesdouyin share/user 页零登录墙，拦 aweme/post 拿作品列表。"""
    from ..download import is_douyin_url

    if not is_douyin_url(url):  # SSRF 闸门：扫描任务会主动打开该 URL
        return None, "仅支持抖音链接", True
    m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", url) or re.search(r"sec_uid=([A-Za-z0-9_-]{20,})", url)
    if not m:
        return None, None, False
    sec = m.group(1)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright", True

    UA_M = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    raw = []
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            ctx = browser.new_context(user_agent=UA_M, locale="zh-CN",
                                      viewport={"width": 400, "height": 850})
            page = ctx.new_page()

            def on_response(resp):
                if "aweme/post" in resp.url:
                    try:
                        d = resp.json()
                        if d.get("aweme_list"):
                            raw.append(d)
                    except Exception:
                        pass

            page.on("response", on_response)
            page.goto(f"https://www.iesdouyin.com/share/user/{sec}",
                      timeout=45000, wait_until="domcontentloaded")
            # 滚动触发分页加载；deadline 轮询到 60s（住宅隧道实测列表 XHR 晚到，
            # 固定短窗把正常页误判成风控——2026-10-28 复盘）。
            # 2026-11-15 深翻页：此前拿到第一页就停（90 天回填吃不到 30 天前的存量）——
            # 改为滚动到凑满 limit 或连续两轮无新增（到底了）；深翻页给更长等待窗。
            deadline = time.time() + (100 if limit > _LIST_LIMIT else 60)
            prev, stall = 0, 0
            while time.time() < deadline:
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(2000)
                got = len({str(a.get("aweme_id") or "")
                           for d in raw for a in (d.get("aweme_list") or [])})
                if got >= limit:
                    break
                stall = stall + 1 if got == prev else 0
                prev = got
                if stall >= 2 and got:
                    break  # 连续两轮零新增 = 已经到底
        except Exception as e:  # noqa: BLE001
            return None, f"分享页路线失败: {str(e)[:120]}", True
        finally:
            browser.close()

    entries: list[dict] = []
    seen: set = set()
    for d in raw:
        for a in d.get("aweme_list") or []:
            aid = str(a.get("aweme_id") or "")
            if aid and aid not in seen:
                seen.add(aid)
                entries.append({"id": aid, "title": str(a.get("desc") or "")[:80],
                                "stats": _extract_stats(a),
                                "ctime": a.get("create_time") or 0})
    entries = entries[:limit]
    if not entries:
        return None, "分享页未吐出作品列表（等待窗内数据未到，可重试）", True
    return entries, None, True


def _list_account_videos_playwright(url: str) -> tuple[Optional[list], Optional[str]]:
    """真浏览器路线：打开博主主页，拦截页面自身的 aweme/post 列表（免登录）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"

    raw: list[dict] = []
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1280, "height": 900})
            page = ctx.new_page()

            def on_response(resp):
                if "aweme/v1/web/aweme/post" in resp.url:
                    try:
                        _walk_awemes(resp.json(), raw)
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_response)
            # 支持分享短链（v.douyin.com 自动 302 到主页）
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            # 下滑触发分页请求，多拿一页
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(3000)
            if not raw:
                # 兜底：主页 HTML 内嵌 _ROUTER_DATA（含首批视频）
                html = page.content()
                m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*</script>",
                              html, re.S)
                if m:
                    try:
                        _walk_awemes(json.loads(m.group(1)), raw)
                    except json.JSONDecodeError:
                        pass
        except Exception as e:  # noqa: BLE001
            return None, f"浏览器路线失败: {str(e)[:120]}"
        finally:
            browser.close()

    seen: set = set()
    entries = []
    for it in raw:
        if it["id"] not in seen:
            seen.add(it["id"])
            entries.append(it)
    if not entries:
        return None, "页面未吐出视频列表（可能触发验证码，可重试）"
    return entries[:_LIST_LIMIT], None


def _resolve_author_via_video(url: str) -> tuple[Optional[dict], Optional[str]]:
    """视频页路线：detail 接口自带作者昵称与 sec_uid，免登录 最稳。"""
    from ..download import resolve_aweme_id

    rid = resolve_aweme_id(url)
    if not rid:
        return None, "不是视频链接"
    detail: dict = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"
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
            page.goto(f"https://www.douyin.com/video/{rid}",
                      timeout=45000, wait_until="domcontentloaded")
            deadline = time.time() + 60
            while not detail and time.time() < deadline:
                page.wait_for_timeout(2000)
        except Exception as e:  # noqa: BLE001
            return None, f"视频页打开失败: {str(e)[:100]}"
        finally:
            browser.close()

    author = (detail.get("aweme_detail") or {}).get("author") or {}
    sec_uid, nickname = author.get("sec_uid"), (author.get("nickname") or "").strip()
    if not sec_uid or not nickname:
        return None, "视频页未吐出作者信息（可能触发验证码，可重试）"
    return {"name": nickname, "url": f"https://www.iesdouyin.com/share/user/{sec_uid}",
            "route": "视频链接反查（免登录）"}, None


def _resolve_author_via_share_page(url: str) -> tuple[Optional[dict], Optional[str]]:
    """分享主页路线：页面标题即「昵称的主页」，免登录。"""
    from ..download import is_douyin_url

    if not is_douyin_url(url):
        return None, "仅支持抖音链接"

    mobile_ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                 "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"
    with sync_playwright() as p:
        browser = launch(p)  # 统一出口（含可选代理）
        try:
            ctx = browser.new_context(user_agent=mobile_ua, locale="zh-CN",
                                      viewport={"width": 420, "height": 900},
                                      is_mobile=True, has_touch=True)
            page = ctx.new_page()
            page.goto(url, timeout=40000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            title = page.title()
        except Exception as e:  # noqa: BLE001
            return None, f"主页打开失败: {str(e)[:100]}"
        finally:
            browser.close()
    m = re.match(r"(.+?)的主页", title or "")
    if not m or not m.group(1).strip():
        return None, "主页标题未吐出昵称（链接可能无效）"
    return {"name": m.group(1).strip(), "url": url, "route": "分享主页标题（免登录）"}, None


def _probe_video_author(rid: str) -> tuple[Optional[dict], Optional[str]]:
    """视频页 detail 免登录 反查作者与热度。返回 (info, None) 或 (None, 错误)。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright"
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
            page.goto(f"https://www.douyin.com/video/{rid}",
                      timeout=45000, wait_until="domcontentloaded")
            deadline = time.time() + 60
            while not detail and time.time() < deadline:
                page.wait_for_timeout(2000)
        except Exception as e:  # noqa: BLE001
            return None, f"视频页打开失败: {str(e)[:100]}"
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    a = ad.get("author") or {}
    st = ad.get("statistics") or {}
    sec, name = a.get("sec_uid") or "", (a.get("nickname") or "").strip()
    if not sec or not name:
        return None, "视频页未吐出作者信息（等待窗内数据未到，可重试）"
    return {
        "name": name,
        "sec_uid": sec,
        "signature": (a.get("signature") or "").strip(),
        "follower_count": int(a.get("follower_count") or 0),
        "video_id": rid,
        "title": (ad.get("desc") or "").strip()[:80],
        "digg": int(st.get("digg_count") or 0),
        "comment": int(st.get("comment_count") or 0),
        "share": int(st.get("share_count") or 0),
        "collect": int(st.get("collect_count") or 0),
    }, None
