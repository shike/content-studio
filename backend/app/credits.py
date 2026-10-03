"""租户积分池：计量操作的预扣与校验。

- 单位锚点：1 积分 = ¥0.01 成本（与 web_search/image_gen 台账估算同口径）
- 计量类任务在提交时按任务类型预扣（保守口径：失败不退，客户定价友好）
- 租户内多用户共享同一个池；余额不足时计量任务直接拒绝
"""
from __future__ import annotations

import math

from fastapi import HTTPException
from sqlmodel import Session

from .db import engine
from .models import CreditTransaction, Tenant

JOB_POINTS: dict[str, int] = {
    "idea_research": 30,
    "benchmark_analyze": 15,
    "script_generate": 30,
    "script_polish": 10,
    "script_finalize": 5,
    "article_generate": 50,
    # avatar_video 不在这里：按秒计费，见 avatar_video_points()（2026-09-27 用户拍板）
}


CLONE_POINTS = 300  # 数字人克隆预扣（平台实际成本约 81 豆≈240 分，留毛利；训练失败不退）

# 数字人视频：按秒计费（蝉镜成本 1 豆/秒 = ¥0.03 = 3 积分；高质版 2 豆/秒 → 6 积分/秒）
AVATAR_POINTS_PER_SEC = 3


def avatar_video_points(seconds: float, model: int = 0) -> int:
    """数字人视频积分 = ceil(秒数 × 费率)：基础 3 积分/秒、高质（2 豆/秒）翻倍。

    预扣用预估秒数（estimate），成片后按实际秒数结算差额。
    """
    rate = AVATAR_POINTS_PER_SEC * (2 if model == 1 else 1)
    return max(1, math.ceil(max(0.0, float(seconds or 0)) * rate))


def job_points(job_type: str) -> int:
    return JOB_POINTS.get(job_type, 5)


def get_balance(tenant_id: int) -> int:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        return t.credits if t else 0


def check_points(tenant_id: int, points: int) -> bool:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        return t is not None and t.status == "active" and (t.credits or 0) >= points


def deduct(tenant_id: int, points: int, reason: str, ref: str = "") -> int:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            raise RuntimeError(f"租户 {tenant_id} 不存在")
        t.credits = max(0, (t.credits or 0) - points)
        s.add(t)
        s.add(CreditTransaction(tenant_id=tenant_id, delta=-points,
                                balance_after=t.credits, reason=reason, kind="consume"))
        s.commit()
        return t.credits


def topup(tenant_id: int, points: int, reason: str) -> int:
    with Session(engine) as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            raise RuntimeError(f"租户 {tenant_id} 不存在")
        t.credits = (t.credits or 0) + points
        s.add(t)
        s.add(CreditTransaction(tenant_id=tenant_id, delta=points,
                                balance_after=t.credits, reason=reason, kind="topup"))
        s.commit()
        return t.credits


def settle(tenant_id: int, pre: int, actual: int, reason: str) -> int:
    """按秒结算：预扣 pre、实际 actual，多退少补（少补也不会把余额打成负数）。

    只处理差额；账本各记一笔，便于对账看清楚退回/补扣来自哪次视频。
    """
    delta = pre - actual
    if delta > 0:
        with Session(engine) as s:
            t = s.get(Tenant, tenant_id)
            if t is None:
                raise RuntimeError(f"租户 {tenant_id} 不存在")
            t.credits = (t.credits or 0) + delta
            s.add(t)
            s.add(CreditTransaction(tenant_id=tenant_id, delta=delta, balance_after=t.credits,
                                    reason=f"{reason}（预扣 {pre}，实际 {actual}，退回差额）",
                                    kind="refund"))
            s.commit()
            return t.credits
    if delta < 0:
        return deduct(tenant_id, -delta, f"{reason}（预扣 {pre}，实际 {actual}，补扣差额）")
    return get_balance(tenant_id)


def require_points(user_tenant_id: int, points: int, what: str = "") -> None:
    """提交计量任务前校验租户积分余额，不足抛 402（前端提示充值）。"""
    from fastapi import HTTPException

    if get_balance(user_tenant_id) < points:
        raise HTTPException(
            status_code=402,
            detail=f"租户积分不足（本次需 {points}，余额不足 {points}）。请联系管理员充值")
