"""Real end-to-end verification of the web client (app/static/) in a real
browser: starts the engine (fake providers - no models/network needed),
launches Chromium via Playwright with a synthesized WAV as the fake
microphone, clicks Start, and asserts captions/translation/audio actually
render in the DOM.

This is how a real bug was caught during development: pydantic's
`ser_json_bytes="base64"` uses the URL-safe base64 alphabet, which browser
`atob()` (and Android's `Base64.DEFAULT`) doesn't understand - silent
garbage/crash on the AUDIO event's payload. Re-run this after touching
app/static/ or app/schema.py's wire format.

Usage (from engine/, with the venv active):
    pip install playwright
    playwright install chromium   # skip if a system Chromium is available -
                                    # pass --chromium-path to use it instead
    python -m scripts.verify_web_client [--chromium-path /path/to/chrome]

Exits non-zero (and prints why) on any failure - JS errors, console errors,
or the expected caption/translation never appearing within the timeout.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = REPO_ROOT / "engine"
TEST_SENTENCE = "Good afternoon, thank you for calling our showroom."
TRAILING_SILENCE_S = 2.0
PORT = 8799


def build_fake_config(tmp_dir: Path) -> Path:
    config_path = tmp_dir / "config.fake.yaml"
    config_path.write_text(
        """
audio: {sample_rate_hz: 16000, frame_ms: 30, channels: 1}
vad: {provider: silero, threshold: 0.5, min_speech_ms: 150, min_silence_ms: 700, speech_pad_ms: 200}
asr: {provider: fake}
translator: {provider: fake, glossary_path: null}
tts: {provider: fake}
""",
        encoding="utf-8",
    )
    return config_path


def build_test_wav(tmp_dir: Path) -> Path:
    if shutil.which("espeak-ng") is None:
        raise RuntimeError("espeak-ng not found - install it to generate the test audio")
    raw_path = tmp_dir / "speech.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", "150", "-w", str(raw_path), TEST_SENTENCE],
        check=True,
        capture_output=True,
    )
    data, sr = sf.read(raw_path)
    # Chromium's --use-file-for-fake-audio-capture loops the file with no
    # gap - without real trailing silence baked in, VAD never sees the
    # 700ms of quiet it needs to end the utterance. See this script's
    # docstring / the commit that added it for how this was diagnosed.
    silence = np.zeros(int(sr * TRAILING_SILENCE_S), dtype=data.dtype)
    padded_path = tmp_dir / "speech_padded.wav"
    sf.write(padded_path, np.concatenate([data, silence]), sr)
    return padded_path


def find_chromium(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path
    default = Path("/opt/pw-browsers")
    if default.is_dir():
        candidates = sorted(default.glob("chromium-*/chrome-linux/chrome"))
        if candidates:
            return str(candidates[-1])
    raise RuntimeError("Chromium not found - pass --chromium-path or run `playwright install chromium`")


def wait_for_healthz(timeout_s: float = 15.0) -> None:
    import httpx

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.3)
    raise RuntimeError("engine did not become healthy in time")


def run_browser_check(chromium_path: str, wav_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chromium_path,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={wav_path}",
            ],
        )
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()

        js_errors: list[str] = []
        console_errors: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: console_errors.append(msg.text)
            if msg.type == "error" and "404" not in msg.text
            else None,
        )

        event_counts: dict[str, int] = {}

        def on_websocket(ws: object) -> None:
            def on_recv(payload: object) -> None:
                if isinstance(payload, str):
                    event = json.loads(payload)
                    event_counts[event["type"]] = event_counts.get(event["type"], 0) + 1

            ws.on("framereceived", on_recv)  # type: ignore[attr-defined]

        page.on("websocket", on_websocket)

        page.goto(f"http://127.0.0.1:{PORT}/")
        page.click("#start-stop")
        page.wait_for_function(
            "document.querySelector('#status').textContent.includes('Connected')", timeout=10_000
        )
        page.wait_for_function("document.querySelector('#turns').children.length > 0", timeout=15_000)

        turns_html = page.inner_html("#turns")
        browser.close()

    print("Event counts received:", event_counts)
    print("Rendered turn HTML:", turns_html[:300])

    assert not js_errors, f"JS page errors: {js_errors}"
    assert not console_errors, f"Console errors: {console_errors}"
    assert event_counts.get("final", 0) >= 1, "No FINAL event rendered"
    assert event_counts.get("translation", 0) >= 1, "No TRANSLATION event rendered"
    assert event_counts.get("audio", 0) >= 1, "No AUDIO event rendered"
    assert "This is a fake transcript." in turns_html, "Expected transcript text not found in DOM"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-path", default=None, help="Path to a Chromium/Chrome executable")
    args = parser.parse_args()

    chromium_path = find_chromium(args.chromium_path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        config_path = build_fake_config(tmp_dir)
        wav_path = build_test_wav(tmp_dir)

        server_env = {**os.environ, "ENGINE_CONFIG_PATH": str(config_path)}
        server = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.server:app",
                "--host", "127.0.0.1", "--port", str(PORT),
            ],
            cwd=ENGINE_DIR,
            env=server_env,
        )
        try:
            wait_for_healthz()
            run_browser_check(chromium_path, wav_path)
        finally:
            server.terminate()
            server.wait(timeout=5)

    print("\nPASS: web client verified end-to-end in a real browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
