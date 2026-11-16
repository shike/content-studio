"""靶子卡闸门组：主线闸/三秒闸/合卡（程序判定层，LLM 链在 forge.py）。

2026-11-16 C 阶段从 service.py 拆出；归一助手与常量从 norms.py 导入（单向）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from sqlmodel import Session, select

from ...db import engine
from ...models import Idea
from ...settings import settings
from .norms import (_ADVICE_OPENER, _CLAUSE_PUNCT, _COVER_RANK, _coverage_of,
                    _max_chars, _norm, _PREDICT_KW, _pron_len)

def _gate(gates: list, gate: str, ok: bool, detail: str = "") -> bool:
    gates.append({"gate": gate, "pass": bool(ok), "detail": str(detail)[:160]})
    return ok


def _recent_card_spines(days: int = 14) -> list[str]:
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    with Session(engine) as s:
        rows = s.exec(select(Idea).where(  # type: ignore[attr-defined]
            Idea.created_at >= cutoff)).all()  # type: ignore[attr-defined]
    return [c.spine for c in rows if c.spine]


def _local_day() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _spine_gate(spine: str, role: str, source: str, gates: list,
                seen_spine_norms: list[str], video_norms: list[str]) -> bool:
    """主线闸（程序）：字数线/劝告开头/复合句/预言腔/覆盖面/撞车/批内（罪名与立场的品味归总法官）。"""
    spine = spine.strip()
    n = _pron_len(spine)
    if not _gate(gates, "主线字数线", 0 < n <= _max_chars(), f"{spine[:24]}（{n} 字，线 {_max_chars()}）"):
        return False
    if not _gate(gates, "劝告开头", not _ADVICE_OPENER.match(spine), spine[:24]):
        return False
    if not _gate(gates, "复合句闸", not _CLAUSE_PUNCT.search(spine), spine[:24]):
        return False
    if not _gate(gates, "预言腔负向", not any(k in spine for k in _PREDICT_KW), spine[:24]):
        return False
    cov = _coverage_of(role)
    floor = _COVER_RANK.get(str(settings.coverage_min or "job_wide"), 1)
    if not _gate(gates, "覆盖面闸", _COVER_RANK.get(cov, 0) >= max(floor, 1),
                 f"role={role[:16]}→{cov}（线 {settings.coverage_min}）"):
        return False
    sn = _norm(spine)
    if not _gate(gates, "14 天撞车", not any(
            SequenceMatcher(None, sn, _norm(t)).ratio() >= 0.55 or
            (len(tn := _norm(t)) >= 8 and (tn in sn or sn in tn))
            for t in _recent_card_spines(14)), spine[:24]):
        return False
    if not _gate(gates, "冷饭闸", not any(
            (SequenceMatcher(None, sn, tn).ratio() >= 0.55) or
            (len(tn) >= 8 and (tn in sn or sn in tn)) for tn in video_norms), spine[:24]):
        return False
    if not _gate(gates, "批内去重", not any(
            SequenceMatcher(None, sn, b).ratio() >= 0.55 for b in seen_spine_norms), spine[:24]):
        return False
    seen_spine_norms.append(sn)
    return True


def _hook_gate(hook: str, gates: list, spine: str = "") -> bool:
    """三秒闸（程序）：字数线 + 开头词根 + 钩子≠主线（照抄=主线出口，违反半句话原则）。"""
    hook = hook.strip()
    n = _pron_len(hook)
    if not _gate(gates, "三秒字数线", 0 < n <= _max_chars(), f"{hook[:24]}（{n} 字，线 {_max_chars()}）"):
        return False
    banned = [w.strip() for w in str(settings.hook_ban_prefixes or "").split(",") if w.strip()]
    hit = next((w for w in banned if hook.startswith(w)), "")
    if not _gate(gates, "开头词根闸", not hit, f"{hook[:24]}" + (f"（命中「{hit}」）" if hit else "")):
        return False
    if "某" in hook:
        return _gate(gates, "点名纪律", False, f"{hook[:24]}（匿名「某X」= 没点名）")
    hn, sn = _norm(hook), _norm(spine or "")
    same = bool(sn) and (hn == sn or (len(sn) >= 8 and (sn in hn or hn in sn)))
    return _gate(gates, "钩子≠主线", not same, f"{hook[:24]}" + ("（照抄主线）" if same else ""))


def _merge_card(i: int, pool_entry: dict, forged: dict, hook: str,
                gates: list, channel_id: int) -> Idea:
    pool = dict(pool_entry["pool"])
    role = str(forged.get("role") or pool.get("role") or "").strip()
    spice = forged.get("spice") if forged.get("spice") in ("low", "mid", "high") else "mid"
    pool["role"] = role
    pool["coverage"] = _coverage_of(role)
    return Idea(channel_id=channel_id, tenant_id=1,
                    batch_date=_local_day(), source=pool_entry["source"],
                     payload={"pool": pool},
                     spine=str(forged.get("spine") or "").strip(),
                     hook=hook.strip(), spice=spice,
                     takeaways=list(forged.get("takeaways") or [])[:3],
                     gates=gates, status="draft")
