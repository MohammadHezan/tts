#!/usr/bin/env bash
# Starts the Meeting Interpreter (docker-compose.yml) and opens its page.
#
#   ./start.sh               start, wait until ready, open the browser
#   ./start.sh --no-browser  same, without opening a browser
#
# The first start downloads about 15 GB (the translator, the meeting bot, the
# translation model) - leave it running. Later starts take about a minute.
set -euo pipefail
cd "$(dirname "$0")"

OPEN_BROWSER=1
[ "${1:-}" = "--no-browser" ] && OPEN_BROWSER=0

say() { printf '%s\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  say "Docker is not installed. Install it, then run ./start.sh again:"
  say "  Linux:  curl -fsSL https://get.docker.com | sh"
  say "  Mac:    https://www.docker.com/products/docker-desktop/"
  exit 1
fi

# This computer's address on the Wi-Fi, for the phones.
lan_ip() {
  if command -v ip >/dev/null 2>&1; then
    ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") {print $(i + 1); exit}}'
  else
    ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
  fi
}
IP="$(lan_ip)"
export INTERPRETER_PHONE_URL="${IP:+http://$IP:8765}"

DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    # Docker installed with get.docker.com needs sudo until you add yourself to
    # the "docker" group (sudo usermod -aG docker $USER, then log in again).
    DOCKER=(sudo --preserve-env=INTERPRETER_PHONE_URL docker)
  else
    say "Docker is installed but not running."
    say "  Linux:  sudo systemctl start docker"
    say "  Mac:    open Docker Desktop and wait until it says it is running"
    say "Then run ./start.sh again."
    exit 1
  fi
fi

MEM_BYTES="$("${DOCKER[@]}" info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
if [ "$MEM_BYTES" -gt 0 ] && [ "$MEM_BYTES" -lt $((10 * 1024 * 1024 * 1024)) ]; then
  say "Warning: Docker can use only $((MEM_BYTES / 1024 / 1024 / 1024)) GB of memory. The interpreter needs"
  say "about 10 GB (translation model, speech recognition, the bot's browser)."
  say "It may be slow or stop. On a Mac: Docker Desktop -> Settings -> Resources -> Memory."
fi

say "Getting the interpreter ready. The first time this downloads about 15 GB."
"${DOCKER[@]}" compose pull --ignore-pull-failures || true
"${DOCKER[@]}" compose up -d

say "Starting up (the first start also downloads the 4.9 GB translation model)..."
started=$(date +%s)
until curl -fsS -m 5 http://localhost:8765/healthz >/dev/null 2>&1; do
  # A one-shot setup step that failed, or the translator crashing in a loop.
  if "${DOCKER[@]}" compose ps -a --format '{{.Service}} {{.State}} {{.ExitCode}}' 2>/dev/null \
      | awk '($1 ~ /^(setup|attendee-setup|ollama-pull)$/ && $2 == "exited" && $3 != "0") || ($1 == "translator" && $2 == "restarting") {bad = 1} END {exit !bad}'; then
    say "Something failed while starting. Details:"
    "${DOCKER[@]}" compose logs --tail 40 setup attendee-setup ollama-pull translator || true
    exit 1
  fi
  if [ $(( $(date +%s) - started )) -gt 5400 ]; then
    say "Still not ready after 90 minutes. Check your internet connection, then run ./start.sh again."
    exit 1
  fi
  sleep 10
  printf '.'
done
say ""

# The translator is up; the meeting service (Attendee) can take a little longer.
for _ in $(seq 1 60); do
  curl -fsS -m 10 http://localhost:8765/api/bots/config 2>/dev/null | grep -q '"attendee_ready":true' && break
  sleep 5
done

say ""
say "Ready."
say "  On this computer:  http://localhost:8765/bot.html"
say "  On your phone:     Interpreter app -> Meeting Bot (it finds this computer by itself)"
[ -n "$INTERPRETER_PHONE_URL" ] && say "                     or open $INTERPRETER_PHONE_URL/bot.html in the phone's browser"
say "  Stop it with:      ./stop.sh"

if [ "$OPEN_BROWSER" = 1 ]; then
  (xdg-open http://localhost:8765/bot.html || open http://localhost:8765/bot.html) >/dev/null 2>&1 || true
fi
