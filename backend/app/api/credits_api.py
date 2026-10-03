"""租户积分池的自查接口。

为什么单独开一个：租户此前只能通过管理后台（平台侧）知道自己有多少积分，
数字人页只能拿蝉镜的豆余额顶上——那既是平台成本口径、也不该给租户看。
租户要能随时看到自己的池子（提交计量任务前心里有数）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import require_user
from ..credits import get_balance, job_points
from ..models import User

router = APIRouter(prefix="/api/credits")


@router.get("")
def my_credits(user: User = Depends(require_user)) -> dict:
    """当前租户积分：余额 + 各计量任务的价目（提交前就能对账）。"""
    return {
        "balance": get_balance(user.tenant_id),
        "tenant_id": user.tenant_id,
        "prices": {
            "idea_research": job_points("idea_research"),
            "benchmark_analyze": job_points("benchmark_analyze"),
            "script_generate": job_points("script_generate"),
            "script_polish": job_points("script_polish"),
            "script_finalize": job_points("script_finalize"),
            "article_generate": job_points("article_generate"),
        },
    }
