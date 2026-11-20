# TSP 后端专用启动脚本 — 只管把 3018 拉起来 (幂等, 顺带确保数据源 3021)
# 场景: start.bat 跑完但后端没起来 / 后端崩溃后快速拉起 / 日常只想重启后端
# 自愈: venv 缺失自动 uv sync (uv 缺失自动装) → 新 clone 的机器双击本脚本也能用
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Root = (Get-Location).Path
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"

function Invoke-Native($File, $ArgList) {
  # PS 5.1 下原生命令 stderr 会被 $ErrorActionPreference=Stop 拦成错误, 经 cmd /c
  # 包一层后由 $LASTEXITCODE 判断成败 (uv/winget 常把进度写到 stderr)
  $args = ($ArgList | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join ' '
  cmd /c "`"$File`" $args 2>&1" | ForEach-Object { "$_" }
  if ($LASTEXITCODE -ne 0) { throw "命令失败 (exit $LASTEXITCODE): $File $args" }
}

$logDir = "$Root\data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# ---- 1. 端口判定: 3018 已被占用 (运行中/启动中) → 不重复拉起 ----
$port = Get-NetTCPConnection -State Listen -LocalPort 3018 -ErrorAction SilentlyContinue
if ($port) {
  Write-Host "[tsp] 后端 3018 已在运行" -ForegroundColor Green
} else {
  # 依赖自愈: venv 缺失则 uv sync; uv 缺失则先装 (winget 优先, 官方安装器兜底)
  $py = "$Root\backend\.venv\Scripts\python.exe"
  if (-not (Test-Path $py)) {
    Write-Host "[tsp] 后端依赖未安装, 自动安装 (首次约 1-2 分钟)..."
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
      if (Get-Command winget -ErrorAction SilentlyContinue) {
        Invoke-Native winget @('install', '--id=astral-sh.uv', '--accept-source-agreements', '--accept-package-agreements', '--silent')
      } else {
        Invoke-Native powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
          "Invoke-WebRequest -Uri https://astral.sh/uv/install.ps1 -UseBasicParsing | Invoke-Expression")
      }
      $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
      if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv 安装失败" }
    }
    Push-Location "$Root\backend"
    Invoke-Native uv @('sync', '--python', '3.12')
    Pop-Location
  }
  if (-not (Test-Path "$Root\.env")) { Copy-Item "$Root\.env.example" "$Root\.env" }

  # ---- 2. 拉起后端 (cmd start 派生分离进程: 不随窗口关闭而终止,
  #      且避开 Start-Process 在大小写重复代理环境变量下的字典键冲突坑) ----
  Write-Host "[tsp] 启动后端 3018 ..."
  $envf = "$Root\.env"
  Push-Location "$Root\backend"
  cmd /c "start /min `"tsp-backend`" cmd /c `"`"$py`" -m uvicorn app.main:app --env-file `"$envf`" --host 0.0.0.0 --port 3018 >> `"$logDir\backend.log`" 2>&1`"" | Out-Null
  Pop-Location
}

# ---- 3. 顺带确保数据源 3021 (后端拉板块/涨停池/资金流要用; 已在跑则跳过) ----
if (-not (Get-NetTCPConnection -State Listen -LocalPort 3021 -ErrorAction SilentlyContinue)) {
  Write-Host "[tsp] 数据源 3021 未运行, 一并启动..."
  $py = "$Root\backend\.venv\Scripts\python.exe"
  if (Test-Path $py) {
    Push-Location "$Root\aksource"
    cmd /c "start /min `"aksource`" cmd /c `"`"$py`" server.py >> `"$logDir\aksource.log`" 2>&1`"" | Out-Null
    Pop-Location
  }
}

# ---- 4. 等就绪 (首次启动需数据预热 30-90s; 已在运行则 1-2s 通过) ----
$ProgressPreference = 'SilentlyContinue'  # 关掉 Invoke-WebRequest 进度条污染输出
Write-Host "[tsp] 等待后端就绪" -NoNewline
$ready = $false
foreach ($i in 1..60) {
  try {
    Invoke-WebRequest -Uri "http://127.0.0.1:3018/api/capabilities" -UseBasicParsing -TimeoutSec 3 | Out-Null
    $ready = $true; break
  } catch {
    if ($_.Exception.Response -ne $null) { $ready = $true; break }  # 有 HTTP 响应即存活
  }
  Write-Host "." -NoNewline
  Start-Sleep -Seconds 2
}
Write-Host ""
if (-not $ready) {
  Write-Host "[tsp] 后端 120s 未就绪, 最近日志 (data\logs\backend.log):" -ForegroundColor Red
  if (Test-Path "$logDir\backend.log") {
    Get-Content "$logDir\backend.log" -Tail 15 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
  }
  Write-Host ""
  Write-Host "[tsp] 疑似僵死: 双击 stop.bat 停掉后再运行本脚本" -ForegroundColor Yellow
  exit 1
}

$l = Get-NetTCPConnection -State Listen -LocalPort 3018 -ErrorAction SilentlyContinue | Select-Object -First 1
Write-Host "[tsp] 后端就绪 (PID $($l.OwningProcess)) → http://localhost:3018" -ForegroundColor Green
Start-Process "http://localhost:3018"
