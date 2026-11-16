"""全站待办统计（服务端真值口径）：菜单徽章/工作台待办的唯一数据源。

本端点一次性给全库（本租户）真值；P2 素材域起：候选/收件箱读 sources+materials
（旧拆解库/候选表已退役）。三库独立（2026-11-02）：选题/脚本待办按线拆分。
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
        pending_scripts_persona = _count(s, tid, "scripts",
                                         status="generated", account_type="persona")
        pending_scripts_leadgen = _count(s, tid, "scripts",
                                         status="generated", account_type="leadgen")
        pending_articles = s.execute(text(
            "SELECT COUNT(*) FROM articles WHERE tenant_id=:tid AND status IN "
            "('pending','draft','edited','rendered')"), {"tid": tid}).scalar() or 0
        # 候选账号待定夺（P2 素材域：sources candidate，account_type 在 config）
        cand_open_persona = s.execute(text(
            "SELECT COUNT(*) FROM sources WHERE tenant_id=:tid AND status='candidate' "
            "AND COALESCE(config->>'account_type','persona')='persona' "
            "AND COALESCE(config->>'dismissed','false') NOT IN ('true','True','1')"),
            {"tid": tid}).scalar() or 0
        cand_open_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM sources WHERE tenant_id=:tid AND status='candidate' "
            "AND COALESCE(config->>'account_type','persona')='leadgen' "
            "AND COALESCE(config->>'dismissed','false') NOT IN ('true','True','1')"),
            {"tid": tid}).scalar() or 0
        # 今日靶子（2026-11-14 选题工场 R1.4）：靶子卡待备稿数——叶子徽章口径
        targets_persona = _count(s, tid, "topic_cards", status="draft")
        # 素材收件箱待定夺（P2 素材域：抖音采集新视频入口）
        materials_inbox = s.execute(text(
            "SELECT COUNT(*) FROM materials WHERE tenant_id=:tid AND status='inbox' "
            "AND origin_type='douyin_video'"), {"tid": tid}).scalar() or 0
    return {"targetsPersona": targets_persona,
            "pendingScriptsPersona": pending_scripts_persona,
            "pendingScriptsLeadgen": pending_scripts_leadgen,
            "pendingArticles": pending_articles,
            "openCandidatesPersona": cand_open_persona,
            "openCandidatesLeadgen": cand_open_leadgen,
            "materialsInbox": materials_inbox}
