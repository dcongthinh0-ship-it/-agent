#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
cd "$SCRIPT_DIR/backend"
if [[ ! -f "$SCRIPT_DIR/backend/.env" ]]; then
  print "缺少独立工作站 backend/.env，请先配置本机样例数据库连接。"
  exit 1
fi
uv sync --extra dev
if [[ ! -d "$SCRIPT_DIR/frontend/node_modules" ]]; then
  cd "$SCRIPT_DIR/frontend"
  npm ci
  cd "$SCRIPT_DIR/backend"
fi
exec .venv/bin/python "$SCRIPT_DIR/scripts/start_demo.py"
