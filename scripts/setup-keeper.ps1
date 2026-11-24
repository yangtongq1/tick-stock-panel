# =====================================================================
# TSP 守护计划任务一键注册 (setup-keeper.bat 的实现)
#
# 注册两个计划任务 (幂等, 重复运行 = 覆盖更新):
#   TSP-aksource-keeper: 每天 09:05 / 12:35 检查拉起 aksource (3021)
#   TSP-backend-keeper:   每天 09:00 检查拉起后端 (3018)
#
# 动机: 普通方式启动的服务进程归终端/会话所有, 终端关闭或机器重启后
# 不会自动恢复; 计划任务归属 Task Scheduler 服务, 掉线即补、开机自愈。
# 注: stop.bat / stop-clean.bat 会自动清理本脚本注册的 TSP-* 任务。
# =====================================================================
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"

Write-Host "[setup-keeper] 仓库根: $Root" -ForegroundColor Cyan

if (-not (Test-Path $py)) {
  Write-Host "[setup-keeper] backend venv 不存在 ($py)" -ForegroundColor Red
  Write-Host "  先双击 start.bat (自动装依赖) 再运行本脚本"
  exit 1
}

# ---- aksource keeper: 09:05 / 12:35 ----
$a1 = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\start-aksource.ps1`""
$t1a = New-ScheduledTaskTrigger -Daily -At '09:05'
$t1b = New-ScheduledTaskTrigger -Daily -At '12:35'
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'TSP-aksource-keeper' -Action $a1 -Trigger $t1a,$t1b `
  -Settings $s -Description 'tick-stock-panel aksource 3021 守护: 开盘前/午休后检查, 掉线即补' -Force | Out-Null
Write-Host "[setup-keeper] 已注册 TSP-aksource-keeper (每天 09:05 / 12:35)" -ForegroundColor Green

# ---- backend keeper: 09:00 ----
$a2 = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Root\scripts\start-backend.ps1`""
$t2 = New-ScheduledTaskTrigger -Daily -At '09:00'
Register-ScheduledTask -TaskName 'TSP-backend-keeper' -Action $a2 -Trigger $t2 `
  -Settings $s -Description 'tick-stock-panel backend 3018 守护: 开盘前检查, 掉线即补' -Force | Out-Null
Write-Host "[setup-keeper] 已注册 TSP-backend-keeper (每天 09:00)" -ForegroundColor Green

# ---- 立即各跑一次, 把没起的服务拉起来 ----
Write-Host "[setup-keeper] 立即执行一次自检..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName 'TSP-aksource-keeper'
Start-ScheduledTask -TaskName 'TSP-backend-keeper'
Start-Sleep -Seconds 10

$aks = Get-NetTCPConnection -LocalPort 3021 -State Listen -ErrorAction SilentlyContinue
$bkd = Get-NetTCPConnection -LocalPort 3018 -State Listen -ErrorAction SilentlyContinue
if ($aks) { Write-Host "[setup-keeper] aksource 3021 运行中 (PID $($aks[0].OwningProcess))" -ForegroundColor Green }
else { Write-Host "[setup-keeper] aksource 3021 未监听 (会在下个触发点重试; 手动查 data\logs\aksource.log)" -ForegroundColor Yellow }
if ($bkd) { Write-Host "[setup-keeper] backend 3018 运行中 (PID $($bkd[0].OwningProcess))" -ForegroundColor Green }
else { Write-Host "[setup-keeper] backend 3018 未监听 (会在下个触发点重试; 手动查 data\logs\backend.log)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "[setup-keeper] 完成。以后开机/掉线自动恢复, 无需手动干预。" -ForegroundColor Cyan
