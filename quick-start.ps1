# tick-stock-panel 一键启动脚本 (Windows PowerShell)
# 目标: git clone 后双击 start.bat → 自动装齐依赖 → 拉起全部服务 → 打开浏览器
#
# 服务拓扑 (全部本机, 互不依赖外网账号):
#   3021 aksource  免费数据源 (腾讯/新浪/东财聚合: 实时/分钟/板块/财务/涨停池)
#   3018 后端      页面+API 同一地址 (直接伺服 frontend/dist 构建产物, 无需 Node)
#
# 自动化范围 (目标: 新机器 clone → 双击 start.bat → 全自动, 零手动步骤):
#   - uv:           缺失时自动 winget 安装
#   - Python 3.12:  uv 自动下载托管解释器 (系统无需装过 Python)
#   - 后端依赖:     uv sync (按 uv.lock 锁定版本, 可离线复现)
#   - 前端:         直接用仓库自带 frontend/dist 构建产物 (无需 Node)
#   - 板块/涨停池/资金流/概念/行业: 后端启动时自动播种配置 + 定时拉取
#   - 行情数据:     随仓库分发 (clone 即得全量历史), 此后每日自动增量
#
# 用法:  .\quick-start.ps1          首次/日常启动 (幂等, 已在运行则跳过)
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

# ============================================================================
# 1/6 Python (uv 托管, 缺失自动装)
# ============================================================================
Write-Host "== 1/6 Python ==" -ForegroundColor Cyan
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"

# uv 优先: uv sync --python 3.12 会在 venv 里自动下载并固定托管 Python,
# 不要求用户系统装过 Python —— 新机器零依赖起步。
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "安装 uv (Python 依赖管理器)..."
  # 首选 winget; LTSC/精简版 Windows 可能没有 winget → 回退官方独立安装器
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    Invoke-Native winget @('install', '--id=astral-sh.uv', '--accept-source-agreements', '--accept-package-agreements', '--silent')
  } else {
    Write-Host "winget 不可用, 用官方独立安装器..."
    Invoke-Native powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
      "Invoke-WebRequest -Uri https://astral.sh/uv/install.ps1 -UseBasicParsing | Invoke-Expression")
  }
  $env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv 安装后不可用, 请新开终端重跑 start.bat" }
}
Write-Host "uv 就绪"

# ============================================================================
# 2/6 配置 (.env)
# ============================================================================
Write-Host "== 2/6 配置 ==" -ForegroundColor Cyan
if (-not (Test-Path .env)) { Copy-Item .env.example .env }

# ============================================================================
# 3/6 后端依赖 (uv sync: 按 uv.lock 精确复现, 自动下载托管 Python)
# ============================================================================
Write-Host "== 3/6 后端依赖 ==" -ForegroundColor Cyan
Push-Location backend
# --python 3.12: 明确指定版本, uv 自动下载托管解释器 (系统无 Python 也能跑)
Invoke-Native uv @('sync', '--python', '3.12')
Pop-Location

$py = "$Root\backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "backend venv 创建失败: $py" }

# ============================================================================
# 4/6 前端 (仓库自带构建产物, 默认跳过)
# ============================================================================
Write-Host "== 4/6 前端 ==" -ForegroundColor Cyan
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
  Write-Host "使用仓库自带构建产物 (重建: .\quick-start.ps1 -Rebuild)"
}

# ============================================================================
# 5/6 拉起服务: aksource 3021 + 后端 3018
# ============================================================================
Write-Host "== 5/6 启动服务 ==" -ForegroundColor Cyan
$logDir = "$Root\data\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# 启动方式统一用 cmd /c start 派生分离进程:
#   - 不随终端/脚本退出而终止 (关掉 start.bat 窗口服务照常运行)
#   - 避开 Start-Process 在大小写重复 proxy 环境变量下
#     (HTTPS_PROXY+https_proxy 并存, 装代理的 Windows 常见)
#     抛 "已添加项" 字典键冲突 → 后端静默不启动的坑

# 顺序: 先后端 3018, 再数据源 3021。
#   - 后端启动不依赖 3021: 启动时拉不到只是 WARNING, PullScheduler 会按配置
#     间隔自动重试 (实测 3021 慢半分钟也无影响);
#   - 先起后端让页面尽早可用; aksource 随后起来, 后端下一轮拉取即拿到数据。

# 后端 3018: host 0.0.0.0 局域网可访问; 已挂载 frontend/dist → 一个地址同时出页面与 API
if (Test-Http "http://127.0.0.1:3018/api/capabilities") {
  Write-Host "[tsp] 后端 3018 已在运行"
} else {
  Write-Host "[tsp] 启动后端 3018 ..."
  $envf = "$Root\.env"
  Push-Location "$Root\backend"
  cmd /c "start /min `"tsp-backend`" cmd /c `"`"$py`" -m uvicorn app.main:app --env-file `"$envf`" --host 0.0.0.0 --port 3018 >> `"$logDir\backend.log`" 2>&1`"" | Out-Null
  Pop-Location
}

# 免费数据源 aksource (腾讯/新浪/东财聚合; 复用 backend venv)
# 注意: 健康探测用 /realtime (真实路由)。/ 根路由不存在返回 404,
# 旧版 Test-Http 会把 404 误判为「在运行」而跳过启动 → 后端全量 10061。
if (Test-Http "http://127.0.0.1:3021/realtime") {
  Write-Host "[tsp] 数据源 3021 已在运行"
} else {
  Write-Host "[tsp] 启动免费数据源 3021 ..."
  Push-Location "$Root\aksource"
  cmd /c "start /min `"aksource`" cmd /c `"`"$py`" server.py >> `"$logDir\aksource.log`" 2>&1`"" | Out-Null
  Pop-Location
  # 等 aksource 就绪 (30s 预热: instruments 加载 + 首轮全市场刷新)
  $aksReady = $false
  foreach ($i in 1..15) {
    if (Test-Http "http://127.0.0.1:3021/realtime") { $aksReady = $true; break }
    Start-Sleep -Seconds 2
  }
  if ($aksReady) {
    Write-Host "[tsp] 数据源 3021 就绪" -ForegroundColor Green
  } else {
    Write-Host "[tsp] 数据源 3021 未在 30s 内就绪 — 查 $logDir\aksource.log" -ForegroundColor Yellow
  }
}

# ============================================================================
# 6/6 等就绪 + 自检
# ============================================================================
Write-Host "== 6/6 等待就绪" -ForegroundColor Cyan
Write-Host "[tsp] 后端首次启动需数据预热 (~30-90s)" -NoNewline
$ready = $false
foreach ($i in 1..60) {
  if (Test-Http "http://127.0.0.1:3018/api/capabilities") { $ready = $true; break }
  Write-Host "." -NoNewline
  Start-Sleep -Seconds 2
}
Write-Host ""
if (-not $ready) {
  Write-Warning "后端 120s 未就绪"
  Write-Host " 排查: data\logs\backend.log (启动日志)"
  Write-Host " 重试: 关掉本窗口再双击 start.bat (脚本幂等)"
  exit 1
}

# 自检: 板块/涨停池扩展表是否已播种 (新机器由启动播种机制自动创建)
try {
  $ext = Invoke-RestMethod -Uri "http://127.0.0.1:3018/api/ext-data" -TimeoutSec 10
  $extCount = ($ext.items | Measure-Object).Count
  Write-Host "[tsp] 扩展数据表: $extCount 张 (板块/涨停池/资金流/概念/行业)"
} catch {
  Write-Host "[tsp] 扩展数据表: 查询失败 (不影响核心功能)"
}

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host " 系统已就绪 → http://localhost:3018"
Write-Host " 页面+API 同一地址; 局域网用本机 IP:3018"
Write-Host ""
Write-Host " 已自动包含:"
Write-Host "   行情数据 (随仓库自带全量历史, 每日自动增量)"
Write-Host "   连板梯队/涨停池/资金流 (启动自动播种, 盘中定时拉取)"
Write-Host "   概念/行业板块 (东财聚合, 每日自动更新)"
Write-Host "   模拟盘持仓 (随仓库分发, 直接可看)"
Write-Host ""
Write-Host " 数据源 http://localhost:3021 (免费, 无需账号)"
Write-Host " 日志: data\logs\ (backend / aksource)"
Write-Host " 停止: 双击 stop.bat"
Write-Host "======================================" -ForegroundColor Green

# 打开浏览器
Start-Process "http://localhost:3018"
