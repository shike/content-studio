#!/bin/bash
# Content Studio 开机自启（launchd）：安装 / 卸载 / 状态
# 用法：scripts/launchd.sh install|uninstall|status
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.content-studio"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
WDLABEL="com.content-studio-watchdog"
WDPLIST="$HOME/Library/LaunchAgents/${WDLABEL}.plist"
# 看门狗脚本必须放在受保护目录之外：macOS TCC 禁止 LaunchAgent 进程读取
# ~/Desktop 等受保护路径（实测 /bin/bash 读 Desktop 脚本报 "Operation not permitted"，
# 看门狗静默失效约 39 小时才被发现）。安装时复制到 Application Support。
WDDIR="$HOME/Library/Application Support/content-studio"
WDSCRIPT="$WDDIR/watchdog.sh"
LOG="/tmp/content-studio-8100.log"
PY="$ROOT/backend/.venv/bin/python"

install_plist() {
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>-m</string>
    <string>uvicorn</string>
    <string>app.main:app</string>
    <string>--app-dir</string>
    <string>${ROOT}/backend</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>8100</string>
  </array>
  <key>WorkingDirectory</key><string>${ROOT}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:${HOME}/.orbstack/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
</dict>
</plist>
EOF
  # 停掉手动起的实例避免端口冲突，再加载
  lsof -ti tcp:8100 | xargs kill 2>/dev/null || true
  sleep 1
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  sleep 2
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '<本机IP>')"
  echo "已安装并启动：本机 http://127.0.0.1:8100 ｜ 局域网 http://${LAN_IP}:8100 （日志 ${LOG} ）"
}

install_watchdog() {
  mkdir -p "$WDDIR"
  cp "$ROOT/scripts/watchdog.sh" "$WDSCRIPT"
  chmod +x "$WDSCRIPT"
  mkdir -p "$(dirname "$WDPLIST")"
  cat > "$WDPLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${WDLABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${WDSCRIPT}</string>
  </array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/tmp/content-studio-watchdog.log</string>
  <key>StandardErrorPath</key><string>/tmp/content-studio-watchdog.log</string>
</dict>
</plist>
EOF
  launchctl unload "$WDPLIST" 2>/dev/null || true
  launchctl load "$WDPLIST"
  echo "看门狗已安装（脚本副本 ${WDSCRIPT}）：每 5 分钟探活，无响应自动拉起 + macOS 通知"
}

uninstall_plist() {
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  launchctl unload "$WDPLIST" 2>/dev/null || true
  rm -f "$WDPLIST"
  echo "已卸载开机自启与看门狗"
}

status_plist() {
  if launchctl list 2>/dev/null | grep -q "$LABEL"; then
    echo "运行中：$(launchctl list | grep "$LABEL")"
    curl -s -o /dev/null -w "健康检查: %{http_code}\n" http://127.0.0.1:8100/api/health
  else
    echo "未安装/未运行"
  fi
  if launchctl list 2>/dev/null | grep -q "$WDLABEL"; then
    echo "看门狗：$(launchctl list | grep "$WDLABEL")"
    if [ -f "$WDSCRIPT" ] && ! cmp -s "$ROOT/scripts/watchdog.sh" "$WDSCRIPT"; then
      echo "  ⚠️ 脚本副本与仓库版本不一致——重跑 install 以同步"
    fi
  else
    echo "看门狗：未安装"
  fi
}

case "${1:-}" in
  install) install_plist && install_watchdog ;;
  uninstall) uninstall_plist ;;
  status) status_plist ;;
  *) echo "用法：$0 install|uninstall|status" && exit 1 ;;
esac
