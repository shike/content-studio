"""schema 审计：SQLModel 模型 DDL ↔ 库实际 DDL 只读 diff（B 阶段护栏，2026-11-16）。

背景：2026-11-15 连出两起 schema 漂移（failure_patterns.kind、content_items.kb_id/
folder_id）——第三轮减负删模型字段时未查生产表残留 NOT NULL 无默认列，INSERT 必炸。
本模块提供只读对账：模型有库无、库有模型无、列集差异三类，任一非空即漂移。

用法（发布口/CI 闸，只读不写）：
    python -m app.schema_audit            # 人类可读报告，漂移时退出码 1
    python -m app.schema_audit --quiet    # 只输出漂移行（给脚本判断）

口径：
- 表级：模型 24 张 ↔ 库实际表（legacy 表已清零，出现新差集即漂移）。
- 列级：列名集合比对（类型/约束差异由迁移脚本负责，不在此误报）；
  漂移=模型有库无、库有模型无两类。
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect as sa_inspect
from sqlmodel import SQLModel


def model_tables() -> dict[str, set[str]]:
    """模型侧：表 → 列名集合（来自 SQLModel metadata，单一真相=模型）。"""
    from . import models  # noqa: F401  确保全部表模型注册

    out: dict[str, set[str]] = {}
    for name, table in SQLModel.metadata.tables.items():
        out[name] = {c.name for c in table.columns}
    return out


def db_tables(engine) -> dict[str, set[str]]:
    """库侧：表 → 列名集合（PRAGMA 只读）。"""
    insp = sa_inspect(engine)
    out: dict[str, set[str]] = {}
    for name in insp.get_table_names():
        out[name] = {c["name"] for c in insp.get_columns(name)}
    return out


def audit(engine) -> tuple[list[str], bool]:
    """返回 (漂移行列表, 是否干净)。只读，不执行任何 DDL。"""
    mtab, dtab = model_tables(), db_tables(engine)
    drifts: list[str] = []

    for t in sorted(set(mtab) - set(dtab)):
        drifts.append(f"缺表：模型有 `{t}`，库无（create_all 未生效或库被手改）")
    for t in sorted(set(dtab) - set(mtab)):
        drifts.append(f"多表：库有 `{t}`，模型无（退役未删或手建表）")

    for t in sorted(set(mtab) & set(dtab)):
        mcols, dcols = mtab[t], dtab[t]
        for c in sorted(mcols - dcols):
            drifts.append(f"缺列：`{t}.{c}` 模型有库无（新字段未迁移到旧库）")
        for c in sorted(dcols - mcols):
            drifts.append(f"残留列：`{t}.{c}` 库有模型无（删字段未清生产残留，"
                          "若为 NOT NULL 无默认列，INSERT 必炸）")

    return drifts, (not drifts)


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .settings import settings

    ap = argparse.ArgumentParser(description="schema 审计：模型 DDL ↔ 库 DDL 只读 diff")
    ap.add_argument("--quiet", action="store_true", help="只输出漂移行（供脚本判断）")
    ap.add_argument("--db", type=str, default="",
                    help="指定库文件路径（默认读 settings.data_dir/studio.db）")
    args = ap.parse_args(argv)

    db_file = Path(args.db) if args.db else settings.db_path
    if not db_file.exists():
        if args.quiet:
            return 0  # 全新部署：库未建，create_all 会按模型建，视为对齐
        print(f"库文件不存在：{db_file}（全新部署由 create_all 建表，视为对齐）")
        return 0

    eng = create_engine(f"sqlite:///{db_file}")
    try:
        drifts, clean = audit(eng)
    finally:
        eng.dispose()

    if args.quiet:
        for d in drifts:
            print(d)
        return 0 if clean else 1

    print(f"schema 审计：{db_file}")
    if clean:
        print("  ✓ 模型与库 DDL 一致（无漂移）")
        return 0
    print(f"  ✗ 漂移 {len(drifts)} 条：")
    for d in drifts:
        print(f"  - {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
