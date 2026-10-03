"""知识图解生成：文章 → LLM 抽取结构化内容 → 排版 → PNG（一页纸知识图解）。

用户 2026-09-30 确认的三类图解之一（一页纸讲义），也是公众号文章配图的主力形态：
文字密度高、全部由排版层渲染（零错字）、品牌取租户配置。出图后以 gen_kg_*.png
落入租户配图目录，随公众号 HTML 内嵌（img 白名单含 gen_ 前缀）。
失败降级：LLM 失败/渲染失败均抛异常由调用方降级，不阻塞成文。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from ..llm import gateway
from ..settings import settings

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

_SPEC_PROMPT = """你是「跃迁内容工作室」的知识图解编辑。把下面的文章提炼成一张「一页纸知识图解」的
结构化内容（JSON）。这张图会贴在公众号文章里，读者扫一眼就能抓住全文干货。

规则：
1. title：图解大标题（15 字内）
2. lead：导语 1~2 句（点出全文最核心的判断，40~80 字）
3. sections：3~5 个小节，按文章逻辑结构划分；每节：
   - h：小节标题（6~14 字）
   - points：3~6 条要点，每条 12~30 字，必须含具体数字/案例/标准，禁止空话；
     数字优先来自国内的公开数据与国内企业实践（可注明来源），国外机构数据仅作补充
4. conclusion：全文最重的判断（30~60 字）
5. conclusion_sub：行动指引一句（15~30 字）
6. 图内全部文字为简体中文，总文字量 400~700 字

输出 JSON（不要解释不要代码块）：
{"title":"...","lead":"...","sections":[{"h":"...","points":["..."]}],"conclusion":"...","conclusion_sub":"..."}

文章：
"""


async def extract_spec(md: str, persona: str) -> dict:
    msgs = [
        {"role": "system", "content": _SPEC_PROMPT + (f"创作者人设参考：{persona[:200]}" if persona else "")},
        {"role": "user", "content": md[:8000]},
    ]
    data = await gateway.complete_json(msgs, purpose="kg_spec", max_tokens=4000,
                                       thinking="disabled")
    spec = {
        "title": str(data.get("title") or "").strip()[:40],
        "lead": str(data.get("lead") or "").strip(),
        "sections": [x for x in (data.get("sections") or []) if isinstance(x, dict)],
        "conclusion": str(data.get("conclusion") or "").strip(),
        "conclusion_sub": str(data.get("conclusion_sub") or "").strip(),
    }
    if not spec["title"] or not spec["sections"]:
        raise ValueError("模型未产出可用的图解结构")
    return spec


def render_onepager_png(spec: dict, out: Path, brand: dict) -> bool:
    """一页纸知识图 → PNG（两遍绘制：先量高度再裁切）。文字全部排版层渲染，零错字。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return False

    W, PAD = 1000, 56

    def _hex_rgb(hx: str, fallback: tuple) -> tuple:
        try:
            hx = (hx or "").lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return fallback

    PRIMARY = _hex_rgb(brand.get("primary"), (22, 163, 74))  # 租户主色（写死旧靛为 P2 前遗留，2026-10-02 修）
    DARK, GRAY, LIGHT = (23, 27, 54), (63, 63, 70), (247, 248, 250)

    def font(bold: bool, size: int):
        p = FONTS / ("NotoSansSC-Bold.otf" if bold else "NotoSansSC-Regular.otf")
        return ImageFont.truetype(str(p), size)

    f_h1, f_sh = font(True, 40), font(True, 22)
    f_lead, f_pt = font(False, 21), font(False, 19)
    f_con, f_cons = font(True, 22), font(False, 15)
    f_tag = font(False, 16)

    def wrap(text: str, f, maxw: float) -> list[str]:
        lines, cur = [], ""
        for ch in text:
            if f.getlength(cur + ch) > maxw and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    H = 2400  # 高画布，末尾按内容裁切
    img = Image.new("RGB", (W, H), LIGHT)
    d = ImageDraw.Draw(img)
    label = f"{brand.get('label_line1', '')}{brand.get('label_line2', '')}" or "跃迁内容"
    y = 48

    d.rounded_rectangle([PAD, y, PAD + 14 + f_tag.getlength(label), y + 34],
                        radius=8, fill=PRIMARY)
    d.text((PAD + 12, y + 5), label, font=f_tag, fill=(255, 255, 255))
    y += 52
    for ln in wrap(spec["title"], f_h1, W - PAD * 2):
        d.text((PAD, y), ln, font=f_h1, fill=(22, 22, 27))
        y += 52
    y += 8

    lead_lines = wrap(spec["lead"], f_lead, W - PAD * 2 - 48)
    bh = len(lead_lines) * 34 + 26
    d.rounded_rectangle([PAD, y, W - PAD, y + bh], radius=10, fill=(255, 255, 255),
                        outline=(228, 228, 231))
    d.line([(PAD, y + 8), (PAD, y + bh - 8)], fill=PRIMARY, width=4)
    for i, ln in enumerate(lead_lines):
        d.text((PAD + 26, y + 14 + i * 34), ln, font=f_lead, fill=GRAY)
    y += bh + 26

    for sec in spec["sections"]:
        d.text((PAD, y), sec.get("h", ""), font=f_sh, fill=(22, 22, 27))
        d.line([(PAD, y + 32), (PAD + 44, y + 32)], fill=PRIMARY, width=3)
        y += 44
        pts = sec.get("points") or []
        colw = (W - PAD * 2 - 24) // 2
        for i in range(0, len(pts), 2):
            hh = 0
            for j, pt in enumerate(pts[i:i + 2]):
                plines = wrap(pt, f_pt, colw - 20)
                x = PAD + j * (colw + 24)
                for k, ln in enumerate(plines):
                    d.ellipse([x, y + 8 + k * 30, x + 8, y + 16 + k * 30], fill=PRIMARY)
                    d.text((x + 16, y + k * 30), ln, font=f_pt, fill=GRAY)
                hh = max(hh, len(plines) * 30)
            y += hh + 14
        y += 10

    con_lines = wrap(spec["conclusion"], f_con, W - PAD * 2 - 60)
    cs_lines = wrap(spec["conclusion_sub"], f_cons, W - PAD * 2 - 60)
    ch = len(con_lines) * 36 + len(cs_lines) * 24 + 40
    d.rounded_rectangle([PAD, y, W - PAD, y + ch], radius=14, fill=DARK)
    ty = y + 20
    for ln in con_lines:
        d.text((PAD + 30, ty), ln, font=f_con, fill=(255, 255, 255))
        ty += 36
    for ln in cs_lines:
        d.text((PAD + 30, ty), ln, font=f_cons, fill=(185, 192, 255))
        ty += 24
    y += ch + 40

    out.parent.mkdir(parents=True, exist_ok=True)
    img.crop((0, 0, W, min(y, H))).save(out)
    return True


async def generate_for_article(article_id: int, tenant_id: int, md: str) -> dict:
    """文章 → 一页纸知识图 PNG（落租户配图目录，文件名 gen_kg_*.png 进 img 白名单）。
    返回 {file, spec}；LLM/渲染失败抛异常由调用方降级。"""
    from ..tenant_brand import brand_of

    brand = brand_of(tenant_id)
    spec = await extract_spec(md, brand.get("persona", ""))
    ts = int(asyncio.get_running_loop().time() * 1000)
    out = settings.data_dir / "article_images" / str(tenant_id or 1) / f"gen_kg_{ts}.png"
    ok = await asyncio.to_thread(render_onepager_png, spec, out, brand)
    if not ok:
        raise RuntimeError("知识图解渲染失败")
    return {"file": out.name, "spec": spec}
