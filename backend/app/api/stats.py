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


@router.get("/todos")
def todo_stats(user: User = Depends(require_user)) -> dict:
    tid = user.tenant_id
    with Session(engine) as s:
        draft_persona = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='draft' AND line='persona' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        draft_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='draft' AND line='leadgen' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        draft_topics = draft_persona + draft_leadgen
        pending_scripts_persona = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='generated' AND account_type='persona' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        pending_scripts_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='generated' AND account_type='leadgen' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        pending_scripts = pending_scripts_persona + pending_scripts_leadgen
        final_scripts = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='final' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        # 待写脚本：已定审且还没生成过任何脚本（按线）
        unwritten_persona = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='approved' AND line='persona' AND tenant_id=:tid"
            " AND id NOT IN (SELECT topic_id FROM scripts WHERE topic_id IS NOT NULL)"
        ), {"tid": tid}).scalar() or 0
        unwritten_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='approved' AND line='leadgen' AND tenant_id=:tid"
            " AND id NOT IN (SELECT topic_id FROM scripts WHERE topic_id IS NOT NULL)"
        ), {"tid": tid}).scalar() or 0
        unwritten = unwritten_persona + unwritten_leadgen
        # 待写长文（公众号独立报题线 2026-11-02）：草稿箱里待写的报题草稿
        pending_articles = s.execute(text(
            "SELECT COUNT(*) FROM articles WHERE status='pending' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        # 知识库选题待拍板（2026-11-09 选题线上产品：选用/不做是本线唯一人工闸门）
        kb_topics = s.execute(text(
            "SELECT COUNT(*) FROM kb_topics WHERE status='new' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        # 候选账号待定夺（2026-11-03 IA 收敛：情报并入产线，徽章随线走）
        cand_open_persona = s.execute(text(
            "SELECT COUNT(*) FROM watch_candidates WHERE status='open' AND account_type='persona' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        cand_open_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM watch_candidates WHERE status='open' AND account_type='leadgen' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
    return {"draftTopics": draft_topics,
            "draftTopicsPersona": draft_persona, "draftTopicsLeadgen": draft_leadgen,
            "pendingScripts": pending_scripts,
            "pendingScriptsPersona": pending_scripts_persona,
            "pendingScriptsLeadgen": pending_scripts_leadgen,
            "unwrittenTopics": unwritten,
            "unwrittenPersona": unwritten_persona, "unwrittenLeadgen": unwritten_leadgen,
            "pendingArticles": pending_articles,
            "kbTopics": kb_topics,
            "openCandidatesPersona": cand_open_persona,
            "openCandidatesLeadgen": cand_open_leadgen,
            "finalScripts": final_scripts}
