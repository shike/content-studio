"""视觉工厂（M7 2026-11-22）：封面模板库 + 图文卡片工厂。

零图片模型依赖——全部 PIL 代码渲染（用户裁决 v1 口径：图片模型留通道位后续立项）。
模板=参数化渲染函数集（共用品牌色/署名/字体探测）；deck=LLM 页面规划+逐页统一母版渲染。
"""
from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor
from ..models import Content, Material
from ..prompts import render
from ..settings import settings

COVER_TEMPLATES = ("classic", "split", "leftblock", "paper", "number")
_TEMPLATE_NAMES = {
    "classic": "墨绿满版（默认）",
    "split": "上下分割",
    "leftblock": "左字右块",
    "paper": "白纸墨字",
    "number": "超大数字",
}

W, H = 1080, 1440
_DECK_W, _DECK_H = 1080, 1440


def _font(size: int):
    """字体探测（复用 charts.py 的生产验证候选表，macOS/Linux 双端；全缺退默认不断摆）。"""
    from PIL import ImageFont

    from ..articles.charts import _FONT_CANDIDATES

    for cand in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(cand, size)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _brand(tenant_id: int) -> tuple[str, str, str]:
    from ..tenant_brand import brand_of

    brand = brand_of(tenant_id)
    return ((brand.get("primary") or "#0a2f24").lstrip("#"),
            (brand.get("accent") or "#c9a227").lstrip("#"),
            (brand.get("signature") or "").strip())


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """按像素宽折行（中文逐字，英文按词）。"""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=font) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines[:8]


def _save(img, tenant_id: int, out_rel: str) -> str:
    from PIL import Image  # noqa: F401

    base = settings.data_dir / "visuals" / str(tenant_id or 1)
    path = base / out_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG")
    return str(path)


def _tpl_classic(title, sub, tenant_id, signature):
    """墨绿满版（原 xhs render_cover 迁入，形态不变）。"""
    from PIL import Image, ImageDraw

    primary, accent, _ = _brand(tenant_id)
    img = Image.new("RGB", (W, H), "#" + primary)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 16], fill="#" + accent)
    d.rectangle([80, H - 120, W - 80, H - 116], fill="#" + accent)
    text = (title or "").strip()[:12] or "笔记"
    size = 150 if len(text) <= 6 else 120
    font = _font(size)
    per_line = max(4, int(6 * (150 / size)))
    y = 300
    for ln in [text[i:i + per_line] for i in range(0, len(text), per_line)][:5]:
        d.text((100, y), ln, font=font, fill="#f5f1e6")
        y += int(size * 1.25)
    if (sub or "").strip():
        d.text((100, min(y + 20, H - 320)), sub[:14], font=_font(56), fill="#" + accent)
    if signature:
        d.text((100, H - 200), signature[:16], font=_font(52), fill="#f5f1e6")
    return img


def _tpl_split(title, sub, tenant_id, signature):
    """上下分割：上 55% 色块大字，下白底副标与署名。"""
    from PIL import Image, ImageDraw

    primary, accent, _ = _brand(tenant_id)
    img = Image.new("RGB", (W, H), "#f7f5ef")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, int(H * 0.55)], fill="#" + primary)
    d.rectangle([0, int(H * 0.55) - 14, W, int(H * 0.55)], fill="#" + accent)
    text = (title or "").strip()[:12] or "笔记"
    size = 140 if len(text) <= 6 else 110
    font = _font(size)
    per_line = max(4, int(6 * (140 / size)))
    y = 220
    for ln in [text[i:i + per_line] for i in range(0, len(text), per_line)][:4]:
        d.text((90, y), ln, font=font, fill="#f5f1e6")
        y += int(size * 1.3)
    if (sub or "").strip():
        sy = int(H * 0.55) + 90
        for ln in _wrap(d, sub[:30], _font(64), W - 180)[:2]:
            d.text((100, sy), ln, font=_font(64), fill="#1f2937")
            sy += 88
    if signature:
        d.text((100, H - 180), signature[:16], font=_font(48), fill="#" + accent)
    return img


def _tpl_leftblock(title, sub, tenant_id, signature):
    """左大字右色块：右侧 38% 品牌色竖块压关键词，左侧白底大字。"""
    from PIL import Image, ImageDraw

    primary, accent, _ = _brand(tenant_id)
    img = Image.new("RGB", (W, H), "#fbfaf6")
    d = ImageDraw.Draw(img)
    d.rectangle([int(W * 0.62), 0, W, H], fill="#" + primary)
    d.rectangle([int(W * 0.62) - 12, 0, int(W * 0.62), H], fill="#" + accent)
    text = (title or "").strip()[:12] or "笔记"
    size = 110 if len(text) <= 8 else 92
    font = _font(size)
    y = 260
    for ln in _wrap(d, text, font, int(W * 0.55))[:5]:
        d.text((90, y), ln, font=font, fill="#111827")
        y += int(size * 1.35)
    if (sub or "").strip():
        sy = y + 30
        for ln in _wrap(d, sub[:28], _font(54), int(W * 0.55))[:2]:
            d.text((90, sy), ln, font=_font(54), fill="#" + accent)
            sy += 76
    if signature:
        d.text((int(W * 0.62) + 60, H - 200), signature[:14], font=_font(48), fill="#f5f1e6")
    return img


def _tpl_paper(title, sub, tenant_id, signature):
    """白纸墨字：纸感反转色——白底、墨绿大字、金色细线框。"""
    from PIL import Image, ImageDraw

    primary, accent, _ = _brand(tenant_id)
    img = Image.new("RGB", (W, H), "#fdfcf8")
    d = ImageDraw.Draw(img)
    d.rectangle([36, 36, W - 36, H - 36], outline="#" + accent, width=6)
    d.rectangle([52, 52, W - 52, H - 52], outline="#" + primary, width=2)
    text = (title or "").strip()[:12] or "笔记"
    size = 130 if len(text) <= 6 else 104
    font = _font(size)
    y = 340
    for ln in _wrap(d, text, font, W - 260)[:4]:
        d.text((140, y), ln, font=font, fill="#" + primary)
        y += int(size * 1.3)
    if (sub or "").strip():
        d.text((140, min(y + 40, H - 340)), sub[:14], font=_font(56), fill="#6b7280")
    if signature:
        d.text((140, H - 200), signature[:16], font=_font(48), fill="#" + accent)
    return img


def _tpl_number(title, sub, tenant_id, signature):
    """超大数字型：从标题抽数字占右半版（无数字退化为首词），左半结论。"""
    import re

    from PIL import Image, ImageDraw

    primary, accent, _ = _brand(tenant_id)
    img = Image.new("RGB", (W, H), "#" + primary)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 16], fill="#" + accent)
    m = re.search(r"\d+[个周天年万%％]+|\d+", (title or ""))
    num = m.group(0) if m else (title or "").strip()[:2] or "3"
    nfont = _font(360 if len(num) <= 2 else 240)
    nb = d.textbbox((0, 0), num, font=nfont)
    d.text(((W - (nb[2] - nb[0])) // 2, 180), num, font=nfont, fill="#" + accent)
    rest = (title or "").strip()[:14] or "落地进度"
    y = 760
    for ln in _wrap(d, rest, _font(88), W - 200)[:3]:
        d.text((100, y), ln, font=_font(88), fill="#f5f1e6")
        y += 118
    if (sub or "").strip():
        d.text((100, min(y + 24, H - 300)), sub[:14], font=_font(54), fill="#c9d1c5")
    if signature:
        d.text((100, H - 180), signature[:16], font=_font(48), fill="#f5f1e6")
    return img


_TPL_FN = {"classic": _tpl_classic, "split": _tpl_split, "leftblock": _tpl_leftblock,
           "paper": _tpl_paper, "number": _tpl_number}


def default_template(tenant_id: int) -> str:
    """设置页选的默认封面模板（AppSetting xhs_cover_template，坏值回落 classic）。"""
    from sqlmodel import Session

    from ..models import AppSetting

    with Session(engine) as s:
        row = s.get(AppSetting, "xhs_cover_template")
    v = (row.value or "").strip() if row else ""
    return v if v in COVER_TEMPLATES else "classic"


def render_cover_template(template: str, title_big: str, sub: str,
                          tenant_id: int, out_rel: str, signature: str | None = None) -> str:
    """按模板渲染封面 PNG，返回落盘路径。template 不在库=回落 classic。"""
    _, accent, sig = _brand(tenant_id)
    fn = _TPL_FN.get(template, _tpl_classic)
    img = fn(title_big, sub, tenant_id, signature if signature is not None else sig)
    return _save(img, tenant_id, out_rel)


def template_preview(template: str, tenant_id: int) -> str:
    """模板样图（固定示例字，供模板库页预览）。"""
    return render_cover_template(template, "3周上线24个系统", "验收卡死？缺的是FDE",
                                 tenant_id, f"preview_{template}.png")


# ─────────────────────────── 图文卡片工厂（deck） ───────────────────────────

def render_deck_page(page: dict, idx: int, total: int, tenant_id: int, out_rel: str) -> str:
    """deck 单页渲染：统一母版——白底、品牌页眉条、标题、正文要点、页码。"""
    from PIL import Image, ImageDraw

    primary, accent, signature = _brand(tenant_id)
    img = Image.new("RGB", (_DECK_W, _DECK_H), "#fdfcf8")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, _DECK_W, 12], fill="#" + primary)
    d.rectangle([0, 12, _DECK_W, 16], fill="#" + accent)
    title = str(page.get("title") or "")[:20] or f"第 {idx + 1} 页"
    y = 130
    for ln in _wrap(d, title, _font(84), _DECK_W - 200)[:2]:
        d.text((100, y), ln, font=_font(84), fill="#" + primary)
        y += 110
    d.line([100, y + 20, _DECK_W - 100, y + 20], fill="#" + accent, width=4)
    y += 70
    body = str(page.get("body") or "").strip()
    bullets = [str(x) for x in (page.get("bullets") or [])][:6]
    body_font = _font(52)
    if bullets:
        if body:
            for ln in _wrap(d, body[:400], body_font, _DECK_W - 220)[:4]:
                d.text((100, y), ln, font=body_font, fill="#374151")
                y += 74
            y += 30
        for b in bullets:
            d.ellipse([104, y + 16, 124, y + 36], fill="#" + accent)
            for ln in _wrap(d, b[:120], body_font, _DECK_W - 260)[:2]:
                d.text((150, y), ln, font=body_font, fill="#1f2937")
                y += 74
            y += 22
    elif body:
        for ln in _wrap(d, body[:600], _font(56), _DECK_W - 220)[:8]:
            d.text((100, y), ln, font=_font(56), fill="#374151")
            y += 80
    d.text((100, _DECK_H - 130), (signature or "")[:16], font=_font(44), fill="#9ca3af")
    d.text((_DECK_W - 160, _DECK_H - 130), f"{idx + 1} / {total}", font=_font(44), fill="#9ca3af")
    return _save(img, tenant_id, out_rel)


@register_executor("visual_deck")
async def visual_deck(ctx: JobContext, payload: dict) -> dict:
    """图文卡片：来源（笔记稿/素材）→ LLM 页面规划 → 逐页统一母版渲染。"""
    content_id = int(payload.get("content_id") or 0)
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "visual_deck":
            raise RuntimeError(f"visual_deck #{content_id} 不存在")
        p = dict(c.payload or {})
        src_kind = str(p.get("source_kind") or "note")
        src_id = int(p.get("source_id") or 0)
        tenant_id = c.tenant_id or 1
        want_pages = int(p.get("page_count") or 0)
        p["deck_state"] = "generating"
        c.payload = p
        s.add(c)
        s.commit()

    source_text = ""
    with Session(engine) as s:
        if src_kind == "note":
            c2 = s.get(Content, src_id)
            if c2 is not None and c2.kind == "note":
                source_text = f"《{c2.title}》\n{c2.body}"
        elif src_kind == "material":
            m = s.get(Material, src_id)
            if m is not None:
                source_text = f"《{m.title}》\n{(m.body or '')[:4000]}"
        if not source_text:
            raise RuntimeError("deck 来源内容为空（笔记/素材不存在或无正文）")

    ctx.set_progress(30, "规划图文页面（3~10 页）")
    from ..llm import gateway

    plan = await gateway.complete_json(
        [{"role": "user", "content": render("visual_deck_plan",
                                            SOURCE=source_text[:4000],
                                            PAGE_COUNT=str(want_pages or "auto"))}],
        max_tokens=4000, thinking="disabled")
    pages = [x for x in (plan or {}).get("pages") or [] if isinstance(x, dict)][:10]
    if len(pages) < 3:
        raise RuntimeError(f"页面规划不足 3 页（{len(pages)}）——稍后自动重试")

    ctx.set_progress(60, f"逐页渲染 {len(pages)} 页")
    paths = []
    for i, pg in enumerate(pages):
        rel = f"deck_{content_id}/page_{i}.png"
        paths.append(render_deck_page(pg, i, len(pages), tenant_id, rel))
        ctx.set_progress(60 + int(35 * (i + 1) / len(pages)), f"渲染第 {i + 1}/{len(pages)} 页")

    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["pages"] = [{"role": str(x.get("role") or "")[:12], "title": str(x.get("title") or "")[:20]}
                      for x in pages]
        p["image_paths"] = paths
        p["deck_state"] = "ready"
        c.payload = p
        c.title = str(pages[0].get("title") or c.title)[:40]
        c.status = "ready"
        s.add(c)
        s.commit()
    ctx.set_progress(100, f"完成 {len(pages)} 页图文卡")
    return {"content_id": content_id, "pages": len(pages)}


def _visual_deck_fail(payload: dict, _exc: Exception) -> None:
    """规划/渲染失败回退 drafting（梯子重试）。"""
    with Session(engine) as s:
        c = s.get(Content, int(payload.get("content_id") or 0))
        if c is not None and (c.payload or {}).get("deck_state") == "generating":
            p = dict(c.payload or {})
            p["deck_state"] = "drafting"
            c.payload = p
            s.add(c)
            s.commit()


register_executor("visual_deck", on_fail=_visual_deck_fail)
