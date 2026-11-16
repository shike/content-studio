"""靶子卡 LLM 锻造链：pick/forge(修复循环)/cast/judge（程序闸在 gates.py）。

2026-11-16 C 阶段从 service.py 拆出；归一助手与常量从 norms.py 导入（单向）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from ...db import engine
from ...llm import gateway
from ...models import Material, SelfVideo
from ...prompts import render
from ...settings import settings
from .norms import (_ADVICE_OPENER, _CLAUSE_PUNCT, _PREDICT_KW, _max_chars,
                    _pron_len)

async def _llm_pick(menu_text: str, persona: str, audience_note: str) -> list[dict]:
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "pool_pick", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            POOL_MAX=str(int(settings.pool_pick_max or 8)),
            MENU=menu_text[:6000])}],
        max_tokens=4000, thinking="disabled")
    return [x for x in (data.get("picks") or []) if isinstance(x, dict) and x.get("title")]


def _fewshot_lines(limit: int = 14) -> str:
    """few-shot 句式标尺：素材库近 30 天高分拆解的钩子/高光 + 已发视频标题（正样本）。"""
    from datetime import timedelta as _td

    from ...materials.service import analyses_map

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - _td(days=30)
    lines: list[str] = []
    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "douyin_video",  # type: ignore[attr-defined]
            Material.status == "ready",  # type: ignore[attr-defined]
            Material.created_at >= cutoff)).all()  # type: ignore[attr-defined]
        amap = analyses_map(s, [b.id for b in rows], kinds=("breakdown",))
        rows = sorted(
            (b for b in rows
             if (amap.get(b.id) or {}).get("breakdown") or b.title),
            key=lambda b: -float((b.quality or {}).get("heat") or 0))
        for b in rows:
            # 夹具（2026-11-16 修）：拆解 JSON 的 hook 是 dict（type/position_text/position_sec）、
            # highlights 是英文键——按结构取：hook.position_text 优先，兼容字符串形态；
            # highlights/高光/金句键收列表字符串。
            an = ((amap.get(b.id) or {}).get("breakdown") or {}).get("payload") or {}
            author = b.author or "?"
            taken = False
            hook_v = an.get("hook")
            hook_text = ""
            if isinstance(hook_v, dict):
                hook_text = str(hook_v.get("position_text") or "").strip()
            elif isinstance(hook_v, str):
                hook_text = hook_v.strip()
            if hook_text:
                lines.append(f"- 「{hook_text[:44]}」（素材库 {author}）")
                taken = True
            if not taken:
                for k, v in an.items():
                    key = str(k).lower()
                    if not any(w in key for w in ("highlight", "高光", "金句", "钩", "开场")):
                        continue
                    vals = v if isinstance(v, list) else [v]
                    for x in vals:
                        if isinstance(x, str) and x.strip():
                            lines.append(f"- 「{x.strip()[:44]}」（素材库 {author}）")
                            taken = True
                            break
                    if taken:
                        break
            if not taken and b.title:
                lines.append(f"- 「{b.title[:44]}」（素材库 {author}）")
            if len(lines) >= limit:
                break
    lines += [f"- 「{(v.title or '')[:44]}」（自己的爆款）" for v in _self_titles(6) if v.title]
    return "\n".join(lines[:limit + 6]) or "（暂无已验证样本）"


def _self_titles(n: int) -> list[SelfVideo]:
    """自己已发布视频按播放排序取前 n（三份同查询合并点，2026-11-16）。"""
    with Session(engine) as s:
        vids = s.exec(select(SelfVideo).where(  # type: ignore[attr-defined]
            SelfVideo.published_at != "")).all()  # type: ignore[attr-defined]
    return sorted(vids, key=lambda v: int((v.stats or {}).get("play") or 0), reverse=True)[:n]


def _spine_shots() -> str:
    """用户自己发过的视频标题（三秒原则的活样本）——句式标尺 few-shot。"""
    ranked = _self_titles(12)
    shots = [f"- 「{(v.title or '').strip()[:30]}」" for v in ranked if v.title]
    return chr(10).join(shots) or "（暂无已发标题）"


def _spine_static_fail(spine: str) -> Optional[str]:
    """主线静态死因（不含需查库的撞车/批内闸）——重锻循环的输入。"""
    spine = spine.strip()
    n = _pron_len(spine)
    if not spine:
        return "空"
    if n > _max_chars():
        return f"{n} 字超线（线 {_max_chars()}），砍「的/了/正在/被」这类虚词"
    if _ADVICE_OPENER.match(spine):
        return "劝告开头"
    if "某" in spine:
        return "匿名（某X）= 没点名（点名纪律）"
    if _CLAUSE_PUNCT.search(spine):
        return "复合句"
    if any(k in spine for k in _PREDICT_KW):
        return "预言腔"
    # 罪名与立场的品味判断归总法官（judge_cards：无罪名无立场的正确废话 ≤3 分）——
    # 程序层不再做词表追杀（实测证明词表永远追不上锋利新表述，误杀率 100%）
    return None


def _norm_takeaways(x) -> list[str]:
    """评论要点留痕清洗（程序只清形不判品味）：≤3 条、每条 ≤40 字。"""
    if not isinstance(x, list):
        return []
    out = [str(t or "").strip()[:40] for t in x]
    return [t for t in out if t][:3]


async def _llm_forge(pools: list[dict], persona: str, audience_note: str,
                     spines_hint: Optional[dict[int, dict]] = None) -> dict[int, dict]:
    pool_inputs = []
    for i, p in enumerate(pools, 1):
        dy = p["pool"].get("douyin_evidence") or {}
        row = {"i": i, "source": p["source"],
               "由头": (p["pool"].get("heat_evidence") or "")[:60],
               "域内证据": (p["pool"].get("domain_evidence") or "")[:60]}
        if dy.get("digest"):
            row["拆解要点"] = str(dy["digest"])[:80]
        top = [c for c in (dy.get("top") or []) if isinstance(c, dict)
               and str(c.get("t") or "").strip()]
        if top:  # 高赞评论原文=观点的矿脉（R1.4 进料）：先抽评论要点，主线从要点里长出来
            row["高赞评论"] = [f"「{str(c.get('t') or '').strip()[:60]}」（赞{max(0, int(c.get('d') or 0))}）"
                               for c in top]
        if dy.get("kind") == "study_meta":
            pct = int(round(float(dy.get("study_share") or 0) * 100))
            row["备注"] = (f"求学现象卡：此池评论区约 {pct}% 是求资料/学习打卡。"
                           "唯一可用角度=对「大家都在求学」这个现象本身立立场"
                           "（示范：「开口就问考证的，我不收徒」——筛选立场）；"
                           "禁止把求资料内容当观点素材；锻不出立场判词就跳过该池")
        h = (spines_hint or {}).get(i)
        if h:  # 修复循环：上一版原句 + 死因，逼模型砍到位
            row["上一版"] = h.get("spine") or ""
            row["上次被拒原因"] = h.get("last_fail") or ""
        pool_inputs.append(row)
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "spine_forge", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            MAX_CHARS=str(_max_chars()), SPINE_SHOTS=_spine_shots(),
            POOLS=json.dumps(pool_inputs, ensure_ascii=False))}],
        max_tokens=4000, thinking="disabled")  # 4000：takeaways 并入输出预留头位（台账规则：初值待实测×2 校准）
    out: dict[int, dict] = {}
    if isinstance(data, dict):
        for x in (data.get("cards") or []):
            if (isinstance(x, dict) and str(x.get("i") or "").isdigit()
                    and str(x.get("spine") or "").strip()):
                out[int(x["i"])] = {**x, "takeaways": _norm_takeaways(x.get("takeaways"))}
    return out


async def _forge_with_retry(pools: list[dict], persona: str, audience_note: str,
                            meta: Optional[dict] = None) -> dict[int, dict]:
    """锻主线 + 修复循环 ×1：静态死因（超线/劝告/复合句/预言腔/无判词词根）的
    带原句与死因重锻一次——法律不放宽，逼模型砍虚词补判词。"""
    forged = await _llm_forge(pools, persona, audience_note)
    redo_hint: dict[int, dict] = {}
    for i, p in enumerate(pools, 1):
        f = forged.get(i)
        if not f:
            continue
        fail = _spine_static_fail(str(f.get("spine") or ""))
        if fail:
            redo_hint[i] = {"spine": str(f.get("spine") or "").strip(), "last_fail": fail}
    if redo_hint:
        print(f"[cards] 车间二修复循环：{len(redo_hint)} 条重锻")
        forged2 = await _llm_forge(pools, persona, audience_note, spines_hint=redo_hint)
        for k, v in forged2.items():
            if k in redo_hint:
                forged[k] = v
        if meta is not None:
            meta["forge_retry"] = len(redo_hint)
    return forged


async def _llm_cast(items: list[dict], persona: str, audience_note: str) -> dict[int, dict]:
    spine_inputs = []
    for it in items:
        row = {"i": it["i"], "spine": it.get("spine") or "", "role": it.get("role") or "",
               "由头": (it["pool"].get("heat_evidence") or "")[:50]}
        if it.get("last_fail"):  # 修复循环：带上一次死因，模型针对性改写
            row["上次被拒原因"] = it["last_fail"][:80]
        spine_inputs.append(row)
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "hook_cast", PERSONA=persona, AUDIENCE_NOTE=audience_note,
            MAX_CHARS=str(_max_chars()), FEWSHOT=_fewshot_lines(),
            SPINES=json.dumps(spine_inputs, ensure_ascii=False))}],
        max_tokens=2000, thinking="disabled")
    return {int(x["i"]): x for x in (data.get("cards") or [])
            if isinstance(x, dict) and str(x.get("i") or "").isdigit()
            and str(x.get("hook") or "").strip()} if isinstance(data, dict) else {}


async def _llm_judge(items: list[dict]) -> dict[int, dict]:
    card_inputs = [{"i": it["i"], "source": it["source"], "spine": it.get("spine") or "",
                    "hook": it.get("hook") or "", "coverage": (it["pool"] or {}).get("coverage") or "",
                    "热度证据": (it["pool"].get("heat_evidence") or "")[:60],
                    "一手证据": bool(it["pool"].get("douyin_evidence")),
                    "peer_covered": bool(it["pool"].get("peer_covered"))}
                   for it in items]
    data = await gateway.complete_json(
        [{"role": "system", "content": render(
            "judge_cards", JUDGE_LINE=str(int(settings.judge_pass_line or 7)),
            CARDS=json.dumps(card_inputs, ensure_ascii=False))}],
        max_tokens=4000, thinking="disabled")
    return {int(x["i"]): x for x in (data.get("cards") or [])
            if isinstance(x, dict) and str(x.get("i") or "").isdigit()} if isinstance(data, dict) else {}
