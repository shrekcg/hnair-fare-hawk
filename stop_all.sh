#!/usr/bin/env bash
# 停止海航监控双进程（按 PID 文件）
set -euo pipefail
cd "$(dirname "$0")"

for name in daemon web; do
  if [ -f "$name.pid" ]; then
    pid=$(cat "$name.pid")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "$name 已停止 (PID $pid)"
    else
      echo "$name 未在运行（PID 文件残留已清理）"
    fi
    rm -f "$name.pid"
  else
    echo "$name 无 PID 文件，跳过"
  fi
done