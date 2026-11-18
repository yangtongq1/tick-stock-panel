#!/usr/bin/env bash
# tick-stock-panel 一键部署脚本 (Linux / macOS / Git Bash)
# 目标: git clone 后只需这一条命令 → 浏览器打开 http://localhost:3018 即是完整系统
# (行情数据随仓库分发, 无需拉取; 前端构建一次, 之后不再需要 Node 常驻)
#
# 用法:  ./quick-start.sh           首次/日常启动 (幂等: 已装依赖/已构建自动跳过)
#        ./quick-start.sh rebuild   强制重建前端
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
REBUILD="${1:-}"

echo "== 1/5 依赖检查 =="
command -v python3 >/dev/null || { echo "缺少 Python 3.11+"; exit 1; }
command -v node >/dev/null || { echo "缺少 Node 20+"; exit 1; }

# uv (Python 依赖管理)
if ! command -v uv >/dev/null; then
  echo "安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# pnpm (仅构建前端时需要, 不常驻)
if ! command -v pnpm >/dev/null; then
  echo "安装 pnpm..."
  npm i -g pnpm
fi

echo "== 2/5 配置 =="
[ -f .env ] || cp .env.example .env

echo "== 3/5 后端依赖 =="
(cd backend && uv sync)

echo "== 4/5 前端构建 (一次, 之后不再需要 Node) =="
if [ "$REBUILD" = "rebuild" ] || [ ! -f frontend/dist/index.html ]; then
  (cd frontend && pnpm install --silent && pnpm build)
else
  echo "已构建, 跳过 (强制重建: ./quick-start.sh rebuild)"
fi

# ---- 5/5 三件套: aksource 3021 + 后端 3018 (直接伺服前端) ----
mkdir -p data/logs

PY=backend/.venv/bin/python
[ -x "$PY" ] || PY=backend/.venv/Scripts/python.exe   # Git Bash on Windows

# 免费数据源 aksource (板块/财务/除权/实时/分钟; 复用 backend venv)
# 探测根路径: 任何 HTTP 响应 (含 4xx/5xx) 都算存活——不能因上游数据源抖动
# 误判 "不在运行" 而重复起服务
if curl -s --max-time 2 -o /dev/null http://127.0.0.1:3021/; then
  echo "[tsp] 数据源 3021 已在运行"
else
  echo "[tsp] 启动免费数据源 3021 ..."
  (cd aksource && nohup "$ROOT/$PY" server.py >> "$ROOT/data/logs/aksource.log" 2>&1 &)
  sleep 2
fi

# 后端 3018: 已挂载 frontend/dist → 一个地址同时出页面与 API
if curl -s --max-time 2 -o /dev/null http://127.0.0.1:3018/api/capabilities; then
  echo "[tsp] 后端 3018 已在运行"
else
  echo "[tsp] 启动后端 3018 ..."
  (cd backend && nohup "$ROOT/$PY" -m uvicorn app.main:app --env-file ../.env \
     --host 0.0.0.0 --port 3018 >> "$ROOT/data/logs/backend.log" 2>&1 &)
fi

# 等后端就绪 (首次启动要做数据预热, 最多 90s)
printf "[tsp] 等待后端就绪"
ready=0
for i in $(seq 1 45); do
  if curl -s --max-time 2 -o /dev/null http://127.0.0.1:3018/api/capabilities; then ready=1; break; fi
  printf .
  sleep 2
done
echo ""
[ "$ready" = "1" ] || echo "警告: 后端 90s 未就绪, 请查看 data/logs/backend.log"

echo ""
echo "======================================"
echo " 系统已就绪 → http://localhost:3018"
echo " (页面+API 同一地址; 局域网用本机 IP:3018)"
echo " 数据源 http://localhost:3021 (免费: 实时/分钟/板块/财务)"
echo " 行情数据随仓库自带, 开箱即用"
echo " 停止: pkill -f uvicorn / pkill -f server.py 即可"
echo "======================================"

# 打开浏览器 (有 GUI 时)
(command -v xdg-open >/dev/null && xdg-open http://localhost:3018 >/dev/null 2>&1) ||
(command -v open >/dev/null && open http://localhost:3018 >/dev/null 2>&1) || true
