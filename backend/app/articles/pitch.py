"""选题会（2026-11-20）：从源文抽 N 个可写判断，每个带对手、代价、证据。

两步改编的第一步（用户裁决 A 方案）：人选定判断后，写稿只拿「判断 + 证据」，
不再喂全书——防读书笔记，也防判断永远是源文的那一个。
"""
from __future__ import annotations

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
