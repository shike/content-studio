"""话题雷达采集（素材域 P2）：监控话题 → 巡检新视频 → 低粉高赞命中自动入库拆解。

数据源=m.douyin.com challenge/aweme 接口（免登录直连，实测无互动统计字段）。
监控项=sources(kind=radar_topic)：config{seen_ids, hits, notify}；spike 判定与
命中登记语义与旧 radar_topics 表一致（P2 迁移映射见 TDD §15）。
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta, timezone

import httpx
from sqlmodel import Session, select

from ...db import engine
from ...jobs.errors import TransientError
from ...jobs.runner import JobContext, register_concurrent, register_executor
from ...models import Material, Source

_UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
SEEN_CAP = 300
SPIKE_NEW = 5  # 单次巡检新增视频数达到即判飙升
PROBE_PER_CHECK = 5  # 每次巡检每话题探测的最新视频条数（每条 ~10s，控制巡检时长）
LOW_FANS = 100_000   # 低粉线：粉丝 <10 万
RATIO_MIN = 2.0      # 高赞线：赞/粉比 ≥2


def _topic_sources(s: Session) -> list[Source]:
    return list(s.exec(select(Source).where(  # type: ignore[attr-defined]
        Source.kind == "radar_topic",  # type: ignore[attr-defined]
        Source.status == "active")).all())  # type: ignore[attr-defined]


def fetch_topic_videos(ch_id: str, count: int = 20) -> list[dict]:
    """拉话题下最新视频（免登录直连）。失败抛 TransientError 走任务重试。"""
    resp = httpx.get(
        "https://m.douyin.com/web/api/v2/challenge/aweme/",
        params={"ch_id": ch_id, "count": count},
        headers={"User-Agent": _UA_M},
        timeout=15,
    )
    if resp.status_code != 200:
        raise TransientError(f"话题接口 http {resp.status_code}")
    data = resp.json()
    if data.get("status_code") not in (0, None):
        raise TransientError(f"话题接口 status_code={data.get('status_code')}")
    out = []
    for a in data.get("aweme_list") or []:
        aid = str(a.get("aweme_id") or "")
        if aid:
            out.append({"id": aid, "desc": (a.get("desc") or "")[:60]})
    return out


def _notify(text: str) -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{text}" with title "跃迁内容工作室 话题雷达"'],
            capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001 通知失败不影响巡检
        pass


def _wait_detail(page, detail: dict, deadline_s: float = 60.0) -> None:
    """60s 轮询等待 detail XHR + _ROUTER_DATA 兜底（2026-11-13 从固定短窗升级）。"""
    import json as _json
    import re as _re
    import time as _time

    end = _time.time() + deadline_s
    while _time.time() < end:
        if detail:
            return
        page.wait_for_timeout(2000)
    if not detail:
        try:
            m = _re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
                           page.content(), _re.S)
            if m:
                data = _json.loads(m.group(1))
                for v in (data.get("loaderData") or {}).values():
                    items = (v.get("videoInfoRes") or {}).get("item_list") or []
                    if items:
                        detail.update({"aweme_detail": items[0]})
                        return
        except Exception:  # noqa: BLE001
            pass


def _fetch_aweme_detail(vid: str) -> dict:
    """免登录拉单视频 detail（PC web detail XHR 拦截 + _ROUTER_DATA 兜底）。
    _probe_video / _extract_hashtags 共用通道。"""
    from playwright.sync_api import sync_playwright

    from ...browser import launch, ua

    detail: dict = {}
    with sync_playwright() as p:
        browser = launch(p)
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
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            _wait_detail(page, detail)
        finally:
            browser.close()

    return detail.get("aweme_detail") or {}


def _probe_video(vid: str) -> dict:
    """免登录探测单视频：点赞/评论 + 作者粉丝数（PC web detail 双字段齐备）。"""
    ad = _fetch_aweme_detail(vid)
    a = ad.get("author") or {}
    st = ad.get("statistics") or {}
    ct = int(ad.get("create_time") or 0)
    return {
        "digg": int(st.get("digg_count") or 0),
        "comment": int(st.get("comment_count") or 0),
        "followers": int(a.get("follower_count") or 0),
        "author": (a.get("nickname") or "").strip()[:24],
        "create_time": (datetime.fromtimestamp(ct, tz=timezone.utc).replace(tzinfo=None)
                        if ct else None),
    }


def _merge_hits(existing: list, candidates: list[dict], cap: int = 20) -> tuple[list, int]:
    """合并低粉高赞命中：去重按视频 id，赞粉比降序，保留 cap 条。返回 (新清单, 新增数)。"""
    merged = {h["id"]: h for h in (existing or [])}
    added = 0
    for c in candidates:
        if c["id"] in merged:
            continue
        merged[c["id"]] = c
        added += 1
    out = sorted(merged.values(), key=lambda h: -h.get("ratio", 0))[:cap]
    return out, added


def _extract_hashtags(vid: str) -> list[dict]:
    """免登录：从视频 detail 的 text_extra 提取话题（ch_id + 名称）。"""
    ad = _fetch_aweme_detail(vid)
    out: list[dict] = []
    seen: set[str] = set()
    for t in ad.get("text_extra") or []:
        hid = str(t.get("hashtag_id") or "")
        hname = str(t.get("hashtag_name") or "").strip()
        if hid.isdigit() and hname and hid not in seen:
            seen.add(hid)
            out.append({"ch_id": hid, "name": hname})
    return out


async def check_topic(row: Source) -> dict:
    """巡检单个话题（sources 行）：拉内容样本 → 识别新出现视频 → 刷新样本快照 →
    低粉高赞命中自动入库（inbox）过拆解闸。row 可为脱管实例（调用方 merge）。"""
    vids = await asyncio.to_thread(fetch_topic_videos, row.ext_id)
    cfg = dict(row.config or {})
    seen = set(cfg.get("seen_ids") or [])
    fresh_ids = {v["id"] for v in vids if v["id"] not in seen}
    name = row.name
    known_hit_ids = {h.get("id") for h in (cfg.get("hits") or []) if isinstance(h, dict)}
    notify = bool(cfg.get("notify", True))
    with Session(engine) as s:
        row = s.merge(row)
        c2 = dict(row.config or {})
        c2["seen_ids"] = ((c2.get("seen_ids") or []) + sorted(fresh_ids))[-SEEN_CAP:]
        row.config = c2
        row.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)
        s.add(row)
        s.commit()
    spiked = bool(row.status == "active" and notify and len(fresh_ids) >= SPIKE_NEW and seen)

    # 低粉高赞探测：话题下最新 N 条，赞粉比 ≥2 且粉丝 <10万 入素材（inbox 过闸）。
    # 免登录话题接口返回固定精选集（排序/翻页参数全被无视），已命中过的 id 不再重复探。
    hit_candidates: list[dict] = []
    for v in vids[:PROBE_PER_CHECK]:
        if v["id"] in known_hit_ids:
            continue
        try:
            info = await asyncio.to_thread(_probe_video, v["id"])
            ratio = round(info["digg"] / max(info["followers"], 1), 1)
            if info["followers"] < LOW_FANS and ratio >= RATIO_MIN:
                hit_candidates.append({
                    "id": v["id"], "desc": v["desc"], "author": info["author"],
                    "digg": info["digg"], "followers": info["followers"], "ratio": ratio,
                })
        except Exception as e:  # noqa: BLE001 单条探测失败跳过
            print(f"[radar] 探测 {v['id']} 失败跳过: {type(e).__name__}: {str(e)[:60]}")
    hit_note = ""
    if hit_candidates:
        with Session(engine) as s:
            row2 = s.merge(row)
            c3 = dict(row2.config or {})
            hits, added = _merge_hits(c3.get("hits") or [], hit_candidates)
            c3["hits"] = hits
            row2.config = c3
            s.add(row2)
            s.commit()
        hit_note = (f"，低粉高赞 +{added}（{hit_candidates[0]['desc'][:20]}"
                    f" 赞粉比 {hit_candidates[0]['ratio']}）")
        await asyncio.to_thread(_notify, f"#{name} 发现低粉高赞 {added} 条：{hit_candidates[0]['desc'][:60]}")
        # 雷达命中自动入库+拆解：达标命中建 inbox 素材 → 同一道质量闸
        try:
            from ..service import gate_rows, upsert_material

            new_rows = []
            with Session(engine) as s:
                for h in hit_candidates:
                    url = f"https://www.douyin.com/video/{h['id']}"
                    m, created = upsert_material(
                        s, tenant_id=row2.tenant_id or 1, origin_type="douyin_video",
                        origin_url=url, author=str(h.get("author") or "")[:60],
                        title=str(h.get("desc") or "")[:80],
                        stats={"digg": int(h.get("digg") or 0),
                               "followers": int(h.get("followers") or 0),
                               "ratio": h.get("ratio")},
                        source_id=row2.id, note="话题雷达命中")
                    if created:
                        new_rows.append(m)
            if new_rows:
                n_radar = await gate_rows(new_rows, f"话题雷达#{name}")
                if n_radar:
                    hit_note += f"，自动拆解 {n_radar} 条"
        except Exception as e:  # noqa: BLE001 自动拆解失败不影响雷达巡检本身
            hit_note += f"（自动入库拆解失败：{type(e).__name__}）"
    if spiked:
        titles = "；".join(v["desc"] for v in vids[:2])
        await asyncio.to_thread(_notify, f"#{name} 新增 {len(fresh_ids)} 条视频：{titles[:80]}")
    return {"name": name, "new": len(fresh_ids), "spiked": spiked}


register_concurrent("material_topic_backfill")


@register_executor("material_topic_check")
async def material_topic_check(ctx: JobContext, payload: dict) -> dict:
    """单话题即时巡检（采集源页「巡检」按钮）：样本中新出现的低粉高赞照旧自动入库拆解。"""
    source_id = int(payload.get("source_id") or 0)
    with Session(engine) as s:
        row = s.get(Source, source_id)
        if row is None or row.kind != "radar_topic":
            raise RuntimeError("话题不存在")
    r = await check_topic(row)
    return {"name": r["name"], "new": r["new"], "spiked": r["spiked"]}


@register_executor("material_topic_backfill")
async def material_topic_backfill(ctx: JobContext, payload: dict) -> dict:
    """话题存量回填（追溯窗参数化）：逐启用话题把精选集全部探一遍——发布时间
    ≤backfill_window_days + 拆解闸同口径 → 未入库建 inbox 素材自动拆解。
    免登录话题接口为固定精选集（翻页参数被无视），回填深度=精选集全量；
    命中登记进 config.hits（夜间探测不再重复探同一条）。"""
    from ..service import gate_rows, upsert_material
    from ...settings import settings

    cmin = max(1, int(settings.dissect_comment_min or 20))
    dmin = max(1, int(settings.dissect_digg_min or 300))
    days = max(1, int(settings.backfill_window_days or 30))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)
    with Session(engine) as s:
        topics = _topic_sources(s)
    if not topics:
        ctx.set_progress(100, "无启用话题，诚实空过")
        return {"topics": 0, "probed": 0, "created": 0, "queued": 0, "notes": []}
    probed = created_n = queued = 0
    notes: list[str] = []
    for i, trow in enumerate(topics, 1):
        ctx.set_progress(int(90 * (i - 1) / max(len(topics), 1)), f"回填 #{trow.name}（{i}/{len(topics)}）")
        try:
            vids = await asyncio.to_thread(fetch_topic_videos, trow.ext_id, 20)
        except Exception as e:  # noqa: BLE001 单话题失败不拖垮整体
            notes.append(f"#{trow.name} 拉取失败：{type(e).__name__}: {str(e)[:50]}")
            continue
        created_rows: list = []
        probed_local = 0
        for v in vids:
            url = f"https://www.douyin.com/video/{v['id']}"
            with Session(engine) as s:
                if s.exec(select(Material).where(  # type: ignore[attr-defined]
                        Material.origin_url == url)).first() is not None:
                    continue  # 已在素材库（日常增量/此前回填），跳过
            try:
                info = await asyncio.to_thread(_probe_video, v["id"])
            except Exception:  # noqa: BLE001 单条探测失败跳过
                continue
            probed += 1
            probed_local += 1
            if not info.get("create_time") or info["create_time"] < cutoff:
                continue  # 超追溯窗（精选集里也可能混旧内容）
            c, dg = int(info.get("comment") or 0), int(info.get("digg") or 0)
            followers = int(info.get("followers") or 0)
            if c < cmin and dg < dmin:
                continue  # 不过拆解闸——不建行不耗拆解
            with Session(engine) as s:
                row = s.merge(trow)
                m, created = upsert_material(
                    s, tenant_id=row.tenant_id or 1, origin_type="douyin_video",
                    origin_url=url, author=str(info.get("author") or "")[:60],
                    title=str(v.get("desc") or "")[:80],
                    stats={"comment": c, "digg": dg, "followers": followers},
                    source_id=row.id, published_at=info.get("create_time"),
                    note=f"雷达回填（{days} 天存量）")
                if created:
                    created_rows.append(m)
                    created_n += 1
                c2 = dict(row.config or {})
                hits, _added = _merge_hits(c2.get("hits") or [],
                                           [{"id": v["id"], "desc": v.get("desc", ""),
                                             "author": info.get("author", ""),
                                             "digg": dg, "followers": followers,
                                             "ratio": round(dg / max(followers, 1), 1)}])
                c2["hits"] = hits
                row.config = c2
                s.add(row)
                s.commit()
        if created_rows:
            n = await gate_rows(created_rows, f"话题回填#{trow.name}")
            queued += n
        notes.append(f"#{trow.name}：精选 {len(vids)}，探 {probed_local}，过闸建行 {len(created_rows)}")
        await asyncio.sleep(2)
    ctx.set_progress(100, f"回填完成（{days} 天窗）：{len(topics)} 话题 / 探 {probed} / 过闸建行 {created_n} / 排队拆解 {queued}")
    return {"topics": len(topics), "probed": probed, "created": created_n,
            "queued": queued, "days": days, "notes": notes[:20]}
