"""Checks a running docker-compose.yml stack the way a person would use it, with
the real bundled Attendee (run by .github/workflows/one-click-stack.yml after
./start.sh). Standard library only, so it runs on any machine with Python.

    python3 deploy/verify_stack.py [--url http://localhost:8765]

1. The dashboard's status: meeting service ready, voice on, and the bot's
   audio address is wss:// (Attendee refuses anything else).
2. Sends a bot to a Google Meet link, through the same API the dashboard and
   the phone use. Real Attendee validates the whole request (websocket and
   recording settings included) - a mismatch with its API fails here.
3. Follows the bot: Attendee's worker has to pick it up and launch its browser.
   There is no real meeting behind the link, so it ends with a reason - which
   must come back as a plain sentence, not a code.

Joining a real call can't be checked without a real meeting; everything up to
that point is.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

MEETING_URL = "https://meet.google.com/abc-defg-hij"
ENDED = {"fatal_error", "ended"}


def call(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        raise SystemExit(f"FAIL: {method} {path} -> {error.code}: {error.read().decode(errors='replace')}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8765")
    parser.add_argument("--follow-s", type=float, default=300, help="how long to follow the bot")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    config = call(base, "GET", "/api/bots/config")
    print("config:", json.dumps(config))
    assert config["attendee_ready"], f"meeting service not ready: {config['attendee_problem']}"
    assert config["tts_enabled"], "speech output is off"
    assert config["callback_is_secure"], f"bot audio address is not wss://: {config['callback_ws_url']}"

    bot = call(base, "POST", "/api/bots", {"meeting_url": MEETING_URL, "bot_name": "AI Interpreter"})
    print("created:", json.dumps(bot))
    bot_id = bot["id"]

    states: list[str] = []
    detail: dict[str, Any] = bot
    deadline = time.time() + args.follow_s
    while time.time() < deadline:
        detail = call(base, "GET", f"/api/bots/{bot_id}")
        state = detail.get("state", "unknown")
        if not states or states[-1] != state:
            states.append(state)
            print(f"  state: {state}", flush=True)
        if state in ENDED:
            break
        time.sleep(3)

    events = [e.get("type") + (f"/{e['sub_type']}" if e.get("sub_type") else "") for e in detail.get("events") or []]
    print("states:", " -> ".join(states))
    print("events:", ", ".join(events) or "(none)")
    print("problem shown to the person:", detail.get("problem"))

    if states[-1] not in ENDED:
        call(base, "POST", f"/api/bots/{bot_id}/leave")

    assert any(state not in ("ready", "scheduled", "staged") for state in states), (
        "Attendee never launched the bot - is attendee-worker running? (docker compose logs attendee-worker)"
    )
    assert events, "Attendee recorded no events for the bot"
    if states[-1] == "fatal_error":
        problem = detail.get("problem") or ""
        assert problem and "_" not in problem, f"failure reason not phrased for people: {problem!r}"

    print("PASS: the bundled stack accepts, launches and reports on a bot through the dashboard API.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
