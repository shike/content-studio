"""小红书笔记选题会（2026-11-21 阶段 3）：KB 源文抽 5 个角度三元组。

角度=人群+痛点+搜索词（R5.3 spec 升级的核心）：人先选角度，生成对着三元组写。
与 article_pitch 同构（拉源文→LLM→job.result），复用其执行器注册模式。
"""
from __future__ import annotations

from ..jobs.runner import register_executor
from ..llm import gateway
from ..prompts import render

_SRC_CAP = 100000  # 与改编器/长文选题会同源同截


def _clean_angles(v: dict | None) -> list[dict]:
    out: list[dict] = []
    for a in (v or {}).get("angles") or []:
        if not isinstance(a, dict):
            continue
        triple = {k: str(a.get(k) or "").strip()[:60]
                  for k in ("audience", "pain", "keyword")}
        title = str(a.get("title") or "").strip()[:20]
        hook = str(a.get("hook") or "").strip()[:120]
        if not all(triple.values()) or not title:
            continue  # 三元组或标题缺一即弃
        out.append({**triple, "title": title, "hook": hook})
    return out[:5]


async def generate_note_angles(source: str, n: int = 5) -> list[dict]:
    """出角度：失败返回空列表（调用方给跳过兜底）。"""
    if not (source or "").strip():
        return []
    v = await gateway.complete_json(
        [{"role": "user", "content": render("note_pitch", SOURCE=source, N=str(n))}],
        max_tokens=4000, thinking="disabled")
    return _clean_angles(v)


@register_executor("note_pitch")
async def note_pitch(ctx, payload: dict) -> dict:
    """源文开笔记选题会：拉全文 → 抽 5 个角度三元组 → 存 job.result。失败语义同 article_pitch。"""
    from sqlmodel import Session as _Session

    from ..articles.ima_kb import fetch_full_doc, kb_config
    from ..db import engine
    from ..jobs.errors import TransientError
    from ..models import Material

    item_id = int(payload.get("item_id") or 0)
    with _Session(engine) as s:
        it = s.get(Material, item_id)
        if it is None or it.origin_type != "kb_doc":
            raise RuntimeError(f"清单项 #{item_id} 不存在")
        mid = it.origin_id
        src_title = it.title or ""
        cfg = kb_config(s)
        if cfg is None:
            raise RuntimeError("选题会未启用：缺少 ima_kb_enabled / 凭证配置")
        client_id, api_key = cfg["client_id"], cfg["api_key"]

    ctx.set_progress(20, f"拉源文全文《{src_title[:30]}》")
    try:
        source = await fetch_full_doc(client_id, api_key, mid, cap=_SRC_CAP)
    except Exception as e:  # noqa: BLE001 ima 侧抖动走重试梯子
        raise TransientError(f"源文全文拉取失败（{type(e).__name__}），稍后自动重试")
    if not (source or "").strip():
        raise RuntimeError("源文全文不可读（media 已失效），开不了选题会")

    ctx.set_progress(55, "从源文抽角度三元组（人群+痛点+搜索词）")
    angles = await generate_note_angles(source, n=5)
    if not angles:
        raise TransientError("选题会没有出合格的角度（LLM 输出为空或缺三元组），稍后重试")
    ctx.set_progress(100, f"出 {len(angles)} 个角度")
    return {"angles": angles, "title": src_title}
