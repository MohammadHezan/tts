@echo off
rem Starts the Meeting Interpreter - see deploy\start.ps1. Works both in this
rem repository and in the Windows download, where everything else is in "app".
set "SCRIPT=%~dp0deploy\start.ps1"
if exist "%~dp0app\deploy\start.ps1" set "SCRIPT=%~dp0app\deploy\start.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
echo.
pause
