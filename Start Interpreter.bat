@echo off
rem Starts the Meeting Interpreter - see deploy\start.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\start.ps1" %*
echo.
pause
