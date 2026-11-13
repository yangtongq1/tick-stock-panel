# TSP 双服务启动脚本（前端 3011 + 后端 3018，独立进程，不随终端退出）
# 用途: 开机自启 / 手动双击启动
$ErrorActionPreference = 'Stop'
$Root = 'C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel'
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
$logDir = "$Root\data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# ---- AkShare 免费数据源 3021 ----
$aksRunning = Get-NetTCPConnection -State Listen -LocalPort 3021 -ErrorAction SilentlyContinue
if ($aksRunning) {
  Write-Host "[tsp] 数据源 3021 已在运行"
} else {
  Write-Host "[tsp] 启动 AkShare 数据源 3021 ..."
  Start-Process -WindowStyle Hidden -FilePath 'powershell.exe' `
    -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',
      "Set-Location '$Root\aksource'; python server.py *> '$logDir\aksource.log'"
}

# ---- 后端 3018 ----
$backendRunning = Get-NetTCPConnection -State Listen -LocalPort 3018 -ErrorAction SilentlyContinue
if ($backendRunning) {
  Write-Host "[tsp] 后端 3018 已在运行"
} else {
  Write-Host "[tsp] 启动后端 3018 ..."
  Start-Process -WindowStyle Hidden -FilePath "$Root\backend\.venv\Scripts\python.exe" `
    -ArgumentList '-m','uvicorn','app.main:app','--env-file',"$Root\.env",'--host','127.0.0.1','--port','3018' `
    -WorkingDirectory "$Root\backend" `
    -RedirectStandardOutput "$logDir\backend.log" -RedirectStandardError "$logDir\backend.err.log"
}

# ---- 前端 3011 ----
$frontendRunning = Get-NetTCPConnection -State Listen -LocalPort 3011 -ErrorAction SilentlyContinue
if ($frontendRunning) {
  Write-Host "[tsp] 前端 3011 已在运行"
} else {
  Write-Host "[tsp] 启动前端 3011 ..."
  Start-Process -WindowStyle Hidden -FilePath 'powershell.exe' `
    -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',
      "Set-Location '$Root\frontend'; npx pnpm dev 2>&1 | Out-File -Append '$logDir\frontend.log'" `
    -WorkingDirectory "$Root\frontend"
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " 前端  http://localhost:3011  (主入口)"
Write-Host " 后端  http://localhost:3018  (API)"
Write-Host "======================================" -ForegroundColor Green
