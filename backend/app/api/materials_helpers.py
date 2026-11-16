"""素材列表查询（api/materials 与徽章统计共用）：筛选/分页/counts 一处实现。"""
from __future__ import annotations

from sqlalchemy import func, or_
from sqlmodel import Session, select

from ..models import Material


def material_list_query(s: Session, *, status: str = "", origin_type: str = "",
                        ore: str = "", q: str = "", author: str = "",
                        limit: int = 20, offset: int = 0) -> tuple[list, int, dict]:
    """返回 (页内行, 筛选后总数, counts)。counts 恒全库口径（chips 数字不随筛选漂移）。"""
    def _typed(qry):
        if origin_type:
            qry = qry.where(Material.origin_type == origin_type)  # type: ignore[attr-defined]
        if ore == "leadgen":
            # JSONB 走 ->> 取文本（quality.comment_kind）；SQLModel 兼容写法用 text 条件
            from sqlalchemy import text as _text
            qry = qry.where(_text("materials.quality->>'comment_kind' = 'leadgen_ore'"))
        if q:
            like = f"%{q.strip()}%"
            qry = qry.where(or_(Material.title.ilike(like),  # type: ignore[attr-defined]
                                Material.author.ilike(like),  # type: ignore[attr-defined]
                                Material.transcript.ilike(like)))  # type: ignore[attr-defined]
        if author:
            qry = qry.where(Material.author == author)  # type: ignore[attr-defined]
        return qry

    counts = {"all": s.exec(select(func.count(Material.id))).one() or 0}
    for st in ("inbox", "ready", "ignored", "archived"):
        counts[st] = s.exec(select(func.count(Material.id)).where(  # type: ignore[arg-type]
            Material.status == st)).one() or 0  # type: ignore[attr-defined]
    counts["ore_leadgen"] = s.exec(
        select(func.count(Material.id)).where(  # type: ignore[arg-type]
            func.coalesce(Material.quality.op('->>')('comment_kind'), '') == 'leadgen_ore')  # type: ignore[attr-defined]
    ).one() or 0

    qy = _typed(select(Material))
    if status and status != "all":
        qy = qy.where(Material.status == status)  # type: ignore[attr-defined]
    total = s.exec(select(func.count()).select_from(qy.subquery())).one() or 0
    rows = s.exec(qy.order_by(Material.id.desc())  # type: ignore[attr-defined]
                  .offset(max(0, offset)).limit(max(1, min(limit, 100)))).all()
    return rows, total, counts
