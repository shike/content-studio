#!/bin/bash
# Content Studio 服务器部署（Linux/systemd）：安装 / 卸载 / 状态
# 用法：sudo scripts/deploy-linux.sh install [--host 127.0.0.1] [--port 8100]
#       sudo scripts/deploy-linux.sh uninstall
#            scripts/deploy-linux.sh status
#
# 安装后：content-studio.service（常驻，崩了自动拉起）+ content-studio-watchdog.timer
# （每 5 分钟探活，连续失败自动重启）。Nginx/HTTPS 模板见 deploy/nginx-content-studio.conf.example。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SVC="content-studio"
UNIT="/etc/systemd/system/${SVC}.service"
WDSVC="${SVC}-watchdog"
WDUNIT="/etc/systemd/system/${WDSVC}.service"
WDTIMER="/etc/systemd/system/${WDSVC}.timer"
WDDIR="/usr/local/lib/${SVC}"
WDSCRIPT="${WDDIR}/watchdog.sh"
NGINX_EXAMPLE="${ROOT}/deploy/nginx-content-studio.conf.example"
RUN_USER="${SUDO_USER:-$(id -un)}"
HOST_ARG="127.0.0.1"   # 默认只绑本机，由 Nginx 反代（公网直连用 --host 0.0.0.0）
PORT_ARG="8100"

CMD="${1:-}"
[ $# -gt 0 ] && shift
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST_ARG="$2"; shift 2 ;;
    --port) PORT_ARG="$2"; shift 2 ;;
    *) shift ;;
  esac
done

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root：sudo $0 $*" >&2
    exit 1
  fi
}

preflight() {
  [ -x "${ROOT}/backend/.venv/bin/python" ] || { echo "缺少 backend/.venv（先跑 uv sync 建环境）"; exit 1; }
  [ -x "${ROOT}/backend/.venv/bin/alembic" ] || { echo "缺少 alembic（uv pip install -r backend/requirements.txt）"; exit 1; }
  "${ROOT}/backend/.venv/bin/python" -c 'import psycopg' 2>/dev/null || { echo "缺少 psycopg（uv pip install -r backend/requirements.txt）"; exit 1; }
  [ -f "${ROOT}/frontend/dist/index.html" ] || echo "⚠️ frontend/dist 不存在——后端将服务内置回退页；先 cd frontend && npm run build"
  command -v systemctl >/dev/null 2>&1 || { echo "本机没有 systemd"; exit 1; }
  command -v pg_isready >/dev/null 2>&1 || { echo "缺 PostgreSQL 客户端（apt install postgresql）"; exit 1; }
  grep -q '^DATABASE_URL=' "${ROOT}/.env" 2>/dev/null || {
    echo "缺 DATABASE_URL：在 ${ROOT}/.env 配 postgresql+psycopg://user:pass@127.0.0.1:5432/content_studio"; exit 1; }
  pg_isready -q || echo "⚠️ PostgreSQL 未就绪（systemctl start postgresql）"
}

install_all() {
  need_root install
  preflight
  echo "部署用户：${RUN_USER}；监听 ${HOST_ARG}:${PORT_ARG}（Nginx 反代时保持 127.0.0.1）"

  cat > "$UNIT" <<EOF
[Unit]
Description=Content Studio（单管理员内容生产流水线）
After=network-online.target postgresql.service
Wants=network-online.target postgresql.service

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${ROOT}
EnvironmentFile=-${ROOT}/.env
Environment=TZ=Asia/Shanghai
Environment=CS_REPO_ROOT=${ROOT}
ExecStart=${ROOT}/backend/.venv/bin/python -m uvicorn app.main:app \\
  --app-dir ${ROOT}/backend --host ${HOST_ARG} --port ${PORT_ARG} \\
  --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

  # 看门狗：脚本拷到 /usr/local/lib（与 macOS TCC 同理，不依赖仓库路径可读性），
  # timer 每 5 分钟探活；连续失败由脚本内 systemctl restart 拉起。
  mkdir -p "$WDDIR"
  cp "${ROOT}/scripts/watchdog.sh" "$WDSCRIPT"
  chmod +x "$WDSCRIPT"
  cat > "$WDUNIT" <<EOF
[Unit]
Description=Content Studio 看门狗（健康检查 + 自动拉起）

[Service]
Type=oneshot
User=root
EnvironmentFile=-${ROOT}/.env
Environment=CS_PORT=${PORT_ARG}
Environment=CS_REPO_ROOT=${ROOT}
ExecStart=/bin/bash ${WDSCRIPT}
EOF
  cat > "$WDTIMER" <<EOF
[Unit]
Description=Content Studio 看门狗定时器（每 5 分钟）

[Timer]
OnBootSec=120
OnUnitActiveSec=300
AccuracySec=30

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload
  echo "→ Alembic 升级（backend/migrations → head）"
  (cd "$ROOT" && sudo -u "$RUN_USER" env PYTHONPATH="$ROOT/backend" \
     "$ROOT/backend/.venv/bin/python" -m alembic -c backend/alembic.ini upgrade head)
  systemctl enable --now "${SVC}"
  systemctl enable --now "${WDSVC}.timer"
  sleep 3
  curl -s -o /dev/null -w "健康检查: %{http_code}\n" "http://127.0.0.1:${PORT_ARG}/api/health" || true
  cat <<TIP

已安装。后续步骤：
  1) PostgreSQL：apt install postgresql；建库/建用户后把 DATABASE_URL 写进 ${ROOT}/.env；
     首次迁移：sudo -u ${RUN_USER} env PYTHONPATH=${ROOT}/backend \\
       ${ROOT}/backend/.venv/bin/python -m alembic -c backend/alembic.ini upgrade head
  2) Nginx + HTTPS：把 ${NGINX_EXAMPLE}
     改好域名后放到 /etc/nginx/conf.d/，certbot --nginx -d 你的域名 签发证书；
     .env 里设 COOKIE_SECURE=1（HTTPS 下会话 Cookie 只走加密通道）。
  3) Playwright 系统依赖（抓取抖音用）：backend/.venv/bin/playwright install-deps chromium
  4) ASR 模型（首次）：跑一次拆解任务会自动下载（large-v3 约 3.5GB；建议 .env 设 ASR_MODEL=small
     约 466MB 够用，HF_ENDPOINT=https://hf-mirror.com 加速）
  5) 备份：pg_dump 由每日调度（data/backups/pg-daily-*.dump），恢复用 pg_restore；
     发版前 sync-server 自动打 pre-migrate 快照。
  6) 查看日志：journalctl -u ${SVC} -f ｜ 看门狗：journalctl -u ${WDSVC} -n 20
TIP
}

uninstall_all() {
  need_root uninstall
  systemctl disable --now "${WDSVC}.timer" 2>/dev/null || true
  systemctl disable --now "${SVC}" 2>/dev/null || true
  rm -f "$UNIT" "$WDUNIT" "$WDTIMER"
  rm -rf "$WDDIR"
  systemctl daemon-reload
  echo "已卸载 systemd 服务与看门狗（数据与 .env 保留）"
}

status_all() {
  echo "服务：$(systemctl is-active "${SVC}" 2>/dev/null || echo 未安装)"
  echo "看门狗定时器：$(systemctl is-active "${WDSVC}.timer" 2>/dev/null || echo 未安装)"
  curl -s -o /dev/null -w "健康检查: %{http_code}\n" "http://127.0.0.1:${PORT_ARG}/api/health" || true
}

case "$CMD" in
  install) install_all ;;
  uninstall) uninstall_all ;;
  status) status_all ;;
  *) echo "用法：sudo $0 install [--host 127.0.0.1] [--port 8100] | uninstall | status" && exit 1 ;;
esac
