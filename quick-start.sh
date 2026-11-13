#!/usr/bin/env bash
# tick-stock-panel 一键部署脚本 (Linux / macOS / Git Bash)
# 用法: ./quick-start.sh
set -e
cd "$(dirname "$0")"

echo "== 1/5 依赖检查 =="
command -v python3 >/dev/null || { echo "缺少 Python 3.11+"; exit 1; }
command -v node >/dev/null || { echo "缺少 Node 20+"; exit 1; }

# uv
if ! command -v uv >/dev/null; then
  echo "安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# pnpm
if ! command -v pnpm >/dev/null; then
  echo "安装 pnpm..."
  npm i -g pnpm
fi

echo "== 2/5 配置 =="
[ -f .env ] || cp .env.example .env

echo "== 3/5 后端依赖 =="
cd backend
uv sync 2>/dev/null || uv pip install -e .
cd ..

echo "== 4/5 前端依赖 =="
cd frontend
pnpm install
cd ..

echo "== 5/5 启动 =="
if command -v ./dev.sh >/dev/null && [ ! -d "/proc/version" ] || uname -s | grep -qv MINGW; then
  ./dev.sh
else
  # Git Bash / 无 dev.sh 环境分起前后端
  echo "分起前后端..."
  (cd backend && ./.venv/bin/python -m uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 3018 &
   cd ../frontend && npx pnpm dev &)
fi

echo ""
echo "======================================"
echo " 后端  http://localhost:3018"
echo " 前端  http://localhost:3011"
echo " 首次拉数据: curl -X POST http://127.0.0.1:3018/api/pipeline/run"
echo " (约 6 分钟, 5892 只 A 股 × 近 1 年日K, 免费)"
echo "======================================"
