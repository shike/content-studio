"""朋友圈线（M9 2026-11-22 v1）：母题派生的私域文案生成。

边界：复制人工发布+payload 标记已发；不做指标回流（私域数据手工无法稳定回流，用户裁）。
素材纪律与全系统一致——不虚构号主经历/客户反馈/当天行踪，口播稿有真实细节才复用。
"""
from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor, register_concurrent
from ..llm import gateway
from ..models import Content, Idea, Motif
from ..prompts import render


def _motif_script_body(s: Session, motif_id: int) -> str:
    """母题下最新一篇有正文的口播稿（真实细节的唯一来源；没有就不硬编）。"""
    ideas = s.exec(select(Idea).where(  # type: ignore[attr-defined]
        Idea.motif_id == motif_id,  # type: ignore[attr-defined]
        Idea.status != "discarded")).all()  # type: ignore[attr-defined]
    idea_ids = [i.id for i in ideas]
    if not idea_ids:
        return ""
    scripts = s.exec(select(Content).where(  # type: ignore[attr-defined]
        Content.idea_id.in_(idea_ids),  # type: ignore[attr-defined]
        Content.kind == "video_script")).all()  # type: ignore[attr-defined]
    for sc in sorted(scripts, key=lambda x: -(x.updated_at.timestamp() if x.updated_at else 0)):
        if (sc.body or "").strip():
            return sc.body.strip()
    return ""


register_concurrent("moments_generate")


def _moments_generate_fail(payload: dict, _exc: Exception) -> None:
    """生成失败回退：moments 回 drafting（梯子重试同一内容）。"""
    with Session(engine) as s:
        c = s.get(Content, int(payload.get("content_id") or 0))
        if c is not None and (c.payload or {}).get("moments_state") == "generating":
            p = dict(c.payload or {})
            p["moments_state"] = "drafting"
            c.payload = p
            s.add(c)
            s.commit()


@register_executor("moments_generate", on_fail=_moments_generate_fail)
async def moments_generate(ctx: JobContext, payload: dict) -> dict:
    content_id = int(payload.get("content_id") or 0)
    with Session(engine) as s:
        c = s.get(Content, content_id)
        if c is None or c.kind != "moments":
            raise RuntimeError(f"moments #{content_id} 不存在")
        p = dict(c.payload or {})
        motif_id = int(p.get("motif_id") or 0)
        motif = s.get(Motif, motif_id) if motif_id else None
        script_body = _motif_script_body(s, motif_id) if motif_id else ""
        title = motif.title if motif else (c.title or "")
        angle_note = (motif.note if motif else "")[:200]
        p["moments_state"] = "generating"
        c.payload = p
        s.add(c)
        s.commit()

    ctx.set_progress(30, "生成朋友圈文案（1~2 条）")
    data = await gateway.complete_json(
        [{"role": "user", "content": render("moments_generate", TITLE=title or "（无标题）",
                                            ANGLE_NOTE=angle_note or "（无）",
                                            SCRIPT_EXCERPT=script_body[:2000] or "（无口播稿素材——只按母题写观点，不编细节）")}],
        max_tokens=2000, thinking="disabled")
    posts = []
    for x in (data or {}).get("posts") or []:
        if not isinstance(x, dict):
            continue
        body = str(x.get("body") or "").strip()
        if not body:
            continue
        posts.append({"content_type": str(x.get("content_type") or "")[:12],
                      "body": body[:500],
                      "visual": str(x.get("visual_suggestion") or "no_image")[:16]})
    if not posts:
        raise RuntimeError("朋友圈生成空响应（posts 缺失）——稍后自动重试")

    with Session(engine) as s:
        c = s.get(Content, content_id)
        p = dict(c.payload or {})
        p["posts"] = posts
        p["moments_state"] = "review"
        c.payload = p
        c.body = posts[0]["body"]  # 首条进 body（台账/检索可见）
        c.status = "review"
        s.add(c)
        s.commit()
    ctx.set_progress(100, f"完成 {len(posts)} 条文案（待发）")
    return {"content_id": content_id, "posts": len(posts)}


def mark_moments_published(s: Session, content_id: int) -> None:
    """手动标记已发（复制发布后点一下；不做指标回流）。"""
    c = s.get(Content, content_id)
    if c is None or c.kind != "moments":
        return
    p = dict(c.payload or {})
    p["moments_state"] = "published"
    p["published_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M")
    c.payload = p
    c.status = "published"
    s.add(c)
