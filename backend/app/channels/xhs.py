"""小红书线（PRD R5）：图文笔记生成 + 封面模板渲染。

- 选题：选题会出角度三元组（2026-11-21 阶段 3）或手动发起，每日自动产线仍不做（R5.2 宁缺勿滥）；
- 笔记稿：note_generate（三元组进稿/标题必含搜索词/首行搜索词/收藏钩子/站队点/轻质量闸）；
- 封面：模板渲染（品牌底色+大字+副标+署名角标，Pillow 直出），不做 AI 生图（R5.3）；
- 发布：人工（R5.4），publications 登记；不做浏览器自动化（R5.5）。
"""
from __future__ import annotations

import asyncio
import re

from sqlmodel import Session

from ..db import engine
from ..playbook import playbook_block
from ..jobs.runner import JobContext, register_executor
from ..llm import gateway
from ..materials.service import record_usage
from ..models import Content
from ..prompts import render
from ..settings import settings


def render_cover(title_big: str, tenant_id: int, out_name: str, sub: str = "") -> str:
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
    # 副标（2026-11-21 阶段 3：补一句利益/悬念，与大字互补；金色小字压在大字块下方）
    sub = (sub or "").strip()[:14]
    if sub:
        draw.text((100, min(y + 20, H - 320)), sub, font=_font(56), fill="#" + accent)
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
        # 角度三元组（2026-11-21 阶段 3 选题会选定；空=手填旧行为）
        audience = str((c.payload or {}).get("audience") or "")
        pain = str((c.payload or {}).get("pain") or "")
        keyword = str((c.payload or {}).get("keyword") or "")
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
        # 知识库素材 → 生产资料包（2026-11-18 车道一：因果链/操作/边界/数字账，深度的原料）
        kit = {"chain": [], "actions": [], "boundaries": [], "numbers": []}
        for m in [x for x in mats if x is not None] if material_ids else []:
            if m.origin_type == "kb_doc":
                from ..articles.prod_kit import ensure_prod_kit, kit_block

                kit = await ensure_prod_kit(m.id)
                break

    # 角度三元组注入（2026-11-21 阶段 3）：对着人群说话、从痛点切入、搜索词进标题与首行
    angle_lines = [x for x in (f"- 写给谁：{audience}" if audience else "",
                               f"- 痛点：{pain}" if pain else "",
                               f"- 搜索词：{keyword}" if keyword else "") if x]
    angle_block = "\n".join(angle_lines) if angle_lines else "（无三元组——按切角/素材自由发挥）"
    if keyword:
        angle_block += f"\n写稿硬约束：搜索词「{keyword}」必须进标题与正文首行；对着这个人群说话，从痛点切入。"

    ctx.set_progress(15, "生成笔记稿（候选标题/正文/标签）")
    data = await gateway.complete_json(
        [{"role": "user", "content": render("note_generate", PERSONA=persona_text, KIT=kit_block(kit),
                                            ANGLE_BLOCK=angle_block, ANGLE_KEYWORD=keyword or "（无）")
          + "\n\n选题：" + (idea_title or "（无标题，按素材自由发挥）")
          + ("\n\n素材：\n" + "\n\n".join(blocks) if blocks else "\n\n素材：（无——按选题与人设写常青干货）")
          + playbook_block("note")}],
        max_tokens=2048, thinking="disabled")
    options = [str(t).strip() for t in (data.get("title_options") or []) if str(t).strip()][:5]
    title = str(data.get("title") or "").strip()
    body = str(data.get("body") or "").strip()
    tags = [str(t).lstrip("#").strip()[:16] for t in (data.get("tags") or []) if str(t).strip()][:8]
    if options and not title:
        title = options[0]

    # 标题点击力评分选主（2026-11-18 车道一：数字/身份词/信息差三维，此前无标题评分）
    if len(options) >= 2:
        ctx.set_progress(18, "候选标题点击力评分选主")
        pick = await gateway.complete_json(
            [{"role": "user", "content":
              "小红书标题点击力评分（收藏率导向）：数字具体度/身份词命中/信息差承诺，各 0~10。\n"
              "候选：\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(options)) +
              '\n输出 JSON：{"best": 最佳序号}'}],
            max_tokens=1024, thinking="disabled")
        try:
            bi = int((pick or {}).get("best") or 1)
            if 1 <= bi <= len(options):
                title = options[bi - 1]
        except (TypeError, ValueError):
            pass

    # 深度覆盖核检（知识库素材时）：资料包 actions/chain/boundaries 是否讲到，缺项补写一次
    if any(kit.values()):
        import json as _json

        ctx.set_progress(22, "深度覆盖核检（对照资料包）")
        chk = await gateway.complete_json(
            [{"role": "user", "content":
              "对照资料包核检笔记覆盖：chain/actions/boundaries 每条判定 covered|missing（numbers 不查）。\n"
              "资料包：\n" + _json.dumps(kit, ensure_ascii=False) + "\n\n笔记正文：\n" + body +
              '\n输出 JSON：{"missing": ["未覆盖条目原文，最多 3 条"]} 全覆盖给空数组'}],
            max_tokens=1024, thinking="disabled")
        missing = [str(x)[:120] for x in (chk or {}).get("missing") or []][:3]
        if missing:
            ctx.set_progress(26, f"深度缺 {len(missing)} 项，定向补写")
            fix = await gateway.complete_json(
                [{"role": "user", "content":
                  render("note_revise", PERSONA=persona_text, FEEDBACK="补讲这些缺失环节（因果链讲到位、操作落到步骤）："
                                                                   + "；".join(missing), TITLE=title, DRAFT=body) + playbook_block("note")}],
                max_tokens=2048, thinking="disabled")
            n_body = str(fix.get("body") or "").strip()
            if n_body:
                body = n_body
            n_title = str(fix.get("title") or "").strip()
            if n_title:
                title = n_title[:30]
    cover_title = str(data.get("cover_title") or "").strip()[:10]
    cover_sub = str(data.get("cover_sub") or "").strip()[:14]
    if not title or not body:
        raise RuntimeError("笔记生成空响应（title/body 缺失），稍后自动重试")

    # ── 轻质量闸（2026-11-21 阶段 3）：机检+一次轻审，红提示落 payload.issues，不拦发布 ──
    ctx.set_progress(80, "轻质量体检（机检+轻审）")
    issues: list[str] = []
    n_chars = len(re.sub(r"\s", "", body))
    if n_chars < 300 or n_chars > 800:
        issues.append(f"正文 {n_chars} 字出 300~800 区间")
    if not (3 <= len(tags) <= 8):
        issues.append(f"标签 {len(tags)} 个出 3~8 区间")
    if keyword and keyword not in body[:80]:
        issues.append(f"正文首行没有搜索词「{keyword}」")
    if keyword and all(keyword not in t for t in ([title] + options)):
        issues.append(f"标题都没有搜索词「{keyword}」")
    if cover_title and cover_title in title:
        issues.append("封面大字与标题原词重复（要换说法）")
    for w in ("保证收益", "百分百", "稳赚", "宝子们", "小编", "总的来说", "综上所述", "本文", "这篇笔记"):
        if w in body:
            issues.append(f"正文出现禁词「{w}」")
    # 去AI味机检（2026-11-22 太上老君框架 P1-4）：确定性规则（假对比/旁白腔/填充词/最长句）
    from ..materials.deai_lint import lint

    issues += lint(body, "xhs")
    try:
        lr = await gateway.complete_json(
            [{"role": "user", "content":
              "小红书笔记轻审（只报真问题，每条 ≤30 字，没有问题给空数组）：\n"
              "1. AI 腔：元叙述（这篇/本文）、书面长句、总结腔；\n"
              "2. 封面与标题是否呼应（大字「" + (cover_title or "无") + "」标题「" + title[:20] + "」）；\n"
              "3. 收藏钩子是否真实存在：正文里有没有真能截图带走的清单/步骤/表。\n"
              "正文：\n" + body +
              '\n输出 JSON：{"issues": ["..."]}'}],
            max_tokens=1024, thinking="disabled")
        issues += [str(x)[:60] for x in (lr or {}).get("issues") or []][:3]
    except Exception as e:  # noqa: BLE001 轻审失败不挡成稿
        ctx.set_progress(82, f"轻审跳过（{type(e).__name__}）")

    ctx.set_progress(70, "渲染封面（品牌模板）")
    cover_path = ""
    try:
        cover_path = await asyncio.to_thread(
            render_cover, cover_title or title[:10], tenant_id,
            f"xhs_{content_id}.png", sub=cover_sub)
    except Exception as e:  # noqa: BLE001 封面失败不挡成稿（可手补）
        ctx.set_progress(72, f"封面渲染失败（可后台补）：{type(e).__name__}: {str(e)[:60]}")

    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["note_state"] = "review"
        p["tags"] = tags
        p["cover"] = cover_path
        p["cover_sub"] = cover_sub
        p["issues"] = issues  # 红提示（终审参考，不拦发布）
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
