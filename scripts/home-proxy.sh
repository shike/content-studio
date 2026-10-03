#!/bin/bash
# 住宅出口：本机 SOCKS5 + 到服务器的 SSH 反向隧道（自愈守护，常驻由 launchd 拉起）。
# 用途=让服务器上的抖音抓取走本机家宽 IP（机房 IP 会被风控，见 scripts/home_proxy.py 注释）。
# 用法：scripts/home-proxy.sh [install|uninstall|status]（install 装 launchd 常驻）
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${CS_HOME_PROXY_PORT:-11080}"
LABEL="com.content-studio.homeproxy"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
# 服务器目标不入库：仓库 scripts/.deploy.env 或已安装副本同目录的 .deploy.env（均 git 忽略）
for _f in "$ROOT/scripts/.deploy.env" "$HOME/Library/Application Support/content-studio/.deploy.env"; do
  [ -f "$_f" ] && . "$_f"
done
KEY="${CS_HOME_PROXY_KEY:-$HOME/.ssh/id_ed25519}"
# 脚本必须放受保护目录之外：macOS TCC 禁止 LaunchAgent 读取 ~/Desktop
# （实测 /bin/bash 读仓库脚本报 Operation not permitted，与看门狗同款坑）
APPDIR="$HOME/Library/Application Support/content-studio"
INSTALLED="$APPDIR/home-proxy.sh"

run_loop() {
  SERVER="${CS_HOME_PROXY_SERVER:?缺隧道目标：在 scripts/.deploy.env 写 CS_HOME_PROXY_SERVER=user@server（该文件不入库）}"
  # 反向端口转发：服务器 127.0.0.1:${PORT} → 本机 127.0.0.1:${PORT}（Mac 上的住宅出口 SOCKS5）。
  # 服务器上抓取进程用 DOUYIN_PROXY=socks5://127.0.0.1:${PORT} 即可从家宽 IP 出网。
  SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
  echo "[home-proxy] 启动住宅出口 SOCKS5 :${PORT}"
  # --upstream 7890：duckduckgo 等境外目标家宽直连不通，经本机外网代理出海（服务器 search 的 DDG 走这条）
  python3 "$SELF_DIR/home_proxy.py" --port "$PORT" --upstream 7890 &
  PROXY_PID=$!
  trap 'kill $PROXY_PID 2>/dev/null' EXIT INT TERM
  sleep 1
  while true; do
    echo "[home-proxy] 建立反向隧道 → ${SERVER}:${PORT}"
    ssh -i "$KEY" -N -T \
      -o BatchMode=yes -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
      -R "127.0.0.1:${PORT}:127.0.0.1:${PORT}" "$SERVER"
    echo "[home-proxy] 隧道断开，5 秒后重连（检查本机网络/服务器可达性）"
    kill -0 $PROXY_PID 2>/dev/null || { echo "[home-proxy] 出口进程已死，整组重启"; exec "$0" run; }
    sleep 5
  done
}

install_agent() {
  mkdir -p "$(dirname "$PLIST")" "$APPDIR"
  cp "$ROOT/scripts/home-proxy.sh" "$INSTALLED"
  cp "$ROOT/scripts/home_proxy.py" "$APPDIR/home_proxy.py"
  # launchd 副本也要能拿到隧道目标（.deploy.env 不入库，随 install 一并拷贝）
  [ -f "$ROOT/scripts/.deploy.env" ] && cp "$ROOT/scripts/.deploy.env" "$APPDIR/.deploy.env"
  chmod +x "$INSTALLED"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${INSTALLED}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/content-studio-homeproxy.log</string>
  <key>StandardErrorPath</key><string>/tmp/content-studio-homeproxy.log</string>
</dict>
</plist>
PLIST_EOF
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "已安装并启动：本机 SOCKS5 :${PORT} + 到 ${SERVER} 的反向隧道（日志 /tmp/content-studio-homeproxy.log）"
}

uninstall_agent() {
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  pkill -f "home_proxy.py" 2>/dev/null || true
  echo "已卸载住宅出口（服务器端 douyin_proxy 需清空或保持不通时自动降级）"
}

status_agent() {
  launchctl list 2>/dev/null | grep -q "$LABEL" && echo "出口守护：运行中（$(launchctl list | grep "$LABEL")）" || echo "出口守护：未安装"
  pgrep -f "home_proxy.py" >/dev/null && echo "SOCKS 进程：在" || echo "SOCKS 进程：无"
  if [ -f "$INSTALLED" ] && ! cmp -s "$ROOT/scripts/home-proxy.sh" "$INSTALLED"; then
    echo "  ⚠️ 脚本副本与仓库版本不一致——重跑 install 以同步"
  fi
  lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1 && echo "端口 ${PORT}：监听中" || echo "端口 ${PORT}：未监听"
}

case "${1:-run}" in
  run) run_loop ;;
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *) echo "用法：$0 [run|install|uninstall|status]" ;;
esac
