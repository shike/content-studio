"""热门评论来源范围（2026-11-20 用户裁决）：监控清单号 + 白名单话题两条线共同供给。

白名单存 settings.hot_comment_topics（默认 fde/企业ai落地/企业知识库）。
泛话题（ai/workbuddy）不并入：实测高赞评论以动画/钢琴/时事为主，会淹没 FDE 素材。
雷达接口与评论补采/刷新共用这一个判定，避免两处口径漂移。
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..models import AppSetting, Material, MaterialAnalysis, Source
from ..settings import settings

Scope = tuple[set[int], set[str], set[int], set[str]]


def hot_scope(s: Session) -> Scope:
    """返回 (监控号 id, 监控号名, 白名单话题 id, 白名单话题名)。"""
    mon = s.exec(select(Source).where(  # type: ignore[attr-defined]
        Source.kind == "peer_account",  # type: ignore[attr-defined]
        Source.status == "active",  # type: ignore[attr-defined]
        Source.platform == "douyin")).all()  # type: ignore[attr-defined]
    wanted = {x.strip().casefold() for x in (settings.hot_comment_topics or "").split(",")
              if x.strip()}
    topics = s.exec(select(Source).where(  # type: ignore[attr-defined]
        Source.kind == "radar_topic",  # type: ignore[attr-defined]
        Source.status == "active",  # type: ignore[attr-defined]
        Source.platform == "douyin")).all()  # type: ignore[attr-defined]
    hot = [t for t in topics if (t.name or "").casefold() in wanted]
    return ({x.id for x in mon if x.id is not None},
            {x.name for x in mon if x.name},
            {t.id for t in hot if t.id is not None},
            {t.name for t in hot if t.name})


def in_hot_scope(m: Material, scope: Scope) -> bool:
    """素材是否供给热门评论：监控号（source_id 或作者名）+ 白名单话题。
    旧话题回填素材 source_id 为空，按 note 里的「话题回填#话题名」认领。"""
    mon_ids, mon_names, topic_ids, topic_names = scope
    if (m.source_id or 0) in mon_ids or (m.author or "") in mon_names:
        return True
    if (m.source_id or 0) in topic_ids:
        return True
    note = m.note or ""
    return any(f"话题回填#{n}" in note for n in topic_names)


def comment_bar(s: Session) -> int:
    """热评准入线（AppSetting.hot_comment_bar，默认 50）。"""
    row = s.get(AppSetting, "hot_comment_bar")
    try:
        return int(row.value) if row is not None and row.value else 50
    except (TypeError, ValueError):
        return 50


def collect_hot_comments(s: Session) -> tuple[list[dict], int]:
    """热评雷达数据源（接口与预筛共用，口径只此一处）：
    范围内素材的最新版评论存档里，赞 ≥ 准入线的评论。
    返回 ([{material_id, idx, comment, likes, author, source}], bar)，按赞数降序。"""
    bar = comment_bar(s)
    scope = hot_scope(s)
    latest: dict[int, MaterialAnalysis] = {}
    for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
            MaterialAnalysis.kind == "comments")).all():  # type: ignore[attr-defined]
        cur = latest.get(a.material_id)
        if cur is None or (a.version or 0) > (cur.version or 0):
            latest[a.material_id] = a
    items: list[dict] = []
    for a in latest.values():
        m = s.get(Material, a.material_id)
        if m is None or not in_hot_scope(m, scope):
            continue
        for i, it in enumerate((a.payload or {}).get("items") or []):
            if not isinstance(it, dict):
                continue
            t = str(it.get("t") or "").strip()
            try:
                d = int(it.get("d") or 0)
            except (TypeError, ValueError):
                continue
            if d >= bar and t:
                items.append({"material_id": a.material_id, "idx": i, "comment": t[:200],
                              "likes": d, "author": m.author or "",
                              "source": m.title or ""})
    items.sort(key=lambda x: -x["likes"])
    return items, bar


def screen_map(s: Session) -> dict[str, dict]:
    """预筛结果（kind=comment_screen 最新版）：评论文本 → {verdict, reason, dup_of}。
    键=评论文本（存档截 60 字，稳定）；跨版本合并各素材条目。"""
    latest: dict[int, MaterialAnalysis] = {}
    for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
            MaterialAnalysis.kind == "comment_screen")).all():  # type: ignore[attr-defined]
        cur = latest.get(a.material_id)
        if cur is None or (a.version or 0) > (cur.version or 0):
            latest[a.material_id] = a
    out: dict[str, dict] = {}
    for a in latest.values():
        for it in (a.payload or {}).get("items") or []:
            if isinstance(it, dict) and it.get("t"):
                out[str(it["t"])] = {"verdict": str(it.get("verdict") or ""),
                                     "reason": str(it.get("reason") or ""),
                                     "dup_of": str(it.get("dup_of") or "")}
    return out


def scripted_comment_texts(s: Session) -> set[str]:
    """已出过口播稿的评论全文（键=文本，稳定；存档刷新会重排 idx，不能用 idx 对账）。

    新稿 payload.hot_comment 自带 text 锚，直接取；旧稿只有 idx——按稿子生成时刻
    之前最近的一版评论存档解析（存档版本留痕，当时的 idx 对当时的文本）。"""
    from datetime import datetime

    from ..models import Content

    out: set[str] = set()
    legacy: list[tuple[int, int, datetime | None]] = []
    for c in s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.kind == "video_script")).all():  # type: ignore[attr-defined]
        hc = (c.payload or {}).get("hot_comment") or {}
        if not hc.get("material_id"):
            continue
        t = str(hc.get("text") or "").strip()
        if t:
            out.add(t)
        else:
            legacy.append((int(hc["material_id"]), int(hc.get("idx") or 0), c.created_at))
    if not legacy:
        return out
    versions: dict[int, list[MaterialAnalysis]] = {}
    for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
            MaterialAnalysis.kind == "comments")).all():  # type: ignore[attr-defined]
        versions.setdefault(a.material_id, []).append(a)
    for rows in versions.values():
        rows.sort(key=lambda x: x.created_at or datetime.min)
    for mid, idx, at in legacy:
        hist = [a for a in versions.get(mid, [])
                if at is None or a.created_at is None or a.created_at <= at]
        if not hist:
            continue
        items = (hist[-1].payload or {}).get("items") or []
        if 0 <= idx < len(items) and isinstance(items[idx], dict) and items[idx].get("t"):
            out.add(str(items[idx]["t"]))
    return out
