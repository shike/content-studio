"""知识库文件名重写（2026-11-18 用户裁决）。

全库 kb_doc 的 title 一律**由 LLM 从正文重新抽取**，不沿用原文件名（含 course-lesson-design、
go-live 这类工程名与超长标题）。原题留 quality.title_old（首次）做追溯；
quality.llm_title=1 断点续跑；连败 10 熔断退出。

用法（服务器）：
  nohup backend/.venv/bin/python backend/scripts/kb_title_rewrite.py \
    >> data/kb_title.log 2>&1 < /dev/null &
  抽样：KB_TITLE_LIMIT=10 backend/.venv/bin/python backend/scripts/kb_title_rewrite.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, install_tenant_scope  # noqa: E402
from app.llm import gateway  # noqa: E402
from app.models import Material  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402

install_tenant_scope()

SLEEP_S = 0.8
BREAK_LOSE = 10
LIMIT = int(os.environ.get("KB_TITLE_LIMIT") or 0)  # 0=全量

PROMPT = """给一篇知识库文档起一个**新的标题**（当文件名用）。禁止沿用原文件名。

文档信息：
- 原文件名（只作定位参考，**禁止照抄，禁止保留扩展名/编号/日期串**）：{old}
- 目录：{folder}
- 体裁：{tags}
- 用处摘要：{why_good}
- 正文开头：{excerpt}

要求：
1. 12~24 个字，一句话说清"这篇讲什么、给谁用"；英文专名要写完整，不许截断成半截单词；
2. 必须具体：点名行业/场景/对象/方法名（从正文里找），禁「AI资料」「干货合集」「学习笔记」这类空话；
3. 不带书名号、引号、省略号、文件扩展名；不通顺的英文工程名不要出现；
4. 政策/资讯/报告类：如实写主体与主题（例：「北京市十五五教育改革规划要点」），
   不要伪装成方法论；软文/广告类也如实写清它是什么；
5. 标题里不要出现"本文""这篇文章"。

只输出 JSON：{{"title": "新标题"}}"""


def _todo(s: Session) -> list[Material]:
    rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
        Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
    out = []
    for m in rows:
        q = m.quality or {}
        if q.get("llm_title"):
            continue
        if not ((m.body or "").strip() or q.get("why_good")):
            continue
        out.append(m)
    return out


def _clean(raw: str, old: str) -> str:
    t = str(raw or "").strip()
    t = re.sub(r"^#+\s*", "", t)
    t = t.strip("《》「」『』\"'“”‘’ .。:：")
    t = re.sub(r"\.(pdf|docx?|md|txt|pptx?|xlsx?)$", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 34:
        t = t[:34]
    t = re.sub(r"[A-Za-z0-9_\-]+$", "", t).strip(" :：-·，,")  # 截断处不留半截英文 token
    if t and t == old.strip():
        return ""  # 原样返回=没重写，按失败算
    return t


async def main() -> int:
    load_overrides()
    with Session(engine) as s:
        todo = _todo(s)
    if LIMIT:
        todo = todo[:LIMIT]
    total = len(todo)
    print(f"[kb-title] 待重写 {total} 篇（断点 llm_title；限 {LIMIT or '不限'}）", flush=True)
    ok = fail = skip = lose = 0
    for i, m in enumerate(todo, 1):
        q = dict(m.quality or {})
        old = str(m.title or "")
        prompt = PROMPT.format(
            old=old[:80] or "（无）",
            folder=str(q.get("folder") or "")[:40],
            tags="、".join(m.tags or []) or "（无）",
            why_good=str(q.get("why_good") or "")[:120],
            excerpt=(m.body or "").strip()[:1500].replace("\n", " "))
        try:
            r = await gateway.complete_json([{"role": "user", "content": prompt}],
                                            max_tokens=1024, thinking="disabled")
            lose = 0
        except Exception as e:  # noqa: BLE001
            lose += 1
            fail += 1
            print(f"[kb-title] ({i}/{total}) 失败 {type(e).__name__}: {str(e)[:60]}"
                  f"{'｜连败熔断' if lose >= BREAK_LOSE else ''}", flush=True)
            if lose >= BREAK_LOSE:
                print(f"[kb-title] 连败 {BREAK_LOSE} 熔断退出（已完成 {ok}）——稍后重启续跑", flush=True)
                return 1
            await asyncio.sleep(min(60, 5 * lose))
            continue
        new = _clean(str((r or {}).get("title") or ""), old)
        with Session(engine) as s:
            row = s.get(Material, m.id)
            if row is None:
                continue
            q2 = dict(row.quality or {})
            q2["llm_title"] = 1
            if old and not q2.get("title_old"):
                q2["title_old"] = old[:120]
            row.quality = q2
            if new:
                row.title = new
                ok += 1
            else:
                q2["title_skip"] = 1
                row.quality = q2
                skip += 1
            s.add(row)
            s.commit()
        if i % 50 == 0 or i == total:
            print(f"[kb-title] 进度 {i}/{total}：改 {ok} / 跳过 {skip} / 失败 {fail}", flush=True)
        await asyncio.sleep(SLEEP_S)
    print(f"[kb-title] 完成：改 {ok} / 跳过 {skip} / 失败 {fail}（共 {total}）", flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
