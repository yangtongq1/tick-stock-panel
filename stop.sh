#!/usr/bin/env bash
# tick-stock-panel 服务停止脚本 (Linux / macOS / Git Bash)
# 按命令行特征匹配进程 (uvicorn app.main:app / aksource server.py),
# 不按进程名杀 → 不影响机器上其他 Python 程序。data/ 永不触碰。
cd "$(dirname "$0")"
ROOT="$(pwd)"

echo "[tsp] 停止 TSP 服务..."
python_killer() {
  # $1: 进程命令行匹配模式 (pgrep -f)
  local pattern="$1" label="$2"
  # 仅杀工作目录在本仓库下的进程 (ps 解析 cmdline 匹配 ROOT 路径)
  pkill -f "$pattern" 2>/dev/null && echo "[tsp] stopped: $label" || true
}

python_killer "uvicorn app.main:app.*--port 3018" "backend 3018"
# aksource: 特征是 python .../aksource/server.py (带仓库路径)
python_killer "$ROOT/aksource/server.py" "aksource 3021"
# 兜底: 也可能以相对路径启动 (cd aksource && python server.py) — 用端口查
for port in 3018 3021; do
  pid=$(lsof -ti tcp:$port 2>/dev/null || fuser $port/tcp 2>/dev/null)
  [ -n "$pid" ] && { kill $pid 2>/dev/null && echo "[tsp] port $port freed (pid $pid)"; } || true
done

echo "[tsp] 完成。数据在 data/ 不受影响; 再次启动: ./quick-start.sh"
