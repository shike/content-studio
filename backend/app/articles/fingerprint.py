"""跨篇指纹（2026-11-20）：最近几篇的标题/开头/结尾/小标题模板，写稿前提醒避开，
成文后比对重复。思路来自 jiji262/wechat-publisher 的 fingerprint，按本产线重写。

只比对「写法」不比对「题材」：同题材复用结构可以，原样复用开场/收束/小标题模板才是问题。
"""
from __future__ import annotations

import re

from sqlmodel import Session, select

from ..db import engine
from ..models import Content

_MAX_RECENT = 3
_OPEN_CHARS = 80
_END_CHARS = 80
_SIM_LINE = 0.45  # 2-gram Jaccard 相似线：只拦近乎复用的开头/结尾


def _prose_paras(md: str) -> list[str]:
    out: list[str] = []
    for p in (md or "").split("\n\n"):
        p = p.strip()
        if not p or p.startswith(("#", "!", "|", ">", "-", "*")):
            continue
        out.append(p)
    return out


def _norm(t: str) -> str:
    return re.sub(r"[\s，。！？；：、“”‘’「」『』（）()\[\]—…·.,;:!?\"'<>《》*#]", "", t or "")


def _grams(t: str, n: int = 2) -> set[str]:
    t = _norm(t)
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def _sim(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def fingerprint(md: str) -> dict:
    """一篇稿的写法指纹：开头段、结尾段、小标题。"""
    paras = _prose_paras(md)
    heads = [h.strip() for h in re.findall(r"(?m)^#{2,3}\s*(.+?)\s*$", md or "")]
    return {
        "opening": (paras[0] if paras else "")[:_OPEN_CHARS],
        "ending": (paras[-1] if paras else "")[-_END_CHARS:],
        "heads": heads[:6],
    }


def recent_fingerprints(limit: int = _MAX_RECENT, exclude_id: int | None = None) -> list[dict]:
    """最近已成稿（待发/已发）的文章指纹，新的在前。"""
    with Session(engine) as s:
        rows = s.exec(select(Content).where(
            Content.kind == "long_article",  # type: ignore[attr-defined]
            Content.status.in_(["ready", "published"]),  # type: ignore[attr-defined]
        ).order_by(Content.id.desc()).limit(limit + 1)).all()  # type: ignore[attr-defined]
    out: list[dict] = []
    for c in rows:
        if exclude_id and c.id == exclude_id:
            continue
        fp = fingerprint(c.body or "")
        if not fp["opening"]:
            continue
        fp["title"] = (c.title or "").strip()
        fp["id"] = c.id
        out.append(fp)
        if len(out) >= limit:
            break
    return out


def fingerprint_brief(recents: list[dict]) -> str:
    """写稿提示词里的「最近用过」清单。"""
    if not recents:
        return "（暂无历史稿，本条不适用）"
    lines: list[str] = []
    for i, fp in enumerate(recents, 1):
        heads = "｜".join(fp["heads"][:3]) or "（无小标题）"
        lines.append(f"{i}. 标题：{fp['title'][:32]}\n"
                     f"   开头：{fp['opening'][:60]}\n"
                     f"   结尾：…{fp['ending'][-40:]}\n"
                     f"   小标题：{heads}")
    return "\n".join(lines)


def fingerprint_violations(md: str, recents: list[dict]) -> list[str]:
    """成文比对：开头/结尾/小标题与最近几篇近乎复用。"""
    cur = fingerprint(md)
    out: list[str] = []
    for fp in recents:
        if cur["opening"] and _sim(cur["opening"][:60], fp["opening"][:60]) >= _SIM_LINE:
            out.append(f"开头与第 {fp['id']} 篇高度相似——换一种进入方式，不要复用同一开场")
            break
    for fp in recents:
        if cur["ending"] and _sim(cur["ending"][-50:], fp["ending"][-50:]) >= _SIM_LINE:
            out.append(f"结尾与第 {fp['id']} 篇高度相似——换一种收束方式")
            break
    for fp in recents:
        dup = [h for h in cur["heads"] if h in fp["heads"]]
        if dup:
            out.append(f"小标题与第 {fp['id']} 篇重复（{'、'.join(dup[:2])}）——换成不同的内容短语")
            break
    return out
