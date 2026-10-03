"""海报提示词工场：把文章/任意内容转成即梦(Seedream)风格「图文一体海报」的完整生成提示词。

背景（2026-09-30 用户定版）：要的是 ChatGPT/即梦那种「画面与密集中文一体直出」的培训/公众号海报。
智谱系图像模型（CogView）图内中文乱码已实测出局 → 出图引擎交给即梦/Seedream（手动免费额度或
火山方舟 API 0.2 元/张），本模块只负责系统最擅长的部分：**从内容生成高质量提示词**。

三层端点：
- POST /api/articles/{id}/poster-prompt  单配图位（按 slot 序号取上下文）
- POST /api/articles/{id}/poster-pack    整篇全部配图位（一次 LLM 抽取）
- POST /api/poster/freetext              任意内容/大纲（无文章也能用，培训课件场景）

LLM 失败自动回落关键词模板拼接（按钮永远有产出）。
"""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..auth import require_user
from ..db import engine
from ..jobs.runner import runner
from ..llm import gateway
from ..models import Article, User

router = APIRouter(prefix="/api")

# 风格预设：版式 + 视觉语言（提示词里展开）
STYLE_PRESETS: dict[str, str] = {
    "auto": "根据内容自动选择最合适的版式与风格",
    "banner": "16:9 横幅版式，浅蓝紫科技感背景（渐变光效、城市天际线剪影、玻璃质感元素），三大卡片横排，品牌 logo 位与手写体点缀",
    "recruit": "9:16 竖版，深海军蓝背景 + 橙金点缀，发光门/剪影人物等隐喻视觉，大号渐变数字标题，编号卡片列表，价格区与二维码占位",
    "knowledge": "3:4 竖版，奶油色知识卡片版式，标题+分节要点+结论条，信息密度高但留白呼吸，商务简洁",
    "marketing": "9:16 竖版，深色科技背景 + 手机样机/3D 图标元素，高饱和点缀色，利益点图标行、流程箭头、大字 CTA 横幅",
}


class SlotPromptIn(BaseModel):
    slot_index: int = 0
    style: str = "auto"


class FreeTextIn(BaseModel):
    content: str
    poster_type: str = "knowledge"
    count: int = 3


def _slots_of(md: str) -> list[dict]:
    """文章配图位：search: 占位（含 caption/keywords）+ 生成图卡（cover/flow/chart/gen）。"""
    slots: list[dict] = []
    for m in re.finditer(r"!\[([^\]]*)\]\(search:([^)]*)\)", md or ""):
        slots.append({"kind": "配图", "caption": m.group(1), "kw": m.group(2).strip()})
    for kind in ("cover", "flow", "chart", "gen"):
        for m in re.finditer(rf"!\[([^\]]*)\]\({kind}:([^)]*)\)", md or ""):
            slots.append({"kind": "已生成图", "caption": m.group(1), "kw": ""})
    return slots


def _slot_context(md: str, idx: int, span: int = 900) -> str:
    """取配图位前后各 span/2 字的正文上下文，给 LLM 当素材。"""
    slots = list(re.finditer(r"!\[[^\]]*\]\([^)]*\)", md or ""))
    if idx >= len(slots):
        return (md or "")[:span]
    a = max(0, slots[idx].start() - span // 2)
    b = min(len(md), slots[idx].end() + span // 2)
    return (md or "")[a:b]


def _template_prompt(caption: str, kw: str, style: str) -> str:
    """无 LLM 时的兜底拼接：关键词 + 风格预设。"""
    style_desc = STYLE_PRESETS.get(style, STYLE_PRESETS["knowledge"])
    topic = (caption or kw or "主题").strip()[:40]
    return (f"竖版知识海报 3:4。主题「{topic}」。{style_desc}。"
            f"版面：顶部大标题「{topic}」，中部三个要点卡片（围绕{topic}各写一句要点），"
            f"底部一行结论。图中所有文字为简体中文、清晰准确，商务简洁风，留白呼吸")


def _build_messages(content: str, style_desc: str, count: int, slots: list[dict] | None) -> list[dict]:
    slot_note = ""
    if slots is not None:
        slot_note = "已知的配图位清单（prompt 按此顺序一一对应）：" + json.dumps(
            [{"index": i, **sl} for i, sl in enumerate(slots)], ensure_ascii=False) + "\n"
    system = (
        "你是即梦(Seedream) AI 绘图的资深提示词工程师。任务：为公众号/培训课件生成『图文一体海报』"
        "的完整生成提示词——画面元素与图内中文文案一体描述，文案必须逐字给出（即梦会把这些文字画进图里）。\n"
        "规则：\n"
        "1. 每条提示词结构：版式与画幅 → 背景与视觉元素（具体到道具/场景/光效）→ 标题（中文逐字）→ "
        "内容区块（每块的中文文案逐字，用「」包裹）→ 点缀元素 → 色彩与质感 → 收尾（品牌位/二维码占位）\n"
        "2. 图内中文文案：短句有力、密度高但单块不超过 3 行、商务口语风、数字具体；"
        "避免生僻字、避免中英混排错位风险\n"
        "3. 严格遵循给定的版式与风格描述\n"
        "4. 输出 JSON：{\"posters\":[{\"title\":\"海报标题\",\"layout\":\"9:16|3:4|16:9\","
        "\"prompt\":\"完整提示词（含全部图内中文文案）\"}]}\n"
    )
    user = f"风格与版式：{style_desc}\n{slot_note}\n内容素材：\n{content[:6000]}"
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def _style_desc(style: str) -> str:
    return STYLE_PRESETS.get(style, style)


@router.post("/articles/{article_id}/poster-prompt")
async def article_slot_prompt(article_id: int, body: SlotPromptIn,
                              user: User = Depends(require_user)) -> dict:
    """单个配图位的海报提示词（LLM 精修，失败回落关键词模板）。"""
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="article not found")
        md = art.md
        title = art.title
    slots = _slots_of(md)
    idx = max(0, min(body.slot_index, max(0, len(slots) - 1)))
    sl = slots[idx] if slots else {"kind": "配图", "caption": "", "kw": ""}
    ctx = _slot_context(md, idx)
    style_desc = _style_desc(body.style)
    fallback = _template_prompt(sl.get("caption", ""), sl.get("kw", ""), body.style)
    prompt, mode = fallback, "template"
    try:
        data = await gateway.complete_json(
            _build_messages(ctx, style_desc, 1, [sl]),
            purpose="poster_prompt", max_tokens=3000, thinking="disabled")
        posters = [p for p in (data.get("posters") or []) if isinstance(p, dict)]
        if posters and (posters[0].get("prompt") or "").strip():
            prompt = str(posters[0]["prompt"]).strip()
            mode = "llm"
    except Exception as e:  # noqa: BLE001 LLM 失败回落模板
        print(f"[poster] LLM 失败回落模板: {type(e).__name__}: {str(e)[:80]}")
    return {"prompt": prompt, "mode": mode, "slot": sl,
            "article_title": title, "style": body.style}


@router.post("/articles/{article_id}/poster-pack")
async def article_poster_pack(article_id: int,
                              user: User = Depends(require_user)) -> dict:
    """整篇全部配图位的提示词包（一次 LLM 抽取全部；失败回落模板拼接）。"""
    with Session(engine) as s:
        art = s.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="article not found")
        md, title = art.md, art.title
    slots = _slots_of(md)
    if not slots:
        raise HTTPException(status_code=400, detail="该文章没有配图位（无 search:/生成图 引用）")
    style_desc = _style_desc("auto")
    items: list[dict] = []
    mode = "llm"
    try:
        data = await gateway.complete_json(
            _build_messages(md, style_desc, len(slots), slots),
            purpose="poster_prompt", max_tokens=8000, thinking="disabled")
        posters = [p for p in (data.get("posters") or []) if isinstance(p, dict)]
        for i, sl in enumerate(slots):
            p = posters[i] if i < len(posters) else {}
            prompt = str(p.get("prompt") or "").strip()
            if not prompt:
                prompt = _template_prompt(sl.get("caption", ""), sl.get("kw", ""), "auto")
                mode = "llm+template" if mode == "llm" else mode
            items.append({"slot": i, "caption": sl.get("caption", ""),
                          "kw": sl.get("kw", ""), "title": p.get("title") or sl.get("caption", ""),
                          "layout": p.get("layout") or "3:4", "prompt": prompt})
    except Exception as e:  # noqa: BLE001 LLM 全挂 → 模板兜底，绝不空手
        mode = "template"
        print(f"[poster] pack LLM 失败回落模板: {type(e).__name__}: {str(e)[:80]}")
        for i, sl in enumerate(slots):
            cap = sl.get("caption", "")
            items.append({"slot": i, "caption": cap, "kw": sl.get("kw", ""),
                          "title": cap or f"配图 {i+1}", "layout": "3:4",
                          "prompt": _template_prompt(cap, sl.get("kw", ""), "auto")})
    return {"article_id": article_id, "title": title, "mode": mode, "items": items}


@router.post("/poster/freetext")
async def freetext_posters(body: FreeTextIn,
                           user: User = Depends(require_user)) -> dict:
    """任意内容/大纲 → N 张海报提示词（无文章也能用：培训课件、公众号皆可）。"""
    content = (body.content or "").strip()
    if len(content) < 30:
        raise HTTPException(status_code=400, detail="内容太短（至少 30 字）")
    count = max(1, min(body.count, 8))
    style_desc = _style_desc(body.poster_type)
    try:
        data = await gateway.complete_json(
            _build_messages(content, style_desc, count, None),
            purpose="poster_prompt", max_tokens=6000, thinking="disabled")
        posters = [p for p in (data.get("posters") or [])
                   if isinstance(p, dict) and (p.get("prompt") or "").strip()][:count]
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"提示词生成失败：{str(e)[:120]}")
    if not posters:
        raise HTTPException(status_code=502, detail="模型未产出可用提示词，请重试")
    return {"posters": posters[:count]}
