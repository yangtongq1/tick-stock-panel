# tick-stock-panel 一键部署脚本 (Windows PowerShell)
# 目标: git clone 后只需这一条命令 → 浏览器打开 http://localhost:3018 即是完整系统
# 前端构建产物 (frontend/dist) 与行情数据均随仓库分发 → 运行只需 Python, 无需 Node
#
# 用法:  .\quick-start.ps1          首次/日常启动 (幂等)
#        .\quick-start.ps1 -Rebuild 重建前端 (需要 Node 20+; 仅改前端代码时用)
param([switch]$Rebuild)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Root = (Get-Location).Path

function Test-Http($Url) {
  # 只要有 HTTP 响应 (含 4xx/5xx) 即认为服务存活——不能因上游数据源抖动误判
  # "不在运行" 而重复起服务 (曾因 /boards 接口 500 误判重复拉起)
  try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3 | Out-Null; $true }
  catch { $_.Exception.Response -ne $null }
}

# PS 5.1 下原生命令 stderr 输出会被 $ErrorActionPreference=Stop 拦成错误
# (uv/pnpm 常把进度写到 stderr)。经 cmd /c 包一层后 stderr 不会触发 PowerShell 异常,
# 由 $LASTEXITCODE 判断成败。
function Invoke-Native($File, $ArgList) {
  $args = ($ArgList | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join ' '
  cmd /c "`"$File`" $args 2>&1" | ForEach-Object { "$_" }
  if ($LASTEXITCODE -ne 0) { throw "命令失败 (exit $LASTEXITCODE): $File $args" }
}

Write-Host "== 1/5 依赖检查 ==" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "缺少 Python 3.11+ (winget install Python.Python.3.12)" }

# uv (Python 依赖管理)
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "安装 uv..."
  Invoke-Native winget @('install', '--id=astral-sh.uv', '--accept-source-agreements', '--accept-package-agreements', '--silent')
  $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv 安装后不可用, 请新开终端重跑" }
}

Write-Host "== 2/5 配置 ==" -ForegroundColor Cyan
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

Write-Host "== 3/5 后端依赖 ==" -ForegroundColor Cyan
Push-Location backend
Invoke-Native uv @('sync')
Pop-Location

Write-Host "== 4/5 前端 ==" -ForegroundColor Cyan
if ($Rebuild -or -not (Test-Path "frontend\dist\index.html")) {
  # 重建前端才需要 Node 工具链 (npm 安装路径多样且易损坏, pnpm 官方独立安装器最稳)
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw "重建前端需要 Node 20+ (winget install OpenJS.NodeJS.LTS); 或不传 -Rebuild 直接用仓库自带构建产物" }
  if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host "安装 pnpm (官方独立安装器, 不依赖 npm)..."
    Invoke-Native powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
      "Invoke-WebRequest -Uri https://get.pnpm.io/install.ps1 -UseBasicParsing | Invoke-Expression")
    $env:PATH = "$env:USERPROFILE\AppData\Local\pnpm;$env:PATH"
    if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
      Write-Host "独立安装器未生效, 回退 npm i -g pnpm..."
      Invoke-Native npm @('i', '-g', 'pnpm')
    }
  }
  Push-Location frontend
  Invoke-Native pnpm @('install', '--silent')
  Invoke-Native pnpm @('build')
  Pop-Location
} else {
  Write-Host "使用仓库自带构建产物, 跳过 (重建: .\quick-start.ps1 -Rebuild, 需 Node)"
}

# ---- 5/5 三件套: aksource 3021 + 后端 3018 (直接伺服前端) ----
$logDir = "$Root\data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# 免费数据源 aksource (板块/财务/除权/实时/分钟; 复用 backend venv)
# cmd /c start 派生分离进程: 不随终端退出, 且避开 Start-Process 在大小写重复
# proxy 环境变量下报 "已添加项" 字典键冲突的问题
if (Test-Http "http://127.0.0.1:3021/") {
  Write-Host "[tsp] 数据源 3021 已在运行"
} else {
  Write-Host "[tsp] 启动免费数据源 3021 ..."
  $py = "$Root\backend\.venv\Scripts\python.exe"
  Push-Location "$Root\aksource"
  cmd /c "start /min `"aksource`" cmd /c `"`"$py`" server.py >> `"$logDir\aksource.log`" 2>&1`"" | Out-Null
  Pop-Location
  Start-Sleep -Seconds 2
}

# 后端 3018: host 0.0.0.0 局域网可访问 (QUANT TERMINAL 等对接);
# 已挂载 frontend/dist → 一个地址同时出页面与 API
# 启动方式: cmd /c start 派生分离进程 (与上方 aksource 一致)。
# 不用 Start-Process: 它在大小写重复的代理环境变量下
# (HTTPS_PROXY+https_proxy 同时存在, 常见于装了代理的 Windows) 会抛
# "已添加项" 字典键冲突异常 → 后端静默不启动, 页面空转无 API。
if (Test-Http "http://127.0.0.1:3018/api/capabilities") {
  Write-Host "[tsp] 后端 3018 已在运行"
} else {
  Write-Host "[tsp] 启动后端 3018 ..."
  $py = "$Root\backend\.venv\Scripts\python.exe"
  $envf = "$Root\.env"
  cmd /c "start /min `"tsp-backend`" cmd /c `"`"$py`" -m uvicorn app.main:app --env-file `"$envf`" --host 0.0.0.0 --port 3018 >> `"$logDir\backend.log`" 2>&1`"" | Out-Null
  Start-Sleep -Seconds 2
}

# 等后端就绪 (首次启动要做数据预热, 最多 90s)
Write-Host "[tsp] 等待后端就绪" -NoNewline
$ready = $false
foreach ($i in 1..45) {
  if (Test-Http "http://127.0.0.1:3018/api/capabilities") { $ready = $true; break }
  Write-Host "." -NoNewline
  Start-Sleep -Seconds 2
}
Write-Host ""
if (-not $ready) { Write-Warning "后端 90s 未就绪, 请查看 data\logs\backend.err.log" }

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " 系统已就绪 → http://localhost:3018"
Write-Host " (页面+API 同一地址; 局域网用本机 IP:3018)"
Write-Host " 数据源 http://localhost:3021 (免费: 实时/分钟/板块/财务)"
Write-Host " 行情+前端构建产物随仓库自带, 开箱即用"
Write-Host " 停止: 任务管理器结束 python (uvicorn/server.py) 即可"
Write-Host "======================================" -ForegroundColor Green

# 打开浏览器
Start-Process "http://localhost:3018"
