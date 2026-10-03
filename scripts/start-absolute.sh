#!/bin/bash
# 绝对路径启动，任何 CWD 下都能执行。
# 绑定 0.0.0.0：同一局域网的设备可通过 http://<本机IP>:8100 访问（当前 IP 见 ipconfig getifaddr en0）
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/backend/.venv/bin/python" -m uvicorn app.main:app --app-dir "$ROOT/backend" --host 0.0.0.0 --port 8100
