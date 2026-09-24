"""End-to-end check of the meeting-bot path, with a fake Attendee standing in
for the real one: everything on our side is real (engine server, dashboard in
a real Chromium, the /attendee/ws bridge, VAD, the Pipeline), only Attendee
and the Zoom/Meet call behind it are simulated.

    Chromium -> bot.html -> POST /api/bots -> engine -> fake Attendee REST API
    fake Attendee -> connects back to the engine's /attendee/ws over wss://, as
    real Attendee insists (the URL the engine handed it, trusting only the CA
    deploy/setup_secrets.py generates, like the bundled Attendee) -> streams
    espeak-ng speech as realtime_audio.mixed
    engine -> Pipeline -> realtime_audio.bot_output back to fake Attendee,
    plus FINAL/TRANSLATION captions to the dashboard's events socket.

Asserts the dashboard renders what the bot heard and said, that audio really
came back as bot_output, that the create-bot request carried the right
websocket_settings, and that the bridge rejects a connection without the
secret token. Real Attendee's own join flow into a real meeting is exactly
what this cannot cover - that needs a real Attendee install and a real call.

Usage (from engine/, with requirements-dev.txt installed):
    python -m scripts.verify_meeting_bot [--chromium-path /path/to/chrome]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from app.audio_utils import load_wav_as_pcm16_mono
from scripts.verify_web_client import build_fake_config, find_chromium

ENGINE_DIR = Path(__file__).resolve().parents[1]
ENGINE_PORT = 8797
ENGINE_TLS_PORT = 8798
FAKE_ATTENDEE_PORT = 8796
REPO_ROOT = ENGINE_DIR.parent
API_KEY = "e2e-test-key"
BOT_ID = "bot_e2e"
SPEECH = "Good afternoon, I would like to order the walnut dining table."
FAKE_TRANSCRIPT = "This is a fake transcript."  # what FakeAsr always "hears"


class FakeAttendee:
    """Attendee's REST API, plus Attendee's websocket behaviour once a bot 'joins'."""

    def __init__(self, meeting_audio: np.ndarray, ca_file: Path) -> None:
        self.meeting_audio = meeting_audio
        self.ca_file = ca_file
        self.create_body: dict[str, Any] | None = None
        self.state = "none"
        self.bot_output_chunks = 0
        self.bot_output_bytes = 0
        self.stream_error: str | None = None
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/v1/bots", status_code=201)
        async def create_bot(request: Request) -> dict[str, Any]:
            if request.headers.get("Authorization") != f"Token {API_KEY}":
                raise HTTPException(401, "bad token")
            self.create_body = await request.json()
            self.state = "joining"
            asyncio.create_task(self._join_and_stream(self.create_body["websocket_settings"]["audio"]["url"]))
            return {"id": BOT_ID, "state": self.state, "meeting_url": self.create_body["meeting_url"]}

        # The dashboard's readiness check (engine app/server.py _attendee_status).
        @app.get("/api/v1/bots")
        async def list_bots(request: Request) -> dict[str, Any]:
            if request.headers.get("Authorization") != f"Token {API_KEY}":
                raise HTTPException(401, "bad token")
            return {"results": []}

        @app.get("/api/v1/bots/{bot_id}")
        async def get_bot(bot_id: str) -> dict[str, Any]:
            return {"id": bot_id, "state": self.state}

        @app.post("/api/v1/bots/{bot_id}/leave")
        async def leave_bot(bot_id: str) -> dict[str, Any]:
            self.state = "leaving"
            return {"id": bot_id, "state": self.state}

        return app

    async def _join_and_stream(self, ws_url: str) -> None:
        from websockets.asyncio.client import connect

        try:
            trusted = ssl.create_default_context(cafile=str(self.ca_file))
            async with connect(ws_url, ssl=trusted) as ws:
                self.state = "joined_recording"
                receiver = asyncio.create_task(self._collect_bot_output(ws))
                samples_per_chunk = 320  # 20ms @ 16kHz, a live meeting's cadence
                for i, offset in enumerate(range(0, len(self.meeting_audio), samples_per_chunk)):
                    chunk = self.meeting_audio[offset : offset + samples_per_chunk].tobytes()
                    await ws.send(json.dumps({
                        "bot_id": BOT_ID,
                        "trigger": "realtime_audio.mixed",
                        "data": {
                            "chunk": base64.b64encode(chunk).decode("ascii"),
                            "sample_rate": 16000,
                            "timestamp_ms": i * 20,
                        },
                    }))
                    await asyncio.sleep(0.005)
                await asyncio.sleep(8)  # stay "in the meeting" while the bot speaks
                receiver.cancel()
        except Exception as error:  # surfaced by the assertions, not swallowed
            self.stream_error = repr(error)
        finally:
            self.state = "ended"

    async def _collect_bot_output(self, ws: Any) -> None:
        async for raw in ws:
            message = json.loads(raw)
            if message.get("trigger") == "realtime_audio.bot_output":
                self.bot_output_chunks += 1
                self.bot_output_bytes += len(base64.b64decode(message["data"]["chunk"]))


def build_meeting_audio(tmp_dir: Path) -> np.ndarray:
    wav = tmp_dir / "meeting_speech.wav"
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "-w", str(wav), SPEECH], check=True, capture_output=True)
    speech = load_wav_as_pcm16_mono(wav, target_sample_rate=16000)
    return np.concatenate([speech, np.zeros(16000 * 2, dtype=np.int16)])  # trailing silence ends the utterance


def start_fake_attendee(fake: FakeAttendee) -> uvicorn.Server:
    server = uvicorn.Server(uvicorn.Config(fake.app, host="127.0.0.1", port=FAKE_ATTENDEE_PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        raise RuntimeError("fake Attendee did not start")
    return server


def wait_for_engine(timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{ENGINE_PORT}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.3)
    raise RuntimeError("engine did not become healthy in time")


def check_bridge_rejects_missing_token() -> None:
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    async def attempt() -> bool:
        try:
            async with connect(f"ws://127.0.0.1:{ENGINE_PORT}/attendee/ws?token=wrong"):
                return False
        except InvalidStatus:
            return True

    assert asyncio.run(attempt()), "bridge accepted a connection with the wrong token"


def run_dashboard_check(chromium_path: str, fake: FakeAttendee) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chromium_path)
        page = browser.new_page()
        js_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        page.goto(f"http://127.0.0.1:{ENGINE_PORT}/bot.html")
        page.fill("#meeting-url", "https://meet.google.com/abc-defg-hij")
        page.fill("#bot-name", "E2E Interpreter")
        page.click("#send-bot")

        page.wait_for_selector(".turn .source", timeout=30_000)
        page.wait_for_selector(".turn .translation", timeout=15_000)
        page.wait_for_function("document.querySelector('#status').textContent.includes('joined_recording')", timeout=10_000)
        heard = page.inner_text(".turn .source")
        said = page.inner_text(".turn .translation")
        warnings_hidden = page.eval_on_selector("#warnings", "el => el.hidden")
        browser.close()

    deadline = time.time() + 30
    while fake.state != "ended" and time.time() < deadline:  # let the bot finish speaking and leave
        time.sleep(0.5)

    print(f"Dashboard heard: {heard!r}")
    print(f"Dashboard said:  {said!r}")
    print(f"Bot audio returned to 'Attendee': {fake.bot_output_chunks} chunks, {fake.bot_output_bytes} bytes")
    print(f"Create-bot request body: {json.dumps(fake.create_body)}")

    assert fake.stream_error is None, f"fake Attendee's websocket failed: {fake.stream_error}"
    assert not js_errors, f"JS page errors: {js_errors}"
    assert not console_errors, f"Console errors: {console_errors}"
    assert warnings_hidden, "dashboard showed configuration warnings in a fully configured setup"
    assert heard == FAKE_TRANSCRIPT, "dashboard did not render what the bot heard"
    assert said, "dashboard did not render what the bot said"
    assert fake.bot_output_chunks > 0, "no realtime_audio.bot_output ever reached Attendee"
    assert fake.bot_output_bytes >= 16000 * 2, "less than 1s of the bot's ~2s translated speech reached Attendee"
    assert fake.create_body is not None
    assert fake.create_body["bot_name"] == "E2E Interpreter"
    audio_settings = fake.create_body["websocket_settings"]["audio"]
    assert audio_settings["sample_rate"] == 16000
    assert audio_settings["url"].startswith(f"wss://localhost:{ENGINE_TLS_PORT}/attendee/ws?token=")
    assert fake.create_body["recording_settings"] == {"format": "none"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-path", default=None, help="Path to a Chromium/Chrome executable")
    args = parser.parse_args()
    chromium_path = find_chromium(args.chromium_path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        setup_dir = tmp_dir / "setup"
        # The same certificate the bundled docker-compose.yml setup generates.
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "deploy" / "setup_secrets.py")],
            env={**os.environ, "SETUP_DIR": str(setup_dir)},
            check=True,
        )
        fake = FakeAttendee(build_meeting_audio(tmp_dir), setup_dir / "ca.pem")
        fake_server = start_fake_attendee(fake)

        env = {
            **os.environ,
            "ENGINE_CONFIG_PATH": str(build_fake_config(tmp_dir)),
            "ATTENDEE_BASE_URL": f"http://127.0.0.1:{FAKE_ATTENDEE_PORT}",
            "ATTENDEE_API_KEY": API_KEY,
            "ATTENDEE_CALLBACK_WS_URL": f"wss://localhost:{ENGINE_TLS_PORT}/attendee/ws",
            "HOST": "127.0.0.1",
            "PORT": str(ENGINE_PORT),
            "TLS_PORT": str(ENGINE_TLS_PORT),
            "TLS_CERT_FILE": str(setup_dir / "translator.pem"),
            "TLS_KEY_FILE": str(setup_dir / "translator.key"),
        }
        # How docker-compose.yml runs it: HTTP for the dashboard, TLS for the bot.
        engine = subprocess.Popen([sys.executable, "-m", "app.serve"], cwd=ENGINE_DIR, env=env)
        try:
            wait_for_engine()
            check_bridge_rejects_missing_token()
            run_dashboard_check(chromium_path, fake)
        finally:
            engine.terminate()
            engine.wait(timeout=10)
            fake_server.should_exit = True

    print("\nPASS: meeting-bot path verified end-to-end against a fake Attendee.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
