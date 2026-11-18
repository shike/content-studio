"""小红书线（PRD R5）：图文笔记生成 + 封面模板渲染。

- 选题：手动发起（素材库选料/热点/一手案例），本期不做每日自动产线（R5.2 宁缺勿滥）；
- 笔记稿：note_generate（标题≤20/正文 300~800/标签 3~8/合规红线同公众号）；
- 封面：模板渲染（品牌底色+标题大字+署名角标，Pillow 直出），不做 AI 生图（R5.3）；
- 发布：人工（R5.4），publications 登记；不做浏览器自动化（R5.5）。
"""
from __future__ import annotations


from sqlmodel import Session

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..materials.service import record_usage
from ..models import Content
from ..prompts import render
from ..settings import settings


def render_cover(title_big: str, tenant_id: int, out_name: str) -> str:
    """封面模板：1080×1440（3:4），品牌墨绿底 + 白纸标题大字 + 署名角标。
    标题自动折行（每行 ≤6 字封顶，最多 5 行）。失败抛异常（调用方显式降级可见）。"""
    from PIL import Image, ImageDraw, ImageFont

    from ..tenant_brand import brand_of

    brand = brand_of(tenant_id)
    accent = (brand.get("accent") or "#c9a227").lstrip("#")
    primary = (brand.get("primary") or "#0a2f24").lstrip("#")
    signature = (brand.get("signature") or "").strip()

    W, H = 1080, 1440
    img = Image.new("RGB", (W, H), "#" + primary)
    draw = ImageDraw.Draw(img)
    # 顶部金条 + 底部细线（品牌点缀，与站内 moxt 主题同源）
    draw.rectangle([0, 0, W, 16], fill="#" + accent)
    draw.rectangle([80, H - 120, W - 80, H - 116], fill="#" + accent)

    def _font(size: int):
        for cand in ("/System/Library/Fonts/PingFang.ttc",
                     "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:  # noqa: BLE001
                continue
        return ImageFont.load_default()

    # 标题大字：按字数自适应字号，自动折行
    text = (title_big or "").strip()[:12] or "笔记"
    size = 150 if len(text) <= 6 else 120
    font = _font(size)
    per_line = max(4, int(6 * (150 / size)))
    lines = [text[i:i + per_line] for i in range(0, len(text), per_line)][:5]
    y = 300
    for ln in lines:
        draw.text((100, y), ln, font=font, fill="#f5f1e6")
        y += int(size * 1.25)
    if signature:
        draw.text((100, H - 200), signature[:16], font=_font(52), fill="#f5f1e6")

    covers = settings.data_dir / "covers" / str(tenant_id or 1)
    covers.mkdir(parents=True, exist_ok=True)
    path = covers / out_name
    img.save(path, "PNG")
    return str(path)


@register_executor("note_generate")
async def note_generate(ctx: JobContext, payload: dict) -> dict:
    """小红书笔记稿：素材/热点 → 标题+正文+标签+封面大字 → contents(kind=note)。"""
    content_id = payload["content_id"]
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise RuntimeError(f"note content {content_id} 不存在")
        tenant_id = c.tenant_id
        idea = None
        material_ids = list((c.payload or {}).get("material_ids") or [])
        idea_title = c.title
        angle = str((c.payload or {}).get("angle") or "")
        from ..tenant_brand import brand_of

        persona_text = brand_of(tenant_id)["persona"]
        # 素材正文块：引用的素材（转写/正文/拆解要点）作为写作原料
        blocks: list[str] = []
        if material_ids:
            from ..materials.service import analyses_map

            from ..models import Material

            mats = [s.get(Material, mid) for mid in material_ids]
            amap = analyses_map(s, [m.id for m in mats if m is not None])
            for m in mats:
                if m is None:
                    continue
                parts = [f"【素材】{m.title or ''}（@{m.author or '?'}）"]
                body = (m.body or "").strip()
                tr = (m.transcript or "").strip()
                if body:
                    parts.append(body[:1500])
                if tr:
                    parts.append("转写：" + tr[:1500])
                bd = ((amap.get(m.id) or {}).get("breakdown") or {}).get("payload") or {}
                if bd.get("summary"):
                    parts.append("拆解要点：" + str(bd["summary"])[:200])
                blocks.append("\n".join(parts))
        if angle:
            blocks.append(f"【切角要求】{angle}")

    ctx.set_progress(15, "生成笔记稿（标题/正文/标签）")
    data = await gateway.complete_json(
        [{"role": "user", "content": render("note_generate", PERSONA=persona_text)
          + "\n\n选题：" + (idea_title or "（无标题，按素材自由发挥）")
          + ("\n\n素材：\n" + "\n\n".join(blocks) if blocks else "\n\n素材：（无——按选题与人设写常青干货）")}],
        max_tokens=2000, thinking="disabled")
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    tags = [str(t).lstrip("#").strip()[:16] for t in (data.get("tags") or []) if str(t).strip()][:8]
    cover_title = str(data.get("cover_title") or "").strip()[:10]
    if not title or not body:
        raise RuntimeError("笔记生成空响应（title/body 缺失），稍后自动重试")

    ctx.set_progress(70, "渲染封面（品牌模板）")
    cover_path = ""
    try:
        cover_path = await __import__("asyncio").to_thread(
            render_cover, cover_title or title[:10], tenant_id,
            f"xhs_{content_id}.png")
    except Exception as e:  # noqa: BLE001 封面失败不挡成稿（可手补）
        ctx.set_progress(72, f"封面渲染失败（可后台补）：{type(e).__name__}: {str(e)[:60]}")

    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["note_state"] = "review"
        p["tags"] = tags
        p["cover"] = cover_path
        c.payload = p
        c.title = title[:30]
        c.body = body
        c.status = "review"
        s.add(c)
        s.commit()
        if idea_id := c.idea_id:
            from ..models import Idea

            idea = s.get(Idea, idea_id)
            if idea is not None and idea.status == "draft":
                idea.status = "approved"
                idea.content_id = c.id
                s.add(idea)
    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["note_state"] = "review"
        p["tags"] = tags
        p["cover"] = cover_path
        c.payload = p
        c.title = title[:30]
        c.body = body
        c.status = "review"
        s.add(c)
        s.commit()
        if idea_id := c.idea_id:
            from ..models import Idea

            idea = s.get(Idea, idea_id)
            if idea is not None and idea.status == "draft":
                idea.status = "approved"
                idea.content_id = c.id
                s.add(idea)
                s.commit()
        for mid in material_ids:
            record_usage(s, tenant_id=tenant_id, material_id=int(mid),
                         target_type="content", target_id=content_id, role="source")
    ctx.set_progress(100, "完成")
    return {"content_id": content_id, "title": title, "tags": len(tags),
            "cover": bool(cover_path)}


@register_executor("note_revise")
async def note_revise(ctx: JobContext, payload: dict) -> dict:
    """对话框内迭代（2026-11-18 用户裁决：实时生成、不行就在框里说改）：按反馈重写笔记标题+正文。

    封面不动（渲染图与封面大字不随改稿重生成）；反馈逐条落实，未点到处保持原样。"""
    content_id = payload["content_id"]
    feedback = str(payload.get("feedback") or "").strip()
    if not feedback:
        raise RuntimeError("修改反馈为空")
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "note":
            raise RuntimeError(f"笔记 #{content_id} 不存在")
        p = dict(c.payload or {})
        p["note_state"] = "revising"
        c.payload = p
        c.title = c.title  # 占位刷新
        s.add(c)
        s.commit()
        title, body = c.title, c.body or ""
    if not body.strip():
        raise RuntimeError("原稿为空，无法按反馈修改")
    from ..tenant_brand import brand_of

    ctx.set_progress(40, "按反馈重写标题与正文")
    data = await gateway.complete_json(
        [{"role": "user", "content": render("note_revise", PERSONA=brand_of(1)["persona"],
                                            FEEDBACK=feedback[:300], TITLE=title, DRAFT=body)}],
        max_tokens=2048, thinking="disabled")
    n_title = str(data.get("title") or "").strip()
    n_body = str(data.get("body") or "").strip()
    if not n_title or not n_body:
        raise RuntimeError("按反馈修改空响应（title/body 缺失），稍后自动重试")
    n_tags = [str(t).lstrip("#").strip()[:16] for t in (data.get("tags") or []) if str(t).strip()][:8]
    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["note_state"] = "review"
        if n_tags:
            p["tags"] = n_tags
        c.payload = p
        c.title = n_title[:30]
        c.body = n_body
        c.status = "review"
        s.add(c)
        s.commit()
    ctx.set_progress(100, "修改完成")
    return {"content_id": content_id}
