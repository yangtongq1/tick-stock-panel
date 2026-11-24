# aksource 持久启动器 (计划任务/手动双击均可用)
# 归属 Task Scheduler 服务, 不随终端/沙箱会话退出而终止
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
$wd = Join-Path $Root "aksource"
$log = Join-Path $Root "data\logs\aksource.log"
if (Get-NetTCPConnection -LocalPort 3021 -State Listen -ErrorAction SilentlyContinue) {
  Write-Host "[aks-keeper] 3021 已在运行"
  exit 0
}
if (-not (Test-Path $py)) { Write-Host "[aks-keeper] venv 缺失: $py"; exit 1 }
# 收尸: 清理残留僵尸
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'aksource' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 1
$env:NO_PROXY = "127.0.0.1,localhost,::1"
Start-Process -FilePath $py -ArgumentList 'server.py' -WorkingDirectory $wd -WindowStyle Minimized -RedirectStandardOutput $log -RedirectStandardError "$log.err"
Write-Host "[aks-keeper] aksource 已启动"
