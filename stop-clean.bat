@echo off
rem tick-stock-panel deep cleanup (double-click equivalent of "stop.bat clean")
rem Stops services, removes scheduled tasks TSP-*, deletes backend\.venv.
rem Data in data\ is untouched. Next start.bat rebuilds .venv automatically.
call "%~dp0stop.bat" clean
echo.
pause
