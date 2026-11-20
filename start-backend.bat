@echo off
rem TSP backend-only launcher (Windows): bring up 3018 reliably.
rem Idempotent (skips if already running), self-healing (installs deps if missing),
rem also ensures data source 3021. Shows backend.log tail on failure.
rem Logs: data\logs\backend.log   Stop: stop.bat
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%start-backend.ps1" (
  echo [tsp] start-backend.ps1 not found. Run this file from the repo root.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start-backend.ps1" %*
set "EC=%ERRORLEVEL%"
if not "%EC%"=="0" (
  echo.
  echo [tsp] Backend failed to start. Full log: data\logs\backend.log
  echo [tsp] Tip: run stop.bat first if a stale process holds port 3018.
  pause
)
endlocal & exit /b %EC%
