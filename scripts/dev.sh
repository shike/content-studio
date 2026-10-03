#!/usr/bin/env bash
# 一键启动 Content Studio（缺前端构建则先构建，缺 venv 则先创建）
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d backend/.venv ]]; then
  echo "[dev] 创建 Python 虚拟环境（uv 优先，回退 python3）..."
  if command -v uv >/dev/null; then
    uv venv backend/.venv --python 3.12
    uv pip install -r backend/requirements.txt --python backend/.venv/bin/python
  else
    python3 -m venv backend/.venv
    backend/.venv/bin/pip install -r backend/requirements.txt
  fi
fi

if [[ ! -f frontend/dist/index.html || "${FORCE_BUILD:-0}" == "1" ]]; then
  if command -v node >/dev/null; then
    echo "[dev] 构建前端..."
    (cd frontend && npm install --no-audit --no-fund && npm run build)
  else
    echo "[dev] 无 node，使用后端占位页（/api/* 不受影响）"
  fi
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8100}"
echo "[dev] Content Studio → http://${HOST}:${PORT}"
exec backend/.venv/bin/python -m uvicorn app.main:app \
  --app-dir backend --host "$HOST" --port "$PORT"
