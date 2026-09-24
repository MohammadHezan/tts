#!/usr/bin/env bash
# Stops the Meeting Interpreter. Downloads and settings are kept, so the next
# ./start.sh is quick.
set -euo pipefail
cd "$(dirname "$0")"
if docker info >/dev/null 2>&1; then
  docker compose stop
else
  sudo docker compose stop
fi
echo "Stopped."
