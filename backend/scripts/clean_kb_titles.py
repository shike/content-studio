#!/usr/bin/env python3
"""知识库存量标题一次性机械清洗（2026-11-17）：去 .md 尾 / 砍 SEO 下划线串 / 修 CJK 间标记残缺。

用法（仓库根）：PYTHONPATH=backend backend/.venv/bin/python backend/scripts/clean_kb_titles.py [--dry]
幂等：clean_kb_title(clean_kb_title(x)) == clean_kb_title(x)；原始文件名仍在 origin_id 可溯。
规则与入库侧同源（materials.service.clean_kb_title）——入库即清洗由 miner 落地，本脚本只刷存量。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine  # noqa: E402
from app.materials.service import clean_kb_title  # noqa: E402
from app.models import Material  # noqa: E402


def main() -> int:
    dry = "--dry" in sys.argv
    with Session(engine) as s:
        rows = s.exec(
            select(Material).where(Material.origin_type == "kb_doc")).all()  # type: ignore[attr-defined]
        changed = 0
        for m in rows:
            new = clean_kb_title(m.title or "")
            if new and new != (m.title or ""):
                if not dry:
                    m.title = new
                    s.add(m)
                changed += 1
                if changed <= 12:
                    print(f"  {m.title!r}  →  {new!r}")
        if not dry:
            s.commit()
        print(f"[clean-kb-titles] 共 {len(rows)} 篇，需清洗 {changed} 篇{'（dry-run 未写库）' if dry else '，已写库'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
