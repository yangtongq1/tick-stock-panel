@echo off
rem tick-stock-panel one-click launcher (Windows)
rem Runs quick-start.ps1 from repo root (idempotent: skips installed/built parts)
rem First run: installs deps + builds frontend (~1-2 min)
rem Later runs: starts services in seconds and opens http://localhost:3018
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%quick-start.ps1" (
  echo [tsp] quick-start.ps1 not found. Run this file from the repo root.
  pause
  exit /b 1
)
where powershell >nul 2>nul
if errorlevel 1 (
  echo [tsp] PowerShell not found. Install Windows PowerShell 5.1+ first.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%quick-start.ps1" %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo [tsp] Startup failed with exit code %EC%. Check output above.
  echo [tsp] Logs: data\logs\backend.log and data\logs\aksource.log
  echo [tsp] Retry: just double-click start.bat again (idempotent).
  pause
)
endlocal & exit /b %EC%
