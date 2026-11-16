#!/usr/bin/env python3
"""SQLite → PostgreSQL 一次性全量搬迁（P1 迁移专用）。

用法（仓库根）：
    PYTHONPATH=backend backend/.venv/bin/python backend/scripts/etl_sqlite_to_pg.py \
        --sqlite /path/to/studio.db [--truncate]

原则：
- 目标库必须为空（否则拒绝；`--truncate` 显式清空）；
- 保留旧 id；搬完重置每个表的 identity sequence；
- JSON 文本 → JSONB（非法 JSON 直接报错，不静默丢数据）；
- 布尔 0/1 → BOOLEAN；ISO 时间串 → naive datetime；
- 每表行数/最大 id 对账，任何不一致退出码 1。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import Boolean, DateTime, Float, Integer, create_engine, text  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import models  # noqa: E402,F401  注册全部表
from app.settings import settings  # noqa: E402

# 依赖顺序：被引用表在前（仅显式 FK 需要；其余顺序无约束）
TABLE_ORDER = [
    "tenants", "users", "user_sessions", "app_settings", "failure_patterns",
    "topic_cards", "falsify_items", "benchmark_videos",
    "watch_accounts", "watch_candidates", "radar_topics", "content_items",
    "scripts", "articles", "self_videos", "style_profiles",
    "account_metrics_daily", "video_weekly_reports", "daily_ledger",
    "jobs", "schedules",
]

# JSON 列默认值（旧库可能有 NULL/空串）
JSON_LIST_COLS = {
    "history", "takeaways", "gates", "top_comments", "versions", "storyboard",
    "title_candidates", "kb_sources", "best_quotes", "seen_ids", "hits",
    "traits", "exemplars",
}
JSON_DICT_COLS = {
    "payload", "result", "pool", "judge", "stats", "analysis", "brand",
    "report", "style_analysis",
}


def _parse_dt(v):  # noqa: ANN001
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def _to_bool(v):  # noqa: ANN001
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    return bool(int(v))


def _json_default(col: str):  # noqa: ANN001
    return [] if col in JSON_LIST_COLS else {}


def _convert(col_name: str, col_type, value):  # noqa: ANN001
    if isinstance(col_type, JSONB):
        if value is None or value == "":
            return _json_default(col_name)
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"列 {col_name} 不是合法 JSON：{value[:120]!r}（{e}）") from e
    if isinstance(col_type, Boolean):
        return _to_bool(value)
    if isinstance(col_type, DateTime):
        return _parse_dt(value)
    if isinstance(col_type, Integer):
        return 0 if value is None else int(value)
    if isinstance(col_type, Float):
        return 0.0 if value is None else float(value)
    # 字符串/其他
    if value is None:
        return ""
    return value


def _pg_table(name: str):
    return SQLModel.metadata.tables[name]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", required=True, help="旧 SQLite 库路径（只读打开）")
    ap.add_argument("--url", default="", help="目标 PostgreSQL URL（默认 settings.database_url）")
    ap.add_argument("--truncate", action="store_true", help="先清空目标库（危险，仅迁移用）")
    args = ap.parse_args()

    url = args.url or settings.database_url
    if not url:
        print("缺少 DATABASE_URL 或 --url", file=sys.stderr)
        return 2
    src_path = Path(args.sqlite)
    if not src_path.exists():
        print(f"旧库不存在：{src_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    old_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

    eng = create_engine(url)
    missing = [t for t in TABLE_ORDER if t not in old_tables]
    if missing:
        print(f"旧库缺表（无法等价迁移）：{missing}", file=sys.stderr)
        return 2

    report: list[tuple[str, int, int]] = []
    with eng.begin() as pg:
        if args.truncate:
            for t in reversed(TABLE_ORDER):
                pg.execute(text(f'TRUNCATE TABLE "{t}" RESTART IDENTITY CASCADE'))
            print("[etl] 目标库已清空")

        for t in TABLE_ORDER:
            table = _pg_table(t)
            existing = pg.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            if existing:
                print(f"✗ 目标表 {t} 非空（{existing} 行）；先 --truncate 或换空库", file=sys.stderr)
                return 1

            rows = conn.execute(f'SELECT * FROM "{t}"').fetchall()
            cols = [c for c in table.columns if c.name in rows[0].keys()] if rows else list(table.columns)
            payload = []
            for r in rows:
                row = {}
                for c in cols:
                    v = _convert(c.name, c.type, r[c.name])
                    # 旧库用 0 当「无外键」哨兵（SQLite 未启用 FK 校验）；
                    # PostgreSQL 外键列必须为 NULL，否则引用不存在的 id=0
                    if c.foreign_keys and v == 0:
                        v = None
                    row[c.name] = v
                payload.append(row)
            if payload:
                pg.execute(table.insert(), payload)
            got = pg.execute(text(f'SELECT count(*) FROM "{t}"')).scalar()
            maxid = pg.execute(text(f'SELECT max(id) FROM "{t}"')).scalar() if "id" in table.columns else None
            old_max = conn.execute(f'SELECT max(id) FROM "{t}"').fetchone()[0] if "id" in table.columns else None
            report.append((t, len(rows), got))
            print(f"  {t}: 旧 {len(rows)} → 新 {got}  max_id={old_max}/{maxid}")
            if len(rows) != got or old_max != maxid:
                print(f"✗ {t} 行数/最大 id 对账失败", file=sys.stderr)
                return 1

        # 重置 identity sequence（有 id 列的表）
        for t in TABLE_ORDER:
            table = _pg_table(t)
            if "id" not in table.columns:
                continue
            pg.execute(text(
                f'SELECT setval(pg_get_serial_sequence(\'{t}\', \'id\'), '
                f'COALESCE((SELECT max(id) FROM "{t}"), 1), '
                f'(SELECT max(id) FROM "{t}") IS NOT NULL)'
            ))
        print("[etl] identity sequence 已重置")

    conn.close()
    total_old = sum(a for _, a, _ in report)
    print(f"[etl] 完成：{len(report)} 表 / {total_old} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
