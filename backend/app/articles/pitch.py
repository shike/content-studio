"""选题会（2026-11-20）：从源文抽 N 个可写判断，每个带对手、代价、证据。

两步改编的第一步（用户裁决 A 方案）：人选定判断后，写稿只拿「判断 + 证据」，
不再喂全书——防读书笔记，也防判断永远是源文的那一个。
"""
from __future__ import annotations

from ..jobs.runner import register_executor
from ..llm import gateway
from ..prompts import render


def _clean(v: dict | None) -> list[dict]:
    out: list[dict] = []
    for p in (v or {}).get("pitches") or []:
        if not isinstance(p, dict):
            continue
        thesis = str(p.get("thesis") or "").strip()
        ev = [str(x).strip() for x in (p.get("evidence") or []) if str(x).strip()]
        if not thesis or len(ev) < 2:
            continue
        out.append({
            "scene": str(p.get("scene") or "").strip()[:300],
            "thesis": thesis[:120],
            "opponent": str(p.get("opponent") or "").strip()[:80],
            "cost": str(p.get("cost") or "").strip()[:120],
            "title": str(p.get("title") or "").strip()[:40],
            "evidence": ev[:5],
        })
    return out[:5]


async def generate_pitches(source: str, n: int = 5) -> list[dict]:
    """出题：失败返回空列表（调用方决定是否退回旧路径）。"""
    if not (source or "").strip():
        return []
    v = await gateway.complete_json(
        [{"role": "user", "content": render("article_pitch", SOURCE=source, N=str(n))}],
        max_tokens=8000, thinking="disabled")
    return _clean(v)


# ── 选题会任务（2026-11-21 阶段 2：接回 KB 页）——两步改编的第一步变成显式动作：
# 人开完选题会选定现场，改编器按 payload.pitch 只喂证据包（消费端在 wechat_adapt）。

_SRC_CAP = 100000  # 与改编器同源同截：选题会读到的=改编时读到的


@register_executor("article_pitch")
async def article_pitch(ctx, payload: dict) -> dict:
    """源文开选题会：拉全文 → 抽 5 个现场（scene/thesis/title/evidence）→ 存 job.result。

    失败语义：源文不可读/无现场=确定性失败（安静终态，页面给跳过按钮）；
    ima 侧抖动=TransientError 走重试梯子。"""
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

    ctx.set_progress(55, "从源文抽可单独成篇的现场")
    pitches = await generate_pitches(source, n=5)
    if not pitches:
        raise TransientError("选题会没有出可写的现场（LLM 输出为空或缺证据），稍后重试")
    ctx.set_progress(100, f"出 {len(pitches)} 个现场")
    return {"pitches": pitches, "title": src_title}
