"""运维 CLI（发版/看门狗用）：不依赖 psql，直接走应用引擎。

用法：
    PYTHONPATH=backend python -m app.ops inflight          # 打印 running+queued 任务数
    PYTHONPATH=backend python -m app.ops backup daily 14   # pg_dump 快照
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from .db import backup_db, engine


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("用法：app.ops inflight | app.ops backup <tag> [keep]", file=sys.stderr)
        return 2
    cmd = args[0]
    if cmd == "inflight":
        with engine.connect() as c:
            n = c.execute(text(
                "SELECT count(*) FROM jobs WHERE status IN ('running','queued')"
            )).scalar()
        print(int(n or 0))
        return 0
    if cmd == "backup":
        tag = args[1] if len(args) > 1 else "manual"
        keep = int(args[2]) if len(args) > 2 else 14
        print(backup_db(tag, keep=keep))
        return 0
    print(f"未知命令：{cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
