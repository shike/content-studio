#!/bin/bash
# 发服务器唯一正规流程：rsync 代码 → 重启服务 → 健康检查，三步一体不可拆。
# 背景（2026-09-27 头图乱码复发）：手工 rsync 后忘了重启，旧进程一直跑旧代码——
# 文件落盘在服务启动之后，Python 不会热加载；"同步过"不等于"生效了"。
# 用法：仓库根目录执行 scripts/sync-server.sh（前端已构建则直接同步，否则拒绝）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 部署目标不入库：本机在 scripts/.deploy.env（git 忽略）里配置 CS_SYNC_HOST/CS_SYNC_KEY
[ -f "$ROOT/scripts/.deploy.env" ] && . "$ROOT/scripts/.deploy.env"
HOST="${CS_SYNC_HOST:?缺部署目标：在 scripts/.deploy.env 写 CS_SYNC_HOST=user@server（该文件不入库）}"
KEY="${CS_SYNC_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_ROOT="${CS_SYNC_ROOT:-/srv/content-studio}"   # 覆盖仅用于测试闸门
RSH="ssh -o BatchMode=yes -i $KEY"   # 整体作为一个 -e 参数传给 rsync，别拆散（拆散后 -i 会被 rsync 吃掉、密钥路径被当成同步源）
SSH() { ssh -o BatchMode=yes -i "$KEY" "$@"; }

cd "$ROOT"

if [ ! -f frontend/dist/index.html ]; then
  echo "✗ 缺少 frontend/dist，先构建：cd frontend && npx tsc --noEmit && npm run build" >&2
  exit 1
fi
# dist 落后于前端源码 = 将发布旧界面，拒绝静默同步
if [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit 2>/dev/null)" ]; then
  echo "✗ frontend/dist 比前端源码旧（改了前端没重新 build），先：cd frontend && npm run build" >&2
  exit 1
fi

echo "→ 在途任务检查（有 running/queued 就拒重启：会打断用户正在跑的任务）"
INFLIGHT=$(SSH "$HOST" "sqlite3 $REMOTE_ROOT/data/studio.db \"SELECT count(*) FROM jobs WHERE status IN ('running','queued')\"" 2>/dev/null || echo "?")
if [ "$INFLIGHT" = "?" ]; then
  echo "✗ 取不到生产库在途任务数（ssh/sqlite 异常）——按保守拒绝：不知情就别打断" >&2
  [ "${CS_FORCE_RESTART:-0}" = "1" ] || { echo "  确认无任务在跑再发：CS_FORCE_RESTART=1 scripts/sync-server.sh" >&2; exit 1; }
elif [ "$INFLIGHT" != "0" ] && [ "${CS_FORCE_RESTART:-0}" != "1" ]; then
  echo "✗ 生产库有 $INFLIGHT 个在途任务——重启会打断它们（内核 90s 自动重试，但要白烧一次 LLM 额度）" >&2
  echo "  等任务跑完再发，或确认要打断：CS_FORCE_RESTART=1 scripts/sync-server.sh" >&2
  exit 1
fi

echo "→ rsync backend/app"
rsync -az --delete -e "$RSH" backend/app/ "$HOST:$REMOTE_ROOT/backend/app/"
echo "→ rsync frontend/dist"
rsync -az --delete -e "$RSH" frontend/dist/ "$HOST:$REMOTE_ROOT/frontend/dist/"
for f in backend/pyproject.toml backend/uv.lock; do
  [ -f "$f" ] && rsync -az -e "$RSH" "$f" "$HOST:$REMOTE_ROOT/$f/"
done
# 验收套件与文档也同步：服务器上可能要跑 P1~P7 / 查口径，别让副本漂移
for d in acceptance docs; do
  rsync -az --delete -e "$RSH" "$d/" "$HOST:$REMOTE_ROOT/$d/"
done

echo "→ 重启 content-studio"
SSH "$HOST" "sudo systemctl restart content-studio"

echo "→ 健康检查"
sleep 3
code=$(SSH "$HOST" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8100/api/health" || echo 000)
if [ "$code" = "200" ]; then
  echo "✓ 同步并重启完成，服务健康"
else
  echo "✗ 健康检查返回 $code，查日志：$RSH $HOST 'journalctl -u content-studio -n 50'" >&2
  exit 1
fi
