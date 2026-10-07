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

# 任务保护（2026-10-07 内存事故复盘）：有 running 任务时，health 失败/变慢多半是资源高峰
# （ASR 转写/批量下载），重启会打断任务并可能进入"重启→拾起任务→再爆"的循环。
# 策略：running>0 时连续 6 次（约 30 分钟）失败才强拉（任务自身硬超时最长 60min 会先自愈）；
# running=0 或探测失败（sqlite 不可用）按原逻辑立即拉起。
run_guard() {
  for DB in "${CS_DATA_DIR:-}/studio.db" "/srv/content-studio/data/studio.db" \
            "$HOME/Desktop/code/content-studio/data/studio.db"; do
    [ -f "$DB" ] || continue
    v=$(sqlite3 "$DB" "SELECT COUNT(*) FROM jobs WHERE status='running'" 2>/dev/null) || return 1
    echo "$v"
    return 0
  done
  return 1
}
if v=$(run_guard); then
  if [ "${v:-0}" -gt 0 ] && [ "$n" -lt 6 ]; then
    if [ "$n" -eq 1 ] || [ $((n % 12)) -eq 0 ]; then
      notify "健康检查失败 ${n} 次，但仍有 ${v} 个任务运行中——暂缓重启（资源高峰保护），继续观察"
    fi
    exit 0
  fi
fi
restart_service
# 首次达到动作线告警一次，之后每 12 轮（约 1 小时）提醒一次，避免轰炸
if [ "$n" -eq "$FAILS_BEFORE_ACT" ] || [ $((n % 12)) -eq 0 ]; then
  notify "健康检查连续 ${n} 次失败，已尝试自动拉起（端口 ${CS_PORT:-8100}）"
fi
