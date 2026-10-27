#!/bin/bash
# 拆解下载本机 worker 安装器（2026-10-27）：服务器浏览器被抖音扣数据，下载外包本机 Mac。
# 用法：scripts/home-dl.sh install | uninstall | status
# 产物装在 ~/Library/Application Support/content-studio/（launchd 禁读 Desktop 仓库——TCC，
# watchdog 同款教训），独立小 venv + 完整版 Chromium，不碰仓库与项目根 .ms-playwright。
set -euo pipefail

APP_DIR="$HOME/Library/Application Support/content-studio"
WORKER_SRC="$(cd "$(dirname "$0")" && pwd)/home_dl_worker.py"
WORKER_DST="$APP_DIR/home_dl_worker.py"
VENV="$APP_DIR/home-dl-venv"
PLIST="$HOME/Library/LaunchAgents/cn.dropleap.cs-home-dl.plist"
LABEL="cn.dropleap.cs-home-dl"
LOG_DIR="$APP_DIR/logs"

plist_body() {
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$WORKER_DST</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>120</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/home-dl.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/home-dl.log</string>
</dict>
</plist>
EOF
}

case "${1:-}" in
  install)
    [ -f "$WORKER_SRC" ] || { echo "找不到 $WORKER_SRC（必须在仓库内运行）"; exit 1; }
    [ -f "$HOME/.ssh/content_studio_deploy" ] || { echo "缺少部署密钥 ~/.ssh/content_studio_deploy"; exit 1; }
    mkdir -p "$APP_DIR" "$LOG_DIR"
    echo "→ 拷贝 worker"
    cp "$WORKER_SRC" "$WORKER_DST"
    if [ ! -x "$VENV/bin/python" ]; then
      echo "→ 创建独立 venv（playwright 钉 1.64.1：chromium-1248 已在系统默认缓存，免 CDN 下载）"
      # 用仓库 venv 的 3.12 解释器建 venv（系统 /usr/bin/python3=3.9 跑不了 playwright≥1.61；
      # 该解释器锚在 ~/.local/share/uv，不在 Desktop——TCC 不拦 launchd 运行时读取）
      REPO_PY="$(cd "$(dirname "$0")/.." && pwd)/backend/.venv/bin/python"
      [ -x "$REPO_PY" ] || REPO_PY="$(command -v python3)"
      "$REPO_PY" -m venv "$VENV"
      "$VENV/bin/pip" install -q --upgrade pip
      "$VENV/bin/pip" install -q "playwright==1.64.1" httpx
    fi
    "$VENV/bin/python" -m playwright install chromium
    echo "→ 写 LaunchAgent（RunAtLoad + 每 120 秒一轮）"
    plist_body
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "✓ 已安装。服务器侧需同步开启 CS_HOME_DL=1（.env）。日志：$LOG_DIR/home-dl.log"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST" "$WORKER_DST"
    echo "✓ 已卸载（venv 与日志保留在 $APP_DIR，如需彻底清理请手动删）"
    ;;
  status)
    launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | grep -E "state|runs" | head -4 || echo "未安装或未加载"
    tail -15 "$LOG_DIR/home-dl.log" 2>/dev/null || echo "（无日志）"
    ;;
  *)
    echo "用法: $0 install | uninstall | status"; exit 1;;
esac
