"""实测打假线（PRD R1.4③，2026-11-14 定版）：清单、状态、成稿。

实测是物理动作（真金白银买课/上手工具/跑流程），系统无法代劳——
`falsify_hunt` 只负责每天从拆解库分析与当日热源里猎取「一人可实测验证」的候选
（买得起/用得上/跑得完；纯观点争议归靶子卡），`compose_falsify` 只负责把
已实测（tested+结论非空）的条目送进独立实测稿模板。没有实测事实的打假稿=编造。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, register_executor, runner
from ..llm import gateway
from ..models import BenchmarkVideo, FalsifyItem, Script
from ..prompts import render

FALSIFY_STATUS = ("pending", "testing", "tested", "scripted", "dismissed")
_HUNT_MAX = 3

_NORM_RE = re.compile(r"[\s「」『』《》\"'：:，,。！!？?·|()（）\-+#]")


def _norm(t: str) -> str:
    return _NORM_RE.sub("", str(t)).lower()


async def hunt_candidates(ctx: JobContext, tenant_id: int) -> dict:
    """猎取实测候选（card_daily 尾段调用 / falsify_hunt 执行器入口）。
    输入=拆解库近 14 天 analysis + 当日 AIHot/科技头条；LLM 一次；claim 归一化去重 ≥0.55 拦。"""
    from datetime import timedelta as _td

    from ..tenant_brand import brand_of
    from ..topics.hotlist import fetch_aihot_hot, fetch_tech_news

    persona = brand_of(tenant_id)["persona"]
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(days=14)
    with Session(engine) as s:
        rows = s.exec(select(BenchmarkVideo).where(  # type: ignore[attr-defined]
            BenchmarkVideo.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        existing = [f.claim for f in s.exec(select(FalsifyItem)).all()]  # type: ignore[call-overload]
    bench_lines = []
    for b in rows[:30]:
        a = json.dumps(b.analysis or {}, ensure_ascii=False)
        if len(a) > 4:
            bench_lines.append(f"- {b.title[:36]}｜{b.author or '?'}｜{a[:240]}")
    try:
        ah_items, _ = await asyncio.to_thread(fetch_aihot_hot)
        news_items, _ = await asyncio.to_thread(fetch_tech_news)
        hot_lines = ([f"- AIHot：{x['title']}｜{x.get('summary', '')[:50]}" for x in ah_items[:10]]
                     + [f"- 科技：{x['title']}（{x['source']}）" for x in news_items[:10]])
    except Exception as e:  # noqa: BLE001 热源失败不拦猎取（还有拆解库可挑）
        hot_lines = [f"（热源未生效：{type(e).__name__}）"]

    existing_norms = [_norm(t) for t in existing if t]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "falsify_hunt", PERSONA=persona,
            BENCH="\n".join(bench_lines) or "（近 14 天拆解库为空）",
            HOT="\n".join(hot_lines) or "（今日热源为空）",
            EXISTING="\n".join(f"- {t}" for t in existing) or "（清单为空）")}],
        max_tokens=4000, thinking="disabled")
    picked: list[FalsifyItem] = []
    for x in (data.get("claims") or []) if isinstance(data, dict) else []:
        claim = str(x.get("claim") or "").strip()
        if not claim:
            continue
        cn = _norm(claim)
        if any(SequenceMatcher(None, cn, en).ratio() >= 0.55
               or (len(en) >= 8 and (en in cn or cn in en)) for en in existing_norms):
            continue  # 已有清单里的不重复猎
        existing_norms.append(cn)
        picked.append(FalsifyItem(tenant_id=tenant_id, claim=claim[:60],
                                  target=str(x.get("target") or "")[:40],
                                  evidence_note=str(x.get("evidence_note") or "")[:120],
                                  status="pending", auto_source=True))
        if len(picked) >= _HUNT_MAX:
            break
    if picked:
        with Session(engine) as s:
            for it in picked:
                s.add(it)
            s.commit()
    ctx.set_progress(99, f"实测候选猎取：+{len(picked)} 条")
    return {"hunted": len(picked)}


@register_executor("falsify_hunt")
async def falsify_hunt(ctx: JobContext, payload: dict) -> dict:
    """手动触发猎取（日常随 card_daily 尾段自动跑）。"""
    return await hunt_candidates(ctx, payload.get("tenant_id") or 1)


async def compose_falsify(*, item_id: int, user=None, length: str = "short") -> dict:
    """成稿：已实测（tested+结论非空）→ falsify_script 独立模板出稿（打磨/定稿复用既有链）。
    没有实测事实的打假稿=编造——结论未回填的条目在此拦下。"""
    with Session(engine) as s:
        item = s.get(FalsifyItem, item_id)
        if item is None:
            raise KeyError("falsify item not found")
        if item.status != "tested":
            raise RuntimeError(f"状态 {item.status} 不可成稿（先推进到「已实测」并回填结论）")
        if not (item.result_note or "").strip():
            raise RuntimeError("实测结论为空——没有实测事实，成稿就是编造")
        dup = s.exec(select(Script).where(  # type: ignore[attr-defined]
            Script.falsify_item_id == item_id,  # type: ignore[attr-defined]
            Script.status.in_(["generating", "polishing"]))).first()  # type: ignore[attr-defined]
        if dup is not None:
            raise RuntimeError(f"该条目正在成稿（脚本 #{dup.id}），等完成或删除后再试")
        script = Script(falsify_item_id=item_id, account_type="persona",
                        title_hint=(item.claim or "")[:60], status="generating")
        s.add(script)
        s.flush()
        item.status = "scripted"
        item.script_id = script.id
        s.add(item)
        s.commit()
        s.refresh(script)
        script_id = script.id
    actor = (user.tenant_id, user.id, user.role) if user is not None else None
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "falsify_item_id": item_id,
                               "length": length, "account_type": "persona",
                               "use_style": True, "angles": [], "template_id": 0},
                              actor=actor)
    return {"script_id": script_id, "job_id": job.id, "item_id": item_id}
