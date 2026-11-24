# aksource 持久启动器 (计划任务用)
# 归属 Task Scheduler 服务, 不随终端/沙箱会话退出而终止
$py = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\backend\.venv\Scripts\python.exe"
$wd = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\aksource"
$log = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\data\logs\aksource.log"
if (Get-NetTCPConnection -LocalPort 3021 -State Listen -ErrorAction SilentlyContinue) {
  exit 0
}
# 先收尸: kill 残留 python server.py (3021 无监听但进程还活着的僵尸态)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'aksource' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath $py -ArgumentList 'server.py' -WorkingDirectory $wd -WindowStyle Hidden
