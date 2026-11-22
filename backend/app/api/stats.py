"""全站待办统计（服务端真值口径）：菜单徽章/工作台待办的唯一数据源。

本端点一次性给全库（本租户）真值；P2 素材域起：候选/收件箱读 sources+materials
（旧拆解库/候选表已退役）。三库独立（2026-11-02）：选题/脚本待办按线拆分。
R1-1（2026-11-22）：/briefing 晨报聚合——决策队列（要审的）+ 行动清单（要做的）+ 异常。
"""
from datetime import datetime, timedelta

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
        # P3 内容域：脚本待定稿 = contents(video_script, review, script_state=generated) 按渠道
        pending_scripts_persona = s.execute(text(
            "SELECT COUNT(*) FROM contents WHERE tenant_id=:tid AND kind='video_script' "
            "AND status='review' AND COALESCE(payload->>'account_type','persona')='persona'"),
            {"tid": tid}).scalar() or 0
        pending_scripts_leadgen = s.execute(text(
            "SELECT COUNT(*) FROM contents WHERE tenant_id=:tid AND kind='video_script' "
            "AND status='review' AND COALESCE(payload->>'account_type','persona')='leadgen'"),
            {"tid": tid}).scalar() or 0
        pending_articles = s.execute(text(
            "SELECT COUNT(*) FROM contents WHERE tenant_id=:tid AND kind='long_article' "
            "AND status IN ('drafting','review','ready')"), {"tid": tid}).scalar() or 0
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
        targets_persona = s.execute(text(
            "SELECT COUNT(*) FROM ideas WHERE tenant_id=:tid AND status='draft' "
            "AND channel_id=(SELECT id FROM channels WHERE key='persona')"),
            {"tid": tid}).scalar() or 0
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


@router.get("/briefing")
def briefing(user: User = Depends(require_user)) -> dict:
    """晨报聚合（R1-1）：决策队列=要审的（计数+样例），行动清单=要做的，异常=终态失败。

    「该补录」= 发布登记超 48h 且无指标回流（与数据页作品表现 backfilled 同口径）；
    「新判卷」= 近 7 天有回流的发布项按 judgement 判卷取 viral/pass。全本租户真值。"""
    tid = user.tenant_id
    now = datetime.utcnow()
    with Session(engine) as s:
        def _samples(sql: str, params: dict) -> list[dict]:
            return [dict(r) for r in s.execute(text(sql), {"tid": tid, **params}).mappings().all()]

        # ── 决策队列（要审的）──
        scripts_review_n = s.execute(text(
            "SELECT COUNT(*) FROM contents WHERE tenant_id=:tid AND kind='video_script' AND status='review'"),
            {"tid": tid}).scalar() or 0
        scripts_review = _samples(
            "SELECT id, title, COALESCE(payload->>'account_type','persona') AS line FROM contents "
            "WHERE tenant_id=:tid AND kind='video_script' AND status='review' "
            "ORDER BY updated_at DESC LIMIT 5", {})
        notes_review_n = s.execute(text(
            "SELECT COUNT(*) FROM contents WHERE tenant_id=:tid AND kind='note' AND status='review'"),
            {"tid": tid}).scalar() or 0
        notes_review = _samples(
            "SELECT id, title FROM contents WHERE tenant_id=:tid AND kind='note' AND status='review' "
            "ORDER BY updated_at DESC LIMIT 5", {})
        cards_n = s.execute(text(
            "SELECT COUNT(*) FROM ideas WHERE tenant_id=:tid AND status='draft'"), {"tid": tid}).scalar() or 0
        cards = _samples(
            "SELECT id, title_hint AS title FROM ideas WHERE tenant_id=:tid AND status='draft' "
            "ORDER BY updated_at DESC LIMIT 5", {})
        inbox_n = s.execute(text(
            "SELECT COUNT(*) FROM materials WHERE tenant_id=:tid AND status='inbox' AND origin_type='douyin_video'"),
            {"tid": tid}).scalar() or 0
        inbox = _samples(
            "SELECT id, title FROM materials WHERE tenant_id=:tid AND status='inbox' AND origin_type='douyin_video' "
            "ORDER BY updated_at DESC LIMIT 5", {})

        # ── 行动清单（要做的）──
        to_publish_n = s.execute(text(
            "SELECT COUNT(*) FROM contents c WHERE tenant_id=:tid AND c.status='ready' "
            "AND c.kind IN ('video_script','long_article','note') "
            "AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id)"), {"tid": tid}).scalar() or 0
        to_publish = _samples(
            "SELECT c.id, c.title, c.kind FROM contents c WHERE c.tenant_id=:tid AND c.status='ready' "
            "AND c.kind IN ('video_script','long_article','note') "
            "AND NOT EXISTS (SELECT 1 FROM publications p WHERE p.content_id=c.id) "
            "ORDER BY c.updated_at DESC LIMIT 5", {})

        pub_rows = s.execute(text(
            "SELECT p.content_id, p.platform, p.published_at, c.title FROM publications p "
            "JOIN contents c ON c.id=p.content_id WHERE p.tenant_id=:tid AND COALESCE(p.published_at,'')<>'' "
            "ORDER BY p.published_at DESC LIMIT 60"), {"tid": tid}).mappings().all()
        refilled = {r[0] for r in s.execute(text(
            "SELECT DISTINCT subject_id FROM metrics_snapshots WHERE subject_type='publication' AND tenant_id=:tid"),
            {"tid": tid}).all()}
        cutoff = now - timedelta(hours=48)
        from ..analytics.judgement import judge

        to_backfill, recent_results = [], []
        for r in pub_rows:
            try:
                pub_dt = datetime.strptime(str(r["published_at"]), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if pub_dt <= cutoff and r["content_id"] not in refilled:
                to_backfill.append(dict(r))
            if pub_dt >= now - timedelta(days=7):
                snap = s.execute(text(
                    "SELECT metrics FROM metrics_snapshots WHERE subject_type='publication' "
                    "AND subject_id=:cid AND tenant_id=:tid ORDER BY date DESC LIMIT 1"),
                    {"cid": r["content_id"], "tid": tid}).first()
                m = dict(snap[0]) if snap else {}
                v = judge(r["platform"], m).get("verdict")
                if v in ("viral", "pass"):
                    recent_results.append({"title": r["title"], "platform": r["platform"], "verdict": v})
        to_backfill = to_backfill[:5]

        failed = s.execute(text(
            "SELECT COUNT(*) FROM jobs WHERE tenant_id=:tid AND status='failed'"), {"tid": tid}).scalar() or 0

        # ── 发布节奏（R3-1）：本周实发 vs 目标（AppSetting publish_targets，0=不限）──
        import json as _json

        from ..models import AppSetting

        targets = {"douyin_week": 0, "wechat_week": 0, "xhs_week": 0}
        trow = s.get(AppSetting, "publish_targets")
        if trow is not None and (trow.value or "").strip():
            try:
                for k, v in (_json.loads(trow.value) or {}).items():
                    if k in targets and isinstance(v, (int, float)) and v >= 0:
                        targets[k] = int(v)
            except ValueError:
                pass
        local_today = datetime.now().strftime("%Y-%m-%d")  # 周窗口=服务器本地（部署=北京），同 weekly_report 口径
        monday = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
        actuals = {"douyin": 0, "wechat": 0, "xhs": 0}
        _PL_KEY = {"douyin": "douyin", "wechat": "wechat", "xiaohongshu": "xhs"}  # DB 平台名 → 节奏键
        for r in s.execute(text(
            "SELECT platform, COUNT(*) AS n FROM publications WHERE tenant_id=:tid "
            "AND substr(published_at,1,10) >= :monday AND substr(published_at,1,10) <= :today "
            "GROUP BY platform"), {"tid": tid, "monday": monday, "today": local_today}).all():
            key = _PL_KEY.get(str(r[0]))
            if key:
                actuals[key] = int(r[1])
        cadence = {}
        for plat, tkey in (("douyin", "douyin_week"), ("wechat", "wechat_week"), ("xhs", "xhs_week")):
            tg = targets[tkey]
            ac = actuals[plat]
            cadence[plat] = {"target": tg, "actual": ac, "gap": (ac - tg) if tg > 0 else 0}

    return {
        "review": {"scripts": {"n": scripts_review_n, "items": scripts_review},
                   "notes": {"n": notes_review_n, "items": notes_review},
                   "cards": {"n": cards_n, "items": cards},
                   "inbox": {"n": inbox_n, "items": inbox}},
        "action": {"to_publish": {"n": to_publish_n, "items": to_publish},
                   "to_backfill": to_backfill,
                   "recent_results": recent_results[:5]},
        "failed": failed,
        "cadence": cadence,
        "week_start": monday,
    }
