"""API 列表端点公共件：真分页响应 + 抖音链接 SSRF 闸门。

列表响应契约全站统一为 {items, total, counts}——total 是筛选后总数（分页可达），
counts 是分组口径（chips 徽章）。此前 scripts/topics 各写一份 `len(全部拉回)`，
数据量上来后全量加载白烧内存，统一收敛到 paged_list_response。
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select


def paged_list_response(s: Session, q, *, offset: int = 0, limit: int = 50,
                        counts_q=None,
                        serialize: Optional[Callable] = None) -> dict:
    """真分页列表响应。

    - total：COUNT(q.subquery())（api/jobs.py 同款写法），不拉全量行；
    - items：q 自带 where/order，此处统一钳制 offset/limit（上限 500，与旧口径一致）；
    - counts：独立分组查询（select(列, func.count()).group_by(...)），与筛选口径由调用方定；
    - serialize：缺省 model_dump()（topics 列表传 _topic_out 注入关联数据）。
    """
    total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
    rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
    counts = {k: n for k, n in s.exec(counts_q).all()} if counts_q is not None else {}
    ser = serialize or (lambda r: r.model_dump())
    return {"items": [ser(r) for r in rows], "total": total, "counts": counts}


def require_douyin_url(url: str,
                       detail: str = "仅支持抖音链接（防任意 URL 抓取）") -> str:
    """SSRF 闸门：服务端会主动打开/下载该链接，非抖音域一律 400。

    detail 供各端点保持原文案（拆解页与同行监测页文案不同）。校验通过原样返回 url。"""
    from ..benchmarks.downloader import is_douyin_url

    if not is_douyin_url(url):
        raise HTTPException(status_code=400, detail=detail)
    return url
