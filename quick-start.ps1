# tick-stock-panel 一键部署脚本 (Windows PowerShell)
# 用法: .\quick-start.ps1
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "== 1/5 依赖检查 ==" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "缺少 Python 3.11+" }
if (-not (Get-Command node -ErrorAction SilentlyContinue))   { throw "缺少 Node 20+" }

# uv
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "安装 uv..."
  winget install --id=astral-sh.uv --accept-source-agreements --accept-package-agreements --silent
  $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
}

# pnpm
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  Write-Host "安装 pnpm..."
  npm i -g pnpm
}

Write-Host "== 2/5 配置 ==" -ForegroundColor Cyan
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

Write-Host "== 3/5 后端依赖 ==" -ForegroundColor Cyan
Push-Location backend
uv sync
Pop-Location

Write-Host "== 4/5 前端依赖 ==" -ForegroundColor Cyan
Push-Location frontend
pnpm install
Pop-Location

Write-Host "== 5/5 启动 ==" -ForegroundColor Cyan
Write-Host "交互模式: .\dev.ps1"
Write-Host "后台模式: 分起前后端 (见 QUICKSTART.md)"
Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " 后端  http://localhost:3018"
Write-Host " 前端  http://localhost:3011"
Write-Host " 首次拉数据: curl -X POST http://127.0.0.1:3018/api/pipeline/run"
Write-Host " (约 6 分钟, 5892 只 A 股 x 近 1 年日K, 免费)"
Write-Host "======================================" -ForegroundColor Green

# 默认直接进入交互启动
& .\dev.ps1
