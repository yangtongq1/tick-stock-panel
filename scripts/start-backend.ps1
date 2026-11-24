# backend (3018) 持久启动器 (计划任务用)
$py = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\backend\.venv\Scripts\python.exe"
$backendDir = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\backend"
$envf = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\.env"
$log = "C:\Users\admin\WorkBuddy\2026-11-13-14-08-19\tick-stock-panel\data\logs\backend.log"
if (Get-NetTCPConnection -LocalPort 3018 -State Listen -ErrorAction SilentlyContinue) {
  exit 0
}
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Start-Process -FilePath $py -ArgumentList @('-m','uvicorn','app.main:app',"--env-file","`"$envf`"",'--host','0.0.0.0','--port','3018') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err"
