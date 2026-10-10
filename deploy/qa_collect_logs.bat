@echo off
rem Collects the interpreter's logs into logs.txt (next to this file) for a bug report.
cd /d "%~dp0"
if exist "app\docker-compose.yml" cd app
set "COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml"
(
  echo ===== %DATE% %TIME%
  docker compose ps -a
  echo ===== translator
  docker compose logs --no-color --tail 3000 translator
  echo ===== ollama
  docker compose logs --no-color --tail 200 ollama ollama-pull asr-pull
  echo ===== attendee
  docker compose logs --no-color --tail 300 attendee-app attendee-worker
  echo ===== gpu
  nvidia-smi
) > "%~dp0logs.txt" 2>&1
echo Wrote %~dp0logs.txt - send it with your bug report.
pause
