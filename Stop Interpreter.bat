@echo off
rem Stops the Meeting Interpreter. Downloads and settings are kept.
cd /d "%~dp0"
if exist "app\docker-compose.yml" cd app
docker compose stop
echo Stopped.
pause
