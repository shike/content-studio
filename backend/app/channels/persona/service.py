"""选题工场（人设号靶子卡，PRD R1.4——2026-11-14 三原则定版）。

三车间流水线（`card_daily` 单任务五段）：
车间零·信号采集（0 LLM）→ 车间一·池子构建（一手直产 + pool_pick 二手补位）
→ 车间二·主线锻造（spine_forge + 主线闸）→ 车间三·钩子铸造（hook_cast + 三秒闸）
→ 总法官（judge_cards）→ 落卡（draft 前 N / jail 可捞，批次替代，空过合法）。

三原则（法律，见 PRD R1.4）：
①三秒钟=钩子吸引力装置：观点说清是底线、抓人是目的；时长 ≤3 秒——
  字数线=3 秒×语速口径（时长是法律，字数只是代理），计长剔除标点；
②半句话=视频主线：全程论证但理论上面不出口（不进台词/字幕/收尾）；
③广度×热度=域内×覆盖人口×此刻有流量。

进料优先级：一手·抖音原生（监控号新视频评论 ≥pool_peer_min_comments 直产池子卡 /
话题雷达飙升 / 抖音热点榜）＞二手·外站五路（pool_pick ≤pool_pick_max 条补位，
需法官高分）＋自有转写（常青）。
对标圈素材禁当靶子（纪律不变）。每日 LLM 共 4 次。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from ...db import engine
from ...jobs.runner import JobContext, register_executor
from ...models import Content, Idea, Material, Source
from ...settings import settings

# ── C 阶段结构整治（2026-11-16）：归一/闸门/锻造链拆至 norms/gates/forge，本模块留执行器与编排；facade 导入沿用旧名（api/ 既有导入零改动）──


from .forge import (_forge_with_retry, _llm_cast, _llm_judge,
                    _llm_pick, _self_titles)
from .gates import (_hook_gate, _merge_card, _spine_gate)
from .norms import (_COVER_RANK, _kw_hit, _norm)


def _pool_dict(*, domain: str, heat: str, level: str, role: str = "", coverage: str = "",
               secondary: bool = False, douyin: Optional[dict] = None,
               external: Optional[dict] = None, peer_covered: bool = False) -> dict:
    return {"domain_evidence": domain[:80], "heat_evidence": heat[:80],
            "heat_level": level[:20], "role": role[:30], "coverage": coverage,
            "secondary": bool(secondary), "douyin_evidence": douyin,
            "external_evidence": external, "peer_covered": bool(peer_covered)}


def _published_titles() -> list[str]:
    """已发视频标题（冷饭判据：判冷饭不问人，清单就是判据）。"""
    return [v.title for v in _self_titles(100000) if v.title]


async def _hunt_safe(ctx: JobContext, tenant_id: int, meta: dict) -> None:
    """尾段实测候选猎取（失败不拦落卡——猎取是增益不是产线）。"""
    try:
        from .falsify import service as _ff
        r = await _ff.hunt_candidates(ctx, tenant_id)
        meta["hunted"] = r.get("hunted", 0)
    except Exception as e:  # noqa: BLE001
        meta["hunt_error"] = f"{type(e).__name__}: {str(e)[:80]}"


async def _radar_round(ctx: JobContext) -> tuple[list[str], list[dict]]:
    notes: list[str] = []
    spikes: list[dict] = []
    try:
        with Session(engine) as s:
            radar_rows = s.exec(select(Source).where(  # type: ignore[attr-defined]
                Source.kind == "radar_topic",  # type: ignore[attr-defined]
                Source.status == "active")).all()  # type: ignore[attr-defined]
        for rrow in radar_rows:
            try:
                from ...materials.collectors.radar_patrol import check_topic
                r = await check_topic(rrow)
                notes.append(f"#{rrow.name}：新增 {r['new']} 条" + ("（飙升）" if r["spiked"] else ""))
                if r["spiked"]:
                    spikes.append({"name": rrow.name, "new": r["new"]})
            except Exception as e:  # noqa: BLE001 单话题失败不拖垮整轮
                notes.append(f"#{rrow.name}：{type(e).__name__}: {str(e)[:60]}")
            await asyncio.sleep(3)  # 话题接口 pacing
    except Exception as e:  # noqa: BLE001 雷达侧整体失败不阻塞
        notes.append(f"话题巡检失败：{type(e).__name__}: {str(e)[:60]}")
    return notes, spikes


@register_executor("card_daily")
async def card_daily(ctx: JobContext, payload: dict) -> dict:
    """人设号选题工场五段（R1.4）。空过合法：无可怼=诚实空过，失败是异常不是工作流。"""
    from datetime import timedelta as _td

    from ...materials.collectors.hotlist import (fetch_aihot_hot, fetch_baidu_hot,
                                                fetch_douyin_hot, fetch_github_hot,
                                                fetch_tech_news, fetch_zhihu_hot)
    from ...materials.service import analyses_map
    from ...tenant_brand import audience_note_of, brand_of

    tenant_id = payload.get("tenant_id") or 1
    persona = brand_of(tenant_id)["persona"]
    audience_note = audience_note_of(tenant_id)
    ctx.set_progress(5, "车间零：话题雷达巡检 + 采集四层信号（0 LLM）")
    radar_notes, radar_spikes = await _radar_round(ctx)

    # ── 车间一：池子构建 ──
    ctx.set_progress(22, "车间一：一手直产（监控号爆款/雷达飙升/抖音榜）")
    pools: list[dict] = []  # [{source, pool}]
    meta: dict = {"peer": 0, "radar": 0, "douyin_hot": 0, "external": 0}

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(
        hours=max(1, int(settings.pool_peer_window_hours or 30)))
    with Session(engine) as s:
        recent_cards = s.exec(select(Idea).where(  # type: ignore[attr-defined]
            Idea.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - _td(days=14)
        )).all()  # type: ignore[attr-defined]
        seen_refs = {str((((c.payload or {}).get("pool") or {}).get("douyin_evidence") or {}).get("ref") or "")
                     for c in recent_cards}
        # 一手池子线（P2 素材域）：ready 的抖音素材，新鲜度=published_at（缺省 collected_at）
        from sqlalchemy import func as _func

        peer_rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
            Material.status == "ready",  # type: ignore[attr-defined]
            _func.coalesce(Material.published_at, Material.collected_at) >= cutoff)).all()  # type: ignore[attr-defined]
    peer_amap = {}
    if peer_rows:
        with Session(engine) as s:
            peer_amap = analyses_map(s, [r.id for r in peer_rows])
    peer_min = max(1, int(settings.pool_peer_min_comments or 50))
    routed_leadgen = []  # 求学型评论区视频（获客矿）——元角度口子的候选
    for b in peer_rows:
        am = peer_amap.get(b.id) or {}
        breakdown = (am.get("breakdown") or {}).get("payload") or {}
        comments_top = list(((am.get("comments") or {}).get("payload") or {}).get("items") or [])
        stats = b.stats or {}
        comment = int(stats.get("comment") or 0)
        if comment < peer_min or not b.title or b.origin_url in seen_refs:
            continue
        kind = (b.quality or {}).get("comment_kind") or ""
        if kind == "leadgen_ore":
            # 分型路由（R1.4）：求学型评论区=获客需求信号，归获客线，人设号不吃；
            # 每批最多留 1 条做「求学现象卡」（对求学现象立立场的元角度口子，用户批准）
            routed_leadgen.append(b)
            continue
        pools.append({"source": "douyin_peer", "pool": _pool_dict(
            domain=f"监控对标号 {b.author or '?'}（AI 落地赛道，用户精选清单）",
            heat=f"{b.author or '?'}《{b.title[:36]}》评论 {comment}（抖音实数）",
            level="高", douyin={"kind": "peer_video", "ref": b.origin_url,
                                "comments": comment, "diggs": int(stats.get("digg") or 0),
                                "ore_kind": kind or "persona_ore",
                                "study_share": float((b.quality or {}).get("leadgen_share") or 0),
                                "top": [c for c in comments_top
                                        if isinstance(c, dict) and str(c.get("t") or "").strip()][:12],
                                "digest": str(breakdown.get("summary") or "")[:80]},
            peer_covered=True)})
        meta["peer"] += 1
        seen_refs.add(b.origin_url)
        if meta["peer"] >= 10:
            break
    if routed_leadgen:
        meta["peer_routed_leadgen"] = len(routed_leadgen)
        best = max(routed_leadgen, key=lambda x: int((x.stats or {}).get("comment") or 0))
        am_best = peer_amap.get(best.id) or {}
        best_breakdown = (am_best.get("breakdown") or {}).get("payload") or {}
        best_comments = list(((am_best.get("comments") or {}).get("payload") or {}).get("items") or [])
        st = best.stats or {}
        pools.append({"source": "douyin_peer", "pool": _pool_dict(
            domain=f"监控对标号 {best.author or '?'}（AI 落地赛道，用户精选清单）",
            heat=f"{best.author or '?'}《{best.title[:36]}》评论 {int(st.get('comment') or 0)}（抖音实数）",
            level="高", douyin={"kind": "study_meta", "ref": best.origin_url,
                                "comments": int(st.get("comment") or 0),
                                "diggs": int(st.get("digg") or 0),
                                "study_share": float((best.quality or {}).get("leadgen_share") or 0),
                                "top": [c for c in best_comments
                                        if isinstance(c, dict) and str(c.get("t") or "").strip()][:12],
                                "digest": str(best_breakdown.get("summary") or "")[:80]},
            peer_covered=True)})
        meta["peer_meta"] = 1
        seen_refs.add(best.origin_url)

    for sp in radar_spikes[:6]:
        key = f"radar:{sp['name']}"
        if key in seen_refs:
            continue
        pools.append({"source": "radar", "pool": _pool_dict(
            domain=f"话题雷达监控项（AI 话题清单）#{sp['name']}",
            heat=f"话题 #{sp['name']} 巡检新增 {sp['new']} 条视频（飙升线 5）",
            level="高", douyin={"kind": "radar_spike", "ref": sp["name"],
                                "spike_speed": sp["new"]})})
        meta["radar"] += 1

    dy_items, _dy_meta = await asyncio.to_thread(fetch_douyin_hot, 50)
    for rank, x in enumerate(dy_items, 1):
        word = str(x.get("word") or "").strip()
        desc = str(x.get("desc") or "")
        if not word or f"douyin_hot:{word}" in seen_refs or not _kw_hit(word + " " + desc):
            continue
        pools.append({"source": "douyin_hot", "pool": _pool_dict(
            domain="抖音热点榜在榜，命中域内词根",
            heat=f"抖音热点榜第 {rank} 位：{word[:30]}",
            level="高", douyin={"kind": "douyin_hot", "ref": word[:40], "rank": rank})})
        meta["douyin_hot"] += 1
        seen_refs.add(f"douyin_hot:{word}")
        if meta["douyin_hot"] >= 8:
            break

    ctx.set_progress(34, f"车间一：二手外站精选（一手 {meta['peer'] + meta['radar'] + meta['douyin_hot']} 条已入池）")
    hot_items, _ = await asyncio.to_thread(fetch_baidu_hot, 50)
    zh_items, _ = await asyncio.to_thread(fetch_zhihu_hot, 30)
    news_items, news_meta = await asyncio.to_thread(fetch_tech_news)
    gh_items, _ = await asyncio.to_thread(fetch_github_hot)
    ah_items, ah_meta = await asyncio.to_thread(fetch_aihot_hot)
    menu_parts = [
        "【三榜（百度/知乎）科技条目】" + ("\n".join(
            f"- {x.get('word')}（{x.get('desc', '')[:30]}）" for x in (hot_items + zh_items)
            if _kw_hit(str(x.get('word', '')) + str(x.get('desc', '')))) or "（无科技条目）"),
        "【科技媒体+AI 垂媒（48h）】" + ("\n".join(
            f"- {x['title']}（{x['source']}）" for x in news_items) or f"（未生效：{news_meta.get('status')}）"),
        "【GitHub 新起爆仓库】" + ("\n".join(
            f"- {x['word']}（★{x['stars']}）{x['desc']}" for x in gh_items) or "（空）"),
        "【AIHot 精选】" + ("\n".join(
            f"- {x['title']}｜{x.get('summary', '')[:40]}" for x in ah_items) or f"（未生效：{ah_meta.get('status')}）"),
    ]
    try:
        picks = await _llm_pick("\n\n".join(menu_parts), persona, audience_note)
    except Exception as e:  # noqa: BLE001 二手通道失败不阻塞一手
        picks = []
        meta["external_error"] = f"{type(e).__name__}: {str(e)[:80]}"
    floor = _COVER_RANK.get(str(settings.coverage_min or "job_wide"), 1)
    for x in picks:
        cov = str(x.get("coverage") or "")
        if _COVER_RANK.get(cov, 0) < max(floor, 1):
            continue  # 池子闸：窄主语二手卡直接拦
        pools.append({"source": "external", "pool": _pool_dict(
            domain=str(x.get("domain_evidence") or "")[:80],
            heat=f"外站热榜｜{str(x.get('heat_evidence') or '')[:60]}",
            level="中", role=str(x.get("role") or "")[:30], coverage=cov,
            secondary=True, external={"src": "外站五路", "title": str(x.get("title") or "")[:60]})})
        meta["external"] += 1
    if not pools:
        await _hunt_safe(ctx, tenant_id, meta)
        ctx.set_progress(100, f"今日池子空，诚实空过（实测候选 +{meta.get('hunted', 0)}）")
        return {"cards": 0, "jail": 0, "pools": 0, "meta": meta, "radar_notes": radar_notes}

    # ── 车间二：主线锻造 + 主线闸 ──
    ctx.set_progress(48, f"车间二：主线锻造（{len(pools)} 个池子）")
    forged = await _forge_with_retry(pools, persona, audience_note, meta)
    video_norms = [_norm(t) for t in _published_titles() if t]
    seen_spine_norms: list[str] = []
    survivors: list[dict] = []
    spine_fails: list[str] = []
    for i, p in enumerate(pools, 1):
        f = forged.get(i)
        if not f:
            continue
        gates: list = []
        if _spine_gate(str(f.get("spine") or ""), str(f.get("role") or ""),
                       p["source"], gates, seen_spine_norms, video_norms):
            survivors.append({"i": i, "source": p["source"], "pool": p["pool"],
                              "spine": str(f.get("spine") or "").strip(),
                              "forged": f, "gates": gates})
        else:
            spine_fails.append(next((g["detail"] for g in gates if not g["pass"]), "?"))
    meta["spine_gate_fails"] = spine_fails[:10]
    print(f"[cards] 车间二：{len(pools)} 池 → {len(survivors)} 过主线闸")

    if not survivors:
        await _hunt_safe(ctx, tenant_id, meta)
        ctx.set_progress(100, f"今日无卡（全部未过主线闸），诚实空过（实测候选 +{meta.get('hunted', 0)}）")
        return {"cards": 0, "jail": 0, "pools": len(pools), "meta": meta,
                "radar_notes": radar_notes}

    # ── 车间三：钩子铸造 + 三秒闸（失败带死因修复重铸一次，只在失败时多花一次调用）──
    ctx.set_progress(70, f"车间三：钩子铸造（{len(survivors)} 条主线，few-shot=拆解库高分句式）")
    cast = await _llm_cast(survivors, persona, audience_note)
    hooked: list[dict] = []
    hook_fails: list[str] = []
    retry_items: list[dict] = []
    for it in survivors:
        h = cast.get(it["i"])
        if not h:
            hook_fails.append(f"#{it['i']} 钩子空回卷")
            continue
        pre = len(it["gates"])
        if _hook_gate(str(h.get("hook") or ""), it["gates"], it.get("spine") or ""):
            it["hook"] = str(h["hook"]).strip()
            hooked.append(it)
        else:
            fail = next((g["detail"] for g in it["gates"][pre:] if not g["pass"]), "?")
            hook_fails.append(f"#{it['i']} {fail}")
            retry_items.append({**it, "last_fail": fail})
    if retry_items:
        print(f"[cards] 车间三修复循环：{len(retry_items)} 条带死因重铸")
        cast2 = await _llm_cast(retry_items, persona, audience_note)
        for it in retry_items:
            h = cast2.get(it["i"])
            if not h:
                continue
            if _hook_gate(str(h.get("hook") or ""), it["gates"], it.get("spine") or ""):
                it["hook"] = str(h["hook"]).strip()
                hooked.append(it)
            else:
                hook_fails.append(f"#{it['i']} 重铸仍拒")
    meta["hook_gate_fails"] = hook_fails[:10]
    print(f"[cards] 车间三：{len(survivors)} 主线 → {len(hooked)} 过三秒闸")
    if not hooked:
        await _hunt_safe(ctx, tenant_id, meta)
        ctx.set_progress(100, f"今日无卡（全部未过三秒闸），诚实空过（实测候选 +{meta.get('hunted', 0)}）")
        return {"cards": 0, "jail": 0, "pools": len(pools), "meta": meta,
                "radar_notes": radar_notes}

    # ── 总法官 + 落卡 ──
    ctx.set_progress(88, f"总法官终审（{len(hooked)} 张候选卡）")
    judged = await _llm_judge(hooked)
    line = max(1, int(settings.judge_pass_line or 7))
    passed: list[tuple[int, dict]] = []
    killed: list[dict] = []
    for it in hooked:
        j = judged.get(it["i"])
        dims = {k: max(0, min(10, int(j.get(k) or 0)))
                for k in ("hook_pull", "spine_single", "traffic_potential")} if j else {}
        ok = bool(j) and all(v >= line for v in dims.values()) and j.get("verdict") != "kill"
        judge = {"hook_pull": dims.get("hook_pull", 0), "spine_single": dims.get("spine_single", 0),
                 "traffic_potential": dims.get("traffic_potential", 0),
                 "verdict": "pass" if ok else "kill",
                 "reason": str((j or {}).get("reason") or "法官无回卷（按废处理）")[:80]}
        (passed if ok else killed).append((it["i"], {**it, "judge": judge}))

    ctx.set_progress(96, f"落卡（法官 pass {len(passed)} / kill {len(killed)}）")
    limit = max(1, int(settings.daily_card_limit or 20))
    passed.sort(key=lambda x: -(x[1]["judge"]["hook_pull"] + x[1]["judge"]["spine_single"]
                                + x[1]["judge"]["traffic_potential"]))
    jail_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(
        days=max(1, int(settings.jail_window_days or 7)))
    _local_mid = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = (_local_mid - _local_mid.utcoffset()).replace(tzinfo=None)  # naive UTC 本地日零点
    draft_ids: list[int] = []
    n_jail = 0
    n_superseded = 0
    from ..engine import channel_id_of

    with Session(engine) as s:
        persona_ch = channel_id_of(s, "persona")
        # jail 过期清扫（窗口外物理删——弃卡是生成内容不是用户数据）
        for c in s.exec(select(Idea).where(  # type: ignore[attr-defined]
                Idea.status == "jail",  # type: ignore[attr-defined]
                Idea.created_at < jail_cutoff)).all():  # type: ignore[attr-defined]
            s.delete(c)
        # 批次替代：当日更早批次且无下游的 draft 让位（approved/有 script_id 的不动）
        old_drafts = s.exec(select(Idea).where(  # type: ignore[attr-defined]
            Idea.status == "draft",  # type: ignore[attr-defined]
            Idea.created_at < day_start)).all()  # type: ignore[attr-defined]
        old_ids = [c.id for c in old_drafts if not c.content_id]
        for rank, (_, it) in enumerate(passed):
            judge = dict(it["judge"])
            if rank >= limit:
                judge["verdict"] = "overflow"
                judge["reason"] = (judge["reason"] + "｜超出每日上限").strip("｜")
            card = _merge_card(it["i"], {"source": it["source"], "pool": it["pool"]},
                               it["forged"], it["hook"], it["gates"], persona_ch)
            card.judge = judge
            card.status = "jail" if rank >= limit else "draft"
            s.add(card)
            s.flush()
            if card.status == "draft":
                draft_ids.append(card.id)
            else:
                n_jail += 1
        for i, (_, it) in enumerate(killed):
            card = _merge_card(it["i"], {"source": it["source"], "pool": it["pool"]},
                               it["forged"], it["hook"], it["gates"], persona_ch)
            card.judge = it["judge"]
            card.status = "jail"
            s.add(card)
            s.flush()
            n_jail += 1
        for oid in old_ids:
            c = s.get(Idea, oid)
            if c is not None:
                c.status = "discarded"
                c.superseded_by = draft_ids[0] if draft_ids else None
                s.add(c)
                n_superseded += 1
        s.commit()
    await _hunt_safe(ctx, tenant_id, meta)
    ctx.set_progress(100, f"完成：{len(draft_ids)} 张靶子卡进箱（jail {n_jail}，替代 {n_superseded} 张旧卡，实测候选 +{meta.get('hunted', 0)}）")
    return {"cards": len(draft_ids), "jail": n_jail, "superseded": n_superseded,
            "pools": len(pools), "meta": meta, "radar_notes": radar_notes}


async def compose_card(*, card_id: int, hook: str = "", spine: str = "",
                       angle: str = "", user=None) -> dict:
    """备稿：靶子卡（draft/approved）→ 脚本任务。钩子/主线可被用户改写后传入。"""
    from ..engine import new_script_content
    from ..scripts import SIX_ANGLES

    angle = (angle or "").strip()
    if angle not in SIX_ANGLES:
        raise ValueError(f"未知打法：{angle or '（未选主打打法）'}")
    with Session(engine) as s:
        card = s.get(Idea, card_id)
        if card is None:
            raise KeyError("card not found")
        if card.status not in ("draft", "approved"):
            raise RuntimeError(f"状态 {card.status} 不可备稿（仅 draft/approved）")
        dup = s.exec(select(Content).where(  # type: ignore[attr-defined]
            Content.idea_id == card_id,  # type: ignore[attr-defined]
            Content.status == "drafting")).first()  # type: ignore[attr-defined]
        if dup is not None:
            raise RuntimeError(f"该卡正在生成脚本（#{dup.id}），等完成或删除后再试")
        hook_v = (hook or card.hook or "").strip()
        spine_v = (spine or card.spine or "").strip()
        if not hook_v or not spine_v:
            raise RuntimeError("卡上没有钩子/主线，不可备稿")
        spice_v = card.spice if card.spice in ("low", "mid", "high") else "mid"
        script = new_script_content(
            s, tenant_id=card.tenant_id or 1, channel_key="persona", idea_id=card.id,
            title_hint=(str(hook or card.hook) or "")[:60],
            payload={"script_state": "generating", "hook": hook_v[:60], "spine": spine_v[:40],
                     "spice": spice_v, "angle": angle, "account_type": "persona"})
        card.status = "approved"
        card.content_id = script.id
        s.add(card)
        s.commit()
        script_id = script.id
    actor = (user.tenant_id, user.id, user.role) if user is not None else None
    from ...jobs.runner import runner
    job = await runner.submit("script_generate",
                              {"script_id": script_id, "card_id": card_id,
                               "hook": hook_v[:60], "spine": spine_v[:40],
                               "angle": angle, "spice": spice_v,
                               "length": "short", "account_type": "persona",
                               "use_style": True, "angles": [angle]},
                              actor=actor)
    return {"script_id": script_id, "job_id": job.id, "card_id": card_id}
