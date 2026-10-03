"""同行监控：对标账号清单 → 扫描最新视频 → 自动排队拆解。

账号视频列表走真浏览器路线（Playwright 免登录，拦截页面自身发出的
aweme/post 请求，与单视频下载同款思路）；降级原因写进任务 notes（可见）。
视频号无网页公开接口，不支持自动扫描
（App 等外部渠道发现视频后本地上传拆解）。
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from ..browser import launch, ua
from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import register_concurrent, register_executor, runner
from ..models import BenchmarkVideo, WatchAccount, WatchCandidate

_MAX_NEW_PER_ACCOUNT = 10  # 每账号每次扫描最多排队拆解的新视频数（控 LLM 成本）
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
                        "stats": _extract_stats(obj)})
        for v in obj.values():
            _walk_awemes(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_awemes(v, out)


def _list_author_videos_via_anchor(url: str) -> tuple[Optional[list], Optional[str], bool]:
    """锚点视频模式：贴作者任意一条视频链接，从视频页反推作者近期作品。

    免登录：detail 给作者身份，mix/aweme（合集流）吐同作者视频（实测 6+ 条）。
    返回 (entries, error, is_anchor)；is_anchor=False 表示链接不是视频链接。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "未安装 playwright", True

    from ..benchmarks.downloader import resolve_aweme_id

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
            page.wait_for_timeout(5000)
            # 右栏合集流下滑翻页，多拿几批同作者视频
            for _ in range(3):
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

    # 锚点视频本身 + 合集流里同作者的视频（mix 可能混入他人，按 sec_uid 过滤）
    entries: list[dict] = [{"id": str(rid), "title": ""}]
    for it in raw_mix:
        if it.get("sec_uid") in (None, sec_uid):
            entries.append({"id": it["id"], "title": it["title"]})
    seen: set = set()
    uniq = []
    for it in entries:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)
    uniq = uniq[:_LIST_LIMIT]
    if len(uniq) <= 1:
        return None, "合集流未吐出同作者视频（该作者可能无合集，建议贴主页链接）", True
    return uniq, None, True


def _list_author_videos_via_share_page(url: str) -> tuple[Optional[list], Optional[str], bool]:
    """移动分享主页路线：iesdouyin share/user 页零登录墙，拦 aweme/post 拿作品列表。"""
    from ..benchmarks.downloader import is_douyin_url

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
            page.wait_for_timeout(4000)
            for _ in range(3):
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(2200)
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
                                "stats": _extract_stats(a)})
    entries = entries[:_LIST_LIMIT]
    if not entries:
        return None, "分享页未吐出作品列表（可能触发风控，可重试）", True
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


register_concurrent("watch_scan")
@register_executor("watch_scan")
async def watch_scan(ctx, payload: dict) -> dict:
    account_id = payload.get("account_id")
    with Session(engine) as s:
        if account_id:
            acc = s.get(WatchAccount, int(account_id))
            accounts = [acc] if acc is not None else []
        else:
            accounts = s.exec(
                select(WatchAccount).where(  # type: ignore[attr-defined]
                    WatchAccount.enabled == True,  # noqa: E712
                    WatchAccount.kind == "competitor",  # type: ignore[attr-defined]
                )
            ).all()
    if not accounts:
        raise RuntimeError("监控账号不存在或清单为空，先在「同行监测」页添加同行账号")

    douyin_accounts = [a for a in accounts if a.platform == "douyin"]
    notes: list[str] = []
    queued = 0

    with Session(engine) as s:
        known = {b.url for b in s.exec(
            select(BenchmarkVideo).where(BenchmarkVideo.source == "douyin")).all()}
        known_ids: dict[str, int] = {}
        for b in s.exec(select(BenchmarkVideo)).all():
            # source_ref 形如 douyin:{id}（老数据可能没有），统一进已知集合用于去重
            m = re.search(r"(\d{6,})", b.url or "")
            if m:
                known_ids[m.group(1)] = b.id
                known.add(f"douyin:{m.group(1)}")

    backfilled = 0  # 整个扫描任务级累计（多账号循环外初始化，防部分账号无新增时未定义）
    for i, acc in enumerate(douyin_accounts):
        ctx.set_progress(
            5 + int(85 * i / max(1, len(douyin_accounts))),
            f"扫描 {acc.name}（{i + 1}/{len(douyin_accounts)}）")
        # 列表四链路：① 锚点视频（免登录）② 移动分享主页（免登录，需 sec_uid 链接）
        # ③ PC 主页拦截。降级原因可见。
        entries, err, is_anchor = await asyncio.to_thread(_list_author_videos_via_anchor, acc.url)
        if entries is not None:
            notes.append(f"{acc.name}：锚点视频路线（免登录）")
        else:
            share_entries, share_err, _ = await asyncio.to_thread(_list_author_videos_via_share_page, acc.url)
            if share_entries is not None:
                entries, err = share_entries, None
                notes.append(f"{acc.name}：移动分享主页路线（免登录）")
            elif is_anchor is False:
                pass  # 链接不是视频页也不是分享主页 → 交给主页拦截
            else:
                notes.append(f"{acc.name}：锚点未生效（{err}）；分享主页未生效（{share_err}）")
        if entries is None and is_anchor is False:
            pw_err = err or ""
            entries, err = await asyncio.to_thread(_list_account_videos_playwright, acc.url)
            if entries is not None:
                notes.append(f"{acc.name}：主页拦截路线（锚点未生效：{pw_err}）")
        if entries is None:
            notes.append(f"{acc.name}：{err}")
            continue

        created = 0
        for e in entries:
            vid = str(e.get("id") or "")
            stats = e.get("stats") or {}
            if not vid or f"douyin:{vid}" in known:
                continue
            if vid in known_ids:
                # 已在库：顺带回填互动数（赞/评/转），扫描即刷新老视频数据
                if stats:
                    with Session(engine) as s:
                        row = s.get(BenchmarkVideo, known_ids[vid])
                        if row is not None and not row.stats:
                            row.stats = stats
                            s.add(row)
                            s.commit()
                            backfilled += 1
                continue
            if created >= _MAX_NEW_PER_ACCOUNT:
                break
            url = e.get("url") or f"https://www.douyin.com/video/{vid}"
            with Session(engine) as s:
                b = BenchmarkVideo(
                    tenant_id=acc.tenant_id,  # 父账号继承（调度上下文无 ACTOR 归属）
                    url=url,
                    author=acc.name,
                    title=str(e.get("title") or "")[:80],
                    stats=stats,
                    source="douyin",
                    scan_status="pending",  # 只发现不拆解，人工定夺后入队
                )
                s.add(b)
                s.commit()
                known_ids[vid] = b.id
            known.add(f"douyin:{vid}")
            created += 1
            queued += 1

        with Session(engine) as s:
            row = s.get(WatchAccount, acc.id)
            if row is not None:
                row.last_scan_at = datetime.now(timezone.utc)
                s.add(row)
                s.commit()
        notes.append(f"{acc.name}：本次排队 {created} 条新视频" + (f"，回填互动数 {backfilled} 条" if backfilled else ""))

    channels_count = sum(1 for a in accounts if a.platform == "channels")
    if channels_count:
        notes.append("视频号无公开网页接口，不支持自动扫描：请在 App 等外部渠道发现视频后"
                     "「本地视频拆解」上传")

    ctx.set_progress(100, f"完成：排队 {queued} 条")
    return {"queued": queued, "backfilled": backfilled, "notes": notes}


# ---------- 账号自动识别（添加账号时贴链接反查昵称与主页） ----------

def _resolve_author_via_video(url: str) -> tuple[Optional[dict], Optional[str]]:
    """视频页路线：detail 接口自带作者昵称与 sec_uid，免登录 最稳。"""
    from ..benchmarks.downloader import resolve_aweme_id

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
            page.wait_for_timeout(5000)
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
    from ..benchmarks.downloader import is_douyin_url

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


register_concurrent("watch_resolve")
@register_executor("watch_resolve")
async def watch_resolve(ctx, payload: dict) -> dict:
    raw = (payload.get("url") or "").strip()
    if not raw:
        raise RuntimeError("url 不能为空")
    from ..benchmarks.downloader import extract_douyin_url
    url = extract_douyin_url(raw)
    if url != raw:
        ctx.set_progress(10, "已从粘贴文本中提取分享链接")
    ctx.set_progress(20, "识别链接类型并打开页面")
    info, err = await asyncio.to_thread(_resolve_author_via_video, url)
    if info is None:
        ctx.set_progress(60, f"视频链接路线未生效（{err}），改走主页标题")
        info, err2 = await asyncio.to_thread(_resolve_author_via_share_page, url)
        if info is None:
            raise RuntimeError(f"无法识别账号：{err}；{err2}")
    ctx.set_progress(100, f"识别成功：{info['name']}")
    return info


# ---------- 关键词发现对标账号（DDG 挖视频链接 → 免登录 反查作者与热度） ----------
# 抖音搜索页是登录墙、按昵称反查 sec_uid 无解（AGENTS 抓取事实）；可行的公共入口是
# 搜索引擎索引的视频页：DDG `site:douyin.com/video 关键词` 吐真实视频链接（2026-09 实测），
# 视频页 detail 自带 follower_count/nickname/sec_uid——热门排序用真实粉丝数，零 LLM 成本。

_DISCOVER_SAMPLE_CAP = 10  # 最多反查的视频页数（控任务时长 ~2min）
_DISCOVER_QUALITY_FANS = 10000  # 「清单外高赞同行」质量线：候选粉丝 ≥1 万才进周报（2026-10-03）
# 每日发现的关键词轮换：一天一个词，省 DDG 配额也保证覆盖面按天展开
DISCOVER_DAILY_KEYWORDS = ["企业AI落地", "AI落地 培训", "企业AI 数字化转型", "AI 提效 获客"]
_DISCOVER_TOP_N = 8        # 最终保留的候选账号数（按粉丝数）


def _discover_queries(keyword: str, direction: str) -> list[str]:
    """账号发现的检索词组：DDG 组（site: 限定，索引准）+ 通用组（GLM 也能吃）。

    2026-09-28 修：切到 GLM 搜索后 site:douyin.com 恒空（GLM 不吃限定符、也不索引抖音站内），
    任务全部失败成"未挖到链接"。现在 site: 组只给 DDG；GLM 用自然语言组（视频页特征词）。
    """
    kw_t = keyword.strip().split()
    d_t = [t for t in (direction or "").split() if t not in kw_t]
    extra = " ".join(d_t)
    kw = " ".join(kw_t)
    qs = [f"site:douyin.com/video {kw} {extra}".rstrip(),
          f"site:douyin.com/video {kw}",
          f"{kw} 抖音 视频 分享",
          f"{kw} 抖音账号 口播"]
    seen, uniq = set(), []
    for q in qs:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


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
            page.wait_for_timeout(5000)
        except Exception as e:  # noqa: BLE001
            return None, f"视频页打开失败: {str(e)[:100]}"
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    a = ad.get("author") or {}
    st = ad.get("statistics") or {}
    sec, name = a.get("sec_uid") or "", (a.get("nickname") or "").strip()
    if not sec or not name:
        return None, "视频页未吐出作者信息（可能触发风控）"
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


def _fmt_follow(n: int) -> str:
    if n >= 10000:
        w = n / 10000
        return f"{w:.0f}万" if w >= 100 else f"{w:.1f}万"
    return str(n)


register_concurrent("watch_discover")
@register_executor("watch_discover_daily")
async def watch_discover_daily(ctx, payload: dict) -> dict:
    """每日发现清单外同行：关键词按天轮换（省 DDG 配额），产出进候选表待人工定夺。"""
    day_idx = int(datetime.now(timezone.utc).timestamp() // 86400)
    keyword = DISCOVER_DAILY_KEYWORDS[day_idx % len(DISCOVER_DAILY_KEYWORDS)]
    ctx.set_progress(1, f"今日关键词：{keyword}")
    r = await watch_discover(ctx, {"keyword": keyword})
    return {**r, "keyword": keyword}


@register_executor("watch_discover")
async def watch_discover(ctx, payload: dict) -> dict:
    keyword = (payload.get("keyword") or "").strip()
    direction = (payload.get("direction") or "").strip()
    if not keyword:
        raise RuntimeError("keyword 不能为空")
    from ..search import web_search

    # ① DDG 挖视频链接（DDG 限流狠：组间退避，单组失败如实记录不中断）
    queries = _discover_queries(keyword, direction)
    hits: dict[str, str] = {}  # aweme_id -> 标题
    notes: list[str] = []
    limited = False  # DDG 限流标记（限流是 IP 级分钟惩罚窗，本质瞬时）
    for i, q in enumerate(queries):
        ctx.set_progress(2 + int(10 * i / len(queries)), f"检索 {i + 1}/{len(queries)}")
        results, status, provider, note = await asyncio.to_thread(web_search, q, 10)
        n0 = len(hits)
        for r in results:
            m = re.search(r"douyin\.com/(?:m/)?video/(\d+)", r.get("url") or "")
            if m and m.group(1) not in hits:
                hits[m.group(1)] = (r.get("title") or "")[:80]
        got = len(hits) - n0
        notes.append(f"检索{i + 1}（{provider}/{status}）：+{got} 条视频链接")
        if not results:
            notes.append(f"检索{i + 1} 未生效：{note or status}")
            if "rate_limited" in status:
                limited = True
                notes.append("DDG 限流惩罚窗中，本次不再补发检索（越打越封），"
                             "等自动重试窗口恢复")
                break  # 限流时继续打只会延长惩罚，立刻收手
        if len(hits) >= 8:  # 够反查了就收手，省 DDG 配额（限流狠）
            notes.append(f"已有 {len(hits)} 条链接，跳过后续检索")
            break
        if i < len(queries) - 1:
            await asyncio.sleep(8)
    if not hits:
        if limited:
            raise TransientError(
                "DDG 限流惩罚窗中，各组检索都没挖到链接；任务会自动重试"
                "（约 10 分钟后，期间不再消耗检索配额），无需换词")
        raise RuntimeError(
            f"搜索引擎未挖到抖音视频链接（{'；'.join(notes)}）。可换关键词重试，"
            "或直接在 App 分享视频链接走「添加账号→贴链接识别」")
    samples = list(hits.items())[:_DISCOVER_SAMPLE_CAP]

    # ② 逐条反查作者（风控条目重试一次后跳过，跳过原因可见）
    found: dict[str, dict] = {}  # sec_uid -> info（同作者取点赞最高的样本）
    skipped = 0
    for i, (rid, title) in enumerate(samples):
        ctx.set_progress(15 + int(70 * i / len(samples)),
                         f"反查作者 {i + 1}/{len(samples)}")
        info, err = await asyncio.to_thread(_probe_video_author, rid)
        if info is None:
            await asyncio.sleep(3)
            info, err2 = await asyncio.to_thread(_probe_video_author, rid)
            if info is None:
                skipped += 1
                notes.append(f"样本 {rid} 跳过：{err2 or err}")
                continue
        if not info["title"]:
            info["title"] = title
        prev = found.get(info["sec_uid"])
        if prev is None or info["digg"] > prev["digg"]:
            found[info["sec_uid"]] = info
        await asyncio.sleep(1.5)  # 轻 pacing 降风控概率
    if not found:
        raise TransientError(
            f"{len(samples)} 个样本都未反查出作者（大概率触发风控），稍后自动重试或手动重跑")

    # ③ 粉丝数排序取 Top N → 候选表（已在清单自动标 added；已忽略的尊重「不做」不复活）
    ranked = sorted(found.values(),
                    key=lambda d: d["follower_count"], reverse=True)[:_DISCOVER_TOP_N]
    now = datetime.now(timezone.utc)
    new_cnt = added_cnt = 0
    with Session(engine) as s:
        watched_secs = set()
        for a in s.exec(select(WatchAccount).where(  # type: ignore[attr-defined]
                WatchAccount.platform == "douyin")).all():
            m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", a.url or "")
            if m:
                watched_secs.add(m.group(1))
        secs = [d["sec_uid"] for d in ranked]
        existing = {}
        for row in s.exec(select(WatchCandidate)).all():  # 表小，全量拉回按 sec 索引
            existing.setdefault(row.sec_uid, []).append(row)
        for d in ranked:
            stats_json = json.dumps({"digg": d["digg"], "comment": d["comment"],
                                     "share": d["share"], "collect": d["collect"]})
            rows = existing.get(d["sec_uid"]) or []
            open_row = next((r for r in rows if r.status == "open"), None)
            if d["sec_uid"] in watched_secs:
                if open_row is not None:  # 手动加过清单的旧候选，同步成已加入
                    open_row.status = "added"
                    open_row.updated_at = now
                    s.add(open_row)
                added_cnt += 1
                continue
            if any(r.status == "dismissed" for r in rows):
                notes.append(f"{d['name']}：此前已忽略，不再重复推荐")
                continue
            if open_row is not None:
                open_row.keyword, open_row.direction = keyword, direction
                open_row.follower_count = d["follower_count"]
                open_row.signature = d["signature"]
                open_row.sample_video_id, open_row.sample_title = d["video_id"], d["title"]
                open_row.sample_stats = stats_json
                open_row.updated_at = now
                s.add(open_row)
            else:
                s.add(WatchCandidate(
                    keyword=keyword, direction=direction, name=d["name"],
                    sec_uid=d["sec_uid"],
                    url=f"https://www.iesdouyin.com/share/user/{d['sec_uid']}",
                    signature=d["signature"], follower_count=d["follower_count"],
                    sample_video_id=d["video_id"], sample_title=d["title"],
                    sample_stats=stats_json))
            new_cnt += 1
        s.commit()

    notes.append(f"反查 {len(samples)} 个样本：{len(found)} 位作者（跳过 {skipped}），"
                 f"粉丝 Top {len(ranked)} 已入候选")
    ctx.set_progress(100, f"发现 {len(ranked)} 个候选（新增 {new_cnt}，已在清单 {added_cnt}）")
    return {"candidates": len(ranked), "new": new_cnt, "already": added_cnt,
            "skipped": skipped, "top": [f"{d['name']}（{_fmt_follow(d['follower_count'])}粉）"
                                        for d in ranked], "notes": notes}
