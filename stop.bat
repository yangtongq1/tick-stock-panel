@echo off
rem tick-stock-panel stop/cleanup launcher (Windows)
rem Modes:
rem   stop.bat          stop TSP services (backend 3018 / aksource 3021 / dev frontend 3011)
rem   stop.bat clean    also remove scheduled tasks TSP-* and delete backend\.venv
rem Matching: by command line (uvicorn app.main:app, vite) and by working directory
rem (aksource\server.py), never by process name, so other Python programs are safe.
rem Data in data\ is never touched.
setlocal
set "MODE=%~1"
set "ROOT=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='SilentlyContinue';" ^
  "$root = '%ROOT%';" ^
  "$aks = Join-Path $root 'aksource';" ^
  "$killed = @();" ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and (($_.CommandLine -match 'uvicorn app\.main:app') -or ($_.CommandLine -match 'vite(\.js|\s)')) } | ForEach-Object { $killed += [string]$_.ProcessId; Stop-Process -Id $_.ProcessId -Force }; " ^
  "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'server\.py\s*$') -and ( ($_.CommandLine -like ('*' + $aks + '*')) -or ($null -ne (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -ErrorAction SilentlyContinue)) ) } | ForEach-Object { $procPath = (Get-Process -Id $_.ProcessId).Path; if ($procPath -eq ($root + 'backend\.venv\Scripts\python.exe') -or $_.CommandLine -like ('*' + $aks + '*')) { $killed += [string]$_.ProcessId; Stop-Process -Id $_.ProcessId -Force } }; " ^
  "if ($killed.Count -gt 0) { Write-Host ('[tsp] stopped processes: ' + ($killed -join ', ')) } else { Write-Host '[tsp] no running TSP processes found' }; " ^
  "Start-Sleep -Milliseconds 800;" ^
  "foreach ($port in 3018,3021,3011) { $l = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue; if ($l) { $spid = ($l | Select-Object -First 1).OwningProcess; Write-Host ('[tsp] port ' + $port + ' still up (pid ' + $spid + '), killing'); Stop-Process -Id $spid -Force } };" ^
  "if ('%MODE%' -eq 'clean') {" ^
  "  Get-ScheduledTask | Where-Object { $_.TaskName -like 'TSP-*' } | Unregister-ScheduledTask -Confirm:$false; Write-Host '[tsp] scheduled tasks TSP-* removed';" ^
  "  if (Test-Path ($root + 'backend\.venv')) { Remove-Item -Recurse -Force ($root + 'backend\.venv'); Write-Host '[tsp] backend\.venv removed' }" ^
  "}"

echo.
if /i "%MODE%"=="clean" (
  echo [tsp] cleanup complete. Data in data\ is untouched.
) else (
  echo [tsp] services stopped. Use start.bat to run again. Data untouched.
)
endlocal
