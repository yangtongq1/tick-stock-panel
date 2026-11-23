#!/usr/bin/env bash
# tick-stock-panel 一键部署脚本 (Linux / macOS / Git Bash)
# 目标: git clone 后只需这一条命令 → 浏览器打开 http://localhost:3018 即是完整系统
# 零前置依赖: uv 缺失自动装, Python 3.12 由 uv 托管下载 (系统无需装过 Python);
# 前端构建产物 (frontend/dist) 与行情数据均随仓库分发 → 无需 Node。
#
# 用法:  ./quick-start.sh           首次/日常启动 (幂等)
#        ./quick-start.sh rebuild   重建前端 (需要 Node 20+; 仅改前端代码时用)
set -e
cd "$(dirname "$0")"
ROOT="$(pwd)"
REBUILD="${1:-}"

echo "== 1/6 uv + Python =="
# uv sync --python 3.12 自动下载托管解释器 → 系统 Python 版本无所谓
if ! command -v uv >/dev/null; then
  echo "安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null || { echo "uv 安装失败, 请重开终端再跑一次"; exit 1; }
fi
echo "uv 就绪: $(uv --version)"

echo "== 2/6 配置 =="
[ -f .env ] || cp .env.example .env

echo "== 3/6 后端依赖 =="
(cd backend && uv sync --python 3.12)

PY=backend/.venv/bin/python
[ -x "$PY" ] || PY=backend/.venv/Scripts/python.exe   # Git Bash on Windows

echo "== 4/6 前端 =="
if [ "$REBUILD" = "rebuild" ] || [ ! -f frontend/dist/index.html ]; then
  command -v node >/dev/null || { echo "重建前端需要 Node 20+; 不传 rebuild 参数可直接用仓库自带构建产物"; exit 1; }
  if ! command -v pnpm >/dev/null; then
    echo "安装 pnpm (官方独立安装器, 不依赖 npm)..."
    curl -fsSL https://get.pnpm.io/install.sh | sh - \
      && export PATH="$HOME/.local/share/pnpm:$PATH" \
      && command -v pnpm >/dev/null || npm i -g pnpm
  fi
  (cd frontend && pnpm install --silent && pnpm build)
else
  echo "使用仓库自带构建产物, 跳过 (重建: ./quick-start.sh rebuild, 需 Node)"
fi

echo "== 5/6 启动服务 =="
mkdir -p data/logs

# 存活探测: 任何 HTTP 响应 (含 4xx/5xx) 都算活着——不能因上游数据源抖动误判
alive() { curl -s --max-time 2 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null | grep -q .; }

# 后端 3018 先起 (不依赖 3021: 拉取失败自动退避重试 30s→5min, 见 ext_pull)
if alive http://127.0.0.1:3018/api/capabilities; then
  echo "[tsp] 后端 3018 已在运行"
else
  echo "[tsp] 启动后端 3018 ..."
  (cd backend && nohup "$ROOT/$PY" -m uvicorn app.main:app --env-file ../.env \
     --host 0.0.0.0 --port 3018 >> "$ROOT/data/logs/backend.log" 2>&1 &)
fi

# 免费数据源 aksource 3021 (板块/财务/除权/实时/分钟; 复用 backend venv)
if alive http://127.0.0.1:3021/; then
  echo "[tsp] 数据源 3021 已在运行"
else
  echo "[tsp] 启动免费数据源 3021 ..."
  (cd aksource && nohup "$ROOT/$PY" server.py >> "$ROOT/data/logs/aksource.log" 2>&1 &)
fi

echo "== 6/6 等待就绪 =="
printf "[tsp] 后端首次启动需数据预热 (~30-90s)"
ready=0
for i in $(seq 1 60); do
  if alive http://127.0.0.1:3018/api/capabilities; then ready=1; break; fi
  printf .
  sleep 2
done
echo ""
if [ "$ready" != "1" ]; then
  echo "警告: 后端 120s 未就绪"
  echo " 排查: data/logs/backend.log (启动日志)"
  echo " 重试: 再跑一次本脚本 (幂等)"
  exit 1
fi

# 自检: 扩展数据表 (涨停池/连板梯队/概念/行业/资金流)
EXT_COUNT=$(curl -s --max-time 5 http://127.0.0.1:3018/api/ext-data 2>/dev/null | grep -o '"id"' | wc -l | tr -d ' ')
[ -n "$EXT_COUNT" ] && [ "$EXT_COUNT" -gt 0 ] 2>/dev/null && echo "[tsp] 扩展数据表: $EXT_COUNT 张"

echo ""
echo "======================================"
echo " 系统已就绪 → http://localhost:3018"
echo " (页面+API 同一地址; 局域网用本机 IP:3018)"
echo ""
echo " 已自动包含:"
echo "   行情数据 (随仓库自带全量历史, 每日自动增量+断档补拉)"
echo "   连板梯队/涨停池/资金流 (启动自动播种, 失败自动退避重试)"
echo "   概念/行业板块 (东财聚合, 每日自动更新)"
echo "   模拟盘持仓 (随仓库分发, 直接可看)"
echo ""
echo " 数据源 http://localhost:3021 (免费, 无需账号)"
echo " 日志: data/logs/ (backend / aksource)"
echo " 停止: ./stop.sh 或 pkill -f 'uvicorn app.main:app'"
echo "======================================"

# 打开浏览器 (有 GUI 时)
(command -v xdg-open >/dev/null && xdg-open http://localhost:3018 >/dev/null 2>&1) ||
(command -v open >/dev/null && open http://localhost:3018 >/dev/null 2>&1) || true
