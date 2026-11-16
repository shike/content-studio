#!/bin/bash
# 发服务器唯一正规流程：rsync 代码 → 在途检查 → 迁移前备份 → Alembic 升级 →
# schema 审计 → 重启 → 健康检查。三步一体不可拆。
# 背景（2026-09-27 头图乱码复发）：手工 rsync 后忘了重启，旧进程一直跑旧代码——
# 文件落盘在服务启动之后，Python 不会热加载；"同步过"不等于"生效了"。
# 用法：仓库根目录执行 scripts/sync-server.sh（前端已构建则直接同步，否则拒绝）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 部署目标不入库：本机在 scripts/.deploy.env（git 忽略）里配置 CS_SYNC_HOST/CS_SYNC_KEY
[ -f "$ROOT/scripts/.deploy.env" ] && . "$ROOT/scripts/.deploy.env"
HOST="${CS_SYNC_HOST:?缺部署目标：在 scripts/.deploy.env 写 CS_SYNC_HOST=user@server（该文件不入库）}"
KEY="${CS_SYNC_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_ROOT="${CS_SYNC_ROOT:-/srv/content-studio}"   # 覆盖仅用于备用环境/手工测试
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

echo "→ rsync backend/app"
rsync -az --delete -e "$RSH" backend/app/ "$HOST:$REMOTE_ROOT/backend/app/"
echo "→ rsync frontend/dist"
rsync -az --delete -e "$RSH" frontend/dist/ "$HOST:$REMOTE_ROOT/frontend/dist/"
# 文档也同步：服务器上可能要查口径，别让副本漂移
rsync -az --delete -e "$RSH" docs/ "$HOST:$REMOTE_ROOT/docs/"
# Alembic 配置与迁移脚本：单独同步（backend/app 之外）
rsync -az -e "$RSH" backend/alembic.ini "$HOST:$REMOTE_ROOT/backend/alembic.ini"
rsync -az -e "$RSH" backend/requirements.txt "$HOST:$REMOTE_ROOT/backend/requirements.txt"
rsync -az --delete -e "$RSH" backend/migrations/ "$HOST:$REMOTE_ROOT/backend/migrations/"

# 远端 python 优先用仓库 venv；退回 python3（需系统装有 sqlalchemy/psycopg）
AUDIT_PY="$REMOTE_ROOT/backend/.venv/bin/python"
SSH "$HOST" "test -x $AUDIT_PY" || AUDIT_PY=python3
REMOTE_PY="cd $REMOTE_ROOT && PYTHONPATH=$REMOTE_ROOT/backend $AUDIT_PY"
if ! SSH "$HOST" "$AUDIT_PY -c 'import psycopg, alembic'"; then
  echo "✗ 远端缺 psycopg/alembic：先 uv pip install -r $REMOTE_ROOT/backend/requirements.txt" >&2
  exit 1
fi

echo "→ 在途任务检查（有 running/queued 就拒重启：会打断用户正在跑的任务）"
INFLIGHT=$(SSH "$HOST" "$REMOTE_PY -m app.ops inflight" 2>/dev/null | tr -dc '0-9' || true)
if [ -z "$INFLIGHT" ]; then
  echo "✗ 取不到生产库在途任务数（DATABASE_URL/psycopg/远端库异常）——按保守拒绝：不知情就别打断" >&2
  [ "${CS_FORCE_RESTART:-0}" = "1" ] || { echo "  确认无任务在跑再发：CS_FORCE_RESTART=1 scripts/sync-server.sh" >&2; exit 1; }
elif [ "$INFLIGHT" != "0" ] && [ "${CS_FORCE_RESTART:-0}" != "1" ]; then
  echo "✗ 生产库有 $INFLIGHT 个在途任务——重启会打断它们（内核 90s 自动重试，但要白烧一次 LLM 额度）" >&2
  echo "  等任务跑完再发，或确认要打断：CS_FORCE_RESTART=1 scripts/sync-server.sh" >&2
  exit 1
fi

echo "→ 迁移前 pg_dump 快照（保留 5 份）"
if ! SSH "$HOST" "$REMOTE_PY -m app.ops backup pre-migrate 5"; then
  echo "✗ 迁移前备份失败：中止发版（没有安全网不动库）" >&2
  exit 1
fi

echo "→ Alembic 升级（backend/migrations → head）"
if ! SSH "$HOST" "cd $REMOTE_ROOT && PYTHONPATH=$REMOTE_ROOT/backend $AUDIT_PY -m alembic -c backend/alembic.ini upgrade head"; then
  echo "✗ Alembic 升级失败：代码已在服务器、旧进程继续服务；先修迁移再重跑本脚本" >&2
  exit 1
fi

echo "→ schema 审计（新代码模型 ↔ 生产库，只读）"
if ! SSH "$HOST" "$REMOTE_PY -m app.schema_audit --quiet"; then
  echo "✗ schema 漂移：中止重启（代码已同步、旧进程继续服务，生产零影响）。修漂移后重跑本脚本；漂移明细：" >&2
  SSH "$HOST" "$REMOTE_PY -m app.schema_audit" >&2 || true
  exit 1
fi
echo "✓ schema 审计通过（模型与生产库一致）"

echo "→ 重启 content-studio"
SSH "$HOST" "sudo systemctl restart content-studio"

echo "→ 健康检查"
sleep 3
HEALTH_PORT="${CS_HEALTH_PORT:-8100}"   # 与部署端口一致时无需设置；改端口部署须同步
code=$(SSH "$HOST" "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${HEALTH_PORT}/api/health" || echo 000)
if [ "$code" = "200" ]; then
  echo "✓ 同步并重启完成，服务健康"
else
  echo "✗ 健康检查返回 $code，查日志：$RSH $HOST 'journalctl -u content-studio -n 50'" >&2
  exit 1
fi
