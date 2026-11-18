"""体裁打标全量驱动（2026-11-18 用户定版）。

词表=7 个生产体裁：方法论/客户案例/观点判断/操作教程/工具资源/学习路线/职业发展。
主 1 + 次 1（都在词表内）；政策/行业报告/资讯快讯/软文等资讯或广告货**不打标**（空串，
按用户裁决「不打标直接压低」——它们留在全部列表里按分数排序，只是不进体裁 chips）。
落 materials.tags（JSONB 已有列，主在前）；quality.genre_why 留一句理由；
quality.genre_done=1 断点续跑标记。kb_genre_sample.py 的 30 篇样例定过口味。
用法（服务器）：nohup backend/.venv/bin/python backend/scripts/kb_genre_tag.py \
  >> data/kb_genre_tag.log 2>&1 < /dev/null &
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, install_tenant_scope  # noqa: E402
from app.llm import gateway  # noqa: E402
from app.models import Material  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402

install_tenant_scope()

VOCAB = "方法论 客户案例 观点判断 操作教程 工具资源 学习路线 职业发展"
SLEEP_S = 1.0
BREAK_LOSE = 10  # 连败熔断

PROMPT = """给一篇知识库文档打**体裁标签**（这是什么类型的货）。

体裁词表（只能从中选，共 7 个）：{vocab}
不在词表内的体裁——政策/行业报告/白皮书/资讯快讯/新闻通稿/软文广告等——一律不打标
（这些是资讯或广告货，不是生产资料）。杂项（测试夹具/工程备忘/课程仓库说明）也不打标。

文档：
标题：{title}
用处：{why_good}
正文开头：{excerpt}

输出 JSON：{{"primary": "主体裁（词表内 7 选 1；整篇是资讯/报告/政策/软文/杂项就给空串）",
"secondary": "次体裁（词表内），没有就空串", "why": "一句为什么"}}"""


def _todo(s: Session) -> list[Material]:
    rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
        Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
    return [m for m in rows
            if not ((m.quality or {}).get("genre_done"))]


async def main() -> int:
    load_overrides()
    with Session(engine) as s:
        todo = _todo(s)
    total = len(todo)
    print(f"[genre-tag] 待打标 {total} 篇（词表：{VOCAB}；主1+次1；资讯/软文类不打标）", flush=True)
    ok = lose = tagged = fail = 0
    for i, m in enumerate(todo, 1):
        q = m.quality or {}
        prompt = PROMPT.format(
            vocab=VOCAB,
            title=(m.title or "")[:80],
            why_good=str(q.get("why_good") or "")[:120],
            excerpt=(m.body or "")[:1200].replace("\n", " "))
        try:
            r = await gateway.complete_json([{"role": "user", "content": prompt}],
                                            max_tokens=1024, thinking="disabled")
            lose = 0
        except Exception as e:  # noqa: BLE001
            lose += 1
            print(f"[genre-tag] ({i}/{total}) 失败 {type(e).__name__}: {str(e)[:60]}"
                  f"{'｜连败熔断' if lose >= BREAK_LOSE else ''}", flush=True)
            if lose >= BREAK_LOSE:
                print(f"[genre-tag] 连败 {BREAK_LOSE} 熔断退出（已完成 {ok}）——稍后重启续跑", flush=True)
                return 1
            fail += 1
            await asyncio.sleep(min(60, 5 * lose))
            continue
        vocab7 = set(VOCAB.split())
        primary = str((r or {}).get("primary") or "").strip()
        secondary = str((r or {}).get("secondary") or "").strip()
        why = str((r or {}).get("why") or "").strip()[:80]
        tags = [t for t in (primary, secondary) if t in vocab7]
        with Session(engine) as s:
            row = s.get(Material, m.id)
            if row is None:
                continue
            q2 = dict(row.quality or {})
            q2["genre_done"] = 1
            q2["genre_why"] = why
            row.quality = q2
            row.tags = tags  # 主在前；资讯/软文类=空列表（不打标即压低）
            s.add(row)
            s.commit()
        ok += 1
        if tags:
            tagged += 1
        if i % 50 == 0 or i == total:
            print(f"[genre-tag] 进度 {i}/{total}：成 {ok} / 打上 {tagged} / 败 {fail}", flush=True)
        await asyncio.sleep(SLEEP_S)
    # 词频总账
    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
    dist: dict[str, int] = {}
    for b in rows:
        for t in (b.tags or []):
            dist[t] = dist.get(t, 0) + 1
    print("\n[genre-tag] ── 体裁分布（全库）──", flush=True)
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {k} ×{v}", flush=True)
    print(f"\n{{\"done\": {ok}, \"tagged\": {tagged}, \"untagged\": {total - tagged}}}", flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
