@echo off
rem =====================================================================
rem  tick-stock-panel 新机器数据补齐 (一键)
rem
rem  场景: 新电脑 git clone 后 data/ 只是仓库种子快照, 中间缺的日期
rem  服务不会自动补。双击本文件 = 探测本地最新日期 → 补到今天。
rem
rem  前置: 先跑过 start.bat (后端 3018 在线)
rem  幂等: 数据已最新时直接退出, 不做任何事
rem =====================================================================
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%scripts\fix-data.ps1" (
    echo [fix-data] scripts\fix-data.ps1 not found. Run from repo root.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\fix-data.ps1" %*
set "EC=%ERRORLEVEL%"
echo.
if "%EC%"=="0" (
    echo [fix-data] done.
) else (
    echo [fix-data] exited with code %EC%. See messages above.
)
pause
endlocal & exit /b %EC%
