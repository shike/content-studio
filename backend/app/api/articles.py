"""公众号流水线 API（P3 内容域门面）：数据在 contents(kind=long_article) + publications。

输出/入参契约与旧 articles 表完全一致（status/note/md/html/publish_url/stats 经
channels.engine.article_out 整形），Workshop/ContentItems 前端零改动。P5 随旧前端退役。
新增：发布走 /api/contents/{id}/publish（人工登记 or 公众号 API）。
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import func, Session, select

from ..articles.images import sign_image, valid_image_name
from ..articles.renderer import render_wechat_html, sanitize_html
from ..auth import require_user
from ..channels.engine import article_out, new_article_content
from ..db import engine
from ..jobs.runner import runner
from ..models import Content, DailyLedger, Publication, User
from ..settings import settings

router = APIRouter(prefix="/api/articles")


class GenerateIn(BaseModel):
    script_id: int | None = None
    style: str = ""  # 长文风格（deep_dive/practice/comparison/inquiry/anatomy；auto=模型按素材挑）
    length: str = "deep"  # 深度版唯一档位（参数保留仅为兼容，服务端强制 deep）


# ── 内容清单（源文改编产线）——P2 起后端=素材库 kb_doc 子集门面 ──
# 注意：以下路由必须位于 GET /{article_id} 之前，否则被 int 路径参数吞掉


class ItemStatusIn(BaseModel):
    status: str  # pending（入队/强制转正） | dismissed（不做）


class PublishIn(BaseModel):
    url: str


class ReflowIn(BaseModel):
    reads: int = 0
    welook: int = 0
    follows: int = 0
    leads: int = 0


@router.post("/mine", status_code=202)
async def mine_sources(user: User = Depends(require_user)) -> dict:
    """手动补挖：提交一批源文评估（调度每日 04:00 自动跑）。"""
    job = await runner.submit("article_kb_miner", {},
                              dedup_key=f"1:kbminer:{datetime.now():%Y%m%d}")
    return {"job_id": job.id}


_KB_STAGE_OUT = {"pending": "pending", "running": "running", "done": "done",
                 "published": "published", "dismissed": "dismissed", "low": "low",
                 "": "pending"}


def _kb_item_out(m) -> dict:
    """kb 素材行 → 内容清单旧形状（ContentItems/Workshop 前端零改动）。"""
    q = m.quality or {}
    return {
        "id": m.id, "folder_name": q.get("folder") or "", "title": m.title or "",
        "source_author": m.author or "", "quality_score": float(q.get("score") or 0),
        "why_good": q.get("why_good") or "", "adapt_angle": q.get("adapt_angle") or "",
        "best_quotes": q.get("best_quotes") or [], "category": q.get("category") or "",
        "priority": int(q.get("priority") or 0),
        "status": _KB_STAGE_OUT.get(m.stage or "", m.stage),
        "article_id": q.get("article_id"), "note": m.note or "",
        "created_at": m.created_at,
    }


@router.get("/content-items")
def list_content_items(status: str = "", category: str = "",
                       limit: int = 100, offset: int = 0) -> dict:
    """内容清单（素材库 kb_doc 子集门面）：置顶→质量分→新 + 两组计数。"""
    from sqlalchemy import text as _text
    from ..models import Material

    with Session(engine) as s:
        q = select(Material).where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
        if status:
            q = q.where(Material.stage == status)  # type: ignore[attr-defined]
        if category:
            q = q.where(_text("materials.quality->>'category' = :cat").bindparams(cat=category))
        total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
        rows = s.exec(q.order_by(  # type: ignore[attr-defined]
            Material.quality.op('->')('priority').desc().nulls_last(),
            Material.quality.op('->')('score').desc().nulls_last(),
            Material.id.desc())
            .offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
        scq = select(Material.stage, func.count(Material.id)).group_by(Material.stage)  # type: ignore[arg-type]
        scq = scq.where(Material.origin_type == "kb_doc")  # type: ignore[attr-defined]
        cat_expr = Material.quality.op('->>')('category')
        ccq = select(cat_expr, func.count(Material.id)).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc").group_by(cat_expr)  # type: ignore[attr-defined]
        return {"items": [_kb_item_out(r) for r in rows], "total": total,
                "status_counts": {st: n for st, n in s.exec(scq).all()},
                "category_counts": {c or "": n for c, n in s.exec(ccq).all()}}


@router.post("/content-items/{item_id}/status")
def set_content_item_status(item_id: int, body: ItemStatusIn,
                            user: User = Depends(require_user)) -> dict:
    """人工调状态：不做（dismissed）/ 入队或强制转正（pending）。running/done/published 不许手改。"""
    if body.status not in ("pending", "dismissed"):
        raise HTTPException(status_code=400, detail="只允许 pending（入队）或 dismissed（不做）")
    from ..models import Material

    with Session(engine) as s:
        it = s.get(Material, item_id)
        if it is None or it.origin_type != "kb_doc":
            raise HTTPException(status_code=404, detail="清单项不存在")
        if (it.stage or "") in ("running", "done", "published"):
            raise HTTPException(status_code=409, detail=f"当前状态 {it.stage} 不允许手改")
        it.stage = body.status
        it.status = "ignored" if body.status == "dismissed" else "ready"
        s.add(it)
        s.commit()
        s.refresh(it)
        return _kb_item_out(it)


@router.post("/content-items/{item_id}/pin")
def pin_content_item(item_id: int, user: User = Depends(require_user)) -> dict:
    """置顶：「明天改编」——priority=9，改编器先取（再看质量分）。"""
    from ..models import Material

    with Session(engine) as s:
        it = s.get(Material, item_id)
        if it is None or it.origin_type != "kb_doc":
            raise HTTPException(status_code=404, detail="清单项不存在")
        if (it.stage or "") != "pending":
            raise HTTPException(status_code=409, detail="只有待改编项可以置顶")
        q = dict(it.quality or {})
        q["priority"] = 9
        it.quality = q
        s.add(it)
        s.commit()
        s.refresh(it)
        return _kb_item_out(it)


@router.get("/ledger")
def list_ledger(limit: int = 14, user: User = Depends(require_user)) -> dict:
    """产线当日账（近 N 条）：晨检对账数据源。anomaly=成功但无产出且无原因——按故障处理。"""
    with Session(engine) as s:
        rows = s.exec(
            select(DailyLedger).order_by(DailyLedger.id.desc())  # type: ignore[attr-defined]
            .limit(max(1, min(limit, 60)))).all()  # type: ignore[attr-defined]
        out = []
        for r in rows:
            d = r.model_dump()
            d["anomaly"] = bool(r.ok) and not (r.produced or "").strip() and not (r.reason or "").strip()
            out.append(d)
        return {"items": out}


@router.post("/{article_id}/publish")
def publish_article(article_id: int, body: PublishIn,
                    user: User = Depends(require_user)) -> dict:
    """发布登记：人工在公众号后台发表后贴回链接记账。内容置 published + 写 publications。"""
    url = body.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="请贴公众号文章链接")
    with Session(engine) as s:
        art = s.get(Content, article_id)
        if art is None or art.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        p = dict(art.payload or {})
        p["article_state"] = "published"
        p["publication"] = {"url": url[:300], "published_at": now, "mode": "manual"}
        art.payload = p
        art.status = "published"
        s.add(art)
        s.add(Publication(tenant_id=art.tenant_id or 1, content_id=art.id,
                          platform="wechat", url=url[:300], published_at=now,
                          publish_mode="manual"))
        # 清单项联动：源文项标已发布
        if p.get("kb_sources"):
            mid = (p["kb_sources"][0] or {}).get("media_id")
            if mid:
                from ..models import Material

                it = s.exec(select(Material).where(  # type: ignore[attr-defined]
                    Material.origin_type == "kb_doc",  # type: ignore[attr-defined]
                    Material.origin_id == mid)).first()  # type: ignore[attr-defined]
                if it is not None and (it.stage or "") == "done":
                    it.stage = "published"
                    s.add(it)
        s.commit()
        s.refresh(art)
        return article_out(art)


@router.put("/{article_id}/reflow")
def reflow_article(article_id: int, body: ReflowIn,
                   user: User = Depends(require_user)) -> dict:
    """48h 回流：阅读/在看/涨粉/进线 四个数（北极星=进线）。写 payload.stats（P4 迁指标事实表）。"""
    vals = {"reads": max(0, int(body.reads)), "welook": max(0, int(body.welook)),
            "follows": max(0, int(body.follows)), "leads": max(0, int(body.leads))}
    with Session(engine) as s:
        art = s.get(Content, article_id)
        if art is None or art.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        p = dict(art.payload or {})
        stats = dict(p.get("stats") or {})
        stats.update(vals)
        stats["filled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        p["stats"] = stats
        art.payload = p
        s.add(art)
        s.commit()
        s.refresh(art)
        return article_out(art)


@router.get("/style-summary")
def style_summary(user: User = Depends(require_user)) -> dict:
    """公众号独立风格画像（app_settings.article_style_note）。"""
    from ..models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, "article_style_note")
        return {"note": (row.value if row is not None else "") or ""}


@router.post("/extract-style", status_code=202)
async def extract_style(user: User = Depends(require_user)) -> dict:
    """从已成文长文提取公众号风格画像。"""
    job = await runner.submit("article_style_extract", {})
    return {"job_id": job.id}


class ArticleUpdate(BaseModel):
    md: str
    title: str | None = None


class RenderIn(BaseModel):
    md: str | None = None  # 渲染即保存改稿（所见即所渲染）；不传则用库里已存正文


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, user: User = Depends(require_user)) -> dict:
    if body.script_id is None:
        raise HTTPException(status_code=400, detail="需要 script_id")
    with Session(engine) as s:
        dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.parent_content_id == body.script_id,  # type: ignore[attr-defined]
            Content.status == "drafting")).first()  # type: ignore[attr-defined]
        if dup is not None:  # 实体级去重：同源在途不重复生成
            raise HTTPException(status_code=409,
                                detail=f"该脚本正在生成长文（文章 #{dup.id}），请等完成后再试")
        article = new_article_content(s, tenant_id=user.tenant_id, state="pending",
                                      parent_content_id=body.script_id)
        article_id = article.id
    job = await runner.submit("article_generate",
                              {"article_id": article_id, "style": body.style})
    return {"article_id": article_id, "job_id": job.id}


@router.get("")
def list_articles(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(Content).where(Content.kind == "long_article")  # type: ignore[attr-defined]
        cq = select(Content.payload.op('->>')('article_state').label('st'),
                    func.count(Content.id)).where(  # type: ignore[attr-defined]
            Content.kind == "long_article").group_by('st')  # type: ignore[attr-defined]
        if status:
            # 状态入参=旧状态串：映射到 canonical 过滤（draft/edited 同属 review）
            canon = {"pending": "review", "generating": "drafting", "failed": "failed",
                     "draft": "review", "edited": "review", "rendered": "ready",
                     "published": "published"}.get(status)
            if canon:
                q = q.where(Content.status == canon)  # type: ignore[attr-defined]
                if status in ("pending", "generating", "draft", "edited"):
                    q = q.where(Content.payload.op('->>')('article_state') == status)
        total = s.exec(select(func.count()).select_from(q.subquery())).one() or 0
        rows = s.exec(q.order_by(Content.id.desc())  # type: ignore[attr-defined]
                      .offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
        counts_raw = {st or "": n for st, n in s.exec(cq).all()}
        return {"items": [article_out(r) for r in rows], "total": total,
                "counts": {k: v for k, v in counts_raw.items() if k}}


@router.get("/images/{filename}")
def article_image(filename: str, user: User = Depends(require_user)):
    """生成的配图文件服务（防目录穿越 + 租户边界：只读本租户目录）。"""
    if not valid_image_name(filename):
        raise HTTPException(status_code=400, detail="bad filename")
    root = settings.data_dir / "article_images"
    if user.role == "platform_admin":
        for cand in sorted(root.glob(f"*/{filename}")):
            return FileResponse(cand, media_type="image/png")
    else:
        own = root / str(user.tenant_id) / filename
        if own.exists():
            return FileResponse(own, media_type="image/png")
        if user.tenant_id == 1:  # 存量平铺文件（迁移前）属租户 1
            legacy = root / filename
            if legacy.exists():
                return FileResponse(legacy, media_type="image/png")
    raise HTTPException(status_code=404, detail="image not found")


@router.get("/{article_id}")
def get_article(article_id: int) -> dict:
    with Session(engine) as s:
        article = s.get(Content, article_id)
        if article is None or article.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        return article_out(article, sanitize_html=sanitize_html)  # 交付前净化


@router.put("/{article_id}")
def update_article(article_id: int, body: ArticleUpdate) -> dict:
    with Session(engine) as s:
        article = s.get(Content, article_id)
        if article is None or article.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        article.body = body.md
        p = dict(article.payload or {})
        if body.title is not None:
            article.title = body.title.strip()
        p["edited"] = True
        if p.get("article_state") in ("draft", "edited") or (
                article.status == "review" and p.get("article_state") not in ("rendered", "published")):
            p["article_state"] = "edited"
        article.payload = p
        if article.status in ("drafting", "failed"):
            article.status = "review"
        s.add(article)
        s.commit()
        s.refresh(article)
        return article_out(article)


@router.post("/{article_id}/render")
def render(article_id: int, body: RenderIn | None = None) -> dict:
    """渲染公众号内联样式 HTML，返回完整文章（前端据此即时同步预览/状态/复制按钮）。"""
    with Session(engine) as s:
        article = s.get(Content, article_id)
        if article is None or article.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        p = dict(article.payload or {})
        if body is not None and body.md is not None:
            article.body = body.md  # 改稿框内容优先：渲染所见即保存所改
        if not (article.body or "").strip():
            raise HTTPException(status_code=400, detail="文章为空，先填写内容")
        p["html"] = render_wechat_html(article.body)
        p["article_state"] = "rendered"
        article.payload = p
        article.status = "ready"
        s.add(article)
        s.commit()
        s.refresh(article)
        return article_out(article, sanitize_html=sanitize_html)  # 与 GET 同一净化口径


_LOCAL_IMG_SRC = re.compile(r'src="(?:[^"]*?)/api/articles/images/([A-Za-z0-9_.\-]+)"')


@router.get("/{article_id}/wechat-copy")
def wechat_copy(article_id: int) -> dict:
    """公众号粘贴载荷：把配图地址改写成签名公开地址，前端拿它写剪贴板 text/html。"""
    with Session(engine) as s:
        article = s.get(Content, article_id)
        if article is None or article.kind != "long_article":
            raise HTTPException(status_code=404, detail="article not found")
        html = (article.payload or {}).get("html") or ""
        if not html:
            raise HTTPException(status_code=400, detail="尚未渲染，先渲染公众号格式")
        html = sanitize_html(html)
    html = _LOCAL_IMG_SRC.sub(
        lambda m: f'src="/api/public/images/{m.group(1)}?t={sign_image(m.group(1))}"', html)
    return {"html": html}


@router.delete("/{article_id}")
def delete_article(article_id: int) -> dict:
    with Session(engine) as s:
        art = s.get(Content, article_id)
        if art is None or art.kind != "long_article":
            raise HTTPException(404, detail="article not found")
        s.delete(art)
        s.commit()
    return {"ok": True}
