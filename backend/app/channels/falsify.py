"""实测打假线（PRD R1.4③ / R2.3）：清单=素材库 test_record 子集，状态=stage 轴。

实测是物理动作（真金白银买课/上手工具/跑流程），系统无法代劳——
`hunt_candidates` 只负责每天从素材库拆解与当日热源里猎取「一人可实测验证」的候选
（买得起/用得上/跑得完；纯观点争议归靶子卡），`compose_falsify` 只负责把
已实测（stage=tested 且结论非空）的条目送进独立实测稿模板。没有实测事实的打假稿=编造。

存储映射（P2 素材域，2026-11-16）：claim→materials.title、实测结论→body、
线索→note、对象/auto_source/script_id→quality；stage=pending/testing/tested/scripted/dismissed。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlmodel import Session, select

from ..db import engine
from ..jobs.runner import JobContext, runner
from ..llm import gateway
from ..models import Content, Material
from ..prompts import render
from ..materials.service import record_usage

FALSIFY_STATUS = ("pending", "testing", "tested", "scripted", "dismissed")
_HUNT_MAX = 3
from .persona.norms import _norm  # 归一化单点（2026-11-16 合并，原逐字符同款双份）

_ORIGIN = "test_record"


def _item_out(m: Material) -> dict:
    """素材行 → 实测清单条目形状（HotTargets 第三 tab 契约不变）。"""
    q = m.quality or {}
    return {
        "id": m.id, "claim": m.title or "", "target": q.get("target") or "",
        "evidence_note": m.note or "", "result_note": m.body or "",
        "status": m.stage or "pending",
        "auto_source": bool(q.get("auto_source")),
        "script_id": q.get("script_id"),
        "created_at": m.created_at, "updated_at": m.updated_at,
    }


async def hunt_candidates(ctx: JobContext, tenant_id: int) -> dict:
    """猎取实测候选（card_daily 尾段调用）。
    输入=素材库近 14 天拆解 + 当日 AIHot/科技头条；LLM 一次；claim 归一化去重 ≥0.55 拦。"""
    from ..materials.service import analyses_map, upsert_material

    from ..tenant_brand import brand_of
    from ..materials.collectors.hotlist import fetch_aihot_hot, fetch_tech_news

    persona = brand_of(tenant_id)["persona"]
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=14)
    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
            Material.status == "ready",  # type: ignore[attr-defined]
            Material.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        existing = [m.title for m in s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == _ORIGIN)).all()]  # type: ignore[attr-defined]
        amap = analyses_map(s, [r.id for r in rows])
    bench_lines = []
    for b in rows[:30]:
        a = json.dumps(((amap.get(b.id) or {}).get("breakdown") or {}).get("payload") or {},
                       ensure_ascii=False)
        if len(a) > 4:
            bench_lines.append(f"- {b.title[:36]}｜{b.author or '?'}｜{a[:240]}")
    try:
        ah_items, _ = await asyncio.to_thread(fetch_aihot_hot)
        news_items, _ = await asyncio.to_thread(fetch_tech_news)
        hot_lines = ([f"- AIHot：{x['title']}｜{x.get('summary', '')[:50]}" for x in ah_items[:10]]
                     + [f"- 科技：{x['title']}（{x['source']}）" for x in news_items[:10]])
    except Exception as e:  # noqa: BLE001 热源失败不拦猎取（还有素材库可挑）
        hot_lines = [f"（热源未生效：{type(e).__name__}）"]

    existing_norms = [_norm(t) for t in existing if t]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "falsify_hunt", PERSONA=persona,
            BENCH="\n".join(bench_lines) or "（近 14 天素材库拆解为空）",
            HOT="\n".join(hot_lines) or "（今日热源为空）",
            EXISTING="\n".join(f"- {t}" for t in existing) or "（清单为空）")}],
        max_tokens=4000, thinking="disabled")
    picked = 0
    for x in (data.get("claims") or []) if isinstance(data, dict) else []:
        claim = str(x.get("claim") or "").strip()
        if not claim:
            continue
        cn = _norm(claim)
        if any(SequenceMatcher(None, cn, en).ratio() >= 0.55
               or (len(en) >= 8 and (en in cn or cn in en)) for en in existing_norms):
            continue  # 已有清单里的不重复猎
        existing_norms.append(cn)
        with Session(engine) as s:
            upsert_material(
                s, tenant_id=tenant_id, origin_type=_ORIGIN,
                title=claim[:60], note=str(x.get("evidence_note") or "")[:120],
                quality={"target": str(x.get("target") or "")[:40],
                         "auto_source": True, "claim_norm": cn[:60]},
                stage="pending", status="ready")
        picked += 1
        if picked >= _HUNT_MAX:
            break
    ctx.set_progress(99, f"实测候选猎取：+{picked} 条")
    return {"hunted": picked}


async def compose_falsify(*, item_id: int, user=None, length: str = "short") -> dict:
    """成稿：已实测（stage=tested+结论非空）→ falsify_script 独立模板出稿。
    没有实测事实的打假稿=编造——结论未回填的条目在此拦下。"""
    with Session(engine) as s:
        m = s.get(Material, item_id)
        if m is None or m.origin_type != _ORIGIN:
            raise KeyError("falsify item not found")
        if (m.stage or "") != "tested":
            raise RuntimeError(f"状态 {m.stage} 不可成稿（先推进到「已实测」并回填结论）")
        if not (m.body or "").strip():
            raise RuntimeError("实测结论为空——没有实测事实，成稿就是编造")
        dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.payload.op('->>')('falsify_item_id') == str(item_id),  # type: ignore[attr-defined]
            Content.status == "drafting")).first()  # type: ignore[attr-defined]
        if dup is not None:
            raise RuntimeError(f"该条目正在成稿（脚本 #{dup.id}），等完成或删除后再试")
        from .engine import new_script_content

        script = new_script_content(
            s, tenant_id=m.tenant_id or (user.tenant_id if user else 1),
            channel_key="persona", title_hint=(m.title or "")[:60],
            payload={"script_state": "generating", "falsify_item_id": item_id,
                     "account_type": "persona"})
        record_usage(s, tenant_id=m.tenant_id or (user.tenant_id if user else 1),
                     material_id=item_id, target_type="content",
                     target_id=script.id, role="source")
        m.stage = "scripted"
        q = dict(m.quality or {})
        q["script_id"] = script.id
        m.quality = q
        s.add(m)
        s.commit()
        script_id = script.id
    actor = (user.tenant_id, user.id, user.role) if user is not None else None
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "falsify_item_id": item_id,
                               "length": length, "account_type": "persona",
                               "use_style": True, "angles": []},
                              actor=actor)
    return {"script_id": script_id, "job_id": job.id, "item_id": item_id}
