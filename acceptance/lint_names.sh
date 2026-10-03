#!/bin/bash
# 未定义名门禁（F821）：NameError 类 bug 的静态防线。
# 背景：radar 挖话题/低粉高赞因漏 import launch 带病运行数日（任务"成功"但结果恒空）；
# scripts 删除接口丢过整行 for 循环头。这些 py_compile 都不报——只有 F821 能拦。
# 用法：仓库根执行 acceptance/lint_names.sh（需要 uv/uvx，无网络时跳过不阻塞）
set -uo pipefail
cd "$(dirname "$0")/.."
if ! command -v uvx >/dev/null 2>&1; then
  echo "SKIP：无 uvx，跳过静态扫描（建议安装 uv）"; exit 0
fi
uvx ruff check --select F821 backend/app
code=$?
if [ $code -ne 0 ]; then
  echo "FAIL：存在未定义名（F821）——提交/部署前必须修掉" >&2
  exit 1
fi
echo "PASS：无未定义名"
