#!/usr/bin/env python3
"""知识库全量挖掘驱动（2026-11-17）：循环 article_kb_miner 批次直到游标回卷（全库盘完）。

用法（服务器仓库根，配 nohup 后台）：
  PYTHONPATH=backend nohup backend/.venv/bin/python backend/scripts/kb_full_mine.py [--max-batches N] >> data/kb_full_mine.log 2>&1 &

- 每批 = 挖掘器一个日批（18 篇：全文拉取→LLM 评估→达线入库）；
- 已入库文档挖掘器自带 existing 跳过（不耗 LLM），全量重扫安全；
- 单批异常（TransientError 等）不中断整体——游标未推进，下轮重试同一批；连续 5 批失败即停（人工看失败中台）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.articles.miner import article_kb_miner  # noqa: E402


class _Ctx:
    """挖掘器只用到 set_progress（真实 JobContext 同名同步方法，此处打 stdout）。"""

    def set_progress(self, p: int, m: str = "") -> None:
        print(f"  [{p:3}%] {m}", flush=True)


async def main() -> int:
    max_batches = 60
    if "--max-batches" in sys.argv:
        max_batches = int(sys.argv[sys.argv.index("--max-batches") + 1])
    total = {"batches": 0, "evaluated": 0, "added": 0, "skipped_low": 0,
             "failed": 0, "already": 0, "errors": 0}
    consec_fail = 0
    for _ in range(max_batches):
        try:
            r = await article_kb_miner(_Ctx(), {})
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 单批失败不中断全量（游标未推进，下轮重试）
            total["errors"] += 1
            consec_fail += 1
            print(f"[batch {total['batches'] + 1}] 批失败: {type(e).__name__}: {str(e)[:120]}", flush=True)
            if consec_fail >= 5:
                print("[full-mine] 连续 5 批失败，停止（查失败中台/ima 凭证）", flush=True)
                break
            await asyncio.sleep(30)
            continue
        total["batches"] += 1
        for k in ("evaluated", "added", "skipped_low", "failed", "already"):
            total[k] += int(r.get(k) or 0)
        print(f"[batch {total['batches']}] {r}", flush=True)
        if r.get("message") == "全库盘完一轮":
            break
    print(f"[full-mine] 完成 {total}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
