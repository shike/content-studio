#!/bin/bash
# PostgreSQL 逻辑备份（pg_dump -Fc），保留策略由 app.ops 处理。
# 用法：scripts/pg-backup.sh [tag] [keep]   （默认 manual / 14）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/backend/.venv/bin/python}"
[ -x "$PY" ] || { echo "缺少 venv：$PY" >&2; exit 1; }
PYTHONPATH="$ROOT/backend" "$PY" -m app.ops backup "${1:-manual}" "${2:-14}"
