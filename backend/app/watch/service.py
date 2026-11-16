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
import time
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from ..browser import launch, ua
from ..db import engine
from ..llm import gateway
from ..jobs.errors import TransientError
from ..jobs.runner import register_concurrent, register_executor
from ..models import BenchmarkVideo, WatchAccount, WatchCandidate


# ── C 阶段结构整治（2026-11-16）：抓取层拆至 fetch.py、同城线拆至 hl.py；
# 本模块留执行器与发现管线；facade 再导出沿用旧名（selfstyle/api 既有导入零改动）──
from .fetch import (_list_account_videos_playwright,
                    _list_author_videos_via_anchor,
                    _list_author_videos_via_share_page,
                    _probe_video_author)
from .hl import hl_search_matrix  # noqa: F401  执行器注册随导入生效


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
    # 存量待定夺清扫：历史 pending 一律走同一道拆解闸（评论/赞达标才拆，
    # 不过线标 ignored 保留行——与新增视频同一标准，不做无闸批量放行）
    from ..benchmarks import service as _bms
    with Session(engine) as s:
        backlog = s.exec(select(BenchmarkVideo).where(  # type: ignore[attr-defined]
            BenchmarkVideo.scan_status == "pending")).all()  # type: ignore[attr-defined]
    if backlog:
        n_backlog = await _bms.auto_dissect_rows(backlog, "存量清扫")
        notes.append(f"存量待定夺 {len(backlog)} 条过闸：批准拆解 {n_backlog} 条，"
                     f"其余未达线标 ignored")

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
    # 质量闸候选：任务级累计所有账号的新行（循环外初始化——此前定义在循环体内，
    # 全账号抓取失败时 NameError；且质量闸只看最后一个账号，2026-11-07 P2 实测修）
    new_row_ids: list[int] = []
    for i, acc in enumerate(douyin_accounts):
        ctx.set_progress(
            5 + int(85 * i / max(1, len(douyin_accounts))),
            f"扫描 {acc.name}（{i + 1}/{len(douyin_accounts)}）")
        # 列表三链路瀑布：① 锚点视频（免登录）② 移动分享主页（免登录，需 sec_uid 链接）
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
                    scan_status="pending",  # 短暂待定夺态，扫描末尾过拆解质量闸（达标自动批准，未达标 ignored 可手动强拆）
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
                row.last_scan_at = datetime.now(timezone.utc).replace(tzinfo=None)  # 全库 naive-UTC
                s.add(row)
                s.commit()
        notes.append(f"{acc.name}：新入库 {created} 条（过拆解质量闸：评论≥20或赞≥300 自动批准拆）"
                     + (f"，回填互动数 {backfilled} 条" if backfilled else ""))

    # 质量闸自动拆解（2026-11-11 用户定版：评论≥20或赞≥300 才拆）：达标新作品自动批准
    # 进拆解队列，未达标标 ignored（页面上可见，手动批准可强制拆）。
    # 人的角色后移到选题库定审，拆解库日常不用看。
    if new_row_ids:
        try:
            from ..benchmarks import service as bms

            rows = []
            with Session(engine) as s:
                rows = [s.get(BenchmarkVideo, i) for i in new_row_ids]
            rows = [r for r in rows if r is not None]
            n_auto = await bms.auto_dissect_rows(rows, "同行扫描")
            if n_auto:
                notes.append(f"自动拆解 {n_auto} 条（过拆解质量闸：评论≥20 或 赞≥300，未达标行保留可手动强拆）")
        except Exception as e:  # noqa: BLE001 自动拆解失败不影响扫描本身
            notes.append(f"质量闸自动拆解失败：{type(e).__name__}: {str(e)[:80]}")

    channels_count = sum(1 for a in accounts if a.platform == "channels")
    if channels_count:
        notes.append("视频号无公开网页接口，不支持自动扫描：请在 App 等外部渠道发现视频后"
                     "「本地视频拆解」上传")

    ctx.set_progress(100, f"完成：新发现 {queued} 条")
    return {"queued": queued, "backfilled": backfilled, "notes": notes}


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


_DISCOVER_SAMPLE_CAP = 10  # 最多反查的视频页数（控任务时长 ~2min）


_DISCOVER_QUALITY_FANS = 10000  # 「清单外高赞同行」质量线：候选粉丝 ≥1 万才进周报（2026-10-03）


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


def _fmt_follow(n: int) -> str:
    if n >= 10000:
        w = n / 10000
        return f"{w:.0f}万" if w >= 100 else f"{w:.1f}万"
    return str(n)


LEADGEN_DENSITY_MIN = 0.08  # 求带密度 ≥8% 判获客型


_LEADGEN_KEYWORDS = ("求带", "报名", "怎么报名", "怎么学", "哪里学", "求教程", "私信",
                     "扣1", "扣个1", "怎么做", "怎么弄", "带带我", "想学", "请教", "怎么联系")


_LEADGEN_PROBE_CAP = 3      # 每轮发现最多探测 3 条样本（控时长 ~1 分钟）


def _probe_leadgen_signal(video_id: str) -> dict:
    """打开视频页拦评论 XHR，返回 {comments, leadgen, density}。失败抛 RuntimeError。"""
    from playwright.sync_api import sync_playwright

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
                "（退避梯子第一跳 30 分钟，期间不再消耗检索配额），无需换词")
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
    # 获客信号：粉丝 Top N 探评论区求带密度——高密度=高质量获客号（两类号策略 2026-10-28）
    for d in sorted(ranked, key=lambda d: d["follower_count"], reverse=True)[:_LEADGEN_PROBE_CAP]:
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
                               d["follower_count"]), reverse=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # 全库 naive-UTC
    new_cnt = added_cnt = 0
    with Session(engine) as s:
        watched_secs = set()
        for a in s.exec(select(WatchAccount).where(  # type: ignore[attr-defined]
                WatchAccount.platform == "douyin")).all():
            m = re.search(r"share/user/([A-Za-z0-9_-]{20,})", a.url or "")
            if m:
                watched_secs.add(m.group(1))
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


@register_executor("watch_backfill")
async def watch_backfill(ctx, payload: dict) -> dict:
    """监控号存量回填（2026-11-14；2026-11-15 追溯窗参数化）：逐启用对标账号列表近期作品——
    发布时间 ≤backfill_window_days（设置项，默认 30）+ 拆解闸同口径（评论≥20 或赞≥300）
    → 未入库的建行自动拆解（拆解自动采高赞评论+分型路由）。
    深翻页模式（列表 60 条/账号）把高频账号 30 天前的旧片也送进闸口。"""
    from ..benchmarks import service as bms
    from ..settings import settings

    cmin = max(1, int(settings.dissect_comment_min or 20))
    dmin = max(1, int(settings.dissect_digg_min or 300))
    days = max(1, int(settings.backfill_window_days or 30))
    # 深翻页实测（2026-11-16，四路全断）：分享页 UI 滚轮无效+网格 0 渲染、PC 主页匿名登录墙、
    # aweme/post 接口 a_bogus 签名锁死（原样重放/改参/裸调全空体）→ 匿名深度=首发 XHR ~15 条。
    # 上限仍留 20（首发批+停手判定），douyin 若放开深度本链路自愈。
    limit = 20
    with Session(engine) as s:
        accounts = s.exec(select(WatchAccount).where(  # type: ignore[attr-defined]
            WatchAccount.enabled == True,  # noqa: E712
            WatchAccount.platform == "douyin")).all()  # type: ignore[attr-defined]
    if not accounts:
        ctx.set_progress(100, "无启用对标账号，诚实空过")
        return {"accounts": 0, "created": 0, "queued": 0, "notes": []}
    cap = 100  # 单次建行上限（heavy 串行消化，可重复跑续扫）
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    created_n = queued = 0
    notes: list[str] = []
    for i, acc in enumerate(accounts, 1):
        if created_n >= cap:
            notes.append(f"达单次上限 {cap}，剩余账号下次再跑")
            break
        ctx.set_progress(int(90 * (i - 1) / len(accounts)), f"回填 {acc.name}（{i}/{len(accounts)}）")
        entries, err, _is_anchor = await asyncio.to_thread(_list_author_videos_via_anchor, acc.url, limit)
        if entries is None:
            entries, err2, _ = await asyncio.to_thread(_list_author_videos_via_share_page, acc.url, limit)
        if entries is None:
            notes.append(f"{acc.name} 列表未生效：{(err2 or err or '')[:50]}")
            continue
        created_rows: list = []
        for e in entries:
            if created_n >= cap:
                break
            vid = str(e.get("id") or "")
            if not vid:
                continue
            ctime = int(e.get("ctime") or 0)
            st = e.get("stats") or {}
            c = int(st.get("comment") or 0)
            dg = int(st.get("digg") or 0)
            if ctime and datetime.fromtimestamp(ctime, tz=timezone.utc).replace(tzinfo=None) < cutoff:
                continue  # 超追溯窗
            if c < cmin and dg < dmin:
                continue  # 不过拆解闸（R3.7 同口径）
            url = e.get("url") or f"https://www.douyin.com/video/{vid}"
            with Session(engine) as s:
                if s.exec(select(BenchmarkVideo).where(  # type: ignore[attr-defined]
                        BenchmarkVideo.url == url)).first() is not None:
                    continue  # 已在拆解库
                b = BenchmarkVideo(tenant_id=acc.tenant_id, url=url, author=acc.name,
                                   title=str(e.get("title") or "")[:80], stats=st,
                                   source="douyin", account_type=acc.account_type or "persona",
                                   scan_status="pending", notes=f"监控号存量回填（{days} 天）")
                s.add(b)
                s.commit()
                s.refresh(b)
                created_rows.append(b)
            created_n += 1
        if created_rows:
            n = await bms.auto_dissect_rows(created_rows, f"存量回填#{acc.name}")
            queued += n
        notes.append(f"{acc.name}：列 {len(entries)}，过闸建行 {len(created_rows)}")
        await asyncio.sleep(2)
    ctx.set_progress(100, f"存量回填完成（{days} 天窗）：{len(accounts)} 账号 / 过闸建行 {created_n} / 排队拆解 {queued}")
    return {"accounts": len(accounts), "created": created_n, "queued": queued,
            "days": days, "notes": notes[:24]}
