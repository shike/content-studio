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
from datetime import datetime, time as dtime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.articles.miner import article_kb_miner  # noqa: E402
from app.settings_store import load_overrides  # noqa: E402

# 与服务进程同口径：DB 里的运行时配置（LLM 密钥等）覆盖 .env 默认值——
# 不调用它，脚本进程 settings.zhipu_api_key 是空的（密钥只存在 app_settings，.env 无此行）
load_overrides()


class _Ctx:
    """挖掘器只用到 set_progress（真实 JobContext 同名同步方法，此处打 stdout）。"""

    def set_progress(self, p: int, m: str = "") -> None:
        print(f"  [{p:3}%] {m}", flush=True)


def _yield_window_wait() -> float:
    """夜间让路（2026-11-18 04:00 例行挖掘与驱动撞 ima 403 教训）：
    北京 04:00/05:00 是例行挖掘/改编档期（UTC 20:00/21:00），驱动在
    UTC 19:50–21:30 窗口内暂停批循环，把 ima 让给服务内调度。"""
    now = datetime.now(timezone.utc)
    if dtime(19, 50) <= now.time() <= dtime(21, 30):
        end_dt = datetime.combine(now.date(), dtime(21, 30), tzinfo=timezone.utc)
        return (end_dt - now).total_seconds() + 30
    return 0.0


async def main() -> int:
    max_batches = 60
    if "--max-batches" in sys.argv:
        max_batches = int(sys.argv[sys.argv.index("--max-batches") + 1])
    total = {"batches": 0, "evaluated": 0, "added": 0, "skipped_low": 0,
             "failed": 0, "already": 0, "non_text": 0, "errors": 0}
    consec_fail = 0
    for _ in range(max_batches):
        yield_s = _yield_window_wait()
        if yield_s > 0:
            print(f"[full-mine] 夜间让路：例行挖掘/改编档期，暂停 {int(yield_s)}s", flush=True)
            await asyncio.sleep(yield_s)
        try:
            r = await article_kb_miner(_Ctx(), {})
            consec_fail = 0
        except Exception as e:  # noqa: BLE001 单批失败不中断全量（游标未推进，下轮重试）
            total["errors"] += 1
            consec_fail += 1
            print(f"[batch {total['batches'] + 1}] 批失败: {type(e).__name__}: {str(e)[:120]}", flush=True)
            if consec_fail >= 8:
                print("[full-mine] 连续 8 批失败，停止（查失败中台/ima 凭证）", flush=True)
                break
            # 递归遍历列目录调用密集，ima 403 惩罚窗以分钟计——退避给足（2026-11-17 实测 30s 不够）
            wait = min(600, 180 * consec_fail)
            print(f"  退避 {wait}s（连败 {consec_fail}）", flush=True)
            await asyncio.sleep(wait)
            continue
        total["batches"] += 1
        for k in ("evaluated", "added", "skipped_low", "failed", "already", "non_text"):
            total[k] += int(r.get(k) or 0)
        print(f"[batch {total['batches']}] {r}", flush=True)
        if r.get("message") == "全库盘完一轮":
            break
    print(f"[full-mine] 完成 {total}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
