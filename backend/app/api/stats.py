"""全站待办统计（服务端真值口径）：菜单徽章/工作台待办的唯一数据源。

此前 TodoStats 由前端拉 4 个大列表（topics 200/scripts 500/articles 100）自行派生——
窗口口径不一致且随数据量增长失真。本端点一次性给全库（本租户）真值。
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
        draft_topics = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='draft' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        pending_scripts = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='generated' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        final_scripts = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='final' AND tenant_id=:tid"
        ), {"tid": tid}).scalar() or 0
        # 待写脚本：已定审且还没生成过任何脚本
        unwritten = s.execute(text(
            "SELECT COUNT(*) FROM topics WHERE status='approved' AND tenant_id=:tid"
            " AND id NOT IN (SELECT topic_id FROM scripts WHERE topic_id IS NOT NULL)"
        ), {"tid": tid}).scalar() or 0
        # 待写长文：已定稿且没有对应文章、也没被手动忽略的脚本
        pending_articles = s.execute(text(
            "SELECT COUNT(*) FROM scripts WHERE status='final' AND tenant_id=:tid"
            " AND id NOT IN (SELECT script_id FROM articles WHERE script_id IS NOT NULL)"
            " AND id NOT IN (SELECT asset_id FROM dismissals WHERE scope='article')"
        ), {"tid": tid}).scalar() or 0
    return {"draftTopics": draft_topics, "pendingScripts": pending_scripts,
            "unwrittenTopics": unwritten, "pendingArticles": pending_articles,
            "finalScripts": final_scripts}
