@echo off
rem =====================================================================
rem  TSP 守护计划任务一键注册 (双击运行, 幂等)
rem
rem  注册 TSP-aksource-keeper (09:05/12:35) + TSP-backend-keeper (09:00),
rem  注册后立即自检拉起服务。以后掉线/重启自动恢复。
rem  前置: 先跑过 start.bat (venv 已装好)。
rem  卸载: stop.bat (顺带清理本脚本注册的任务)。
rem =====================================================================
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%scripts\setup-keeper.ps1" (
    echo [setup-keeper] scripts\setup-keeper.ps1 not found. Run from repo root.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\setup-keeper.ps1" %*
echo.
pause
endlocal
