"""经营看板 API（PRD R6.3）：总览 / 渠道对比 / 账号趋势 / 作品表现 / 周报。

只读事实表（metrics_snapshots/analytics_reports/contents/publications/self_videos）；
缺数显式「未回流」，不补零；不做素材/内容效果归因（R6.6 边界）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import (Account, Content, MetricsSnapshot,
                      Publication, SelfVideo, User)

router = APIRouter(prefix="/api/analytics")

_ACCOUNT_KEYS = ("fans", "new_fans", "profile_views", "play", "leads")
_PUB_KEYS = ("reads", "welook", "follows", "leads", "exposure", "likes",
             "comments", "shares", "collects")


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
