#!/usr/bin/env python3
"""存量选题/拆解的两类号回填（2026-10-28，一次性维护脚本）。

判定规则（启发式，宁缺勿滥——拿不准的一律 persona，可在选题详情一键改）：
- benchmarks：analysis 里有 cta_analysis/content_pattern/replicable_playbook（获客拆解产出）
  或 title/summary 命中获客引导词 → leadgen；
- topics：title/angle/evidence.summary 命中获客引导词 → leadgen；否则 persona。

在目标环境（生产/隔离实例）直接运行：backend/.venv/bin/python scripts/backfill_account_type.py
幂等可重跑；只写 evidence.account_type 与 benchmark_videos.account_type，不动其他字段。
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import BenchmarkVideo, Topic  # noqa: E402

LEAD = re.compile(r"扣\s*\d?|私信|领取|领资料|资料包|免费送|免费帮|评论区扣|报名|邀约|名额|诊断|引流")


def is_lead(*texts: str) -> bool:
    return any(LEAD.search(t or "") for t in texts)


def main() -> int:
    n_topic = n_bm = 0
    with Session(engine) as s:
        for t in s.exec(select(Topic)).all():
            ev = dict(t.evidence or {})
            lead = is_lead(t.title, t.angle, str(ev.get("summary") or ""))
            kind = "leadgen" if lead else "persona"
            ev["account_type"] = kind
            t.evidence = ev
            s.add(t)
            n_topic += 1
        for b in s.exec(select(BenchmarkVideo)).all():
            a = b.analysis or {}
            lead = bool(a.get("cta_analysis") or a.get("content_pattern")
                        or a.get("replicable_playbook")) or is_lead(b.title, str(a.get("summary") or ""))
            b.account_type = "leadgen" if lead else "persona"
            s.add(b)
            n_bm += 1
        s.commit()
    print(f"回填完成：topics {n_topic} 条、benchmarks {n_bm} 条（启发式判定，可在选题详情一键改）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
