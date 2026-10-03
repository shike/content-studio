"""公众号流水线 API。"""
import asyncio
import re
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..articles.images import sign_image, valid_image_name
from ..articles.renderer import render_wechat_html, sanitize_html
from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..models import Article, User, LLMCall
from ..settings import settings

router = APIRouter(prefix="/api/articles")


class GenerateIn(BaseModel):
    topic_id: int | None = None
    script_id: int | None = None
    style: str = ""  # 长文风格（deep_dive/practice/viewpoint/framework/comparison/inquiry/anatomy/outlook）
    length: str = "feed"  # 双档位：feed=公众号版 1200~1800 字（默认）/ deep=深度版 3000~5000 字


class ArticleUpdate(BaseModel):
    md: str
    title: str | None = None


class RenderIn(BaseModel):
    md: str | None = None  # 渲染即保存改稿（所见即所渲染）；不传则用库里已存正文


@router.post("/generate", status_code=202)
async def generate(body: GenerateIn, user: User = Depends(require_user)) -> dict:
    from ..credits import job_points, require_points
    require_points(user.tenant_id, job_points("article_generate"), "长文生成")
    if body.topic_id is None and body.script_id is None:
        raise HTTPException(status_code=400, detail="topic_id 与 script_id 至少传一个")
    if body.length not in ("feed", "deep"):
        raise HTTPException(status_code=400, detail="length 只支持 feed（公众号版）/ deep（深度版）")
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
                               "length": body.length},
                              points=job_points("article_generate"))
    return {"article_id": article_id, "job_id": job.id}


@router.get("")
def list_articles(status: str = "", limit: int = 50, offset: int = 0) -> dict:
    with Session(engine) as s:
        q = select(Article).order_by(Article.id.desc())  # type: ignore[arg-type]
        if status:
            q = q.where(Article.status == status)
        total = len(s.exec(q).all())
        rows = s.exec(q.offset(max(0, offset)).limit(max(1, min(limit, 200)))).all()  # type: ignore[attr-defined]
        from sqlmodel import func
        counts = {st: n for st, n in s.exec(
            select(Article.status, func.count(Article.id)).group_by(Article.status)  # type: ignore[arg-type]
        ).all()}
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
    theme = (body.caption or body.keywords or "").strip()[:80]
    prompt = (f"为一篇行业深度分析文章生成配图。配图主题：{theme}。"
              "现代简洁的商务科技插画风格，冷色调蓝灰为主，画面干净专业，构图疏朗，"
              "画面中不要出现任何文字，横版构图")
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
    try:
        with Session(engine) as s:
            s.add(LLMCall(purpose="image_gen", model="cogview-3-flash",
                          tokens_in=0, tokens_out=0, cost_est=0.06))
            s.commit()
    except Exception:  # noqa: BLE001 台账失败不影响生图
        pass
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


@router.post("/{article_id}/knowledge-graphic")
async def knowledge_graphic(article_id: int, user: User = Depends(require_user)) -> dict:
    """手动（重新）生成文章的一页纸知识图解：LLM 抽取 → 排版 → PNG，追加进文末。"""
    from ..articles.kg import generate_for_article

    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="article not found")
        md, tenant_id = art.md, art.tenant_id
        title = art.title
    if len((md or "").strip()) < 200:
        raise HTTPException(status_code=400, detail="文章内容太短，先完成正文再生成知识图解")
    r = await generate_for_article(article_id, tenant_id, md)
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is not None:
            art.md = (art.md or "").rstrip() + f"\n\n![知识图解]({r['file']})\n"
            s.add(art)
            s.commit()
    return {"file": r["file"], "title": r["spec"]["title"]}


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
