#!/usr/bin/env python3
"""知识库自由打标（2026-11-18，用户定方向：AI 自由打标 → 词表从内容长出来 → 用户修剪定版）。

第一遍不做预设词表：逐篇读已入库正文（不碰 ima，纯 LLM），让模型给 2~3 个
「这篇到底是关于什么的」具体主题标签，存 quality.free_tags（汇总去重出真实词表后
交用户修剪；定版词表由第二遍规范化写 materials.tags，挖掘器按 kb_tag_vocab 给新文档打标）。

- 断点续跑：quality.free_tags 已存在的行跳过；
- 标题兜底：正文缺失的行只用标题打标（弱标，规范化时会被词表收敛）。

用法（服务器仓库根）：
  PYTHONPATH=backend nohup backend/.venv/bin/python backend/scripts/kb_free_tag.py [--limit N] >> data/kb_free_tag.log 2>&1 &
跑完打印词频表（= 长出来的词表草案）。
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import engine  # noqa: E402
from app.llm import gateway  # noqa: E402
from app.models import Material, PipelineLedger  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

# 与服务进程同口径：DB 里的运行时配置（LLM 密钥等）覆盖 .env 默认值
load_overrides()

_TAG_WAIT = 180      # 单篇打标兜底（gateway 自带 600s httpx 超时，此处防连接级停滞）
_BODY_CAP = 3000     # 打标只读正文开头（主题识别足够，全量读太贵）
_SLEEP = 0.4

_PROMPT = """你在给内容工作室的知识库文档打主题标签。读下面的文档（标题+正文开头），回答「这篇到底是关于什么的」：
- 给 2~3 个标签，每个 2~8 个字
- 标签要具体到内容主题（如：Agent 开发、RAG 检索、离职转型、定价谈判、十五五规划），不要宽泛大类（如：技术、管理、AI）
- 用中文名词短语，不用动词句
输出 JSON：{{"tags": ["标签1", "标签2"]}}

【标题】{title}
【正文】{body}
"""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def main() -> int:
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc").order_by(Material.id)).all()  # type: ignore[attr-defined]
    todo = [m for m in rows
            if not ((m.quality or {}).get("free_tags") or [])]
    if limit:
        todo = todo[:limit]
    print(f"[free-tag] 全库 {len(rows)} 篇，待打标 {len(todo)} 篇", flush=True)
    if not todo:
        print("[free-tag] 没有待打标的行", flush=True)
        return 0

    done = fail = 0
    consec_bad = 0
    freq: collections.Counter[str] = collections.Counter()
    for i, m in enumerate(todo, 1):
        note = None
        try:
            note = await asyncio.wait_for(
                gateway.complete_json(
                    [{"role": "user", "content": _PROMPT.format(
                        title=m.title or f"文档 #{m.id}",
                        body=(m.body or "").strip()[:_BODY_CAP] or "（正文未入库，仅凭标题判断）")}],
                    max_tokens=1024, thinking="disabled"),
                timeout=_TAG_WAIT)
        except Exception as e:  # noqa: BLE001
            print(f"[free-tag] {i}/{len(todo)} 打标失败（{m.title[:24]}）: {type(e).__name__}: {str(e)[:80]}", flush=True)
            fail += 1
        tags = []
        if note:
            raw = (note or {}).get("tags") or []
            tags = [str(t).strip()[:16] for t in raw if str(t).strip()][:3]
        if tags:
            consec_bad = 0
            with Session(engine) as s:
                row = s.get(Material, m.id)
                if row is not None:
                    q = dict(row.quality or {})
                    q["free_tags"] = tags
                    row.quality = q
                    s.add(row)
                    s.commit()
            done += 1
            freq.update(tags)
        else:
            consec_bad += 1
        if consec_bad and consec_bad % 10 == 0:
            print(f"[free-tag] 连续 {consec_bad} 篇无产出，退避 120s", flush=True)
            await asyncio.sleep(120)
        if consec_bad >= 30:
            print("[free-tag] 连续 30 篇失败，停止——修复后重跑断点续标", flush=True)
            break
        if i % 100 == 0:
            with Session(engine) as s:
                s.add(PipelineLedger(day=_today(), pipeline="kb_free_tag", ok=True,
                                  produced=f"打标进度 {i}/{len(todo)}（成 {done} / 败 {fail}）"))
                s.commit()
            print(f"[free-tag] 进度 {i}/{len(todo)}：成 {done} / 败 {fail}", flush=True)
        await asyncio.sleep(_SLEEP)

    print(f"[free-tag] 完成：成 {done} / 败 {fail}", flush=True)
    print("[free-tag] ── 词表草案（词频降序，前 60）──", flush=True)
    for tag, n in freq.most_common(60):
        print(f"  {tag} ×{n}", flush=True)
    with Session(engine) as s:
        s.add(PipelineLedger(day=_today(), pipeline="kb_free_tag", ok=True,
                          produced=f"自由打标完成：{done} 篇，词表草案 {len(freq)} 个标签"))
        s.commit()
    print(json.dumps({"done": done, "fail": fail, "vocab": len(freq)},
                     ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
