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
from ..models import Content, Job, Material, MaterialAnalysis, Source
from ..settings import settings
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
# 解耦成独立批量任务自愈重试。范围=监控清单作者 + 热评白名单话题（与热评雷达同口径）；
# 连败 3 次停采（评论区关闭/视频已死，防每轮空烧）。全程 Playwright 抓评论 XHR，无 LLM
# 开销；light 分道同类合并（不堵 heavy 的生成任务），单轮 cap 防 RetryPolicy 硬超时。
# 2026-11-20 用户裁决 1+2+3：每视频存前 40 条、存档 7 天滚动刷新（每天最多一批防风控）。


def _recapture_plan(refresh_before: datetime | None = None) -> tuple[list[Material], list[Material]]:
    """评论补采计划：(无存档, 超刷新窗)。监控号 + 热评白名单话题两条线；
    无存档按热度降序优先；过期存档按视频热度降序（高热评论区先刷新）。
    refresh_before=一次性补档的固定截止点（只刷此前的旧档，刷过的自然出列）。"""
    from .hot_comments import hot_scope, in_hot_scope

    with Session(engine) as s:
        scope = hot_scope(s)
        busy = {(j.payload or {}).get("material_id") for j in s.exec(select(Job).where(  # type: ignore[attr-defined]
            Job.type == "material_analyze",  # type: ignore[attr-defined]
            Job.status.in_(["queued", "running"]))).all()}  # type: ignore[attr-defined]
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
            Material.status == "ready")).all()  # type: ignore[attr-defined]
        latest: dict[int, MaterialAnalysis] = {}
        for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                MaterialAnalysis.kind == "comments")).all():  # type: ignore[attr-defined]
            cur = latest.get(a.material_id)
            if cur is None or (a.version or 0) > (cur.version or 0):
                latest[a.material_id] = a
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if refresh_before is not None:
        stale_before = refresh_before
    else:
        days = max(1, int(settings.comment_refresh_days or 7))
        stale_before = now - timedelta(days=days)

    def _hotness(m: Material) -> int:
        st = m.stats or {}
        try:
            return int(st.get("digg") or 0) + int(st.get("comment") or 0)
        except (TypeError, ValueError):
            return 0

    missing: list[Material] = []
    stale: list[tuple[datetime | None, Material]] = []
    for m in rows:
        if m.id in busy or not m.origin_url or not in_hot_scope(m, scope):
            continue
        q = m.quality or {}
        a = latest.get(m.id)
        if a is None:
            if int(q.get("comment_fails") or 0) < 3:
                missing.append(m)
            continue
        if int(q.get("comment_refresh_fails") or 0) >= 3:
            continue
        if a.created_at is not None and a.created_at >= stale_before:
            continue
        stale.append((a.created_at, m))
    missing.sort(key=_hotness, reverse=True)
    # 刷新优先高热视频：同样刷一批，评论多的视频更可能捞出新的过线热评
    stale.sort(key=lambda x: (-_hotness(x[1]), x[0] or now))
    return missing, [m for _, m in stale]


@register_executor("comment_recapture")
async def comment_recapture(ctx: JobContext, payload: dict) -> dict:
    """评论补采/刷新批量（light）：无存档的补首次，超刷新窗的重抓最新热序。
    抓到=与拆解链同口径落档（评论存档+分型+账号联动改判）；失败计
    quality.comment_fails（首次）/ comment_refresh_fails（刷新），连败 3 次停采。
    余量由 owed 下轮续扫（每轮 cap 80）。"""
    from .enrich import (_fetch_top_comments, _merge_comment_items,
                         classify_comments, _rejudge_source_type, _save_analysis)

    cap = 80   # 单轮上限：80×(75s 抓取窗+18s 静默) ≈ 2h < task_timeout 4h
    pace_s = 18  # 视频间静默窗：评论接口连发易触发风控窗
    # 一次性补档：payload 带固定截止点（ISO，naive UTC），只刷此前的旧档，可多批接力
    before_raw = (payload or {}).get("refresh_before")
    refresh_before = None
    if before_raw:
        try:
            refresh_before = datetime.fromisoformat(str(before_raw)).replace(tzinfo=None)
        except ValueError:
            refresh_before = None
    missing, stale = _recapture_plan(refresh_before)
    targets = missing + stale
    batch = targets[:cap]
    if not batch:
        ctx.set_progress(100, "没有需要补采或刷新的视频")
        return {"queue": 0, "missing": 0, "stale": 0, "ok": 0, "fail": 0}

    missing_ids = {m.id for m in missing}
    ok = fail = 0
    notes: list[str] = []
    for i, m in enumerate(batch):
        action = "补采" if m.id in missing_ids else "刷新"
        ctx.set_progress(5 + int(90 * i / len(batch)),
                         f"{action}评论 {i + 1}/{len(batch)}：{(m.title or m.author or '')[:36]}")
        try:
            comments, api_total = await asyncio.to_thread(_fetch_top_comments, m.origin_url)
            with Session(engine) as s:
                row = s.get(Material, m.id)
                if row is None:
                    continue
                prev = s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                    MaterialAnalysis.material_id == m.id,  # type: ignore[attr-defined]
                    MaterialAnalysis.kind == "comments")  # type: ignore[attr-defined]
                    .order_by(MaterialAnalysis.version.desc())).first()  # type: ignore[attr-defined]
                prev_payload = (prev.payload or {}) if prev is not None else {}
                deleted = list(prev_payload.get("deleted") or [])
                merged = _merge_comment_items(prev_payload.get("items"), comments, deleted)
                kind, share = classify_comments(merged)
                _save_analysis(s, m.id, row.tenant_id, "comments",
                               {"items": merged, "total": api_total, "deleted": deleted,
                                "captured_at": datetime.now(timezone.utc)
                                .replace(tzinfo=None).isoformat()}, model="xhr")
                q = dict(row.quality or {})
                q["comment_fails"] = 0
                q["comment_refresh_fails"] = 0
                q["comment_kind"], q["leadgen_share"] = kind, share
                q["comment_empty"] = not merged
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
                    key = "comment_fails" if m.id in missing_ids else "comment_refresh_fails"
                    q[key] = int(q.get(key) or 0) + 1
                    row.quality = q
                    s.add(row)
                    s.commit()
            fail += 1
        await asyncio.sleep(pace_s)
    tail = f"；剩余 {max(0, len(targets) - len(batch))} 条 owed 下轮续扫" if len(targets) > len(batch) else ""
    ctx.set_progress(100, f"评论补采/刷新完成：成功 {ok} / 失败 {fail}{tail}")
    # 新评论过线后先预筛再上雷达（三道门前移，2026-11-20 阶段 1）
    try:
        from ..jobs.runner import runner

        await runner.submit("hot_comment_screen", {"auto": True},
                            dedup_key="1:hot_comment_screen")
    except Exception as e:  # noqa: BLE001 预筛排队失败不影响补采本身
        print(f"[comment_recapture] 预筛排队失败: {type(e).__name__}: {str(e)[:80]}")
    return {"queue": len(targets), "missing": len(missing), "stale": len(stale),
            "ok": ok, "fail": fail, "notes": notes[:12]}


def owed_comment_recapture() -> list[tuple[dict, str]]:
    """欠任务声明：①有新视频无评论存档 → 立即补；②存档超刷新窗 → 每天最多一批
    （20h 冷却，防连续刮评论触发风控）。dedup 幂等，在跑时合并。"""
    missing, stale = _recapture_plan()
    if not missing and not stale:
        return []
    if not missing:  # 只有刷新任务：一天一批
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=20)
        with Session(engine) as s:
            recent = s.exec(select(Job).where(  # type: ignore[attr-defined]
                Job.type == "comment_recapture",  # type: ignore[attr-defined]
                Job.created_at >= cutoff)  # type: ignore[attr-defined]
                .order_by(Job.created_at.desc())).first()  # type: ignore[attr-defined]
        if recent is not None:
            return []
    return [({"auto": True}, "1:comment_recapture")]


register_concurrent("comment_recapture")
register_owed("comment_recapture", owed_comment_recapture)


# ── 热评预筛（2026-11-20 阶段 1）：三道门前移到选题层——雷达不再按赞数裸排，
# 先判「背后有没有真事 / 能不能点名对象 / 是否同事件重复」，页面上不可做的灰掉。
# 屏幕结果落 MaterialAnalysis(kind=comment_screen)，键=评论文本，评论刷新不冲掉。


def _screen_pending(cap: int = 40) -> tuple[list[dict], list[str]]:
    """待筛热评 + 已做过稿的评论全文。做过稿=口播稿 payload 的文本锚（新稿自带，
    旧稿按存档解析）。"""
    from .hot_comments import collect_hot_comments, scripted_comment_texts, screen_map

    with Session(engine) as s:
        items, _bar = collect_hot_comments(s)
        done = list(scripted_comment_texts(s))
    screened = screen_map(s)
    pending = [x for x in items
               if x["comment"][:60] not in screened and x["comment"] not in done]
    return pending[:cap], [t for t in done if t][:30]


@register_executor("hot_comment_screen")
async def hot_comment_screen(ctx: JobContext, payload: dict) -> dict:
    """热评可成稿性预筛批量（light，LLM 一次判完本批）：doable/no_story/no_target/dup
    四档 + 大白话 reason，落 kind=comment_screen。批内循环到筛完（≤5 轮，纯 LLM 秒级）；
    owed 兜底：有欠筛任务补队。批内不许再 submit 自己——light 在跑时同类合并会被吞。"""
    from ..llm import gateway
    from ..prompts import render
    from .enrich import _save_analysis
    from .hot_comments import screen_map

    total_screened = total_doable = 0
    for _round in range(5):
        pending, done = _screen_pending()
        if not pending:
            break
        ctx.set_progress(min(90, 20 + 15 * _round), f"第 {_round + 1} 轮预筛 {len(pending)} 条")
        lines = "\n".join(
            f"{i + 1}｜{x['likes']} 赞｜《{(x['source'] or '')[:30]}》｜{x['comment']}"
            for i, x in enumerate(pending))
        data = await gateway.complete_json(
            [{"role": "user", "content": render(
                "comment_screen", DONE="\n".join(f"- {t[:80]}" for t in done) or "（暂无）",
                COMMENTS=lines)}],
            max_tokens=4000, thinking="disabled")
        verdicts: dict[int, dict] = {}
        for it in (data or {}).get("items") or []:
            try:
                i = int((it or {}).get("i") or 0)
            except (TypeError, ValueError):
                continue
            v = str((it or {}).get("verdict") or "")
            if i < 1 or i > len(pending) or v not in ("doable", "no_story", "no_target", "dup"):
                continue
            verdicts[i] = {"verdict": v, "reason": str((it or {}).get("reason") or "")[:80],
                           "dup_of": str((it or {}).get("dup_of") or "")[:40]}
        if not verdicts:
            raise RuntimeError("预筛结果为空（LLM 输出无法解析）")

        by_material: dict[int, list[dict]] = {}
        for i, x in enumerate(pending, 1):
            if i in verdicts:
                by_material.setdefault(int(x["material_id"]), []).append(
                    {"t": x["comment"][:60], **verdicts[i]})
        with Session(engine) as s:
            for mid, new_items in by_material.items():
                prev = s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                    MaterialAnalysis.material_id == mid,  # type: ignore[attr-defined]
                    MaterialAnalysis.kind == "comment_screen")  # type: ignore[attr-defined]
                    .order_by(MaterialAnalysis.version.desc())).first()  # type: ignore[attr-defined]
                merged: dict[str, dict] = {}
                for it in ((prev.payload or {}).get("items") or []) if prev is not None else []:
                    if isinstance(it, dict) and it.get("t"):
                        merged[str(it["t"])] = {k: it.get(k, "") for k in
                                                ("verdict", "reason", "dup_of")}
                for it in new_items:
                    merged[it["t"]] = {k: it.get(k, "") for k in ("verdict", "reason", "dup_of")}
                m_row = s.get(Material, mid)
                _save_analysis(s, mid, (m_row.tenant_id if m_row else 1) or 1,
                               "comment_screen",
                               {"items": [{"t": t, **v} for t, v in merged.items()]},
                               model="llm")
            s.commit()
        total_screened += len(verdicts)
        total_doable += sum(1 for v in verdicts.values() if v["verdict"] == "doable")
    with Session(engine) as s:
        remaining = len(_screen_pending()[0])
    ctx.set_progress(100, f"预筛完成 {total_screened} 条（可成稿 {total_doable}），余 {remaining} 条下轮续")
    return {"screened": total_screened, "doable": total_doable, "remaining": remaining}


register_concurrent("hot_comment_screen")


def owed_hot_comment_screen() -> list[tuple[dict, str]]:
    """欠任务声明（兜底）：还有热评没过预筛 → 补队（comment_recapture 尾部已主动 submit）。"""
    if _screen_pending()[0]:
        return [({"auto": True}, "1:hot_comment_screen")]
    return []


register_owed("hot_comment_screen", owed_hot_comment_screen)


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
