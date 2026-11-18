"""体裁标签样例（2026-11-18 用户定方向：标签轴=「这是什么货」——客户案例/方法论/政策等，
不是主题词）。抽 30 篇分层样例做体裁分类，词表草案+样例效果一起报用户修剪。
词表初稿=用户点名的三个 + ima 目录结构里已经隐含的体裁；允许 LLM 提「其他」——
词表从内容长出来的口径不变，只是轴从主题换成体裁。
用法（服务器）：backend/.venv/bin/python backend/scripts/kb_genre_sample.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, install_tenant_scope  # noqa: E402
from app.llm import gateway  # noqa: E402
from app.models import Material  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402

install_tenant_scope()

# 体裁词表初稿（用户点名：客户案例/方法论/政策；其余从目录结构隐含体裁补齐；空格分隔）
VOCAB = ("客户案例 方法论 政策 行业报告 操作教程 观点判断 职业发展 学习路线 工具资源 数据基准")

PROMPT = """给一篇知识库文档打**体裁标签**（这是什么类型的货），不是主题标签。

体裁词表：{vocab}
若都不贴切，允许自造一个新体裁词（2~4 字，风格与词表一致）。

文档：
标题：{title}
用处：{why_good}
正文开头：{excerpt}

输出 JSON：{{"primary": "主体裁（词表内或自造）", "secondary": "次体裁，没有就空串",
"why": "一句为什么"}}"""


def _pick_sample() -> list[Material]:
    """按目录分层抽样：每目录至多 4 篇，共 ~30。"""
    with Session(engine) as s:
        rows = s.exec(select(Material).where(  # type: ignore[attr-defined]
            Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
    by_folder: dict[str, list[Material]] = {}
    for m in rows:
        f = str((m.quality or {}).get("folder") or "（未知）")
        by_folder.setdefault(f, []).append(m)
    sample: list[Material] = []
    for f, ms in sorted(by_folder.items(), key=lambda kv: -len(kv[1])):
        sample.extend(ms[:4])
    return sample[:30]


async def main() -> int:
    load_overrides()
    sample = _pick_sample()
    print(f"[genre] 样例 {len(sample)} 篇（按目录分层）", flush=True)
    out = []
    for i, m in enumerate(sample, 1):
        q = m.quality or {}
        prompt = PROMPT.format(
            vocab=VOCAB,
            title=(m.title or "")[:80],
            why_good=str(q.get("why_good") or "")[:120],
            excerpt=(m.body or "")[:1200].replace("\n", " "))
        try:
            r = await gateway.complete_json([{"role": "user", "content": prompt}],
                                            max_tokens=1024, thinking="disabled")
        except Exception as e:  # noqa: BLE001 样例单篇失败不挡批
            print(f"[genre] ({i}/{len(sample)}) 失败 {type(e).__name__}: {str(e)[:50]}", flush=True)
            continue
        p = str((r or {}).get("primary") or "").strip()
        sec = str((r or {}).get("secondary") or "").strip()
        why = str((r or {}).get("why") or "").strip()
        out.append({"folder": str(q.get("folder") or ""), "title": (m.title or "")[:44],
                    "primary": p, "secondary": sec, "why": why[:60]})
        print(f"[genre] ({i}/{len(sample)}) 「{(m.title or '')[:36]}」→ {p}"
              + (f" / {sec}" if sec else ""), flush=True)
        await asyncio.sleep(1)
    print("\n[genre] ── 体裁分布（样例 30 篇）──", flush=True)
    dist: dict[str, int] = {}
    for o in out:
        dist[o["primary"]] = dist.get(o["primary"], 0) + 1
    for k, v in sorted(dist.items(), key=lambda kv: -kv[1]):
        print(f"  {k} ×{v}", flush=True)
    print("\n[genre] ── 明细 ──", flush=True)
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    asyncio.run(main())
