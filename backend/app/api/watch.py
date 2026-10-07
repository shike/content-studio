"""同行监测 API：账号增删改查 + 扫描（全量/单号）+ 识别 + 扫描历史 + 关键词发现账号。"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..auth import require_user
from ..db import engine
from ..models import User
from ..jobs.runner import runner
from ..models import BenchmarkVideo, Job, Topic, WatchAccount, WatchCandidate
from ..watch.service import _DISCOVER_QUALITY_FANS
from ..watch.service import _fmt_follow

router = APIRouter(prefix="/api/watch")


@router.get("/digest")
def watch_digest(user: User = Depends(require_user)) -> dict:
    """同行监测周报（近 7 天摘要，实时计算不落库）：新视频/待定夺按作者分布 +
    高分选题 Top3（带切角一句话）。数据按租户隔离。"""
    tid = user.tenant_id
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    cutoff_s = cutoff.isoformat(timespec="seconds")
    with Session(engine) as s:
        vids = s.exec(
            select(BenchmarkVideo).where(  # type: ignore[attr-defined]
                BenchmarkVideo.tenant_id == tid,  # type: ignore[attr-defined]
                BenchmarkVideo.source == "douyin",  # type: ignore[attr-defined]
                BenchmarkVideo.created_at >= cutoff_s,  # type: ignore[attr-defined]
            ).order_by(BenchmarkVideo.id.desc())  # type: ignore[attr-defined]
        ).all()
        pending_authors: dict[str, int] = {}
        for v in vids:
            if v.scan_status == "pending":
                key = v.author or "未知"
                pending_authors[key] = pending_authors.get(key, 0) + 1
        topics = s.exec(
            select(Topic).where(  # type: ignore[attr-defined]
                Topic.tenant_id == tid,  # type: ignore[attr-defined]
                Topic.source_type == "benchmark",  # type: ignore[attr-defined]
                Topic.created_at >= cutoff_s,  # type: ignore[attr-defined]
                Topic.score >= 8,  # type: ignore[attr-defined]
            ).order_by(Topic.score.desc())  # type: ignore[attr-defined]
        ).all()
        top = [{"topic_id": t.id, "title": t.title, "score": round(t.score, 1),
                "angle": (t.angle or "")[:60]} for t in topics[:3]]
        # 清单外高赞同行（每日发现的 open 候选，粉丝过质量线）：价值流转的第三路
        disc = s.exec(
            select(WatchCandidate).where(  # type: ignore[attr-defined]
                WatchCandidate.tenant_id == tid,  # type: ignore[attr-defined]
                WatchCandidate.status == "open",  # type: ignore[attr-defined]
                WatchCandidate.follower_count >= _DISCOVER_QUALITY_FANS,  # type: ignore[attr-defined]
            ).order_by(WatchCandidate.follower_count.desc())  # type: ignore[attr-defined]
        ).all()
        discovered = [{"name": c.name, "follower_count": c.follower_count,
                       "signature": (c.signature or "")[:40]} for c in disc[:3]]
    return {
        "new_videos": len(vids),
        "pending": sum(1 for v in vids if v.scan_status == "pending"),
        "pending_authors": sorted(pending_authors.items(), key=lambda x: -x[1])[:5],
        "topics_7d": len(topics),
        "top_topics": top,
        "discovered": {"count": len(disc), "top": discovered},
    }


class AccountIn(BaseModel):
    platform: str = "douyin"  # douyin | channels
    name: str
    url: str = ""
    note: str = ""
    kind: str = "competitor"  # competitor | self


class AccountUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    note: str | None = None
    enabled: bool | None = None
    kind: str | None = None


class ResolveIn(BaseModel):
    url: str


def _parse_scores(note: str) -> dict | None:
    """从备注解析实测评分文本：实测评分(相关/质量/综合)：9/7.5/9.0。"""
    import re

    m = re.search(r"实测评分\(相关/质量/综合\)：([\d.]+)/([\d.]+)/([\d.]+)", note or "")
    if not m:
        return None
    try:
        return {"relevance": float(m.group(1)), "quality": float(m.group(2)),
                "overall": float(m.group(3))}
    except ValueError:
        return None


@router.get("/accounts")
def list_accounts() -> dict:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    stats: dict[str, dict] = {}
    with Session(engine) as s:
        rows = s.exec(select(WatchAccount).order_by(WatchAccount.id)).all()
        for b in s.exec(select(BenchmarkVideo)).all():
            if b.source != "douyin" or not b.author:
                continue
            st = stats.setdefault(b.author, {"total": 0, "recent": 0})
            st["total"] += 1
            created = b.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created >= week_ago:
                    st["recent"] += 1
        items = []
        for r in rows:
            st = stats.get(r.name) or {"total": 0, "recent": 0}
            d = r.model_dump()
            d["video_total"] = st["total"]
            d["video_recent_7d"] = st["recent"]
            d["scores"] = _parse_scores(r.note or "")
            items.append(d)
    return {"items": items}


@router.post("/accounts", status_code=201)
def create_account(body: AccountIn) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="账号名不能为空")
    if body.platform not in ("douyin", "channels"):
        raise HTTPException(status_code=400, detail="platform 必须是 douyin / channels")
    if body.kind not in ("competitor", "self"):
        raise HTTPException(status_code=400, detail="kind 必须是 competitor / self")
    if body.platform == "douyin" and not body.url.strip():
        raise HTTPException(status_code=400,
                            detail="抖音账号需要主页链接（App 内分享主页 → 复制链接）")
    if body.url.strip():
        from ..benchmarks.downloader import is_douyin_url
        if not is_douyin_url(body.url.strip()):
            raise HTTPException(status_code=400, detail="仅支持抖音链接（防任意 URL 抓取）")
    with Session(engine) as s:
        acc = WatchAccount(platform=body.platform, name=body.name.strip(),
                           url=body.url.strip(), note=body.note.strip(), kind=body.kind)
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return acc.model_dump()


@router.put("/accounts/{account_id}")
def update_account(account_id: int, body: AccountUpdate) -> dict:
    with Session(engine) as s:
        acc = s.get(WatchAccount, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="account not found")
        if body.name is not None:
            if not body.name.strip():
                raise HTTPException(status_code=400, detail="账号名不能为空")
            acc.name = body.name.strip()
        if body.url is not None:
            from ..benchmarks.downloader import is_douyin_url
            if body.url.strip() and not is_douyin_url(body.url.strip()):
                raise HTTPException(status_code=400, detail="仅支持抖音链接（防任意 URL 抓取）")
            acc.url = body.url.strip()
        if body.note is not None:
            acc.note = body.note
        if body.enabled is not None:
            acc.enabled = body.enabled
        if body.kind is not None:
            if body.kind not in ("competitor", "self"):
                raise HTTPException(status_code=400, detail="kind 必须是 competitor / self")
            acc.kind = body.kind
        s.add(acc)
        s.commit()
        s.refresh(acc)
        return acc.model_dump()


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int) -> dict:
    with Session(engine) as s:
        acc = s.get(WatchAccount, account_id)
        if acc is None:
            raise HTTPException(status_code=404, detail="account not found")
        s.delete(acc)
        s.commit()
    return {"deleted": account_id}


@router.post("/scan", status_code=202)
async def scan(user: User = Depends(require_user)) -> dict:
    from datetime import datetime, timezone
    job = await runner.submit("watch_scan", {"auto": True},
                              dedup_key=f"{user.tenant_id}:scan:all:{datetime.now(timezone.utc):%Y%m%d}")
    return {"job_id": job.id}


@router.post("/accounts/{account_id}/scan", status_code=202)
async def scan_account(account_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        if s.get(WatchAccount, account_id) is None:
            raise HTTPException(status_code=404, detail="account not found")
    from datetime import datetime, timezone
    job = await runner.submit("watch_scan", {"account_id": account_id},
                              dedup_key=f"{user.tenant_id}:scan:{account_id}:{datetime.now(timezone.utc):%Y%m%d}")
    return {"job_id": job.id}


@router.post("/resolve", status_code=202)
async def resolve(body: ResolveIn) -> dict:
    if not body.url.strip():
        raise HTTPException(status_code=400, detail="url 不能为空")
    from ..benchmarks.downloader import extract_douyin_url, is_douyin_url
    url = extract_douyin_url(body.url.strip())
    if url and not is_douyin_url(url):
        raise HTTPException(status_code=400, detail="仅支持抖音链接（防任意 URL 抓取）")
    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    job = await runner.submit("watch_resolve", {"url": url}, dedup_key=url)
    return {"job_id": job.id}


@router.get("/scan/history")
def scan_history(limit: int = 8) -> dict:
    with Session(engine) as s:
        rows = s.exec(
            select(Job).where(  # type: ignore[attr-defined]
                Job.type == "watch_scan",  # type: ignore[attr-defined]
                Job.archived == 0,  # type: ignore[attr-defined]
            )
            .order_by(Job.id.desc())  # type: ignore[attr-defined]
            .limit(max(1, min(limit, 30)))
        ).all()
        return {"items": [r.model_dump() for r in rows]}


# ---------- 关键词发现对标账号 ----------

class DiscoverIn(BaseModel):
    keyword: str
    direction: str = ""


def _cand_dict(r: WatchCandidate) -> dict:
    d = r.model_dump()
    try:
        d["stats"] = json.loads(r.sample_stats) if r.sample_stats else {}
    except json.JSONDecodeError:
        d["stats"] = {}
    return d


@router.post("/discover", status_code=202)
async def discover(body: DiscoverIn, user: User = Depends(require_user)) -> dict:
    kw, direction = body.keyword.strip(), body.direction.strip()
    if not kw:
        raise HTTPException(status_code=400, detail="关键词不能为空")
    job = await runner.submit("watch_discover", {"keyword": kw, "direction": direction},
                              dedup_key=f"{user.tenant_id}:discover:{kw}:{direction}")
    return {"job_id": job.id}


@router.get("/discover/candidates")
def list_candidates(status: str = "") -> dict:
    with Session(engine) as s:
        q = select(WatchCandidate)
        if status:
            q = q.where(WatchCandidate.status == status)  # type: ignore[attr-defined]
        rows = s.exec(q.order_by(  # type: ignore[attr-defined]
            WatchCandidate.follower_count.desc(),  # type: ignore[attr-defined]
            WatchCandidate.id.desc(),  # type: ignore[attr-defined]
        )).all()
        return {"items": [_cand_dict(r) for r in rows]}


@router.post("/discover/candidates/{cid}/add")
def add_candidate(cid: int) -> dict:
    with Session(engine) as s:
        c = s.get(WatchCandidate, cid)
        if c is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        if c.status == "dismissed":
            raise HTTPException(status_code=400, detail="已忽略的候选不能加入，请先重新发现")
        dup = any(c.sec_uid and c.sec_uid in (a.url or "")
                  for a in s.exec(select(WatchAccount).where(  # type: ignore[attr-defined]
                      WatchAccount.platform == "douyin")).all())
        acc = None
        if not dup:
            acc = WatchAccount(
                platform="douyin", name=c.name, url=c.url, kind="competitor",
                note=f"发现自关键词「{c.keyword}」· 粉丝 {_fmt_follow(c.follower_count)}")
            s.add(acc)
            s.flush()
        c.status = "added"
        c.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        s.add(c)
        s.commit()
        s.refresh(c)  # commit 后属性过期，model_dump 不触发 refresh 会拿空壳
        return {"candidate": _cand_dict(c),
                "account": acc.model_dump() if acc is not None else None,
                "duplicate": dup}


@router.post("/discover/candidates/{cid}/dismiss")
def dismiss_candidate(cid: int) -> dict:
    with Session(engine) as s:
        c = s.get(WatchCandidate, cid)
        if c is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        c.status = "dismissed"
        c.updated_at = datetime.now(timezone.utc)
        s.add(c)
        s.commit()
        s.refresh(c)
        return _cand_dict(c)


@router.delete("/discover/candidates")
def clear_candidates() -> dict:
    """清掉已处理（已加入/已忽略）的候选记录；待定夺保留。"""
    with Session(engine) as s:
        rows = s.exec(select(WatchCandidate).where(  # type: ignore[attr-defined]
            WatchCandidate.status != "open")).all()  # type: ignore[attr-defined]
        for r in rows:
            s.delete(r)
        s.commit()
        return {"deleted": len(rows)}
