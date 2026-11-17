"""素材域核心服务（PRD R1）：入库去重、质量闸、检索、分析读取、引用追溯。

写入唯一入口=upsert_material（origin_type+origin_id/origin_url 去重）；
加工产物只进 material_analyses（版本留痕不覆盖）；消费方一律经 analyses_map /
material_context 取拆解与评论，禁止直接猜 JSON 形状。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, select

from ..db import engine
from ..models import Material, MaterialAnalysis, MaterialUsage
from ..settings import settings


def content_hash_of(origin_type: str, origin_id: str, origin_url: str, title: str = "") -> str:
    raw = f"{origin_type}:{origin_id or origin_url or title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def aweme_id_of(origin_url: str) -> str:
    """douyin.com/video/{id} → id（无匹配空串）。"""
    import re

    m = re.search(r"/video/(\d{6,})", origin_url or "")
    return m.group(1) if m else ""


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


_PUNCT = set("：:，,。.、；;！!？?·——-（）()「」『』《》〈〉\u3000")


def clean_kb_title(raw: str) -> str:
    """ima 文件名 → 干净标题。纯机械规则（AI 不参与、不加一个词），原始文件名留在 origin_id 可溯：

    - 去 .md 尾；压空白；封顶 120
    - ≥6 段下划线链条 = 抓取源的 SEO 关键词串（『FDE 业务落地实战_FDE_交付_…_极客时间』）→ 砍到首段
    - 其余孤立下划线 = markdown 标记残缺（『从_看数_到_决策_』）：CJK 侧邻接（含全角标点）→ 删；
      两侧都不挨 CJK（如 『了_AI 落地』中 _邻拉丁）→ 还原成空格
    """
    t = (raw or "").strip()
    if t.lower().endswith(".md"):
        t = t[:-3].rstrip()
    parts = t.split("_")
    if len(parts) >= 6:
        t = parts[0]
    else:
        out: list[str] = []
        for i, ch in enumerate(t):
            if ch != "_":
                out.append(ch)
                continue
            prev = t[i - 1] if i else ""
            nxt = t[i + 1] if i + 1 < len(t) else ""
            if _is_cjk(prev) and (_is_cjk(nxt) or nxt in _PUNCT or not nxt):
                out.append("")
            elif _is_cjk(nxt) and (prev in _PUNCT or not prev):
                out.append("")
            else:
                out.append(" ")
        t = "".join(out)
    return " ".join(t.split())[:120]


def upsert_material(s: Session, *, tenant_id: int, origin_type: str,
                    origin_id: str = "", origin_url: str = "", author: str = "",
                    title: str = "", body: str = "", transcript: str = "",
                    source_id: Optional[int] = None, stats: Optional[dict] = None,
                    tags: Optional[list] = None, quality: Optional[dict] = None,
                    stage: str = "", status: str = "inbox", published_at: Optional[datetime] = None,
                    note: str = "", media: Optional[dict] = None) -> tuple[Material, bool]:
    """入库唯一入口：去重回填，返回 (行, 是否新建)。

    去重锚=origin_type + (origin_id → origin_url → content_hash)；命中即回填
    空字段（互动数/标题等采集期信息），不覆盖已有正文与状态。"""
    if origin_type == "douyin_video" and not origin_id and origin_url:
        origin_id = aweme_id_of(origin_url)
    chash = content_hash_of(origin_type, origin_id, origin_url, title)
    row = None
    if origin_id:
        row = s.exec(select(Material).where(
            Material.origin_type == origin_type,  # type: ignore[attr-defined]
            Material.origin_id == origin_id)).first()  # type: ignore[attr-defined]
    if row is None and origin_url:
        row = s.exec(select(Material).where(
            Material.origin_type == origin_type,  # type: ignore[attr-defined]
            Material.origin_url == origin_url)).first()  # type: ignore[attr-defined]
    if row is None:
        row = s.exec(select(Material).where(
            Material.content_hash == chash)).first()  # type: ignore[attr-defined]
    if row is not None:
        changed = False
        if stats and not (row.stats or {}):
            row.stats = stats
            changed = True
        if title and not row.title:
            row.title = title
            changed = True
        if published_at and not row.published_at:
            row.published_at = published_at
            changed = True
        if source_id and not row.source_id:
            row.source_id = source_id
            changed = True
        if changed:
            s.add(row)
            s.commit()
        return row, False
    row = Material(
        tenant_id=tenant_id, source_id=source_id, origin_type=origin_type,
        origin_id=origin_id, origin_url=origin_url, author=author, title=title,
        body=body, transcript=transcript, media=media or {}, stats=stats or {},
        tags=tags or [], quality=quality or {}, stage=stage, status=status,
        content_hash=chash, published_at=published_at, note=note)
    s.add(row)
    s.commit()
    return row, True


# ── 质量闸（2026-11-11 用户定版口径原样迁移：评论≥20 或 赞≥300，设置页可调）──

def heat_score(stats: dict) -> float:
    """热度分：评论/2 + 赞/60 封顶 10（播放数匿名恒拿不到，不入公式）。"""
    comment = stats.get("comment") or 0
    digg = stats.get("digg") or 0
    return round(min(10.0, comment / 2 + digg / 60), 1)


def gate_verdict(stats: dict) -> tuple[bool, str]:
    """拆解闸：评论 ≥dissect_comment_min 或 赞 ≥dissect_digg_min，任一即拆。
    评论未知时只按赞线判，理由如实注明。"""
    c_min, d_min = settings.dissect_comment_min, settings.dissect_digg_min
    comment = stats.get("comment")
    digg = stats.get("digg") or 0
    c_txt = f"评论{comment}" if comment is not None else "评论?"
    if comment is not None and comment >= c_min:
        return True, f"{c_txt}≥{c_min}"
    if digg >= d_min:
        return True, f"赞{digg}≥{d_min}"
    return False, f"{c_txt}/{digg}赞 未达线（评论≥{c_min}或赞≥{d_min}）"


async def gate_rows(rows: list[Material], source_tag: str) -> int:
    """质量闸批量判定：评论缺失先探测补全 → 闸判定 → 过线置 ready 排队拆解，
    不过线置 ignored（gate_reason 留痕，页面可见，手动可翻案强拆）。返回批准条数。"""
    import asyncio

    from .download import probe_video_stats
    from ..jobs.runner import runner

    n = 0
    for r in rows:
        passed, why = False, ""
        with Session(engine) as s:
            row = s.get(Material, r.id)
            if row is None or row.status != "inbox":
                continue  # 已被处理/重复触发
            stats = dict(row.stats or {})
            probed = False
            if stats.get("comment") is None and row.origin_type == "douyin_video" and row.origin_url:
                p = await asyncio.to_thread(probe_video_stats, row.origin_url)
                if p:
                    stats.update(p)
                    row.stats = stats
                    probed = True
            passed, why = gate_verdict(stats)
            q = dict(row.quality or {})
            q["heat"] = heat_score(stats)
            if probed:
                q["stats_source"] = "probe"
            row.quality = q
            tag = f"{(row.note + '｜' if row.note else '')}"
            if passed:
                row.status = "ready"
                row.note = f"{tag}自动拆解（{source_tag}：{why}）"[:300]
            else:
                row.status = "ignored"
                q["gate_reason"] = f"{source_tag}：{why}"
                row.quality = q
                row.note = f"{tag}低质未拆（{source_tag}：{why}）"[:300]
            s.add(row)
            s.commit()
        if passed:
            await runner.submit("material_analyze", {"material_id": r.id},
                                dedup_key=f"mat:{r.id}")
            n += 1
    return n


# ── 加工产物读取（消费方唯一通道）──

def analyses_map(s: Session, material_ids: list[int],
                 kinds: tuple[str, ...] = ("breakdown", "comments")) -> dict[int, dict[str, dict]]:
    """批量取各素材指定 kind 的最新分析：{material_id: {kind: {"payload":..., "text":...}}}。"""
    if not material_ids:
        return {}
    rows = s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
        MaterialAnalysis.material_id.in_(material_ids),  # type: ignore[attr-defined]
        MaterialAnalysis.kind.in_(kinds))  # type: ignore[attr-defined]
        .order_by(MaterialAnalysis.id)).all()  # type: ignore[attr-defined]
    out: dict[int, dict[str, dict]] = {}
    for a in rows:
        out.setdefault(a.material_id, {})[a.kind] = {"payload": a.payload or {}, "text": a.text}
    return out


def material_context(s: Session, material_id: int) -> dict:
    """单素材消费上下文（脚本 ore/实测分支用）：{comments, analysis, comment_kind,
    leadgen_share, title, author, origin_url, stats}。"""
    m = s.get(Material, material_id)
    if m is None:
        return {}
    am = analyses_map(s, [material_id]).get(material_id, {})
    comments = list(((am.get("comments") or {}).get("payload") or {}).get("items") or [])
    return {
        "comments": comments,
        "analysis": (am.get("breakdown") or {}).get("payload") or {},
        "comment_kind": (m.quality or {}).get("comment_kind") or "",
        "leadgen_share": float((m.quality or {}).get("leadgen_share") or 0.0),
        "title": m.title, "author": m.author,
        "origin_url": m.origin_url, "stats": m.stats or {},
    }


def record_usage(s: Session, *, tenant_id: int, material_id: int,
                 target_type: str, target_id: int, role: str = "source") -> None:
    """记一笔素材引用（幂等：同素材同目标同角色不重复）。"""
    dup = s.exec(select(MaterialUsage).where(  # type: ignore[attr-defined]
        MaterialUsage.material_id == material_id,  # type: ignore[attr-defined]
        MaterialUsage.target_type == target_type,  # type: ignore[attr-defined]
        MaterialUsage.target_id == target_id,  # type: ignore[attr-defined]
        MaterialUsage.role == role)).first()  # type: ignore[attr-defined]
    if dup is None:
        s.add(MaterialUsage(tenant_id=tenant_id, material_id=material_id,
                            target_type=target_type, target_id=target_id, role=role))


def usages_of(s: Session, material_id: int) -> list[dict]:
    rows = s.exec(select(MaterialUsage).where(  # type: ignore[attr-defined]
        MaterialUsage.material_id == material_id)).all()  # type: ignore[attr-defined]
    return [{"target_type": u.target_type, "target_id": u.target_id,
             "role": u.role, "created_at": u.created_at} for u in rows]


# ── API 出参兼容层：素材行 → 旧拆解库字段超集（前端/消费方平滑迁移）──

_STATUS_OUT = {"inbox": "pending", "ready": "approved", "ignored": "ignored",
               "archived": "ignored"}


def material_out(m: Material, amap: Optional[dict] = None) -> dict:
    """素材行出参：素材域字段 + 旧拆解库别名字段（scan_status/comment_kind/
    comment_leadgen_share/quality_score/top_comments/analysis/account_type/source）。
    新代码读素材域字段，旧页面读别名——P3/P4 消费方切完后别名随旧页退役。"""
    amap = amap or {}
    q = m.quality or {}
    breakdown = (amap.get("breakdown") or {}).get("payload") or {}
    comments = list(((amap.get("comments") or {}).get("payload") or {}).get("items") or [])
    d = m.model_dump()
    d["scan_status"] = _STATUS_OUT.get(m.status, m.status)
    d["comment_kind"] = q.get("comment_kind") or ""
    d["comment_leadgen_share"] = float(q.get("leadgen_share") or 0.0)
    d["quality_score"] = float(q.get("heat") or 0.0)
    d["top_comments"] = comments
    d["analysis"] = breakdown
    d["account_type"] = q.get("account_type") or "persona"
    d["source"] = "local" if m.origin_type == "video" else "douyin"
    d["media_path"] = (m.media or {}).get("path") or ""
    d["analyzed"] = bool(breakdown)
    d["origin_type"] = m.origin_type
    d["status"] = m.status
    d["url"] = m.origin_url  # 旧面别名（LeadgenOre/HotTargets 行内开链接）
    return d


def count_by_status(s: Session, extra_where=None) -> dict:  # noqa: ANN001
    from sqlalchemy import func

    q = select(Material.status, func.count(Material.id)).group_by(Material.status)  # type: ignore[arg-type]
    if extra_where is not None:
        q = q.where(*extra_where)
    return {st: n for st, n in s.exec(q).all()}


def fresh_cutoff(hours: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=max(1, hours))
