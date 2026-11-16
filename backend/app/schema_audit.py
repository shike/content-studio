"""schema 审计：SQLModel 模型 DDL ↔ 生产 PostgreSQL 只读 diff。

发布口护栏：模型有库无 / 库有模型无 / 列集差异 / 列类型家族不符，任一非空即漂移。
只读，不执行任何 DDL。

用法：
    python -m app.schema_audit                 # 读 settings.database_url，人类可读报告
    python -m app.schema_audit --quiet         # 只输出漂移行（供脚本判断）
    python -m app.schema_audit --url postgresql+psycopg://...
"""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, inspect as sa_inspect
from sqlmodel import SQLModel

_IGNORED_TABLES = {"alembic_version"}

# 迁移期白名单：旧表已停止读写、数据已迁新表，生产库原地只读保留到 P5 收口统一 DROP
# （Expand-Contract；模型已删=新库不再建）。P5 删旧表时同步移出本清单。
# P2 素材域（2026-11-16）+ P3 内容域（2026-11-16）。
_LEGACY_TABLES = {
    # P2 素材域
    "benchmark_videos", "watch_candidates", "radar_topics", "content_items", "falsify_items",
    # P3 内容域
    "topic_cards", "scripts", "articles", "style_profiles",
    # P4 数据域
    "watch_accounts", "account_metrics_daily", "video_weekly_reports", "daily_ledger",
}

# 类型只比「家族」：避免 VARCHAR(200) vs VARCHAR、identity INTEGER vs INTEGER 之类误报
_TYPE_FAMILY = {
    "VARCHAR": "STRING", "TEXT": "STRING", "CHAR": "STRING", "STRING": "STRING",
    "AUTOSTRING": "STRING",  # SQLModel 的 str 列类型
    "INTEGER": "INT", "BIGINT": "INT", "SMALLINT": "INT", "INT": "INT",
    "BOOLEAN": "BOOL", "BOOL": "BOOL",
    "JSONB": "JSON", "JSON": "JSON",
    "TIMESTAMP": "DATETIME", "DATETIME": "DATETIME",
    "DATE": "DATE",
    "FLOAT": "FLOAT", "DOUBLE_PRECISION": "FLOAT", "REAL": "FLOAT",
    "NUMERIC": "FLOAT", "DECIMAL": "FLOAT",
    "BYTEA": "BINARY", "BLOB": "BINARY",
}


def _family(t) -> str:  # noqa: ANN001
    name = type(t).__name__.upper()
    return _TYPE_FAMILY.get(name, name)


def model_tables() -> dict[str, dict[str, str]]:
    """模型侧：表 → {列名: 类型家族}（单一真相=模型）。"""
    from . import models  # noqa: F401  确保全部表模型注册

    out: dict[str, dict[str, str]] = {}
    for name, table in SQLModel.metadata.tables.items():
        out[name] = {c.name: _family(c.type) for c in table.columns}
    return out


def db_tables(engine) -> dict[str, dict[str, str]]:  # noqa: ANN001
    """库侧：表 → {列名: 类型家族}（information_schema / inspector 只读）。"""
    insp = sa_inspect(engine)
    out: dict[str, dict[str, str]] = {}
    for name in insp.get_table_names():
        if name in _IGNORED_TABLES or name in _LEGACY_TABLES:
            continue
        out[name] = {c["name"]: _family(c["type"]) for c in insp.get_columns(name)}
    return out


def audit(engine) -> tuple[list[str], bool]:  # noqa: ANN001
    mtab, dtab = model_tables(), db_tables(engine)
    drifts: list[str] = []

    for t in sorted(set(mtab) - set(dtab)):
        drifts.append(f"缺表：模型有 `{t}`，库无（Alembic 未跑到 head）")
    for t in sorted(set(dtab) - set(mtab)):
        drifts.append(f"多表：库有 `{t}`，模型无（退役未删或手建表）")

    for t in sorted(set(mtab) & set(dtab)):
        mcols, dcols = mtab[t], dtab[t]
        for c in sorted(set(mcols) - set(dcols)):
            drifts.append(f"缺列：`{t}.{c}` 模型有库无（迁移未落库）")
        for c in sorted(set(dcols) - set(mcols)):
            drifts.append(f"残留列：`{t}.{c}` 库有模型无（退役字段未清）")
        for c in sorted(set(mcols) & set(dcols)):
            if mcols[c] != dcols[c]:
                drifts.append(f"类型不符：`{t}.{c}` 模型 {mcols[c]} / 库 {dcols[c]}")

    return drifts, (not drifts)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .settings import settings

    ap = argparse.ArgumentParser(description="schema 审计：模型 DDL ↔ PostgreSQL 只读 diff")
    ap.add_argument("--quiet", action="store_true", help="只输出漂移行（供脚本判断）")
    ap.add_argument("--url", type=str, default="", help="覆盖 DATABASE_URL（默认读 settings）")
    args = ap.parse_args(argv)

    url = args.url or settings.database_url
    if not url:
        print("缺少 DATABASE_URL（或 --url）", file=sys.stderr)
        return 2
    eng = create_engine(url)
    try:
        drifts, clean = audit(eng)
    finally:
        eng.dispose()

    if args.quiet:
        for d in drifts:
            print(d)
        return 0 if clean else 1

    print("schema 审计：PostgreSQL")
    if clean:
        print("  ✓ 模型与库 DDL 一致（无漂移）")
        return 0
    print(f"  ✗ 漂移 {len(drifts)} 条：")
    for d in drifts:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
