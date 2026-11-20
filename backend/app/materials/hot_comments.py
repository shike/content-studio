"""热门评论来源范围（2026-11-20 用户裁决）：监控清单号 + 白名单话题两条线共同供给。

白名单存 settings.hot_comment_topics（默认 fde/企业ai落地/企业知识库）。
泛话题（ai/workbuddy）不并入：实测高赞评论以动画/钢琴/时事为主，会淹没 FDE 素材。
雷达接口与评论补采/刷新共用这一个判定，避免两处口径漂移。
"""
from __future__ import annotations

from sqlmodel import Session, select

from ..models import Material, Source
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
