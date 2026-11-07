"""同行监控：对标账号清单 → 扫描最新视频 → 自动排队拆解。

账号视频列表走真浏览器路线（Playwright 免登录，拦截页面自身发出的
aweme/post 请求，与单视频下载同款思路）；降级原因写进任务 notes（可见）。
视频号无网页公开接口，不支持自动扫描
（App 等外部渠道发现视频后本地上传拆解）。
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import subprocess
import time
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from ..browser import launch, ua
from ..db import engine
from ..llm import gateway
from ..prompts import render
from ..settings import settings
from ..jobs.errors import TransientError
from ..jobs.runner import register_concurrent, register_executor, runner
from ..models import BenchmarkVideo, HarvestProfile, Topic, WatchAccount, WatchCandidate

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
            # 先等 detail 到手（隧道下 XHR 实测 22~23s，固定短窗=连环误判"未吐出"）
            deadline = time.time() + 60
            while not detail and time.time() < deadline:
                page.wait_for_timeout(2000)
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
            # 滚动触发分页加载；deadline 轮询到 60s（住宅隧道实测列表 XHR 晚到，
            # 固定短窗把正常页误判成风控——2026-10-28 复盘）
            deadline = time.time() + 60
            while not raw and time.time() < deadline:
                page.mouse.wheel(0, 2400)
                page.wait_for_timeout(2000)
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
    entries = entries[:_LIST_LIMIT]
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
        new_row_ids: list[int] = []
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
                    account_type=acc.account_type,  # 两类号策略：拆解 prompt 按账号类型分叉
                    scan_status="pending",  # 只发现不拆解，人工定夺后入队
                )
                s.add(b)
                s.commit()
                known_ids[vid] = b.id
                new_row_ids.append(b.id)
            known.add(f"douyin:{vid}")
            created += 1
            queued += 1

        with Session(engine) as s:
            row = s.get(WatchAccount, acc.id)
            if row is not None:
                row.last_scan_at = datetime.now(timezone.utc)
                s.add(row)
                s.commit()
        notes.append(f"{acc.name}：新入库 {created} 条（待定夺，批准后进拆解队列）"
                     + (f"，回填互动数 {backfilled} 条" if backfilled else ""))

    # 质量闸自动拆解（2026-10-28 全自动化裁定）：达标新作品自动批准进拆解队列，
    # 其余保持待定夺。人的角色后移到选题库定审，拆解库日常不用看。
    if new_row_ids:
        try:
            from ..benchmarks import service as bms

            rows = []
            with Session(engine) as s:
                rows = [s.get(BenchmarkVideo, i) for i in new_row_ids]
            rows = [r for r in rows if r is not None]
            n_auto = await bms.auto_dissect_rows(rows, "同行扫描")
            if n_auto:
                notes.append(f"质量闸自动拆解 {n_auto} 条（达标自动批准，超出门槛的其余保留待定夺）")
        except Exception as e:  # noqa: BLE001 自动拆解失败不影响扫描本身
            notes.append(f"质量闸自动拆解失败：{type(e).__name__}: {str(e)[:80]}")

    channels_count = sum(1 for a in accounts if a.platform == "channels")
    if channels_count:
        notes.append("视频号无公开网页接口，不支持自动扫描：请在 App 等外部渠道发现视频后"
                     "「本地视频拆解」上传")

    ctx.set_progress(100, f"完成：新发现 {queued} 条")
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
# 每日发现的关键词轮换：一天一个词，省搜索配额也保证覆盖面按天展开。
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


def _fmt_follow(n: int) -> str:
    if n >= 10000:
        w = n / 10000
        return f"{w:.0f}万" if w >= 100 else f"{w:.1f}万"
    return str(n)


register_concurrent("watch_discover")
# ---------- 获客信号：评论区「求带密度」（2026-10-28 两类号策略） ----------
# 高质量获客号的硬信号不在粉丝数，在评论区：一堆"怎么报名/求带/私信怎么弄"。
# 视频页的评论 XHR（comment/list）免登录可拦，取首屏评论文本算求带占比。

_LEADGEN_KEYWORDS = ("求带", "报名", "怎么报名", "怎么学", "哪里学", "求教程", "私信",
                     "扣1", "扣个1", "怎么做", "怎么弄", "带带我", "想学", "请教", "怎么联系")
LEADGEN_DENSITY_MIN = 0.08  # 求带密度 ≥8% 判获客型
_LEADGEN_PROBE_CAP = 3      # 每轮发现最多探测 3 条样本（控时长 ~1 分钟）


def _probe_leadgen_signal(video_id: str) -> dict:
    """打开视频页拦评论 XHR，返回 {comments, leadgen, density}。失败抛 RuntimeError。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

    comments: list[str] = []
    try:
        with sync_playwright() as p:
            browser = launch(p)
            try:
                ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                          viewport={"width": 1280, "height": 900})
                page = ctx.new_page()

                def on_response(resp):
                    try:
                        if "aweme/v1/web/comment/list/" in resp.url and len(comments) < 60:
                            d = resp.json()
                            for c in (d.get("comments") or [])[:40]:
                                t = (c.get("text") or "").strip()
                                if t:
                                    comments.append(t)
                    except Exception:  # noqa: BLE001
                        pass

                page.on("response", on_response)
                page.goto(f"https://www.douyin.com/video/{video_id}",
                          timeout=45000, wait_until="domcontentloaded")
                # 评论面板不保证随页面自动加载（2026-11-02 服务器实测：需一次滚动才
                # 触发 comment/list），等不到就滚动唤醒，60s 截止轮询。
                deadline = time.monotonic() + 60
                while not comments and time.monotonic() < deadline:
                    page.wait_for_timeout(3000)
                    if not comments and time.monotonic() < deadline:
                        try:
                            page.mouse.wheel(0, 600)
                            page.wait_for_timeout(1500)
                            page.mouse.wheel(0, -600)
                        except Exception:  # noqa: BLE001
                            break  # 页面已跳转/关闭，别空转
                ctx.close()
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"评论页打开失败: {str(e)[:80]}")
    if not comments:
        raise RuntimeError("未拦到评论（评论区可能已关闭或接口未触发）")
    matched = [c for c in comments if any(k in c for k in _LEADGEN_KEYWORDS)]
    leadgen = len(matched)
    density = round(leadgen / len(comments), 2)
    return {"comments": len(comments), "leadgen": leadgen, "density": density,
            "matched": matched[:8]}


@register_executor("watch_discover")
async def watch_discover(ctx, payload: dict) -> dict:
    keyword = (payload.get("keyword") or "").strip()
    direction = (payload.get("direction") or "").strip()
    rank_by = str(payload.get("rank_by") or "followers")  # followers | comment（获客挖掘：评论数=转化意图）
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
            f"{len(samples)} 个样本都未反查出作者（等待窗内数据未到，已放宽等待后仍如此请检查出口延迟），稍后自动重试或手动重跑")

    # ③ 粉丝数排序取 Top N → 候选表（已在清单自动标 added；已忽略的尊重「不做」不复活）
    ranked = sorted(found.values(),
                    key=lambda d: d["follower_count"], reverse=True)[:_DISCOVER_TOP_N]
    # 获客信号：点赞 Top3 探评论区求带密度——高密度=高质量获客号（两类号策略 2026-10-28）
    _rank_key = (lambda d: d["comment"]) if rank_by == "comment" else (lambda d: d["follower_count"])
    for d in sorted(ranked, key=_rank_key, reverse=True)[:_LEADGEN_PROBE_CAP]:
        try:
            sig = await asyncio.to_thread(_probe_leadgen_signal, d["video_id"])
            d["leadgen_density"] = sig["density"]
            if sig["density"] >= LEADGEN_DENSITY_MIN:
                d["account_type"] = "leadgen"
                notes.append(f"{d['name']}：求带密度 {sig['density']:.0%}"
                             f"（{sig['leadgen']}/{sig['comments']} 条）——判获客型")
        except Exception as e:  # noqa: BLE001 探测失败不挡发现
            notes.append(f"{d['name']}：获客信号探测跳过（{str(e)[:50]}）")
    ranked.sort(key=lambda d: (d.get("account_type") == "leadgen",
                               d.get("leadgen_density", 0),
                               _rank_key(d)), reverse=True)
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
                open_row.account_type = d.get("account_type", "persona")
                open_row.leadgen_density = d.get("leadgen_density", 0)
                open_row.updated_at = now
                s.add(open_row)
            else:
                s.add(WatchCandidate(
                    keyword=keyword, direction=direction, name=d["name"],
                    sec_uid=d["sec_uid"],
                    url=f"https://www.iesdouyin.com/share/user/{d['sec_uid']}",
                    signature=d["signature"], follower_count=d["follower_count"],
                    sample_video_id=d["video_id"], sample_title=d["title"],
                    sample_stats=stats_json,
                    account_type=d.get("account_type", "persona"),
                    leadgen_density=d.get("leadgen_density", 0)))
            new_cnt += 1
        s.commit()

    notes.append(f"反查 {len(samples)} 个样本：{len(found)} 位作者（跳过 {skipped}），"
                 f"粉丝 Top {len(ranked)} 已入候选")
    ctx.set_progress(100, f"发现 {len(ranked)} 个候选（新增 {new_cnt}，已在清单 {added_cnt}）")
    return {"candidates": len(ranked), "new": new_cnt, "already": added_cnt,
            "skipped": skipped, "top": [f"{d['name']}（{_fmt_follow(d['follower_count'])}粉）"
                                        for d in ranked], "notes": notes}

# ---- 获客号挖掘 v2（2026-10-31 用户定版：求带评论直接搜 + 近期多条同类验证）----

_LEADGEN_HUNT_SEED_CAP = 16      # 每轮最多扫的种子视频数（控时长）
_LEADGEN_HUNT_MIN_COMMENTS = 3   # 求带评论绝对数门槛：≥3 条判获客候选
_PATROL_BUSINESS_WORDS = ("AI", "人工智能", "大模型", "数字化", "智能", "FDE", "OPC",
                          "自动化", "获客", "落地", "降本", "提效")


def _count_recent_same(entries: list[dict], days: int = 30) -> int:
    """近 N 天内标题命中业务词的作品数（entries 最新在前，带 ctime）。"""
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    n = 0
    for e in entries:
        ct = e.get("ctime") or 0
        if ct and float(ct) < cutoff:
            continue
        title = (e.get("title") or "").lower()
        if any(w.lower() in title for w in _PATROL_BUSINESS_WORDS):
            n += 1
    return n


# ---- 纯获客号挖掘（2026-11-02 用户定版；判定标准 V4：入表一律 LLM 终审）----
# 用户标尺：暖暖AI赋能 297 粉（签名挂报名微信号）——纯获客号越纯越可能是小号，
# 粉丝/赞/求带评论全不参与判定。
# 迭代史：V1/V2 只看转化链路混进船锚厂家/摄影号/广告老板号；V3 加赛道闸但词表仍
# 有入表权，高崇耕（纯内容干货号）靠"落地/企业ai"话题词碰撞直通入表，智典（诊断
# 钩子服务商）靠我把"咨询诊断"写进教培面混进来。V4 根治（2026-11-03 用户拍板
# "只认教/带人形态"）：**词表降级为初筛（只决定要不要解析+进存档），入表唯一
# 门槛 = LLM 终审**——语义读完整签名，不存在话题词/生意词碰撞问题。

_HARVEST_AI_WORDS = ("人工智能", "aigc", "gpt", "大模型", "智能体", "数字员工", "fde")
# 初筛教培词（宽收召回：初筛错放由 LLM 终审拦，初筛错拒本来就走 pending 复判）
_HARVEST_EDU_WORDS = ("培训", "课程", "训练营", "训练", "陪跑", "带教", "学员", "教练",
                      "招生", "组局", "教学", "讲课", "干货", "操盘手", "私教", "导师",
                      "开课", "内训")
_HARVEST_CONTACT_WORDS = ("微信", "加V", "V信", "weixin", "报名", "留电", "☎", "电话", "私信",
                          "领取", "领资料", "加我", "联系", "wx", "V:", "1v1", "加好友")
_HARVEST_BIZ_WORDS = ("组局", "沙龙", "社群", "同城", "训练营", "陪跑", "咨询", "课程", "培训",
                      "学院", "商学院", "招生", "诊断", "带教", "接单", "承接", "获客", "引流",
                      "落地", "合伙人", "代理", "创业", "圈主", "赋能", "ai落地", "数字化转型",
                      "实体", "老板", "操盘", "企业ai", "fde", "线下", "私域")
_HARVEST_SEEN_KEY = "harvest_seen_secs"
_HARVEST_PER_RUN = 30     # 每轮解析主页上限（profile/other 连续解析 ~60 会开始空响应）
_HARVEST_VIDEOS = 5       # 每轮种子视频数（控时长）
_HARVEST_SEEN_CAP = 3000
_HARVEST_LLM_BATCH = 20   # 每轮 LLM 复判条数（一次调用批量判）


def _harvest_budget(key: str, default: int) -> int:
    """战役模式预算覆盖：app_settings.harvest_videos / harvest_profiles 可临时调大
    （挖号冲刺期用），不设或非法回落默认小预算（夜间常态挖不动太快会撞限速）。"""
    from ..models import AppSetting
    k = f"harvest_{key}"
    with Session(engine) as s:
        row = s.get(AppSetting, k)
    try:
        v = int(str(row.value)) if row is not None else default
    except (TypeError, ValueError):
        v = default
    return max(1, min(v, 200))


def judge_leadgen_account(nick: str, sig: str) -> tuple[bool, bool, str]:
    """V4 初筛（无入表权，只决定解析优先级）：返回 (初筛纯, 强信号, 依据)。

    三段：①AI 生态词（船锚厂家/广告公司死这）②教/带人形态词（服务商/内容号降权）
    ③转化链路（联系方式必须与 AI 教育形态同时在场）。初筛纯 → 存档标 lexicon
    优先送 LLM 终审；初筛不过 → pending 排后，终审兜底教培变体写法。"""
    text = f"{nick} {sig or ''}"
    low = text.lower()
    ai_hit = [w for w in _HARVEST_AI_WORDS if w in low] or (
        ["ai"] if re.search(r"(?<![a-z0-9])ai(?![a-z0-9])", low) else [])
    if not ai_hit:
        return False, False, "非AI赛道（签名/昵称无AI生态词）"
    edu = [w for w in _HARVEST_EDU_WORDS if w in low]
    if not edu:
        return False, False, "AI赛道但非教/带人形态（服务商/内容号）"
    contact = [w for w in _HARVEST_CONTACT_WORDS if w.lower() in low]
    biz = [w for w in _HARVEST_BIZ_WORDS if w.lower() in low]
    if contact:
        return True, True, f"初筛纯·转化入口（{'、'.join(contact[:3])}｜教词：{'、'.join(edu[:3])}）"
    if len(biz) >= 2:
        return True, False, f"初筛纯·生意形态（{'、'.join(biz[:3])}）"
    if len(biz) == 1:
        return False, False, f"单一生意词（{biz[0]}），疑似不足"
    return False, False, "无获客信号"


def _load_harvest_seen() -> set[str]:
    from ..models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, _HARVEST_SEEN_KEY)
    try:
        return set(json.loads(row.value)) if row is not None and row.value else set()
    except Exception:  # noqa: BLE001 损坏即重建
        return set()


def _save_harvest_seen(seen: set[str]) -> None:
    from ..models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, _HARVEST_SEEN_KEY)
        val = json.dumps(sorted(seen)[-_HARVEST_SEEN_CAP:])
        if row is None:
            s.add(AppSetting(key=_HARVEST_SEEN_KEY, value=val))
        else:
            row.value = val
            s.add(row)
        s.commit()


def _harvest_video_commenters(vid: str, seen: set[str]) -> list[tuple[str, str]]:
    """开视频页拦 comment/list（滚动唤醒+面板滚动），收评论者 sec_uid（接口只给 sec+昵称）。"""
    from playwright.sync_api import sync_playwright

    got: list[tuple[str, str]] = []
    with sync_playwright() as p:
        browser = launch(p)
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1380, "height": 940})
            page = ctx.new_page()

            def on_resp(r):
                if "aweme/v1/web/comment/list/" in r.url:
                    try:
                        for c in (r.json().get("comments") or []):
                            u = c.get("user") or {}
                            if u.get("sec_uid") and u["sec_uid"] not in seen:
                                seen.add(u["sec_uid"])
                                got.append((u["sec_uid"], (u.get("nickname") or "").strip()[:24]))
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_resp)
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            # 评论面板容器有自己的滚动：悬停后滚 8 屏翻页（每页 5 条）
            for sel in ['[data-e2e="comment-list"]', ".comment-mainContent", 'div[class*="comment"]']:
                loc = page.locator(sel)
                if loc.count() > 0:
                    try:
                        loc.first.hover(timeout=3000)
                        for _ in range(8):
                            page.mouse.wheel(0, 900)
                            page.wait_for_timeout(2500)
                        break
                    except Exception:  # noqa: BLE001
                        continue
        finally:
            browser.close()
    return got


def _resolve_douyin_profile(sec: str) -> tuple[Optional[dict], Optional[list[dict]]]:
    """PC 主页拦 user/profile/other 换全资料 + aweme/post 拿最新作品（限速由调用方控制）。"""
    from playwright.sync_api import sync_playwright

    cur: dict = {"u": None, "posts": []}
    with sync_playwright() as p:
        browser = launch(p)
        try:
            ctx = browser.new_context(user_agent=ua(), locale="zh-CN",
                                      viewport={"width": 1380, "height": 940})
            page = ctx.new_page()

            def on_resp(r):
                if "aweme/v1/web/user/profile/other/" in r.url:
                    try:
                        u = (r.json().get("user") or {})
                        if u.get("sec_uid"):
                            cur["u"] = {
                                "sec": u["sec_uid"],
                                "nick": (u.get("nickname") or "").strip()[:24],
                                # 截 220：联系入口常落在签名尾部（凯凯案例 100 字截断正好切掉 🌐微信号）
                                "sig": (u.get("signature") or "").strip()[:220],
                                "fans": int(u.get("follower_count") or 0),
                                "works": int(u.get("aweme_count") or 0),
                                "ip": (u.get("ip_location") or "").strip()[:12],
                            }
                    except Exception:  # noqa: BLE001
                        pass
                elif "aweme/v1/web/aweme/post/" in r.url:
                    try:
                        for a in (r.json().get("aweme_list") or [])[:3]:
                            cur["posts"].append({"id": str(a.get("aweme_id") or ""),
                                                 "title": (a.get("desc") or "").strip()[:40]})
                    except Exception:  # noqa: BLE001
                        pass

            page.on("response", on_resp)
            page.goto(f"https://www.douyin.com/user/{sec}",
                      timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(4500)
        finally:
            browser.close()
    return cur.get("u"), cur.get("posts") or None


def resolve_author_by_share_url(url: str) -> tuple[Optional[dict], Optional[str]]:
    """贴链判号入口的解析：v.douyin.com 短链 / 视频 / 主页分享链接 → 作者全资料。"""
    import httpx as _hx

    try:
        r = _hx.get(url.strip(), follow_redirects=True, timeout=20,
                    headers={"User-Agent": ua()})
        final = str(r.url)
    except Exception as e:  # noqa: BLE001
        return None, f"链接打不开：{type(e).__name__}"
    m = re.search(r"/video/(\d{6,})", final) or re.search(r"share/video/(\d{6,})", final)
    if m:
        info, err = _probe_video_author(m.group(1))
        return info, err
    m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", final) or \
        re.search(r"/user/([A-Za-z0-9_-]{30,})", final) or \
        re.search(r"sec_uid=([A-Za-z0-9_-]{20,})", final)
    if not m:
        return None, "链接里没有视频 id 或主页 sec_uid（请在 App 里分享主页或视频）"
    sec = m.group(1)
    info, _posts = _resolve_douyin_profile(sec)
    if info is None:
        return None, "主页资料未取到（等待窗内数据未到，可重试）"
    return info, None

_HL_CITIES = ["苏州", "上海", "杭州", "南京", "无锡", "宁波", "温州", "常州", "南通",
              "嘉兴", "绍兴", "湖州", "金华", "台州", "昆山", "吴江"]
_HL_KEYWORDS = ["线下沙龙", "组局", "AI沙龙", "同城聚会", "主理人", "AI活动", "创业沙龙",
                "读书会", "女性创业", "破圈", "私董会", "资源对接", "AI互助", "同城创业",
                "AI圈子", "饭局"]
_HL_SEARCH_BUDGET = 12      # 每晚搜索数（全矩阵 ~9 晚轮完，账号会长新）
_HL_VERIFY_BUDGET = 25      # 每晚取证作者数
_HL_SEEN_KEY = "hl_search_seen"
_HL_SEEN_CAP = 4000
_HL_MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1")
_HL_ACT = ["沙龙", "组局", "聚会", "线下", "圈子", "社群", "主理人", "读书会", "共创",
           "同城", "活动", "交流"]
_HL_SIGNUP = ["报名", "➕", "☎", "电话", "微信", "加V", "wx", "私信", "加我", "领取", "留电", "VX"]
_HL_TOPIC = ["AI", "Ai", "ai", "人工智能", "创业", "副业", "成长", "IP", "赚钱", "生意",
             "财商", "认知", "链接", "资源", "搞钱", "自由职业", "出海"]
_HL_NEG = ["金价", "美业", "护肤", "探店", "好物", "穿搭", "美食", "带货", "橱窗",
           "餐饮", "婚纱", "装修", "驾校", "海鲜", "黄金"]


def _hl_setting(key: str, default: list[str]) -> list[str]:
    from ..models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, key)
    try:
        v = json.loads(row.value) if row is not None and row.value else default
        return v if isinstance(v, list) and v else default
    except Exception:  # noqa: BLE001
        return default


def _hl_get_cursor() -> list[str]:
    from ..models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, "hl_search_cursor")
    try:
        v = json.loads(row.value) if row is not None and row.value else []
        return v if isinstance(v, list) else []
    except Exception:  # noqa: BLE001
        return []


def _hl_save_cursor(done: list[str]) -> None:
    from ..models import AppSetting
    with Session(engine) as s:
        row = s.get(AppSetting, "hl_search_cursor")
        val = json.dumps(done, ensure_ascii=False)
        if row is None:
            s.add(AppSetting(key="hl_search_cursor", value=val))
        else:
            row.value = val
            s.add(row)
        s.commit()


def _hl_classify(nick: str, sig: str, descs: list[str]):
    text_all = f"{nick} {sig} {' '.join(descs)}"
    if any(w in text_all for w in _HL_NEG):
        return None
    act = [w for w in _HL_ACT if w in text_all]
    signup = [w for w in _HL_SIGNUP if w in sig]
    topic = [w for w in _HL_TOPIC if w in text_all]
    if act and signup and topic:
        return "A"
    if len(act) >= 2 and topic:
        return "B"
    if act and topic:
        return "B"
    return None


def _hl_crawl_sync(ctx, done_queries: list[str], cities: list[str], keywords: list[str],
                   n_search: int, n_verify: int) -> dict:
    """同步爬取体（executor 在线程池里调用）：搜索矩阵 → 逐号取证 → 分类。"""
    from playwright.sync_api import sync_playwright

    matrix = [f"{c} {k}" for c in cities for k in keywords]
    todo = [m for m in matrix if m not in done_queries]
    if not todo:  # 一轮走完：重置游标，账号会长新
        done_queries = []
        todo = matrix[:]
    batch, todo = todo[:n_search], todo[n_search:]

    authors: dict[str, dict] = {}
    searched = []
    with sync_playwright() as p:
        browser = launch(p)
        ctxm = browser.new_context(user_agent=_HL_MOBILE_UA, locale="zh-CN",
                                   viewport={"width": 390, "height": 844}, is_mobile=True)
        page = ctxm.new_page()

        def walk(o, bag):
            if isinstance(o, dict):
                sec = o.get("sec_uid")
                if sec:
                    prev = bag.get(sec, {})
                    bag[sec] = {"nick": (o.get("nickname") or prev.get("nick") or "")[:24],
                                "signature": (o.get("signature") or prev.get("signature") or "")[:100]}
                for v in o.values():
                    walk(v, bag)
            elif isinstance(o, list):
                for v in o:
                    walk(v, bag)

        def on_resp(r):
            u = r.url
            if ("search" in u and "aweme" in u) or "general/search" in u \
                    or "search/item" in u or "search/user" in u:
                try:
                    body = r.text()
                    if body.strip().startswith("{"):
                        walk(json.loads(body), authors)
                except Exception:
                    pass

        page.on("response", on_resp)
        for m in batch:
            try:
                page.goto(f"https://www.douyin.com/search/{quote(m)}", timeout=30000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(6000)
                try:
                    tab = page.locator("text=用户").first
                    if tab.count() > 0:
                        tab.click(timeout=2000)
                        page.wait_for_timeout(3500)
                except Exception:
                    pass
                for _ in range(2):
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(2000)
                searched.append(m)
                ctx.set_progress(int(50 * len(searched) / max(len(batch), 1)),
                                 f"搜索 {len(searched)}/{len(batch)}：{m}（作者池 {len(authors)}）")
            except Exception as e:  # noqa: BLE001 单查询失败不断轮
                ctx.set_progress(10, f"搜索失败 {m}：{type(e).__name__}")
            time.sleep(random.uniform(3.5, 5.0))
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass

        # ---- 逐号取证 ----
        verified: list[dict] = []
        seen_mark: set[str] = set()

        def on_w(r, rec):
            if "aweme/post" in r.url and "/web/api/v2/" in r.url:
                try:
                    d = r.json()
                    for a in (d.get("aweme_list") or [])[:15]:
                        dsc = (a.get("desc") or "").strip()[:60]
                        if dsc:
                            rec["works"].append(dsc)
                except Exception:
                    pass

        for i, sec in enumerate(list(authors.keys())[:n_verify]):
            rec = {"sec": sec, "nick": authors[sec].get("nick", ""), "signature": "", "works": []}

            def on_w2(r, rec=rec):
                on_w(r, rec)

            page.on("response", on_w2)
            try:
                page.goto(f"https://www.douyin.com/share/user/{sec}", timeout=30000,
                          wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                head = page.evaluate("() => document.body.innerText.slice(0, 320)")
                m2 = re.search(r"([\u4e00-\u9fa5A-Za-z0-9_·]{2,24})\n抖音号\s*([^\n]+)\n([^\n]+)", head)
                if m2:
                    rec["nick"] = rec["nick"] or m2.group(1)
                    rec["signature"] = m2.group(3)[:100]
                for _ in range(2):
                    page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1800)
                verified.append(rec)
                seen_mark.add(sec)
            except Exception:
                pass
            finally:
                try:
                    page.remove_listener("response", on_w2)
                except Exception:
                    pass
            ctx.set_progress(50 + int(45 * (i + 1) / max(n_verify, 1)),
                             f"取证 {i + 1}/{min(n_verify, len(authors))}（候选 {len(verified)}）")
            time.sleep(random.uniform(2.2, 3.5))
        browser.close()
    return {"searched": searched, "todo_left": todo, "done_queries": done_queries,
            "authors": authors, "verified": verified, "seen_mark": seen_mark}


@register_executor("hl_search_matrix")
async def hl_search_matrix(ctx, payload: dict) -> dict:
    """同城组局号搜索矩阵挖掘（每晚调度）：城市×关键词搜索 → 作者池 → 取证 →
    四要素筛 → LLM 分级 → 判 A/B 入候选表（keyword=搜索矩阵，人工定夺）。"""
    cities = _hl_setting("hl_search_cities", _HL_CITIES)
    keywords = _hl_setting("hl_search_keywords", _HL_KEYWORDS)
    n_search = int(_harvest_budget("hl_search", _HL_SEARCH_BUDGET))
    n_verify = min(_harvest_budget("hl_verify", _HL_VERIFY_BUDGET), 60)

    seen = _load_harvest_seen()  # 复用同一份已扫池（两类管线互斥物种，seen 合并无害）
    with Session(engine) as s:
        known = {c.sec_uid for c in s.exec(select(WatchCandidate)).all()  # type: ignore[attr-defined]
                 if c.sec_uid}

    done_queries = _hl_get_cursor()
    result = await asyncio.to_thread(_hl_crawl_sync, ctx, done_queries, cities,
                                     keywords, n_search, n_verify)
    searched = result["searched"]
    _hl_save_cursor(result["done_queries"] + searched)

    notes: list[str] = []
    cands = []
    for rec in result["verified"]:
        if rec["sec"] in known:
            continue
        tier = _hl_classify(rec["nick"], rec["signature"], rec["works"])
        if tier:
            cands.append({**rec, "tier": tier})
    if not cands:
        ctx.set_progress(100, f"搜索 {len(searched)}，作者池 {len(result['authors'])}，"
                              f"取证 {len(result['verified'])}，无新候选")
        _save_harvest_seen(_load_harvest_seen() | result["seen_mark"])
        return {"searches": len(searched), "secs": len(result["authors"]),
                "verified": len(result["verified"]), "inserted": 0, "notes": notes}

    # ---- LLM 分级（暖暖标准）----
    batch = [{"id": i + 1, "昵称": c["nick"], "签名": c["signature"],
              "作品": " / ".join(c["works"][:3])} for i, c in enumerate(cands)]
    sys_prompt = (
        "你是「同城组局获客号」鉴定员。目标物种（标尺=「暖暖AI赋能」）：在抖音组织/推广线下同城活动"
        "（AI/创业/成长/生意主题的沙龙、组局、社群）获客的个人主理人号——同城标签、活动词、报名或加微入口，"
        "小号也无妨。判「强」：江浙沪城市词+活动词+主题命中+获客意图；判「中」：江浙沪+活动词+主题但证据较弱；"
        "判「否」：实体商家卖货、机构公司号、纯生活号、外地号、搭子喝酒等无关号。"
        '输出 JSON：{"ranks":[{"id":1,"tier":"强|中|否","why":"≤14字"}]}')
    data = await gateway.complete_json(
        [{"role": "system", "content": sys_prompt},
         {"role": "user", "content": json.dumps(batch, ensure_ascii=False)}],
        purpose="leadgen_judge", max_tokens=4000, thinking="disabled")
    jmap = {r.get("id"): r for r in (data.get("ranks") or []) if isinstance(r, dict)}

    inserted = 0
    strong = mid = rejected = 0
    with Session(engine) as s:
        known = {c.sec_uid for c in s.exec(select(WatchCandidate)).all()  # type: ignore[attr-defined]
                 if c.sec_uid}
        for i, c in enumerate(cands):
            j = jmap.get(i + 1)
            if not isinstance(j, dict):
                continue
            tier = str(j.get("tier") or "")
            why = str(j.get("why") or "")[:40]
            if tier not in ("强", "中") or c["sec"] in known:
                rejected += 1
                continue
            s.add(WatchCandidate(
                keyword="搜索矩阵", direction="同城组局", name=c["nick"][:60], sec_uid=c["sec"],
                url=f"https://www.iesdouyin.com/share/user/{c['sec']}",
                signature=c["signature"], follower_count=0,
                sample_video_id="", sample_title="",
                sample_stats=json.dumps({"works": len(c.get("works", []))}, ensure_ascii=False),
                account_type="leadgen",
                pure_reason=f"搜索矩阵·LLM判{tier} · {why}"[:90], status="open"))
            inserted += 1
            if tier == "强":
                strong += 1
            else:
                mid += 1
            notes.append(f"LLM判{tier}：{c['nick']} · {why}")
        s.commit()
    _save_harvest_seen(_load_harvest_seen() | result["seen_mark"])
    ctx.set_progress(100, f"搜索 {len(searched)}，取证 {len(result['verified'])}，"
                          f"LLM 入表 {inserted}（强{strong} 中{mid} 否{rejected}）")
    return {"searches": len(searched), "secs": len(result["authors"]),
            "verified": len(result["verified"]), "inserted": inserted,
            "strong": strong, "mid": mid, "rejected": rejected, "notes": notes}
