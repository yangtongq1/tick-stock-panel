# =====================================================================
# 新机器数据补齐一键脚本 (fix-data.bat 的实现)
#
# 场景: 新电脑 git clone 后, data/ 只有仓库种子快照 (上次管道成功提交
# 的数据)。中间缺的日期服务不会自动补——这个脚本一键拉齐。
#
# 做什么:
#   1. 探测本地 kline_daily 最新日期 (max(date=...))
#   2. 从「本地最新日期 + 1 天」到今天, 调后端 POST /api/kline/repair_daily
#      (复用盘后管道全流程: 日K + 除权因子 + enriched + 视图刷新 + 自动发布)
#   3. 轮询 job 进度直到 succeeded / failed, 实时打印
#
# 前置: 后端 3018 已在运行 (quick-start.ps1 / start.bat 已跑过)
# 用法: 双击 fix-data.bat, 或 powershell -File scripts/fix-data.ps1
# =====================================================================
$ErrorActionPreference = "Stop"
$Base = "http://127.0.0.1:3018"
$Root = Split-Path $PSScriptRoot -Parent
$DataDir = Join-Path $Root "data"

# --- NO_PROXY: 绝不让桌面代理劫持 loopback 请求 ---
$env:NO_PROXY = "127.0.0.1,localhost,::1"

function Write-Step($msg) { Write-Host "[fix-data] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[fix-data] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[fix-data] $msg" -ForegroundColor Yellow }

# --- 1. 后端活着吗 ---
Write-Step "检查后端 3018 ..."
try {
    $caps = Invoke-RestMethod -Uri "$Base/api/capabilities" -TimeoutSec 5
} catch {
    Write-Warn2 "后端未响应: $($_.Exception.Message)"
    Write-Host "  请先双击 start.bat (或运行 quick-start.ps1) 等服务起来后再跑本脚本。"
    exit 1
}
Write-Ok "后端在线"

# --- 2. 探测本地数据最新日期 ---
$dailyDir = Join-Path $DataDir "kline_daily"
$latest = $null
if (Test-Path $dailyDir) {
    $dates = Get-ChildItem $dailyDir -Directory -Filter "date=*" |
             ForEach-Object { $_.Name -replace '^date=', '' } |
             Sort-Object -Descending
    if ($dates) { $latest = $dates[0] }
}
if (-not $latest) {
    Write-Warn2 "本地 kline_daily 为空 — 无种子数据, 建议先跑 quick-start 或手动 git pull"
    exit 1
}
Write-Ok "本地日K最新日期: $latest"

# --- 3. 计算补数起点 (跳过周末, 简单 +1 天起拉, 管道会自然处理非交易日) ---
$start = [datetime]::ParseExact($latest, "yyyy-MM-dd", $null).AddDays(1)
$today = [datetime]::Today
if ($start -gt $today) {
    Write-Ok "数据已是最新 ($latest), 无需补数"
    exit 0
}
$startDate = $start.ToString("yyyy-MM-dd")
$gapDays = ($today - $start).Days
Write-Step "补数范围: $startDate ~ $($today.ToString('yyyy-MM-dd')) (跨度 $gapDays 天)"

# --- 4. 触发 repair_daily job ---
Write-Step "触发补数任务 (POST /api/kline/repair_daily) ..."
try {
    $resp = Invoke-RestMethod -Uri "$Base/api/kline/repair_daily" -Method Post `
        -ContentType "application/json" `
        -Body (@{ start_date = $startDate } | ConvertTo-Json) -TimeoutSec 15
} catch {
    Write-Warn2 "触发失败: $($_.Exception.Message)"
    if ($_.ErrorDetails.Message) { Write-Host "  详情: $($_.ErrorDetails.Message)" }
    exit 1
}
$jobId = $resp.job_id
if ($resp.status -eq "reused") {
    Write-Ok "已有任务在跑, 复用 job $jobId"
} else {
    Write-Ok "任务已创建: $jobId"
}

# --- 5. 轮询进度 ---
$lastLine = ""
$spin = @('|', '/', '-', '\')
$i = 0
while ($true) {
    try {
        $job = Invoke-RestMethod -Uri "$Base/api/pipeline/jobs/$jobId" -TimeoutSec 10
    } catch {
        Write-Warn2 "轮询失败: $($_.Exception.Message), 3s 后重试"
        Start-Sleep 3
        continue
    }
    $line = "{0,3}% [{1}] {2}" -f $job.progress, $job.stage, $job.log[-1]
    if ($line -ne $lastLine) {
        Write-Host "`r$($spin[$i % 4]) $line" -NoNewline
        $lastLine = $line
    } else {
        Write-Host "`r$($spin[$i % 4]) $line" -NoNewline
    }
    $i++
    if ($job.status -in @("succeeded", "failed", "cancelled")) { break }
    Start-Sleep 3
}
Write-Host ""

# --- 6. 结果 ---
if ($job.status -eq "succeeded") {
    Write-Ok "补数完成! 耗时 $([math]::Round($job.duration_s, 1))s"
    if ($job.result) {
        $r = $job.result
        if ($r.days)     { Write-Host "  同步天数: $($r.days)" }
        if ($r.symbols_written) { Write-Host "  写入标的: $($r.symbols_written)" }
        if ($r.adj_written)     { Write-Host "  除权因子行: $($r.adj_written)" }
        if ($r.enriched_days)   { Write-Host "  enriched 天数: $($r.enriched_days)" }
    }
    Write-Host ""
    Write-Host "  看板已可用: http://localhost:3018 (Ctrl+F5 强刷)"
    exit 0
} else {
    Write-Warn2 "任务结束: $($job.status)"
    if ($job.error) { Write-Host "  错误: $($job.error)" }
    Write-Host "  日志: data\logs\backend.log"
    exit 1
}
