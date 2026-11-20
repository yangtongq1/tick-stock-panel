# TSP 三服务启动脚本（aksource 3021 + 后端 3018, 独立进程, 不随终端退出）
# 用途: 开机自启 / 手动双击启动; 路径自适应 (git clone 到任意目录均可)
# 后端 3018 已挂载 frontend/dist 构建产物 → 一个地址同时出页面与 API,
# 不再需要独立的 3011 前端 dev server (需 Node 且与 3018 页面重复)。
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
$logDir = "$Root\data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# ---- 免费数据源 3021 (新浪/腾讯/东财聚合: 实时/分钟/板块/财务/除权因子) ----
# 复用 backend venv (fastapi/uvicorn/pandas/httpx 齐全, requests 缺失时走 httpx 兼容层)
# 注: 用 cmd /c start 派生分离进程——避开 Start-Process 在大小写重复 proxy
#     变量环境下报 "已添加项" 字典键冲突的问题; 分离进程不随终端会话退出
$aksRunning = Get-NetTCPConnection -State Listen -LocalPort 3021 -ErrorAction SilentlyContinue
if ($aksRunning) {
  Write-Host "[tsp] 数据源 3021 已在运行"
} else {
  Write-Host "[tsp] 启动免费数据源 3021 ..."
  $py = "$Root\backend\.venv\Scripts\python.exe"
  Push-Location "$Root\aksource"
  cmd /c "start /min `"aksource`" cmd /c `"`"$py`" server.py >> `"$logDir\aksource.log`" 2>&1`"" | Out-Null
  Pop-Location
}

# ---- 后端 3018 ----
# host 0.0.0.0: 局域网可访问 (10.2.207.117 QUANT TERMINAL 对接 TSP API)
# 启动方式与上方 aksource 一致 (cmd /c start 派生分离进程): Start-Process 的
# -RedirectStandard* 在大小写重复代理环境变量 (HTTPS_PROXY+https_proxy 并存,
# 装代理的 Windows 常见) 下抛 "已添加项" 字典键冲突 → 后端静默不启动。
$backendRunning = Get-NetTCPConnection -State Listen -LocalPort 3018 -ErrorAction SilentlyContinue
if ($backendRunning) {
  Write-Host "[tsp] 后端 3018 已在运行"
} else {
  Write-Host "[tsp] 启动后端 3018 ..."
  $py = "$Root\backend\.venv\Scripts\python.exe"
  $envf = "$Root\.env"
  Push-Location "$Root\backend"
  cmd /c "start /min `"tsp-backend`" cmd /c `"`"$py`" -m uvicorn app.main:app --env-file `"$envf`" --host 0.0.0.0 --port 3018 >> `"$logDir\backend.log`" 2>&1`"" | Out-Null
  Pop-Location
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " 系统入口  http://localhost:3018  (页面+API)"
Write-Host " 数据源    http://localhost:3021  (免费源)"
Write-Host " 局域网    本机 IP:3018"
Write-Host "======================================" -ForegroundColor Green
