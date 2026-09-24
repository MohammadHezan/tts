@echo off
rem Stops the Meeting Interpreter. Downloads and settings are kept.
cd /d "%~dp0"
docker compose stop
echo Stopped.
pause
