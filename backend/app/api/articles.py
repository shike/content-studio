"""公众号流水线 API。"""
import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlmodel import func, or_, Session, select

from ..articles.images import sign_image, valid_image_name
from ..articles.service import find_pending_article
from ..models import KBTopic
from ..articles.renderer import render_wechat_html, sanitize_html
from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import Article, ContentItem, DailyLedger, User
from ..settings import settings

router = APIRouter(prefix="/api/articles")


class GenerateIn(BaseModel):
    topic_id: int | None = None
    script_id: int | None = None
    style: str = ""  # 长文风格（deep_dive/practice/viewpoint/framework/comparison/inquiry/anatomy/outlook）
    length: str = "deep"  # 深度版唯一档位（2026-11-02 feed 退役；参数保留仅为兼容，服务端强制 deep）


class DraftIn(BaseModel):
    title: str
    note: str = ""  # 报题备注/参考链接


class WriteIn(BaseModel):
    style: str = "auto"


@router.post("/draft", status_code=201)
def create_draft(body: DraftIn, user: User = Depends(require_user)) -> dict:
    """报题即建长文草稿（公众号独立报题线 2026-11-02）：无选题阶段，直接进草稿箱待写。"""
    title = body.title.strip()[:60]
    if not title:
        raise HTTPException(status_code=400, detail="报题一句话不能为空")
    with Session(engine) as s:
        dup = find_pending_article(title)
        if dup is not None:
            raise HTTPException(status_code=409, detail=f"同题草稿已存在（#{dup.id}）")
        row = Article(title=title, note=body.note.strip()[:300], status="pending")
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.model_dump()


@router.post("/kb-topics", status_code=202)
async def kb_topics(user: User = Depends(require_user)) -> dict:
    """知识库选题线（手动触发）：遍历 ima 核心目录 → 提炼选题入池（待人工选用）。"""
    job = await runner.submit("article_kb_topics", {})
    return {"job_id": job.id}


@router.get("/kb-topics")
def list_kb_topics(status: str = "new", user: User = Depends(require_user)) -> dict:
    """选题池列表（status=new 待选 | selected 已选用 | dismissed 不做 | written 已成文）。"""
    with Session(engine) as s:
        rows = s.exec(select(KBTopic).where(
            KBTopic.status == status).order_by(KBTopic.id.desc())).all()  # type: ignore[attr-defined]
    return {"topics": [r.model_dump() for r in rows]}


@router.post("/kb-topics/{topic_id}/select", status_code=202)
async def select_kb_topic(topic_id: int, user: User = Depends(require_user)) -> dict:
    """选用选题 → 建 pending 草稿（绑定依据文档）→ 自动链写成文（article_kb_write）。"""
    with Session(engine) as s:
        topic = s.get(KBTopic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        if topic.status != "new":
            raise HTTPException(status_code=409, detail="该选题已处理")
        if not topic.docs:
            raise HTTPException(status_code=409, detail="该选题缺少依据文档，无法成文")
        art = Article(title=topic.title, status="pending",
                      note=f"判断：{topic.judgment[:80]}｜读者：{topic.audience[:12]}｜依据：{topic.why[:80]}")
        art.kb_doc_ids = topic.docs
        s.add(art)
        s.commit()
        s.refresh(art)
        aid, docs = art.id, list(art.kb_doc_ids or [])
        topic.status = "selected"
        topic.article_id = aid
        s.add(topic)
        s.commit()
    job = await runner.submit("article_kb_write", {"article_id": aid, "kb_doc_ids": docs})
    with Session(engine) as s:
        art = s.get(Article, aid)
        if art is not None:
            art.status = "generating"
            s.add(art)
            s.commit()
    return {"article_id": aid, "job_id": job.id}


@router.post("/kb-topics/{topic_id}/dismiss")
async def dismiss_kb_topic(topic_id: int, user: User = Depends(require_user)) -> dict:
    with Session(engine) as s:
        topic = s.get(KBTopic, topic_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="topic not found")
        topic.status = "dismissed"
        s.add(topic)
        s.commit()
    return {"ok": True}


# ── 内容清单（源文改编产线，2026-11-11）──
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


@router.post("/adapt", status_code=202)
async def adapt_once(user: User = Depends(require_user)) -> dict:
    """手动改编：从清单取项成文（调度每日 05:00 自动跑）。"""
    job = await runner.submit("article_adapt", {},
                              dedup_key=f"1:adapt:{datetime.now():%Y%m%d}")
    return {"job_id": job.id}


@router.get("/content-items")
def list_content_items(status: str = "", category: str = "",
                       limit: int = 100, offset: int = 0) -> dict:
    """内容清单：源文列表（置顶→质量分→新）+ 两组计数（按状态/按栏目）。"""
    with Session(engine) as s:
        q = select(ContentItem).order_by(
            ContentItem.priority.desc(), ContentItem.quality_score.desc(), ContentItem.id.desc())
        if status:
            q = q.where(ContentItem.status == status)
        if category:
            q = q.where(ContentItem.category == category)
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
        scq = select(ContentItem.status, func.count(ContentItem.id)).group_by(ContentItem.status)
        ccq = select(ContentItem.category, func.count(ContentItem.id)).group_by(ContentItem.category)
        return {"items": [r.model_dump() for r in rows], "total": total,
                "status_counts": {st: n for st, n in s.exec(scq).all()},
                "category_counts": {c: n for c, n in s.exec(ccq).all()}}


@router.post("/content-items/{item_id}/status")
def set_content_item_status(item_id: int, body: ItemStatusIn,
                            user: User = Depends(require_user)) -> dict:
    """人工调状态：不做（dismissed）/ 入队或强制转正（pending）。running/done/published 不许手改。"""
    if body.status not in ("pending", "dismissed"):
        raise HTTPException(status_code=400, detail="只允许 pending（入队）或 dismissed（不做）")
    with Session(engine) as s:
        it = s.get(ContentItem, item_id)
        if it is None:
            raise HTTPException(status_code=404, detail="清单项不存在")
        if it.status in ("running", "done", "published"):
            raise HTTPException(status_code=409, detail=f"当前状态 {it.status} 不允许手改")
        it.status = body.status
        s.add(it)
        s.commit()
        s.refresh(it)
        return it.model_dump()


@router.post("/content-items/{item_id}/pin")
def pin_content_item(item_id: int, user: User = Depends(require_user)) -> dict:
    """置顶：「明天改编」——priority=9，改编器先取（再看质量分）。"""
    with Session(engine) as s:
        it = s.get(ContentItem, item_id)
        if it is None:
            raise HTTPException(status_code=404, detail="清单项不存在")
        if it.status != "pending":
            raise HTTPException(status_code=409, detail="只有待改编项可以置顶")
        it.priority = 9
        s.add(it)
        s.commit()
        s.refresh(it)
        return it.model_dump()


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
    """发布登记：人工在公众号后台发表后贴回链接记账（发布永远人工）。"""
    url = body.url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="请贴公众号文章链接")
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="article not found")
        art.publish_url = url[:300]
        art.published_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        art.status = "published"
        if art.kb_sources:  # 清单项联动：源文项标已发布
            mid = (art.kb_sources[0] or {}).get("media_id")
            if mid:
                it = s.exec(select(ContentItem).where(  # type: ignore[attr-defined]
                    ContentItem.media_id == mid)).first()  # type: ignore[attr-defined]
                if it is not None and it.status == "done":
                    it.status = "published"
                    s.add(it)
        s.add(art)
        s.commit()
        s.refresh(art)
        return art.model_dump()


@router.put("/{article_id}/reflow")
def reflow_article(article_id: int, body: ReflowIn,
                   user: User = Depends(require_user)) -> dict:
    """48h 回流：阅读/在看/涨粉/进线 四个数（北极星=进线）。"""
    vals = {"reads": max(0, int(body.reads)), "welook": max(0, int(body.welook)),
            "follows": max(0, int(body.follows)), "leads": max(0, int(body.leads))}
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="article not found")
        stats = dict(art.stats or {})
        stats.update(vals)
        stats["filled_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        art.stats = stats
        s.add(art)
        s.commit()
        s.refresh(art)
        return art.model_dump()


@router.post("/{article_id}/write", status_code=202)
async def write_draft(article_id: int, body: WriteIn,
                      user: User = Depends(require_user)) -> dict:
    """待写草稿 → 生成长文（深度版唯一档位）。"""
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        if article.status == "generating":
            raise HTTPException(status_code=409, detail="该草稿正在生成长文")
        topic_docs = list(article.kb_doc_ids or [])
    if topic_docs:  # 知识库选题线草稿：绑定文档全文取材，走书面语短文规格
        job = await runner.submit("article_kb_write",
                                  {"article_id": article_id, "kb_doc_ids": topic_docs})
    else:
        job = await runner.submit("article_generate",
                                  {"article_id": article_id, "style": body.style or "auto",
                                   "length": "deep", "kb_grounding": True})
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is not None:
            article.status = "generating"
            s.add(article)
            s.commit()
    return {"article_id": article_id, "job_id": job.id}


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


@router.post("/extract-hotspots", status_code=202)
async def extract_hotspots(user: User = Depends(require_user)) -> dict:
    """独立热点报题任务（手动触发；调度每晚 0:30 自动跑）：产出待写长文草稿。"""
    job = await runner.submit("article_hotspots", {"tenant_id": user.tenant_id})
    return {"job_id": job.id}


class ArticleUpdate(BaseModel):
    md: str
    title: str | None = None


class RenderIn(BaseModel):
    md: str | None = None  # 渲染即保存改稿（所见即所渲染）；不传则用库里已存正文


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, user: User = Depends(require_user)) -> dict:
    if body.topic_id is None and body.script_id is None:
        raise HTTPException(status_code=400, detail="topic_id 与 script_id 至少传一个")
    with Session(engine) as s:
        dup = None
        if body.topic_id:
            dup = s.exec(select(Article).where(
                Article.topic_id == body.topic_id,
                Article.status == "generating")).first()
        if dup is None and body.script_id:
            dup = s.exec(select(Article).where(
                Article.script_id == body.script_id,
                Article.status == "generating")).first()
        if dup is not None:  # 实体级去重：同源在途不重复生成
            raise HTTPException(status_code=409,
                                detail=f"该选题正在生成长文（文章 #{dup.id}），请等完成后再试")
        article = Article(topic_id=body.topic_id, script_id=body.script_id, status="generating")
        s.add(article)
        s.commit()
        s.refresh(article)
        article_id = article.id
    job = await runner.submit("article_generate",
                              {"article_id": article_id, "style": body.style,
                               "length": "deep"})  # 深度版唯一档位
    return {"article_id": article_id, "job_id": job.id}


@router.get("")
def list_articles(status: str = "", limit: int = 50, offset: int = 0,
                  source: str = "") -> dict:
    """source='trending'：只看热点切角来源的文章（按选题来源 join 过滤，服务端计数）。"""
    from ..models import Topic as _Topic
    from sqlmodel import func

    with Session(engine) as s:
        q = select(Article).order_by(Article.id.desc())  # type: ignore[arg-type]
        # 双视角互斥：trending=仅热点切角来源；topic=非热点来源（含无选题关联的文章）——空=全部
        if source == "trending":
            q = q.join(_Topic, Article.topic_id == _Topic.id).where(  # type: ignore[attr-defined]
                _Topic.source_type == "trending")  # type: ignore[attr-defined]
        elif source == "topic":
            q = q.outerjoin(_Topic, Article.topic_id == _Topic.id).where(  # type: ignore[attr-defined]
                or_(_Topic.id.is_(None),  # type: ignore[attr-defined]
                    _Topic.source_type != "trending"))  # type: ignore[attr-defined]
        if status:
            q = q.where(Article.status == status)
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()  # type: ignore[attr-defined]
        cq = select(Article.status, func.count(Article.id)).group_by(Article.status)  # type: ignore[arg-type]
        if source == "trending":
            cq = cq.join(_Topic, Article.topic_id == _Topic.id).where(  # type: ignore[attr-defined]
                _Topic.source_type == "trending")  # type: ignore[attr-defined]
        elif source == "topic":
            cq = cq.outerjoin(_Topic, Article.topic_id == _Topic.id).where(  # type: ignore[attr-defined]
                or_(_Topic.id.is_(None),  # type: ignore[attr-defined]
                    _Topic.source_type != "trending"))  # type: ignore[attr-defined]
        counts = {st: n for st, n in s.exec(cq).all()}
        return {"items": [r.model_dump() for r in rows], "total": total, "counts": counts}



class GenImageIn(BaseModel):
    caption: str = ""
    keywords: str = ""


@router.post("/generate-image")
async def generate_image(body: GenImageIn) -> dict:
    """AI 生图（智谱 CogView）：为配图位生成插画，落本地并返回访问 URL。

    计费约 0.06 元/张（cogview-3-flash）；生成 URL 有时效，必须下载落盘。"""
    if not settings.zhipu_api_key:
        raise HTTPException(status_code=400, detail="未配置 ZHIPU_API_KEY")
    # prompt 组装在 images.gen_image 内部（此处的 theme/prompt 死变量 2026-11-13 清除）
    out_dir = settings.data_dir / "article_images"
    out_dir.mkdir(parents=True, exist_ok=True)

    def _gen() -> dict:
        from ..articles.images import gen_image

        return gen_image(body.caption, body.keywords)

    try:
        r = await asyncio.to_thread(_gen)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"生图失败: {str(e)[:120]}")
    return r


@router.get("/images/{filename}")
def article_image(filename: str, user: User = Depends(require_user)):
    """生成的配图文件服务（防目录穿越 + 租户边界：只读本租户目录）。

    文件按租户分目录存储（article_images/{tenant_id}/）；平台管理员可读全部；
    迁移前的老文件平铺在根目录、归属租户 1，仅对租户 1 兼容放行。
    公众号粘贴用图不走这里（微信编辑器带不了 Cookie），走 /api/public/images/{f}?t=签名。"""
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
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        d = article.model_dump()
        if d.get("html"):
            d["html"] = sanitize_html(d["html"])  # 交付前净化（覆盖历史存量行）
        return d


@router.put("/{article_id}")
def update_article(article_id: int, body: ArticleUpdate) -> dict:
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        article.md = body.md
        if body.title is not None:
            article.title = body.title.strip()
        article.status = "edited"
        s.add(article)
        s.commit()
        s.refresh(article)
        return article.model_dump()


@router.post("/{article_id}/render")
def render(article_id: int, body: RenderIn | None = None) -> dict:
    """渲染公众号内联样式 HTML，返回完整文章（前端据此即时同步预览/状态/复制按钮）。

    2026-09-27 修：此前只回 {html}，而前端按文章 id 匹配更新列表——匹配不上，
    点「渲染」界面毫无反应（用户报"没有任何信息提示和进展同步"）。
    """
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        if body is not None and body.md is not None:
            article.md = body.md  # 改稿框内容优先：渲染所见即保存所改
        if not article.md.strip():
            raise HTTPException(status_code=400, detail="文章为空，先填写内容")
        article.html = render_wechat_html(article.md)
        article.status = "rendered"
        s.add(article)
        s.commit()
        s.refresh(article)
        d = article.model_dump()
        d["html"] = sanitize_html(d["html"])  # 与 GET 同一净化口径（前端直接进 DOM）
        return d


_LOCAL_IMG_SRC = re.compile(r'src="(?:[^"]*?)/api/articles/images/([A-Za-z0-9_.\-]+)"')


@router.get("/{article_id}/wechat-copy")
def wechat_copy(article_id: int) -> dict:
    """公众号粘贴载荷：把配图地址改写成签名公开地址，前端拿它写剪贴板 text/html。

    为何不能直接用 article.html：图是相对路径且要登录，公众号编辑器跨域抓图带不了 Cookie
    → 粘贴过去裂图。签名图（/api/public/images/...?t=）无需登录即可抓取，微信会转存素材库。
    返回相对地址，由前端按自身 origin 补全（局域网/本地开发都成立）。
    """
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        if not article.html:
            raise HTTPException(status_code=400, detail="尚未渲染，先渲染公众号格式")
        html = sanitize_html(article.html)
    html = _LOCAL_IMG_SRC.sub(
        lambda m: f'src="/api/public/images/{m.group(1)}?t={sign_image(m.group(1))}"', html)
    return {"html": html}


@router.get("/{article_id}/wechat.html")
def wechat_html(article_id: int):
    with Session(engine) as s:
        article = s.get(Article, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="article not found")
        if not article.html:
            raise HTTPException(status_code=400, detail="尚未渲染，先调用 render")
        # 双保险：白名单净化 + CSP（即便未来出现漏网标签也无法在本源执行脚本）
        return HTMLResponse(sanitize_html(article.html), headers={
            "Content-Security-Policy":
                "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'",
        })


@router.delete("/{article_id}")
def delete_article(article_id: int) -> dict:
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(404, detail="article not found")
        s.delete(art)
        s.commit()
    return {"ok": True}
