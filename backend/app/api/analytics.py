"""经营看板 API（PRD R6.3）：总览 / 渠道对比 / 账号趋势 / 作品表现 / 周报。

只读事实表（metrics_snapshots/analytics_reports/contents/publications/self_videos）；
缺数显式「未回流」，不补零；不做素材/内容效果归因（R6.6 边界）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import (Account, Content, MetricsSnapshot,
                      Publication, SelfVideo, User)

router = APIRouter(prefix="/api/analytics")

_ACCOUNT_KEYS = ("fans", "new_fans", "profile_views", "play", "leads")
_PUB_KEYS = ("reads", "welook", "follows", "leads", "exposure", "likes",
             "comments", "shares", "collects", "read_done")

# 判卷指标白名单（2026-11-21 阶段 4 通用补录：键必须落在对应平台的口径内）
_PUB_METRIC_KEYS = {
    "douyin": ("exposure", "play", "likes", "comments", "shares", "collects", "follows", "leads"),
    "xiaohongshu": ("exposure", "likes", "comments", "shares", "collects", "follows", "leads"),
    "wechat": ("reads", "read_done", "welook", "shares", "follows", "leads"),
}


@router.get("/judgement")
def judgement(days: int = 30, user: User = Depends(require_user)) -> dict:
    """三平台判卷（2026-11-21 阶段 4）：已发布作品逐一判爆款/过线/待改进（线=judgement.py 假设值）。

    数据源优先级：MetricsSnapshot（publication 维度最新一行）→ payload.stats →
    抖音自有 SelfVideo 快照（复用阶段 1 联表）。判卷权在数据，这里只折算。"""
    from ..analytics.judgement import judge

    import re as _re

    with Session(engine) as s:
        pubs = s.exec(select(Publication).where(  # type: ignore[attr-defined]
            Publication.tenant_id == user.tenant_id)).all()  # type: ignore[attr-defined]
        vids = {v.aweme_id: v for v in s.exec(select(SelfVideo)).all()  # type: ignore[attr-defined]
                if v.aweme_id}
        items: list[dict] = []
        for p in sorted(pubs, key=lambda x: (x.published_at or ""), reverse=True):
            c = s.get(Content, p.content_id)
            if c is None:
                continue
            snap = s.exec(
                select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_type == "publication",  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_id == c.id)  # type: ignore[attr-defined]
                .order_by(MetricsSnapshot.date.desc())).first()  # type: ignore[attr-defined]
            m = dict((snap.metrics if snap else None) or (c.payload or {}).get("stats") or {})
            if p.platform == "douyin" and not m.get("play"):
                mm = _re.search(r"video/(\d+)", p.url or "")
                v = vids.get(mm.group(1)) if mm else None
                if v is not None:
                    m = dict(v.stats or {})
            verdict = judge(p.platform, m)
            items.append({"content_id": c.id, "kind": c.kind, "platform": p.platform,
                          "title": c.title, "url": p.url,
                          "published_at": p.published_at,
                          "metrics": {k: int(m.get(k) or 0) for k in _PUB_KEYS if m.get(k)},
                          **verdict})
        summary: dict[str, int] = {}
        for it in items:
            summary[it["verdict"]] = summary.get(it["verdict"], 0) + 1
        return {"items": items, "summary": summary,
                "lines": {"douyin_play_viral": 30000, "xhs_interact_viral": 3.0,
                          "wechat_readdone_viral": 65.0}}


class BackfillIn(BaseModel):
    metrics: dict  # 键=平台口径（见 _PUB_METRIC_KEYS），值=非负整数


@router.post("/publications/{content_id}/metrics")
def backfill_publication_metrics(content_id: int, body: BackfillIn,
                                 user: User = Depends(require_user)) -> dict:
    """作品指标通用补录（2026-11-21 阶段 4）：按平台口径键白名单收，同主体同日按列合并。

    口径：wechat 原有 /articles/{id}/reflow 保留不破坏；xhs/douyin 统一走本端点。"""
    with Session(engine) as s:
        pub = s.exec(select(Publication).where(  # type: ignore[attr-defined]
            Publication.content_id == content_id)).first()  # type: ignore[attr-defined]
        if pub is None:
            raise HTTPException(status_code=404, detail="该内容没有发布登记")
        platform = pub.platform
        allowed = _PUB_METRIC_KEYS.get(platform)
        if not allowed:
            raise HTTPException(status_code=400, detail=f"平台 {platform} 不支持补录")
        vals = {}
        for k, v in (body.metrics or {}).items():
            if k not in allowed:
                raise HTTPException(status_code=400, detail=f"{platform} 不收指标「{k}」")
            try:
                vals[k] = max(0, int(v))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"指标「{k}」必须是整数") from None
        if not vals:
            raise HTTPException(status_code=400, detail="没有可补录的指标")
        c = s.get(Content, content_id)
        if c is None:
            raise HTTPException(status_code=404, detail="内容不存在")
        today = datetime.now().strftime("%Y-%m-%d")
        # payload.stats 快照（作品表现页的兜底源）
        p = dict(c.payload or {})
        stats = dict(p.get("stats") or {})
        stats.update(vals)
        stats["filled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        p["stats"] = stats
        c.payload = p
        s.add(c)
        # 事实表：同主体同日按列合并（R6.2 口径）
        row = s.exec(select(MetricsSnapshot).where(  # type: ignore[attr-defined]
            MetricsSnapshot.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
            MetricsSnapshot.subject_type == "publication",  # type: ignore[attr-defined]
            MetricsSnapshot.subject_id == content_id,  # type: ignore[attr-defined]
            MetricsSnapshot.date == today)).first()  # type: ignore[attr-defined]
        if row is None:
            row = MetricsSnapshot(tenant_id=user.tenant_id, subject_type="publication",
                                  subject_id=content_id, platform=platform, date=today,
                                  metrics={}, source="manual")
        merged = dict(row.metrics or {})
        merged.update(vals)
        row.metrics = merged
        row.source = "manual"
        s.add(row)
        s.commit()
    from ..analytics.judgement import judge

    return {"ok": True, "metrics": vals, "verdict": judge(platform, vals)}


def _days_param(days: int) -> str:
    return (date.today() - timedelta(days=max(1, min(days, 120)))).isoformat()


def _since_dt(days: int) -> datetime:
    """N 天前的 00:00（datetime 对象——PG timestamp 列不吃 varchar 比较，冒烟实测）。"""
    return datetime.combine(date.today() - timedelta(days=max(1, min(days, 120))),
                            datetime.min.time())


@router.get("/overview")
def overview(days: int = 30, user: User = Depends(require_user)) -> dict:
    """总览：账号经营合计（近 N 天）+ 各渠道内容产出/发布数 + 数据新鲜度。"""
    since = _days_param(days)  # metrics.date 是 varchar 日期，字符串比较正确
    with Session(engine) as s:
        snaps = s.exec(
            select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                MetricsSnapshot.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
                MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                MetricsSnapshot.date >= since)).all()  # type: ignore[attr-defined]
        agg: dict[str, int] = {k: 0 for k in _ACCOUNT_KEYS}
        latest_fans = 0
        latest_date = ""
        for r in sorted(snaps, key=lambda x: x.date):
            m = r.metrics or {}
            for k in _ACCOUNT_KEYS:
                agg[k] += int(m.get(k) or 0)
            if int(m.get("fans") or 0) and r.date >= latest_date:
                latest_fans = int(m.get("fans") or 0)
                latest_date = r.date
        contents_n = s.exec(
            select(Content.id).where(  # type: ignore[attr-defined]
                Content.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
                Content.created_at >= _since_dt(days))).all()  # type: ignore[attr-defined]
        pubs = s.exec(
            select(Publication).where(  # type: ignore[attr-defined]
                Publication.tenant_id == user.tenant_id)).all()  # type: ignore[attr-defined]
        pub_recent = [p for p in pubs if (p.published_at or "")[:10] >= since]
        last_pub = max((p.published_at or "" for p in pubs), default="")
        return {
            "days": days,
            "accounts": {**agg, "fans_current": latest_fans,
                         "fans_as_of": latest_date or None,
                         "days_with_data": len({r.date for r in snaps})},
            "contents_new": len(contents_n),
            "published_total": len(pubs),
            "published_recent": len(pub_recent),
            "last_publish_at": last_pub or None,
        }


@router.get("/channels")
def channels_compare(days: int = 30, user: User = Depends(require_user)) -> dict:
    """渠道对比：每渠道的内容量/发布量/账号指标合计（自有账号渠道才有指标）。"""
    with Session(engine) as s:
        from ..models import Channel

        chans = s.exec(select(Channel)).all()  # type: ignore[attr-defined]
        out = []
        for ch in chans:
            n_contents = s.exec(
                select(func.count(Content.id)).where(  # type: ignore[arg-type]
                    Content.channel_id == int(ch.id or 0),  # type: ignore[attr-defined]
                    Content.created_at >= _since_dt(days))).one() or 0  # type: ignore[attr-defined]
            n_pubs = 0
            for c in s.exec(select(Content).where(  # type: ignore[attr-defined]
                    Content.channel_id == int(ch.id or 0))).all():  # type: ignore[attr-defined]
                n_pubs += s.exec(
                    select(func.count(Publication.id)).where(  # type: ignore[arg-type]
                        Publication.content_id == int(c.id or 0))).one() or 0  # type: ignore[attr-defined]
            accs = s.exec(select(Account).where(  # type: ignore[attr-defined]
                Account.channel_id == int(ch.id or 0))).all()  # type: ignore[attr-defined]
            fans = 0
            for a in accs:
                latest = s.exec(
                    select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                        MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                        MetricsSnapshot.subject_id == int(a.id or 0))  # type: ignore[attr-defined]
                    .order_by(MetricsSnapshot.date.desc())).first()  # type: ignore[attr-defined]
                fans += int((latest.metrics or {}).get("fans") or 0) if latest else 0
            out.append({"key": ch.key, "name": ch.name, "platform": ch.platform,
                        "enabled": bool(ch.enabled), "contents_new": n_contents,
                        "published": n_pubs, "accounts": len(accs),
                        "fans_total": fans or None})
        return {"items": out, "days": days}


@router.get("/accounts")
def accounts_trend(days: int = 30, user: User = Depends(require_user)) -> dict:
    """账号趋势：每自有账号的最新摘要 + 近 N 天序列（缺回流标「未回流」）。"""
    since = _days_param(days)
    with Session(engine) as s:
        accs = s.exec(select(Account).order_by(Account.id)).all()  # type: ignore[attr-defined]
        out = []
        for a in accs:
            rows = s.exec(
                select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_type == "account",  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_id == int(a.id or 0),  # type: ignore[attr-defined]
                    MetricsSnapshot.date >= since)).all()  # type: ignore[attr-defined]
            rows = sorted(rows, key=lambda r: r.date)
            series = [{"date": r.date, **{k: int((r.metrics or {}).get(k) or 0)
                                          for k in _ACCOUNT_KEYS}} for r in rows]
            latest = series[-1] if series else None
            out.append({"id": a.id, "name": a.name, "platform": a.platform,
                        "enabled": bool(a.enabled), "days": len(series),
                        "latest": latest,
                        "series": series})
        return {"items": out, "days": days}


@router.get("/publications")
def publications_perf(channel: str = "", limit: int = 30,
                      user: User = Depends(require_user)) -> dict:
    """作品表现：已发布登记的作品 + 各自指标（公众号走事实表；抖音自有走 self_videos 快照）。"""
    with Session(engine) as s:
        from ..channels.engine import channel_id_of

        ch_filter = channel_id_of(s, channel) if channel else None
        pubs = s.exec(
            select(Publication).where(  # type: ignore[attr-defined]
                Publication.tenant_id == user.tenant_id)).all()  # type: ignore[attr-defined]
        out = []
        for p in sorted(pubs, key=lambda x: (x.published_at or ""), reverse=True):
            c = s.get(Content, p.content_id)
            if c is None or (ch_filter and c.channel_id != ch_filter):
                continue
            snap = s.exec(
                select(MetricsSnapshot).where(  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_type == "publication",  # type: ignore[attr-defined]
                    MetricsSnapshot.subject_id == c.id)  # type: ignore[attr-defined]
                .order_by(MetricsSnapshot.date.desc())).first()  # type: ignore[attr-defined]
            m = (snap.metrics if snap else None) or (c.payload or {}).get("stats") or {}
            out.append({"content_id": c.id, "title": c.title, "kind": c.kind,
                        "platform": p.platform, "url": p.url,
                        "published_at": p.published_at, "mode": p.publish_mode,
                        "metrics": {k: int(m.get(k) or 0) for k in _PUB_KEYS if k in m},
                        "backfilled": bool(snap)})
        # 抖音自有作品（self_videos：播放/互动快照，风格素材兼作品层）
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
        for v in sorted(vids, key=lambda x: x.published_at or "", reverse=True)[:max(0, limit - len(out))]:
            st = v.stats or {}
            if not int(st.get("play") or 0):
                continue
            out.append({"content_id": None, "title": v.title, "kind": "douyin_video",
                        "platform": "douyin", "url": v.url,
                        "published_at": (v.published_at or "")[:16], "mode": "manual",
                        "metrics": {k: int(st.get(k) or 0)
                                    for k in ("play", "digg", "comment", "share", "collect") if st.get(k)},
                        "backfilled": True})
        return {"items": out[:max(1, min(limit, 100))]}


@router.get("/weekly")
def weekly(user: User = Depends(require_user)) -> dict:
    """最新一期周报（只读缓存）。"""
    from ..selfstyle.weekly_report import cached_weekly_report

    rep = cached_weekly_report(user.tenant_id)
    if rep is None:
        return {"week": "", "message": "还没有周报：点「生成周报」跑一次（数据来自每日回流）"}
    return rep


@router.post("/weekly/recompute", status_code=202)
async def weekly_recompute(user: User = Depends(require_user)) -> dict:
    job = await runner.submit("video_weekly_report", {"force": True},
                              dedup_key=f"data:weekly:{datetime.now():%Y%m%d}",
                              actor=(user.tenant_id, user.id, user.role))
    return {"job_id": job.id}


# ── 发布排期与指标轨迹（R3 2026-11-22） ──

_KIND_PLATFORM = {"video_script": "douyin", "long_article": "wechat", "note": "xiaohongshu"}


@router.get("/schedule")
def schedule_overview(days: int = 14, user: User = Depends(require_user)) -> dict:
    """排期总览（R3-2）：未来 N 天按日分组的排期项 + ready 未排期清单 + 本周已发布。

    排期=Content.payload.publish_plan（人工设定）；平台按 kind 推断（登记后以登记为准）。"""
    days = max(1, min(days, 31))
    from datetime import timedelta

    with Session(engine) as s:
        planned = s.execute(text(
            "SELECT id, title, kind, payload->>'publish_plan' AS plan FROM contents "
            "WHERE tenant_id=:tid AND status IN ('ready','review') "
            "AND COALESCE(payload->>'publish_plan','')<>'' "
            "ORDER BY payload->>'publish_plan'"), {"tid": user.tenant_id}).mappings().all()
        unscheduled = s.execute(text(
            "SELECT id, title, kind, updated_at FROM contents c "
            "WHERE tenant_id=:tid AND status='ready' "
            "AND kind IN ('video_script','long_article','note') "
            "AND COALESCE(payload->>'publish_plan','')='' "
            "AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id) "
            "ORDER BY updated_at DESC LIMIT 30"), {"tid": user.tenant_id}).mappings().all()
        local_today = datetime.now()
        monday = (local_today - timedelta(days=local_today.weekday())).strftime("%Y-%m-%d")
        published_week = s.execute(text(
            "SELECT p.content_id, p.platform, p.published_at, c.title FROM publications p "
            "JOIN contents c ON c.id=p.content_id WHERE p.tenant_id=:tid "
            "AND substr(p.published_at,1,10) >= :monday "
            "ORDER BY p.published_at DESC LIMIT 30"), {"tid": user.tenant_id, "monday": monday}).mappings().all()

    today_s = local_today.strftime("%Y-%m-%d")
    horizon = (local_today + timedelta(days=days - 1)).strftime("%Y-%m-%d")
    by_day: dict[str, list] = {}
    for r in planned:
        day = str(r["plan"])[:10]
        if day < today_s or day > horizon:
            continue
        by_day.setdefault(day, []).append({
            "content_id": int(r["id"]), "title": r["title"], "kind": r["kind"],
            "publish_plan": str(r["plan"]), "platform": _KIND_PLATFORM.get(r["kind"], ""),
        })
    upcoming = [{"date": d, "items": by_day[d]} for d in sorted(by_day)]
    return {
        "upcoming": upcoming,
        "unscheduled": [{"id": int(r["id"]), "title": r["title"], "kind": r["kind"],
                         "platform": _KIND_PLATFORM.get(r["kind"], "")} for r in unscheduled],
        "published_week": [dict(r) for r in published_week],
        "today": today_s,
    }


@router.get("/publications/{content_id}/series")
def publication_series(content_id: int, user: User = Depends(require_user)) -> dict:
    """指标轨迹（R3-3）：该作品全部快照按日升序，tn=相对发布登记日的天数（发布日=0）。"""
    with Session(engine) as s:
        rows = s.exec(select(MetricsSnapshot).where(  # type: ignore[attr-defined]
            MetricsSnapshot.tenant_id == user.tenant_id,  # type: ignore[attr-defined]
            MetricsSnapshot.subject_type == "publication",  # type: ignore[attr-defined]
            MetricsSnapshot.subject_id == content_id)  # type: ignore[attr-defined]
            .order_by(MetricsSnapshot.date)).all()  # type: ignore[attr-defined]
        pub = s.exec(select(Publication).where(  # type: ignore[attr-defined]
            Publication.content_id == content_id)).first()  # type: ignore[attr-defined]
        published_at = (pub.published_at or "")[:10] if pub else ""
    from datetime import date as _date

    try:
        base = _date.fromisoformat(published_at) if published_at else None
    except ValueError:
        base = None
    items = []
    for r in rows:
        try:
            tn = (_date.fromisoformat(r.date) - base).days if base else None
        except ValueError:
            tn = None
        items.append({"date": r.date, "tn": tn,
                      "metrics": {k: int((r.metrics or {}).get(k) or 0)
                                  for k in _PUB_KEYS if (r.metrics or {}).get(k)}})
    return {"items": items, "published_at": published_at}


@router.get("/pipeline")
def pipeline_board(user: User = Depends(require_user)) -> dict:
    """内容计划看板（遗留收尾批 M8）：六列流程视图一次取全。

    列定义：选题待定夺（ideas draft）→ 成稿中（drafting）→ 待终审（review）→
    待发布（ready 无登记）→ 已发布（published/有登记）→ 已复盘（发布且指标已回流）。
    只读视图——真实状态流转在各线页面，看板不做拖拽。"""
    tid = user.tenant_id
    with Session(engine) as s:
        def _cards(sql: str, params: dict) -> list[dict]:
            return [dict(r) for r in s.execute(text(sql), {"tid": tid, **params}).mappings().all()]

        ideas = _cards(
            "SELECT i.id, i.title_hint AS title, c.key AS channel, i.updated_at "
            "FROM ideas i JOIN channels c ON c.id=i.channel_id "
            "WHERE i.tenant_id=:tid AND i.status='draft' ORDER BY i.updated_at DESC LIMIT 40", {})
        drafting = _cards(
            "SELECT id, title, kind, updated_at FROM contents WHERE tenant_id=:tid "
            "AND status='drafting' ORDER BY updated_at DESC LIMIT 40", {})
        review = _cards(
            "SELECT id, title, kind, updated_at FROM contents WHERE tenant_id=:tid "
            "AND status='review' ORDER BY updated_at DESC LIMIT 40", {})
        ready = _cards(
            "SELECT c.id, c.title, c.kind, c.updated_at FROM contents c WHERE c.tenant_id=:tid "
            "AND c.status='ready' AND c.kind IN ('video_script','long_article','note') "
            "AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id) "
            "ORDER BY c.updated_at DESC LIMIT 40", {})
        published = _cards(
            "SELECT c.id, c.title, c.kind, c.updated_at FROM contents c WHERE c.tenant_id=:tid "
            "AND (c.status='published' OR EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id)) "
            "AND NOT EXISTS (SELECT 1 FROM metrics_snapshots ms WHERE ms.subject_type='publication' "
            "AND ms.subject_id=c.id) ORDER BY c.updated_at DESC LIMIT 40", {})
        reviewed = _cards(
            "SELECT c.id, c.title, c.kind, c.updated_at FROM contents c WHERE c.tenant_id=:tid "
            "AND (c.status='published' OR EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id)) "
            "AND EXISTS (SELECT 1 FROM metrics_snapshots ms WHERE ms.subject_type='publication' "
            "AND ms.subject_id=c.id) ORDER BY c.updated_at DESC LIMIT 40", {})

    from datetime import datetime as _dt

    now = _dt.utcnow()
    def _age(rows):
        for r in rows:
            try:
                u = _dt.fromisoformat(str(r["updated_at"]))
                r["days"] = max(0, (now - u).days)
            except ValueError:
                r["days"] = None
        return rows

    cols = {"ideas": ideas, "drafting": drafting, "review": review,
            "ready": ready, "published": _age(published), "reviewed": _age(reviewed)}
    return {"columns": {k: {"n": len(v), "items": v} for k, v in cols.items()}}
