"""全站待办统计（服务端真值口径）：菜单徽章/工作台待办的唯一数据源。

此前 TodoStats 由前端拉 4 个大列表（topics 200/scripts 500/articles 100）自行派生——
窗口口径不一致且随数据量增长失真。本端点一次性给全库（本租户）真值。
三库独立（2026-11-02）：选题/脚本待办按线拆分（persona 人设库 / leadgen 获客库）。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlmodel import Session

from ..auth import require_user
from ..db import engine
from ..models import User

router = APIRouter(prefix="/api/stats")


def _count(s: Session, tid: int, table: str, **eq) -> int:
    """单表计数，租户过滤只写这一遍（漏写即跨租户计数）。

    table 与 eq 的键全部来自本文件内的代码字面量（非用户输入），无注入面。"""
    conds = " AND ".join(f"{k}=:{k}" for k in eq)
    return s.execute(text(
        f"SELECT COUNT(*) FROM {table} WHERE {conds} AND tenant_id=:tid"
    ), {**eq, "tid": tid}).scalar() or 0


@router.get("/todos")
def todo_stats(user: User = Depends(require_user)) -> dict:
    tid = user.tenant_id
    with Session(engine) as s:
        draft_persona = _count(s, tid, "topics", status="draft", line="persona")
        draft_leadgen = _count(s, tid, "topics", status="draft", line="leadgen")
        pending_scripts_persona = _count(s, tid, "scripts",
                                         status="generated", account_type="persona")
        pending_scripts_leadgen = _count(s, tid, "scripts",
                                         status="generated", account_type="leadgen")
        # 待写长文（公众号独立报题线 2026-11-02）：草稿箱里待写的报题草稿
        # 内容工场待决策稿（2026-11-10 用户定版「系统全干、用户只决策」）：
        # 排队写 + 待发（draft/edited/rendered）——选题池降级为系统内部机制，不再人工挑选
        pending_articles = s.execute(text(
            "SELECT COUNT(*) FROM articles WHERE tenant_id=:tid AND status IN "
            "('pending','draft','edited','rendered')"), {"tid": tid}).scalar() or 0
        # 候选账号待定夺（2026-11-03 IA 收敛：情报并入产线，徽章随线走）
        cand_open_persona = _count(s, tid, "watch_candidates",
                                   status="open", account_type="persona")
        cand_open_leadgen = _count(s, tid, "watch_candidates",
                                   status="open", account_type="leadgen")
        # 今日靶子（2026-11-14 选题工场 R1.4）：靶子卡待备稿数——叶子徽章口径
        targets_persona = _count(s, tid, "topic_cards", status="draft")
    return {"draftTopics": draft_persona + draft_leadgen,
            "draftTopicsLeadgen": draft_leadgen,
            "targetsPersona": targets_persona,
            "pendingScriptsPersona": pending_scripts_persona,
            "pendingScriptsLeadgen": pending_scripts_leadgen,
            "pendingArticles": pending_articles,
            "openCandidatesPersona": cand_open_persona,
            "openCandidatesLeadgen": cand_open_leadgen}
