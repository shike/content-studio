"""素材域执行器（PRD R1）：扫描 / 识别 / 发现 / 回填 / 拆解。

全部写入 sources+materials（旧素材面 2026-11-16 起停写，迁移映射见 TDD §15）。
命名映射：watch_scan→material_watch_scan、watch_resolve→material_resolve、
watch_discover→material_discover、watch_backfill→material_backfill、
topic_backfill→material_topic_backfill（collectors/radar_patrol）、
hl_search_matrix→material_hl_search（collectors/hl）、benchmark_analyze→material_analyze。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import (JobContext, register_concurrent, register_executor,
                           register_owed)
from ..models import Job, Material, MaterialAnalysis, Source
from . import service
from .collectors import douyin_list
from .collectors.hl import material_hl_search  # noqa: F401  执行器注册随导入生效
from .collectors.radar_patrol import (material_topic_backfill,  # noqa: F401
                                      check_topic)
from .download import extract_douyin_url
from .enrich import run_analyze


@register_executor("material_analyze")
async def material_analyze(ctx: JobContext, payload: dict) -> dict:
    """素材拆解：下载→评论存档+分型→ASR→LLM 拆解→版本留痕（管线在 enrich）。"""
    material_id = payload["material_id"]
    return await run_analyze(ctx, int(material_id))


@register_executor("material_watch_scan")
async def material_watch_scan(ctx: JobContext, payload: dict) -> dict:
    """同行采集源扫描：逐源列最新作品 → 素材库去重入库 → 同一道质量闸自动批准拆解。"""
    source_id = payload.get("source_id")
    with Session(engine) as s:
        if source_id:
            src = s.get(Source, int(source_id))
            sources = [src] if src is not None else []
        else:
            sources = s.exec(
                select(Source).where(  # type: ignore[attr-defined]
                    Source.kind == "peer_account",  # type: ignore[attr-defined]
                    Source.status == "active",  # type: ignore[attr-defined]
                    Source.platform == "douyin",  # type: ignore[attr-defined]
                )
            ).all()
    sources = [x for x in sources if x is not None]
    if not sources:
        raise RuntimeError("采集源为空：先在「素材库·采集源」页添加同行账号")

    notes: list[str] = []
    queued = 0
    # 存量收件箱清扫：历史 inbox 一律走同一道拆解闸（不过线标 ignored，可手动翻案）
    with Session(engine) as s:
        backlog = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.status == "inbox",  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video")).all()  # type: ignore[attr-defined]
    if backlog:
        n_backlog = await service.gate_rows(backlog, "存量清扫")
        notes.append(f"收件箱存量 {len(backlog)} 条过闸：批准拆解 {n_backlog} 条，其余未达线标 ignored")

    with Session(engine) as s:
        known_ids: dict[str, int] = {}
        for b in s.exec(select(Material).where(  # type: ignore[attr-defined]
                Material.origin_type == "douyin_video")).all():  # type: ignore[attr-defined]
            if b.origin_id:
                known_ids[b.origin_id] = b.id

    backfilled = 0
    new_row_ids: list[int] = []
    for i, src in enumerate(sources):
        ctx.set_progress(
            5 + int(85 * i / max(1, len(sources))),
            f"扫描 {src.name}（{i + 1}/{len(sources)}）")
        # 列表三链路瀑布：① 锚点视频（免登录）② 移动分享主页（免登录）③ PC 主页拦截
        entries, err, is_anchor = await asyncio.to_thread(douyin_list._list_author_videos_via_anchor, src.url)
        if entries is not None:
            notes.append(f"{src.name}：锚点视频路线（免登录）")
        else:
            share_entries, share_err, _ = await asyncio.to_thread(
                douyin_list._list_author_videos_via_share_page, src.url)
            if share_entries is not None:
                entries, err = share_entries, None
                notes.append(f"{src.name}：移动分享主页路线（免登录）")
            elif is_anchor is False:
                pass  # 链接不是视频页也不是分享主页 → 交给主页拦截
            else:
                notes.append(f"{src.name}：锚点未生效（{err}）；分享主页未生效（{share_err}）")
        if entries is None and is_anchor is False:
            pw_err = err or ""
            entries, err = await asyncio.to_thread(douyin_list._list_account_videos_playwright, src.url)
            if entries is not None:
                notes.append(f"{src.name}：主页拦截路线（锚点未生效：{pw_err}）")
        if entries is None:
            notes.append(f"{src.name}：{err}")
            continue

        created = 0
        for e in entries:
            vid = str(e.get("id") or "")
            stats = e.get("stats") or {}
            if not vid:
                continue
            if vid in known_ids:
                # 已在库：顺带回填互动数（扫描即刷新老素材数据）
                if stats:
                    with Session(engine) as s:
                        row = s.get(Material, known_ids[vid])
                        if row is not None and not (row.stats or {}):
                            row.stats = stats
                            s.add(row)
                            s.commit()
                            backfilled += 1
                continue
            url = e.get("url") or f"https://www.douyin.com/video/{vid}"
            ctime = int(e.get("ctime") or 0)
            pub = (datetime.fromtimestamp(ctime, tz=timezone.utc).replace(tzinfo=None)
                   if ctime else None)
            with Session(engine) as s:
                m, created_new = service.upsert_material(
                    s, tenant_id=src.tenant_id or 1, origin_type="douyin_video",
                    origin_id=vid, origin_url=url, author=src.name,
                    title=str(e.get("title") or "")[:80], stats=stats,
                    source_id=src.id, published_at=pub,
                    quality={"account_type": (src.config or {}).get("account_type") or "persona"})
                if created_new:
                    known_ids[vid] = m.id
                    new_row_ids.append(m.id)
                    created += 1
                    queued += 1

        with Session(engine) as s:
            row = s.get(Source, src.id)
            if row is not None:
                row.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)
                s.add(row)
                s.commit()
        notes.append(f"{src.name}：新入库 {created} 条（过拆解质量闸：评论≥20或赞≥300 自动批准拆）"
                     + (f"，回填互动数 {backfilled} 条" if backfilled else ""))

    # 质量闸自动拆解：达标新素材自动批准进拆解队列，未达标标 ignored（可手动翻案强拆）
    if new_row_ids:
        try:
            rows = []
            with Session(engine) as s:
                rows = [s.get(Material, i) for i in new_row_ids]
            rows = [r for r in rows if r is not None]
            n_auto = await service.gate_rows(rows, "同行扫描")
            if n_auto:
                notes.append(f"自动拆解 {n_auto} 条（过拆解质量闸：评论≥20 或 赞≥300，未达标行保留可手动强拆）")
        except Exception as e:  # noqa: BLE001 自动拆解失败不影响扫描本身
            notes.append(f"质量闸自动拆解失败：{type(e).__name__}: {str(e)[:80]}")

    channels_count = sum(1 for a in sources if a.platform == "channels")
    if channels_count:
        notes.append("视频号无公开网页接口，不支持自动扫描：请在 App 等外部渠道发现视频后"
                     "「本地视频拆解」上传")

    ctx.set_progress(100, f"完成：新发现 {queued} 条")
    return {"queued": queued, "backfilled": backfilled, "notes": notes}


# ── 评论补采（2026-11-18 用户裁决·扩量）：拆解时抓评论只此一次、成功率 ~5%，失败永不补——
# 解耦成独立批量任务自愈重试。范围=监控清单作者（与热评雷达同口径）；连败 3 次停采
# （评论区关闭/视频已死，防每轮空烧）。全程 Playwright 抓评论 XHR，无 LLM 开销；
# light 分道同类合并（不堵 heavy 的生成任务），单轮 cap 防 RetryPolicy 硬超时。


def _monitor_author_filter(s: Session) -> tuple[set[int], set[str]]:
    """监控清单作者（active peer_account）：source_id 集合 + 作者名集合。"""
    mon = s.exec(select(Source).where(  # type: ignore[attr-defined]
        Source.kind == "peer_account",  # type: ignore[attr-defined]
        Source.status == "active",  # type: ignore[attr-defined]
        Source.platform == "douyin")).all()  # type: ignore[attr-defined]
    return ({x.id for x in mon if x.id is not None}, {x.name for x in mon})


def _recapture_targets() -> list[Material]:
    """补采目标（雷达扩量队列）：监控作者 + ready + 有链接 + 未在拆解 + 未连败停采 +
    无评论存档；按互动降序（高热视频的评论区先补）。"""
    with Session(engine) as s:
        mon_ids, mon_names = _monitor_author_filter(s)
        busy = {(j.payload or {}).get("material_id") for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.type == "material_analyze",  # type: ignore[attr-defined]
            Job.status.in_(["queued", "running"]))).all()}  # type: ignore[attr-defined]
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
            Material.status == "ready")).all()  # type: ignore[attr-defined]
    targets = [m for m in rows
               if m.id not in busy
               and m.origin_url
               and ((m.source_id or 0) in mon_ids or (m.author or "") in mon_names)
               and int((m.quality or {}).get("comment_fails") or 0) < 3]
    if not targets:
        return []
    with Session(engine) as s:
        have = {a.material_id for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
            MaterialAnalysis.material_id.in_([t.id for t in targets]),  # type: ignore[arg-type]
            MaterialAnalysis.kind == "comments")).all()}  # type: ignore[attr-defined]
    targets = [t for t in targets if t.id not in have]

    def _hotness(m: Material) -> int:
        st = m.stats or {}
        try:
            return int(st.get("digg") or 0) + int(st.get("comment") or 0)
        except (TypeError, ValueError):
            return 0

    targets.sort(key=_hotness, reverse=True)
    return targets


@register_executor("comment_recapture")
async def comment_recapture(ctx: JobContext, payload: dict) -> dict:
    """评论补采批量（light）：对「有拆解、无评论存档」的监控作者视频逐条重抓评论。
    抓到=与拆解链同口径落档（评论存档+分型+账号联动改判）；失败计 quality.comment_fails，
    连败 3 次停采。余量由 owed 下轮续扫（每轮 cap 100）。"""
    from .enrich import (_fetch_top_comments, classify_comments,
                         _rejudge_source_type, _save_analysis)

    cap = 100   # 单轮上限：100×(60s 抓取窗+18s 静默) ≈ 2.6h < task_timeout 4h
    pace_s = 18  # 视频间静默窗：评论接口连发易触发风控窗
    targets = _recapture_targets()
    batch = targets[:cap]
    if not batch:
        ctx.set_progress(100, "没有需要补采的视频（监控作者全部有评论存档或已停采）")
        return {"queue": 0, "ok": 0, "fail": 0}

    ok = fail = 0
    notes: list[str] = []
    for i, m in enumerate(batch):
        ctx.set_progress(5 + int(90 * i / len(batch)),
                         f"补采评论 {i + 1}/{len(batch)}：{(m.title or m.author or '')[:36]}")
        try:
            comments = await asyncio.to_thread(_fetch_top_comments, m.origin_url)
            kind, share = classify_comments(comments)
            with Session(engine) as s:
                row = s.get(Material, m.id)
                if row is None:
                    continue
                _save_analysis(s, m.id, row.tenant_id, "comments",
                               {"items": comments}, model="xhr")
                q = dict(row.quality or {})
                q["comment_fails"] = 0
                q["comment_kind"], q["leadgen_share"] = kind, share
                row.quality = q
                s.add(row)
                s.commit()
                tenant_id, source_id, author = row.tenant_id, row.source_id, row.author or ""
            if kind == "leadgen_ore":  # 与拆解链同口径：获客矿联动账号改判
                note = await asyncio.to_thread(_rejudge_source_type, tenant_id, source_id, author)
                if note:
                    notes.append(note[:80])
            ok += 1
        except Exception:  # noqa: BLE001 单视频失败不挡批次；计数累积，连败停采
            with Session(engine) as s:
                row = s.get(Material, m.id)
                if row is not None:
                    q = dict(row.quality or {})
                    q["comment_fails"] = int(q.get("comment_fails") or 0) + 1
                    row.quality = q
                    s.add(row)
                    s.commit()
            fail += 1
        await asyncio.sleep(pace_s)
    tail = f"；剩余 {max(0, len(targets) - len(batch))} 条 owed 下轮续扫" if len(targets) > len(batch) else ""
    ctx.set_progress(100, f"评论补采完成：成功 {ok} / 失败 {fail}{tail}")
    return {"queue": len(targets), "ok": ok, "fail": fail, "notes": notes[:12]}


def owed_comment_recapture() -> list[tuple[dict, str]]:
    """欠任务声明：监控作者还有「有拆解、无评论存档、未停采」的视频 → 补一个批量任务
    （dedup 幂等，在跑时合并；reaper 每轮检查，批次跑完自动续扫）。"""
    if not _recapture_targets():
        return []
    return [({"auto": True}, "1:comment_recapture")]


register_concurrent("comment_recapture")
register_owed("comment_recapture", owed_comment_recapture)


@register_executor("material_resolve")
async def material_resolve(ctx, payload: dict) -> dict:
    """贴链识别账号（免登录）：分享口令全文提取链接 → 视频页反查 → 主页标题兜底。"""
    raw = (payload.get("url") or "").strip()
    if not raw:
        raise RuntimeError("url 不能为空")
    url = extract_douyin_url(raw)
    if url != raw:
        ctx.set_progress(10, "已从粘贴文本中提取分享链接")
    ctx.set_progress(20, "识别链接类型并打开页面")
    info, err = await asyncio.to_thread(douyin_list._resolve_author_via_video, url)
    if info is None:
        ctx.set_progress(60, f"视频链接路线未生效（{err}），改走主页标题")
        info, err2 = await asyncio.to_thread(douyin_list._resolve_author_via_share_page, url)
        if info is None:
            raise RuntimeError(f"无法识别账号：{err}；{err2}")
    ctx.set_progress(100, f"识别成功：{info['name']}")
    return info


_DISCOVER_SAMPLE_CAP = 10  # 最多反查的视频页数（控任务时长 ~2min）
_DISCOVER_QUALITY_FANS = 10000  # 「清单外高赞同行」质量线：候选粉丝 ≥1 万才进周报
_DISCOVER_TOP_N = 8        # 最终保留的候选账号数（按粉丝数）

LEADGEN_DENSITY_MIN = 0.08  # 求带密度 ≥8% 判获客型
_LEADGEN_KEYWORDS = ("求带", "报名", "怎么报名", "怎么学", "哪里学", "求教程", "私信",
                     "扣1", "扣个1", "怎么做", "怎么弄", "带带我", "想学", "请教", "怎么联系")
_LEADGEN_PROBE_CAP = 3      # 每轮发现最多探测 3 条样本（控时长 ~1 分钟）


def _fmt_follow(n: int) -> str:
    if n >= 10000:
        w = n / 10000
        return f"{w:.0f}万" if w >= 100 else f"{w:.1f}万"
    return str(n)


def _discover_queries(keyword: str, direction: str) -> list[str]:
    """账号发现的检索词组：DDG 组（site: 限定，索引准）+ 通用组（GLM 也能吃）。"""
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
                deadline = time.monotonic() + 60
                while not comments and time.monotonic() < deadline:
                    page.wait_for_timeout(3000)
                    if not comments and time.monotonic() < deadline:
                        try:
                            page.mouse.wheel(0, 600)
                            page.wait_for_timeout(1500)
                            page.mouse.wheel(0, -600)
                        except Exception:  # noqa: BLE001
                            break
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


@register_executor("material_discover")
async def material_discover(ctx, payload: dict) -> dict:
    """关键词发现对标账号（DDG→视频链接→反查作者→粉丝 Top N→候选表）。"""
    keyword = (payload.get("keyword") or "").strip()
    direction = (payload.get("direction") or "").strip()
    if not keyword:
        raise RuntimeError("keyword 不能为空")
    from ..search import web_search

    queries = _discover_queries(keyword, direction)
    hits: dict[str, str] = {}
    notes: list[str] = []
    limited = False
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
                notes.append("DDG 限流惩罚窗中，本次不再补发检索（越打越封），等自动重试窗口恢复")
                break
        if len(hits) >= 8:
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
            "或直接在 App 分享视频链接走「贴链识别」")
    samples = list(hits.items())[:_DISCOVER_SAMPLE_CAP]

    found: dict[str, dict] = {}
    skipped = 0
    for i, (rid, title) in enumerate(samples):
        ctx.set_progress(15 + int(70 * i / len(samples)), f"反查作者 {i + 1}/{len(samples)}")
        info, err = await asyncio.to_thread(douyin_list._probe_video_author, rid)
        if info is None:
            await asyncio.sleep(3)
            info, err2 = await asyncio.to_thread(douyin_list._probe_video_author, rid)
            if info is None:
                skipped += 1
                notes.append(f"样本 {rid} 跳过：{err2 or err}")
                continue
        if not info["title"]:
            info["title"] = title
        prev = found.get(info["sec_uid"])
        if prev is None or info["digg"] > prev["digg"]:
            found[info["sec_uid"]] = info
        await asyncio.sleep(1.5)
    if not found:
        raise TransientError(
            f"{len(samples)} 个样本都未反查出作者（等待窗内数据未到，已放宽等待后仍如此请检查出口延迟），稍后自动重试或手动重跑")

    ranked = sorted(found.values(),
                    key=lambda d: d["follower_count"], reverse=True)[:_DISCOVER_TOP_N]
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
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_cnt = added_cnt = 0
    with Session(engine) as s:
        active_secs: dict[str, Source] = {}
        cand_rows: dict[str, list[Source]] = {}
        for a in s.exec(select(Source).where(  # type: ignore[attr-defined]
                Source.kind == "peer_account")).all():  # type: ignore[attr-defined]
            if a.status == "active" and a.ext_id:
                active_secs.setdefault(a.ext_id, a)
            elif a.status == "candidate" and a.ext_id:
                cand_rows.setdefault(a.ext_id, []).append(a)
        for d in ranked:
            cfg = {"keyword": keyword, "direction": direction,
                   "signature": (d.get("signature") or "")[:100],
                   "account_type": d.get("account_type", "persona"),
                   "leadgen_density": d.get("leadgen_density", 0),
                   "follower_count": d["follower_count"],
                   "sample_video_id": d["video_id"], "sample_title": d["title"],
                   "stats": {"digg": d["digg"], "comment": d["comment"],
                             "share": d["share"], "collect": d["collect"]}}
            open_rows = cand_rows.get(d["sec_uid"]) or []
            open_row = next((r for r in open_rows
                             if not (r.config or {}).get("dismissed")), None)
            if d["sec_uid"] in active_secs:
                added_cnt += 1
                continue
            if any((r.config or {}).get("dismissed") for r in open_rows):
                notes.append(f"{d['name']}：此前已忽略，不再重复推荐")
                continue
            if open_row is not None:
                open_row.handle = d["name"][:60]
                open_row.config = cfg
                open_row.updated_at = now
                s.add(open_row)
            else:
                s.add(Source(
                    tenant_id=1, kind="peer_account", platform="douyin",
                    name=d["name"][:60], handle=d["name"][:60], ext_id=d["sec_uid"],
                    status="candidate",
                    url=f"https://www.iesdouyin.com/share/user/{d['sec_uid']}",
                    config=cfg))
            new_cnt += 1
        s.commit()

    notes.append(f"反查 {len(samples)} 个样本：{len(found)} 位作者（跳过 {skipped}），"
                 f"粉丝 Top {len(ranked)} 已入候选")
    ctx.set_progress(100, f"发现 {len(ranked)} 个候选（新增 {new_cnt}，已在清单 {added_cnt}）")
    return {"candidates": len(ranked), "new": new_cnt, "already": added_cnt,
            "skipped": skipped, "top": [f"{d['name']}（{_fmt_follow(d['follower_count'])}粉）"
                                        for d in ranked], "notes": notes}


@register_executor("material_backfill")
async def material_backfill(ctx, payload: dict) -> dict:
    """同行账号存量回填（追溯窗参数化）：逐启用源列近期作品——发布时间
    ≤backfill_window_days + 拆解闸同口径 → 未入库建 inbox 素材自动拆解。
    匿名列表深度天花板 ~15 条/账号（2026-11-16 实测，契约见 TDD §6）。"""
    from ..settings import settings

    cmin = max(1, int(settings.dissect_comment_min or 20))
    dmin = max(1, int(settings.dissect_digg_min or 300))
    days = max(1, int(settings.backfill_window_days or 30))
    limit = 20  # 首发批+停手判定（匿名深度天花板，douyin 若放开深度本链路自愈）
    with Session(engine) as s:
        sources = s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account",  # type: ignore[attr-defined]
            Source.status == "active",  # type: ignore[attr-defined]
            Source.platform == "douyin")).all()  # type: ignore[attr-defined]
    if not sources:
        ctx.set_progress(100, "无启用对标账号，诚实空过")
        return {"accounts": 0, "created": 0, "queued": 0, "notes": []}
    cap = 100  # 单次建行上限（heavy 串行消化，可重复跑续扫）
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    created_n = queued = 0
    notes: list[str] = []
    for i, src in enumerate(sources, 1):
        if created_n >= cap:
            notes.append(f"达单次上限 {cap}，剩余账号下次再跑")
            break
        ctx.set_progress(int(90 * (i - 1) / len(sources)), f"回填 {src.name}（{i}/{len(sources)}）")
        entries, err, _is_anchor = await asyncio.to_thread(
            douyin_list._list_author_videos_via_anchor, src.url, limit)
        if entries is None:
            entries, err2, _ = await asyncio.to_thread(
                douyin_list._list_author_videos_via_share_page, src.url, limit)
        if entries is None:
            notes.append(f"{src.name} 列表未生效：{(err2 or err or '')[:50]}")
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
            pub = (datetime.fromtimestamp(ctime, tz=timezone.utc).replace(tzinfo=None)
                   if ctime else None)
            if pub and pub < cutoff:
                continue  # 超追溯窗
            if c < cmin and dg < dmin:
                continue  # 不过拆解闸（同口径）
            url = e.get("url") or f"https://www.douyin.com/video/{vid}"
            with Session(engine) as s:
                if s.exec(select(Material).where(  # type: ignore[attr-defined]
                        Material.origin_url == url)).first() is not None:
                    continue  # 已在素材库
                m, created = service.upsert_material(
                    s, tenant_id=src.tenant_id or 1, origin_type="douyin_video",
                    origin_id=vid, origin_url=url, author=src.name,
                    title=str(e.get("title") or "")[:80], stats=st,
                    source_id=src.id, published_at=pub,
                    quality={"account_type": (src.config or {}).get("account_type") or "persona"},
                    note=f"监控号存量回填（{days} 天）")
                if created:
                    created_rows.append(m)
                    created_n += 1
        if created_rows:
            n = await service.gate_rows(created_rows, f"存量回填#{src.name}")
            queued += n
        notes.append(f"{src.name}：列 {len(entries)}，过闸建行 {len(created_rows)}")
        await asyncio.sleep(2)
    ctx.set_progress(100, f"存量回填完成（{days} 天窗）：{len(sources)} 账号 / 过闸建行 {created_n} / 排队拆解 {queued}")
    return {"accounts": len(sources), "created": created_n, "queued": queued,
            "days": days, "notes": notes[:24]}


def discover_quality_fans() -> int:
    return _DISCOVER_QUALITY_FANS
