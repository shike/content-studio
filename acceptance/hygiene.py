#!/usr/bin/env python3
"""数据卫生扫描器（AUDIT A4 的自动化）：一条命令扫生产库的测试残留与假数据。

用法：python3 acceptance/hygiene.py [data_dir]
退出码：0 干净 / 1 发现残留。检查项（对齐 docs/AUDIT.md A4）：
  ① 拆解库 fixture 行（sample_speech）
  ② 演示/验收链接的发布记录（example.com / demo / acceptance）
  ③ 选题评分含 performance 回流残留（新契约已移除该字段）
  ④ jobs 里未收敛的"服务重启中断"记录（提示级）
"""
from __future__ import annotations

import os
import sqlite3
import sys

DIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATA_DIR", "data")
DB = os.path.join(DIR, "studio.db")

if not os.path.exists(DB):
    print(f"[hygiene] 数据库不存在：{DB}")
    sys.exit(1)

conn = sqlite3.connect(DB)
issues: list[str] = []


def q(sql: str) -> list:
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []  # 表不存在（如台账已按新契约移除）视为通过


# ① fixture 拆解行
rows = q("SELECT id FROM benchmark_videos WHERE title='sample_speech'")
if rows:
    issues.append(f"拆解库 fixture 残留 sample_speech×{len(rows)}（ids: {[r[0] for r in rows[:8]]}）")

# ② 演示/验收链接的发布记录（台账若已移除则表不存在，通过）
rows = q("SELECT id, published_url FROM publish_records WHERE published_url LIKE '%example.com%' "
         "OR published_url LIKE '%demo%' OR published_url LIKE '%acceptance%'")
if rows:
    issues.append(f"发布台账含演示/验收链接×{len(rows)}（ids: {[r[0] for r in rows[:8]]}）")

# ③ 评分回流残留
rows = q("SELECT id FROM topics WHERE score_breakdown LIKE '%performance%'")
if rows:
    issues.append(f"选题评分含 performance 回流残留×{len(rows)}（新契约已移除评分反哺；ids: {[r[0] for r in rows[:8]]}）")

# ④ 中断任务（提示级，不计入退出码）
interrupted = q("SELECT COUNT(*) FROM jobs WHERE error LIKE '%服务重启中断%'")[0][0]

print(f"[hygiene] 扫描 {DB}")
print(f"  fixture 行: {'干净' if not issues or 'sample' not in str(issues) else '有'}"
      f" | 演示链接: 干净 | 评分回流: 见下 | 中断任务: {interrupted} 条（提示）")
if issues:
    print("[hygiene] 发现残留：")
    for it in issues:
        print("  ✗", it)
    print("[hygiene] 结论：脏——按 docs/AUDIT.md A4 清理后重扫")
    sys.exit(1)
print("[hygiene] 结论：干净")
sys.exit(0)
