"""ima 知识库线管理 API（素材库「知识库」tab，2026-11-17）。

知识库=ima 源这条线的管理台：目录清单（读写 ima_topic_folders 设置，格式 `folder_id:名称` 逗号串）
+ 凭证状态（只写不读口径：只报已配置与否）+ 最近一次挖掘结果。
文档本体是素材（origin_type=kb_doc，quality.folder 记目录），全量进「推荐选题」可检索；挖掘触发复用 /api/articles/mine。
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlmodel import Session, func, select

from ..articles.ima_kb import kb_config, topic_folders
from ..auth import require_user
from ..db import engine
from ..models import AppSetting, Content, Job, Material, MaterialUsage, User

# 稿件 kind → 已产出标记文案（知识库行上「→视频稿 #12」这类）
_KIND_LABEL = {"video_script": "视频稿", "long_article": "长文", "note": "笔记"}

router = APIRouter(prefix="/api/kb")


def _effective_folders(s: Session) -> list[tuple[str, str]]:
    """当前生效目录（设置空值时回落内置默认——增删都以生效集合为基底写回）。"""
    return topic_folders(s)


def _write_folders(s: Session, folders: list[tuple[str, str]]) -> None:
    raw = ",".join(f"{fid}:{name}" for fid, name in folders)
    row = s.get(AppSetting, "ima_topic_folders")
    if row is None:
        s.add(AppSetting(key="ima_topic_folders", value=raw))
    else:
        row.value = raw
        s.add(row)
    s.commit()


@router.get("/overview")
def kb_overview(user: User = Depends(require_user)) -> dict:
    """知识库总览：凭证状态 + 目录清单（各目录入库数/最近入库）+ 最近挖掘 + 文档统计。"""
    with Session(engine) as s:
        cfg = kb_config(s)
        folders = _effective_folders(s)
        # 各目录入库统计（kb 文档 quality.folder 记目录名）
        rows = s.exec(
            select(Material.quality, func.count(Material.id), func.max(Material.created_at))  # type: ignore[arg-type]
            .where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
            .group_by(Material.quality)).all()  # type: ignore[attr-defined]
        per_folder: dict[str, dict] = {}
        stage = {"pending": 0, "low": 0}
        total_docs = 0
        for quality, n, latest in rows:
            q = quality or {}
            total_docs += n
            name = str(q.get("folder") or "（未知目录）")
            agg = per_folder.setdefault(name, {"docs": 0, "latest": None})
            agg["docs"] += n
            lt = str(latest or "")
            if lt > str(agg["latest"] or ""):
                agg["latest"] = lt
        for agg in per_folder.values():
            if agg["latest"]:
                agg["latest"] = str(agg["latest"]).replace(" ", "T")  # isoformat 口径（前端 relTime 补 Z 解析）
        # stage 统计与目录无关，单独查
        stage_rows = s.exec(
            select(Material.stage, func.count(Material.id))  # type: ignore[arg-type]
            .where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
            .group_by(Material.stage)).all()  # type: ignore[attr-defined]
        for st, n in stage_rows:
            if st in stage:
                stage[st] = n
        last = s.exec(
            select(Job).where(Job.type == "article_kb_miner")  # type: ignore[attr-defined]
            .order_by(Job.updated_at.desc())).first()  # type: ignore[attr-defined]
        return {
            "configured": bool(cfg),
            "folders": [{"folder_id": fid, "name": name,
                         "docs": (per_folder.get(name) or {}).get("docs", 0),
                         "latest": (per_folder.get(name) or {}).get("latest")}
                        for fid, name in folders],
            "unknown_folder_docs": (per_folder.get("（未知目录）") or {}).get("docs", 0),
            "docs_total": total_docs,
            "stage": stage,
            "last_mine": ({"status": last.status, "message": last.message,
                           "updated_at": str(last.updated_at or "").replace(" ", "T")} if last else None),
        }


def _used_map(s: Session, material_ids: list[int]) -> dict[int, list[dict]]:
    """kb 文档的产线引用（material_usages→contents）：已产出标记数据源。"""
    out: dict[int, list[dict]] = {}
    if not material_ids:
        return out
    usages = s.exec(select(MaterialUsage).where(  # type: ignore[attr-defined]
        MaterialUsage.material_id.in_(material_ids))).all()  # type: ignore[attr-defined]
    cids = sorted({u.target_id for u in usages if u.target_type == "content"})
    cmap: dict[int, Content] = {}
    if cids:
        for c in s.exec(select(Content).where(Content.id.in_(cids))).all():  # type: ignore[attr-defined]
            cmap[c.id or 0] = c
    for u in usages:
        if u.target_type != "content":
            continue
        c = cmap.get(u.target_id)
        if c is None:
            continue
        out.setdefault(u.material_id, []).append({
            "kind": c.kind, "kind_label": _KIND_LABEL.get(c.kind, c.kind),
            "id": c.id, "title": (c.title or "")[:60], "status": c.status})
    return out


@router.get("/docs")
def kb_docs(q: str = "", user: User = Depends(require_user)) -> dict:
    """知识库内容榜：全量 kb 文档（评分降序，低分含内、无分垫底）。

    q=服务端检索（标题/作者/正文 ILIKE；空=全量）——正文 1.2 万字/篇，
    全量带正文会把列表撑爆，故列表只带 300 字 excerpt，读全文走 /kb/docs/{id}。
    每行带 used=产线引用标记（→视频稿/长文/笔记 #id）。"""
    with Session(engine) as s:
        stmt = select(Material).where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
        kw = q.strip().replace("%", "").replace("_", "")
        if kw:
            like = f"%{kw}%"
            stmt = stmt.where(or_(  # type: ignore[arg-type]
                Material.title.ilike(like),  # type: ignore[attr-defined]
                Material.author.ilike(like),  # type: ignore[attr-defined]
                Material.body.ilike(like)))  # type: ignore[attr-defined]
        rows = s.exec(stmt).all()  # type: ignore[attr-defined]
        used = _used_map(s, [m.id for m in rows if m.id is not None])
        items = [{
            "id": m.id, "title": m.title, "author": m.author or "",
            "folder": str((m.quality or {}).get("folder") or ""),
            "tags": list(m.tags or []),  # 体裁标签（2026-11-18 用户定版 7 词表，资讯/软文类=空）
            "score": (m.quality or {}).get("score"),
            "why_good": str((m.quality or {}).get("why_good") or ""),
            "adapt_angle": str((m.quality or {}).get("adapt_angle") or ""),
            "best_quotes": (m.quality or {}).get("best_quotes") or [],
            "excerpt": (m.body or "").strip()[:300],
            "used": used.get(m.id or 0, []),
            "status": m.status, "stage": m.stage,
            "created_at": str(m.created_at or "").replace(" ", "T"),
        } for m in rows]
        def _score(d: dict) -> float | None:
            try:
                return round(float(d["score"]), 1) if d["score"] is not None else None
            except (TypeError, ValueError):
                return None
        items.sort(key=lambda d: (_score(d) is None, -(_score(d) or 0)))
        for d in items:
            d["score"] = _score(d)
        return {"items": items, "total": len(items)}


@router.get("/docs/{doc_id}")
def kb_doc(doc_id: int, user: User = Depends(require_user)) -> dict:
    """单篇详情（展开行时懒加载）：含全文 body（入库 cap 1.2 万字）+ 产线引用标记。"""
    with Session(engine) as s:
        m = s.get(Material, doc_id)
        if m is None or m.origin_type != "kb_doc":  # type: ignore[attr-defined]
            raise HTTPException(status_code=404, detail="文档不存在")
        used = _used_map(s, [doc_id]).get(doc_id, [])
        q = m.quality or {}
        return {
            "id": m.id, "title": m.title, "author": m.author or "",
            "folder": str(q.get("folder") or ""),
            "tags": list(m.tags or []),
            "score": q.get("score"),
            "why_good": str(q.get("why_good") or ""),
            "adapt_angle": str(q.get("adapt_angle") or ""),
            "best_quotes": q.get("best_quotes") or [],
            "body": m.body or "",
            "used": used, "status": m.status, "stage": m.stage,
            "created_at": str(m.created_at or "").replace(" ", "T"),
        }


@router.get("/docs/{doc_id}/hotspot_suggest")
async def hotspot_suggest(doc_id: int, user: User = Depends(require_user)) -> dict:
    """热点引信自动挂靠（2026-11-18 用户裁决「系统挂，不推给人」）。

    站外热点（百度热搜+科技媒体，与公众号直出线同源）× 文档主题 → 匹配 0~1 个可挂热点，
    给「为什么挂/怎么挂」；前端预填，用户保留否决权（清空=纯干货稿）。"""
    from ..llm import gateway
    from ..materials.collectors.hotlist import fetch_baidu_hot, fetch_tech_news

    with Session(engine) as s:
        m = s.get(Material, doc_id)
        if m is None or m.origin_type != "kb_doc":  # type: ignore[attr-defined]
            raise HTTPException(status_code=404, detail="文档不存在")
        q = m.quality or {}
        doc_desc = (f"《{m.title}》用处：{str(q.get('why_good') or '')[:120]}；"
                    f"改编角度：{str(q.get('adapt_angle') or '')[:150]}")
    try:
        baidu, _ = await asyncio.to_thread(fetch_baidu_hot, 12)
        news, _ = await asyncio.to_thread(fetch_tech_news, 6, 48)
    except Exception:  # noqa: BLE001 热点源挂=诚实降级，可手动填
        return {"hotspot": "", "reason": "热点源暂不可用，可手动填写"}
    items = [{"i": i + 1, "来源": "百度热搜", "标题": str(h.get("title") or "")[:40]}
             for i, h in enumerate(baidu[:8])]
    items += [{"i": len(items) + j + 1, "来源": str(n.get("source") or "科技媒体"),
               "标题": str(n.get("title") or "")[:60]} for j, n in enumerate(news[:10])]
    if not items:
        return {"hotspot": "", "reason": "当前热点榜无数据，可手动填写"}

    screen = await gateway.complete_json(
        [{"role": "user", "content":
          "给一条知识库文档自动挂靠当前热点（抖音口播·爆款基因模式：热点×强情绪立场×一线身份视角）。\n"
          "本号生意：教不懂技术的普通人做 AI 落地（FDE）的线下实训营；观众=实体老板、想转行的普通人。\n"
          f"文档：{doc_desc}\n"
          "候选热点（在榜=正在流量窗口期）：\n" + json.dumps(items, ensure_ascii=False) +
          '\n挑「最能让文档的判断借上热点流量」的一个热点，双锚缺一不选：'
          '① 观众看得懂、跟他们的生意/饭碗/机会有关；② 话题必须长在 AI 上或直接冲击他们的工作\n'
          '没有就 pick=0（宁缺勿滥，不硬挂）。\n'
          '输出 JSON：{"pick": 序号或0, "reason": "为什么挂它/为什么不挂，一句", '
          '"angle_hint": "挂法：一线落地者对这个热点甩什么立场，一句"}'}],
        max_tokens=1024, thinking="disabled")
    try:
        pick = int((screen or {}).get("pick") or 0)
    except (TypeError, ValueError):
        pick = 0
    hit = next((x for x in items if x["i"] == pick), None)
    if hit is None:
        reason = str((screen or {}).get("reason") or "当前无可挂的热点（宁缺勿滥），可手动填写")
        return {"hotspot": "", "reason": reason[:120]}
    return {"hotspot": hit["标题"], "reason": str((screen or {}).get("angle_hint")
                                                  or (screen or {}).get("reason") or "")[:120]}


@router.get("/hot_comments")
def hot_comments(user: User = Depends(require_user)) -> dict:
    """热评雷达（2026-11-18 车道二·用户定版「别人视频的热门评论适合做口播」）：
    高赞评论=受众用赞投票的共鸣点，口播选题源。准入线赞数 AppSetting.hot_comment_bar（默认 50）。
    口径收窄（2026-11-18 用户裁决）：**只出监控清单作者**（active peer_account，扫描入库
    按 source_id、存量按作者名双匹配）——监控号自己的评论存档太薄（273 个视频待补采），
    扩量靠评论补采任务+存量回填。"""
    from ..models import MaterialAnalysis, Source

    with Session(engine) as s:
        row = s.get(AppSetting, "hot_comment_bar")
        try:
            bar = int(row.value) if row is not None and row.value else 50
        except (TypeError, ValueError):
            bar = 50
        mon = s.exec(select(Source).where(  # type: ignore[attr-defined]
            Source.kind == "peer_account",  # type: ignore[attr-defined]
            Source.status == "active",  # type: ignore[attr-defined]
            Source.platform == "douyin")).all()  # type: ignore[attr-defined]
        mon_ids = {x.id for x in mon if x.id is not None}
        mon_names = {x.name for x in mon}
        items: list[dict] = []
        for a in s.exec(select(MaterialAnalysis).where(  # type: ignore[attr-defined]
                MaterialAnalysis.kind == "comments")).all():  # type: ignore[attr-defined]
            m = s.get(Material, a.material_id)
            if m is None or ((m.source_id or 0) not in mon_ids
                             and (m.author or "") not in mon_names):
                continue  # 只出监控清单作者（雷达口径收窄，2026-11-18）
            for i, it in enumerate((a.payload or {}).get("items") or []):
                if not isinstance(it, dict):
                    continue
                t = str(it.get("t") or "").strip()
                try:
                    d = int(it.get("d") or 0)
                except (TypeError, ValueError):
                    continue
                if d >= bar and t:
                    items.append({"material_id": a.material_id, "idx": i, "comment": t[:200],
                                  "likes": d, "author": m.author or "",
                                  "source": m.title or ""})
    items.sort(key=lambda x: -x["likes"])
    return {"items": items[:50], "bar": bar, "total": len(items)}


class FolderIn(BaseModel):
    folder_id: str
    name: str
@router.post("/folders", status_code=201)
def add_folder(body: FolderIn, user: User = Depends(require_user)) -> dict:
    """加挖掘目录（写 ima_topic_folders；同名/同 id 幂等）。"""
    fid = body.folder_id.strip()
    if not fid:
        raise HTTPException(status_code=400, detail="folder_id 不能为空")
    name = body.name.strip() or fid[:16]
    with Session(engine) as s:
        folders = _effective_folders(s)
        if any(x[0] == fid for x in folders):
            raise HTTPException(status_code=409, detail="该目录已在清单")
        folders.append((fid, name[:30]))
        _write_folders(s, folders)
        return {"folders": [{"folder_id": a, "name": b} for a, b in folders]}


@router.delete("/folders/{folder_id}")
def remove_folder(folder_id: str, user: User = Depends(require_user)) -> dict:
    """移除挖掘目录（已入库文档不动，仍在推荐选题可检索）。"""
    with Session(engine) as s:
        folders = _effective_folders(s)
        kept = [x for x in folders if x[0] != folder_id]
        if len(kept) == len(folders):
            raise HTTPException(status_code=404, detail="目录不在清单")
        if not kept:
            raise HTTPException(status_code=400, detail="至少保留一个目录（清空会回落内置默认 12 个）")
        _write_folders(s, kept)
        return {"folders": [{"folder_id": a, "name": b} for a, b in kept]}
