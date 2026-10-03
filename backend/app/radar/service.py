"""话题雷达：监控泛 AI 类抖音话题，按日巡检话题下新视频测热度。

数据源=m.douyin.com challenge/aweme 接口（免登录 直连，实测无互动统计字段），
热度代理=话题下新增视频速度；单次巡检新增 ≥5 条判为飙升，macOS 通知推送。
"""
from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timezone

import httpx
import re
from playwright.sync_api import sync_playwright
from sqlmodel import Session, select

from ..db import engine
from ..jobs.errors import TransientError
from ..jobs.runner import JobContext, register_concurrent, register_executor
from ..models import RadarTopic

_UA_M = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
SEEN_CAP = 300
SPIKE_NEW = 5  # 单次巡检新增视频数达到即判飙升
PROBE_PER_CHECK = 5  # 每次巡检每话题探测的最新视频条数（每条 ~10s，控制巡检时长）
LOW_FANS = 100_000   # 低粉线：粉丝 <10 万
RATIO_MIN = 2.0      # 高赞线：赞/粉比 ≥2

SEED_TOPICS: list[tuple[str, str]] = []  # 监控话题是运营资产不入库：部署者在雷达页添加
                                         # （贴一条带话题的视频链接可自动提取 ch_id，见 extract_hashtags）


def fetch_topic_videos(ch_id: str, count: int = 20) -> list[dict]:
    """拉话题下最新视频（免登录 直连）。失败抛 TransientError 走任务重试。"""
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


def _probe_video(vid: str) -> dict:
    """免登录 探测单视频：点赞数 + 作者粉丝数（PC web detail 双字段齐备）。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

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
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    a = ad.get("author") or {}
    st = ad.get("statistics") or {}
    out = {
        "digg": int(st.get("digg_count") or 0),
        "followers": int(a.get("follower_count") or 0),
        "author": (a.get("nickname") or "").strip()[:24],
    }
    return out


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
    """免登录：从视频 detail 的 text_extra 提取话题（ch_id + 名称）。

    这是"添加监控话题免输 ch_id"的正解——贴一条带该话题的视频即可。"""
    from playwright.sync_api import sync_playwright

    from ..browser import launch, ua

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
            page.goto(f"https://www.douyin.com/video/{vid}",
                      timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
        finally:
            browser.close()

    ad = detail.get("aweme_detail") or {}
    out: list[dict] = []
    seen: set[str] = set()
    for t in ad.get("text_extra") or []:
        hid = str(t.get("hashtag_id") or "")
        hname = str(t.get("hashtag_name") or "").strip()
        if hid.isdigit() and hname and hid not in seen:
            seen.add(hid)
            out.append({"ch_id": hid, "name": hname})
    return out


register_concurrent("radar_extract")
@register_executor("radar_extract")
async def extract_hashtags(ctx: JobContext, payload: dict) -> dict:
    """雷达话题提取：贴视频链接 → 提取话题 ch_id/名称。风控间歇失败，自动重试。"""
    from ..benchmarks.downloader import extract_douyin_url, resolve_aweme_id
    from ..browser import launch, ua

    url = extract_douyin_url((payload.get("url") or "").strip())
    vid = resolve_aweme_id(url) if url else None
    if not vid:
        raise RuntimeError("不是有效的抖音视频链接")
    ctx.set_progress(30, "打开视频页提取话题")

    def _run() -> list[dict]:
        detail: dict = {}
        with sync_playwright() as p:
            browser = launch(p)  # 统一出口（含可选代理）
            try:
                c = browser.new_context(user_agent=ua(), locale="zh-CN",
                                        viewport={"width": 1280, "height": 900})
                page = c.new_page()

                def on_response(resp):
                    try:
                        if "aweme/v1/web/aweme/detail" in resp.url and not detail:
                            detail.update(resp.json())
                    except Exception:  # noqa: BLE001
                        pass

                page.on("response", on_response)
                page.goto(f"https://www.douyin.com/video/{vid}",
                          timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(6000)
            finally:
                browser.close()
        ad = detail.get("aweme_detail") or {}
        out: list[dict] = []
        seen: set[str] = set()
        for t in ad.get("text_extra") or []:
            hid = str(t.get("hashtag_id") or "")
            hname = str(t.get("hashtag_name") or "").strip()
            if hid.isdigit() and hname and hid not in seen:
                seen.add(hid)
                out.append({"ch_id": hid, "name": hname})
        return out

    tags = await asyncio.to_thread(_run)
    if not tags:
        raise TransientError("该视频未提取到话题标签（可能无标签或被风控），重试或换一条带话题的视频")
    ctx.set_progress(100, f"提取到 {len(tags)} 个话题")
    return {"video_id": vid, "hashtags": tags}


async def check_topic(row: RadarTopic) -> dict:
    """巡检单个话题：拉最新视频 → 识别新增（热度速度）→ 刷新最新视频列表 → 飙升通知。

    row 可为脱管实例（调用方会话已关）：merge 回当前会话再改。
    recent 恒为话题下当前最新视频（每次巡检刷新，带 new 标记）——
    没有新增时列表不能被清空（用户要始终看得到话题里在发生什么）。"""
    vids = await asyncio.to_thread(fetch_topic_videos, row.ch_id)
    seen = set(row.seen_ids or [])
    fresh_ids = {v["id"] for v in vids if v["id"] not in seen}
    spiked = False
    name = row.name
    recent = [dict(v, new=v["id"] in fresh_ids) for v in vids[:10]]
    with Session(engine) as s:
        row = s.merge(row)
        row.seen_ids = ((row.seen_ids or []) + sorted(fresh_ids))[-SEEN_CAP:]
        row.new_since_last = len(fresh_ids)
        row.recent = recent
        row.last_checked_at = datetime.now(timezone.utc)
        spiked = bool(row.enabled and row.notify and row.new_since_last >= SPIKE_NEW and seen)
        s.add(row)
        s.commit()

    # 低粉高赞探测（小账号爆款信号）：探测话题下最新 5 条，赞粉比 ≥2 且粉丝 <10万 入清单。
    # 每条 ~10s（playwright detail）；单条失败跳过不阻塞。
    hit_candidates: list[dict] = []
    for i, v in enumerate(vids[:PROBE_PER_CHECK]):
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
            row = s.merge(row)
            hits, added = _merge_hits(row.hits, hit_candidates)
            row.hits = hits
            s.add(row)
            s.commit()
        hit_note = (f"，低粉高赞 +{added}（{hit_candidates[0]['desc'][:20]}"
                    f" 赞粉比 {hit_candidates[0]['ratio']}）")
        await asyncio.to_thread(_notify, f"#{name} 发现低粉高赞 {added} 条：{hit_candidates[0]['desc'][:60]}")
    if spiked:
        titles = "；".join(v["desc"] for v in recent[:2])
        await asyncio.to_thread(_notify, f"#{name} 新增 {row_new if (row_new := len(fresh_ids)) else 0} 条视频：{titles[:80]}")
    return {"name": name, "new": len(fresh_ids), "spiked": spiked}


async def mine_from_benchmarks(ctx: JobContext) -> dict:
    """从拆解库挖掘可监控话题：探测带 # 标签的已批准视频，汇总话题 chips。"""
    from ..models import BenchmarkVideo

    with Session(engine) as s:
        vids = s.exec(
            select(BenchmarkVideo).where(  # type: ignore[attr-defined]
                BenchmarkVideo.scan_status == "approved"  # type: ignore[attr-defined]
            )
        ).all()
    from ..benchmarks.downloader import resolve_aweme_id

    cands = [v for v in vids if "#" in (v.title or "")]
    cands.sort(key=lambda v: -v.id)
    cands = cands[:6]
    if not cands:
        return {"hashtags": [], "notes": ["拆解库暂无带话题标签的视频"]}
    chips: dict[str, dict] = {}
    for i, v in enumerate(cands):
        ctx.set_progress(int(90 * i / len(cands)), f"探测 {v.title[:28]}")
        vid = resolve_aweme_id(v.url) if "douyin.com" in (v.url or "") else None
        if not vid:
            vid = re.search(r"(\d{15,})", v.url or "").group(1) if re.search(r"(\d{15,})", v.url or "") else None
        if not vid:
            continue
        try:
            tags = await asyncio.to_thread(_extract_hashtags, vid)
        except Exception as e:  # noqa: BLE001 单视频失败跳过
            print(f"[radar-mine] {vid} 提取失败跳过: {type(e).__name__}: {str(e)[:60]}")
            continue
        for t in tags:
            if t["ch_id"] not in chips:
                chips[t["ch_id"]] = {**t, "from": (v.title or "")[:36]}
    ctx.set_progress(100, f"挖掘到 {len(chips)} 个话题")
    return {"hashtags": list(chips.values())}


def ensure_seeded() -> None:
    """空表时种入预置话题（SEED_TOPICS 默认空=不预置，部署者自行添加）。"""
    with Session(engine) as s:
        if s.exec(select(RadarTopic)).first() is not None:
            return
        for ch_id, name in SEED_TOPICS:
            s.add(RadarTopic(ch_id=ch_id, name=name))
        s.commit()


register_concurrent("radar_mine")
@register_executor("radar_mine")
async def radar_mine_ep(ctx: JobContext, payload: dict) -> dict:
    return await mine_from_benchmarks(ctx)


register_concurrent("topic_radar")
@register_executor("topic_radar")
async def topic_radar(ctx: JobContext, payload: dict) -> dict:
    with Session(engine) as s:
        rows = s.exec(
            select(RadarTopic).where(RadarTopic.enabled == True)  # noqa: E712
        ).all()
    if not rows:
        ctx.set_progress(100, "无启用的监控话题")
        return {"notes": ["未配置监控话题"], "spiked": 0}
    notes: list[str] = []
    spiked = failed = 0
    for i, row in enumerate(rows):
        ctx.set_progress(int(90 * i / len(rows)), f"巡检 #{row.name}")
        try:
            r = await check_topic(row)
        except TransientError as e:
            failed += 1
            notes.append(f"#{row.name}：{e}")
            continue
        except Exception as e:  # noqa: BLE001 单话题失败不拖垮整轮
            failed += 1
            notes.append(f"#{row.name}：{type(e).__name__}: {str(e)[:60]}")
            continue
        notes.append(f"#{row.name}：新增 {r['new']} 条" + ("（飙升，已推送）" if r["spiked"] else ""))
        spiked += int(r["spiked"])
        await asyncio.sleep(3)  # 话题接口 pacing
    if failed == len(rows):
        raise TransientError(f"全部 {len(rows)} 个话题巡检失败（接口可能限流），稍后重试")
    ctx.set_progress(100, f"巡检完成：{len(rows)} 个话题，{spiked} 个飙升")
    return {"spiked": spiked, "failed": failed, "notes": notes}
