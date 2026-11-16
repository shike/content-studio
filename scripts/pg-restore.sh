#!/bin/bash
# PostgreSQL 恢复（危险操作）：pg_restore --clean --if-exists 覆盖当前业务库。
# 用法：CS_RESTORE_CONFIRM=1 scripts/pg-restore.sh <dump 文件> [DATABASE_URL]
# 安全：必须显式 CS_RESTORE_CONFIRM=1；默认从仓库根 .env 读 DATABASE_URL。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DUMP="${1:-}"
[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "用法：CS_RESTORE_CONFIRM=1 $0 <dump> [url]" >&2; exit 1; }
[ "${CS_RESTORE_CONFIRM:-0}" = "1" ] || { echo "拒绝执行：需 CS_RESTORE_CONFIRM=1（会覆盖当前库）" >&2; exit 1; }

URL="${2:-}"
if [ -z "$URL" ]; then
  URL=$(grep -E '^DATABASE_URL=' .env 2>/dev/null | head -1 | cut -d= -f2- || true)
fi
[ -n "$URL" ] || { echo "缺 DATABASE_URL（参数或 .env）" >&2; exit 1; }
URL="${URL/postgresql+psycopg/postgresql}"

PG_RESTORE=$(command -v pg_restore || true)
if [ -z "$PG_RESTORE" ]; then
  CAND=$(backend/.venv/bin/python -c "import pgserver,pathlib;print(pathlib.Path(pgserver.__file__).parent/'pginstall/bin/pg_restore')" 2>/dev/null || true)
  [ -x "$CAND" ] && PG_RESTORE="$CAND"
fi
[ -n "$PG_RESTORE" ] || { echo "找不到 pg_restore（安装 postgresql-client 或本地 pgserver）" >&2; exit 1; }

echo "→ 恢复 $DUMP → 业务库（--clean --if-exists）"
"$PG_RESTORE" --clean --if-exists --no-owner --no-privileges -d "$URL" "$DUMP"
echo "✓ 恢复完成；随后跑一次 schema 审计：PYTHONPATH=backend backend/.venv/bin/python -m app.schema_audit"
