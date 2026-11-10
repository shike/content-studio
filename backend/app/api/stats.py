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
        pending_articles = _count(s, tid, "articles", status="pending")
        # 知识库选题待拍板（2026-11-09 选题线上产品：选用/不做是本线唯一人工闸门）
        kb_topics = _count(s, tid, "kb_topics", status="new")
        # 候选账号待定夺（2026-11-03 IA 收敛：情报并入产线，徽章随线走）
        cand_open_persona = _count(s, tid, "watch_candidates",
                                   status="open", account_type="persona")
        cand_open_leadgen = _count(s, tid, "watch_candidates",
                                   status="open", account_type="leadgen")
    return {"draftTopics": draft_persona + draft_leadgen,
            "draftTopicsPersona": draft_persona, "draftTopicsLeadgen": draft_leadgen,
            "pendingScriptsPersona": pending_scripts_persona,
            "pendingScriptsLeadgen": pending_scripts_leadgen,
            "pendingArticles": pending_articles,
            "kbTopics": kb_topics,
            "openCandidatesPersona": cand_open_persona,
            "openCandidatesLeadgen": cand_open_leadgen}
