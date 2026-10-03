#!/bin/bash
# Content Studio 看门狗：服务探活 + 自动拉起 + macOS 通知。
# 独立于主服务进程（独立 LaunchAgent，每 5 分钟一发）：
# 主服务死透或被 launchd 节流时，由它负责发现、拉起、告诉你。
set -uo pipefail

LABEL="com.content-studio"          # macOS LaunchAgent 标签
SYSTEMD_UNIT="content-studio"      # Linux systemd 服务名
HEALTH="http://127.0.0.1:${CS_PORT:-8100}/api/health"
STATE="${TMPDIR:-/tmp}/cs-watchdog-fails"
FAILS_BEFORE_ACT=2   # 连续 2 次（约 10 分钟）无响应才动作，避免重启窗口误报

notify() {  # macOS 弹通知；Linux 记 journal（systemd timer 直接看 journalctl -u content-studio-watchdog）
  if [ "$(uname)" = "Darwin" ]; then
    osascript -e "display notification \"$1\" with title \"Content Studio\"" 2>/dev/null || true
  else
    echo "[watchdog] $1"
  fi
}

restart_service() {
  if [ "$(uname)" = "Darwin" ]; then
    launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  elif command -v systemctl >/dev/null 2>&1; then
    systemctl restart "${SYSTEMD_UNIT}" 2>/dev/null || true
  fi
}

code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HEALTH" 2>/dev/null || echo 000)
if [ "$code" = "200" ]; then
  if [ -f "$STATE" ] && [ "$(cat "$STATE" 2>/dev/null || echo 0)" -ge "$FAILS_BEFORE_ACT" ]; then
    notify "服务已恢复"
  fi
  rm -f "$STATE"
  exit 0
fi

n=$(($(cat "$STATE" 2>/dev/null || echo 0) + 1))
echo "$n" > "$STATE"
restart_service
# 首次达到动作线告警一次，之后每 12 轮（约 1 小时）提醒一次，避免轰炸
if [ "$n" -eq "$FAILS_BEFORE_ACT" ] || [ $((n % 12)) -eq 0 ]; then
  notify "健康检查连续 ${n} 次失败，已尝试自动拉起（端口 ${CS_PORT:-8100}）"
fi
