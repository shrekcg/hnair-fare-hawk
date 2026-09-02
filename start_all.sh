#!/usr/bin/env bash
# 启动海航监控双进程：daemon + Web 页面（Web 仅监听本机 127.0.0.1，避免局域网暴露配置与 SendKey）
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "未找到 .venv/bin/python，请先安装依赖（python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）"
  exit 1
fi

# 1) daemon 常驻抓价
if [ -f daemon.pid ] && kill -0 "$(cat daemon.pid)" 2>/dev/null; then
  echo "daemon 已在运行 (PID $(cat daemon.pid))"
else
  nohup env -u ELECTRON_RUN_AS_NODE "$PY" daemon.py >> run_log.txt 2>&1 &
  echo $! > daemon.pid
  echo "daemon 已启动 (PID $(cat daemon.pid))"
fi

# 2) Web 前端（仅本机可访问）
if [ -f web.pid ] && kill -0 "$(cat web.pid)" 2>/dev/null; then
  echo "Web 已在运行 (PID $(cat web.pid))"
else
  nohup env -u ELECTRON_RUN_AS_NODE "$PY" -m streamlit run app.py \
    --server.headless true \
    --server.port 8501 \
    --server.address 127.0.0.1 \
    >> web_stdout.log 2>&1 &
  echo $! > web.pid
  echo "Web 已启动 (PID $(cat web.pid))，地址 http://127.0.0.1:8501"
fi