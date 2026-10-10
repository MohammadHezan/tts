"""End to end, without a real meeting: the real engine and models, a fake
Attendee that records what the bot types in the meeting chat, and a client that
plays speech into the bot's audio socket at real-time pace, the way Attendee
would. Checks each dashboard switch (voice/TTS, chat text, pause) and times how
long after the speaker stops the chat lines appear.

    python test_meeting_e2e.py --config ../deploy/config.docker-gpu.yaml \
        --asr-model C:/path/to/cohere-transcribe-03-2026 --ollama http://localhost:11434 \
        --english clip_en.wav --arabic clip_ar.wav

Needs Ollama running with the translation model, and the GPU free for the speech model.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
import uvicorn
import websockets
import yaml
from fastapi import FastAPI, Request

PORT, FAKE_PORT, TOKEN, BOT = 8777, 8999, "e2e-token", "bot-e2e"
SR = 16000


def load_clip(path: Path) -> bytes:
    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    if sr != SR:
        mono = np.interp(np.linspace(0, len(mono) - 1, int(len(mono) * SR / sr)), np.arange(len(mono)), mono)
    return (np.clip(mono, -1, 1) * 32767).astype(np.int16).tobytes()


class FakeAttendee:
    """Records what the bot types in the meeting chat (and every call it makes)."""

    def __init__(self) -> None:
        self.posts: list[tuple[float, str]] = []
        self.calls: list[str] = []
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        async def anything(path: str, request: Request) -> dict:
            self.calls.append(f"{request.method} /{path}")
            if path.endswith("send_chat_message"):
                self.posts.append((time.monotonic(), (await request.json())["message"]))
            return {"state": "joined_recording", "results": [], "next": None}

        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=FAKE_PORT, log_level="error"))
        threading.Thread(target=self._server.run, daemon=True).start()

    def stop(self) -> None:
        self._server.should_exit = True


class Engine:
    def __init__(self, config_path: Path) -> None:
        env = dict(os.environ, ENGINE_CONFIG_PATH=str(config_path), ATTENDEE_BASE_URL=f"http://127.0.0.1:{FAKE_PORT}",
                   ATTENDEE_API_KEY="e2e", ATTENDEE_BRIDGE_TOKEN=TOKEN, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.log: list[str] = []
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(PORT), "--log-level", "warning"],
            cwd=Path(__file__).parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        )
        threading.Thread(target=lambda: [self.log.append(line.rstrip()) for line in self.proc.stdout], daemon=True).start()

    def wait_ready(self, timeout_s: float = 180) -> None:
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}/healthz", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(1)
        raise RuntimeError("engine did not start:\n" + "\n".join(self.log[-20:]))

    def stop(self) -> None:
        self.proc.terminate()


def switch(feature: str, value: str, language: str | None = None) -> dict:
    r = httpx.post(f"http://127.0.0.1:{PORT}/api/bots/{BOT}/switch", json={"feature": feature, "value": value, "language": language})
    r.raise_for_status()
    return r.json()


async def play(ws, pcm: bytes, tail_silence_s: float = 2.5) -> float:
    """Streams speech then silence at real-time pace; returns when the speech ended (monotonic)."""
    chunk = SR // 10 * 2
    ms = 0
    speech_end = 0.0
    padded = bytes(SR // 2 * 2) + pcm + bytes(int(tail_silence_s * SR) * 2)
    for offset in range(0, len(padded), chunk):
        piece = padded[offset : offset + chunk]
        await ws.send(json.dumps({"bot_id": BOT, "trigger": "realtime_audio.mixed",
                                  "data": {"chunk": base64.b64encode(piece).decode(), "sample_rate": SR, "timestamp_ms": ms}}))
        ms += 100
        if offset + chunk >= SR // 2 * 2 + len(pcm) and not speech_end:
            speech_end = time.monotonic()
        await asyncio.sleep(0.1)
    return speech_end


async def scenario(ws, fake: FakeAttendee, engine: Engine, audio_times: list[float], name: str, pcm: bytes) -> dict:
    posts0, audio0, log0 = len(fake.posts), len(audio_times), len(engine.log)
    speech_end = await play(ws, pcm)
    quiet_since = time.monotonic()
    seen = (len(fake.posts), len(audio_times))
    while time.monotonic() - quiet_since < 5 and time.monotonic() - speech_end < 40:  # wait until nothing new arrives for 5s
        await asyncio.sleep(0.3)
        now = (len(fake.posts), len(audio_times))
        if now != seen:
            seen, quiet_since = now, time.monotonic()
    posts = fake.posts[posts0:]
    audio = audio_times[audio0:]
    synthesized = any("tts[" in line for line in engine.log[log0:])
    return {
        "name": name,
        "chat": [(round(t - speech_end, 2), m) for t, m in posts],
        "audio_chunks": len(audio),
        "first_audio_s": round(audio[0] - speech_end, 2) if audio else None,
        "tts_ran": synthesized,
    }


async def run(args, engine: Engine, fake: FakeAttendee) -> list[str]:
    en, ar = load_clip(Path(args.english)), load_clip(Path(args.arabic))
    audio_times: list[float] = []
    problems: list[str] = []

    async with websockets.connect(f"ws://127.0.0.1:{PORT}/attendee/ws?token={TOKEN}", max_size=None) as ws:
        async def reader() -> None:
            async for message in ws:
                if json.loads(message).get("trigger") == "realtime_audio.bot_output":
                    audio_times.append(time.monotonic())
        read_task = asyncio.create_task(reader())

        print("warming up (loads the speech model) ...")
        await scenario(ws, fake, engine, audio_times, "warm-up", en)

        results = []

        async def step(name: str, pcm: bytes, expect: dict) -> None:
            r = await scenario(ws, fake, engine, audio_times, name, pcm)
            results.append(r)
            print(f"\n== {name}")
            for t, m in r["chat"]:
                print(f"   chat +{t:5.2f}s  {m}")
            print(f"   voice: {r['audio_chunks']} chunks, first at +{r['first_audio_s']}s, tts ran: {r['tts_ran']}")
            lines = [m for _, m in r["chat"]]
            for key, want in expect.items():
                got = {
                    "heard": any(m.startswith("[EN]" if pcm is en else "[AR]") for m in lines),
                    "translation": any(m.startswith("[AR]" if pcm is en else "[EN]") for m in lines),
                    "voice": r["audio_chunks"] > 0,
                    "tts_ran": r["tts_ran"],
                }[key]
                if got != want:
                    problems.append(f"{name}: expected {key}={want}, got {got}")

        await step("1. defaults, English", en, {"heard": True, "translation": True, "voice": True, "tts_ran": True})
        await step("2. defaults, Arabic", ar, {"heard": True, "translation": True, "voice": True, "tts_ran": True})
        print("\n-> dashboard: voice (TTS) off", switch("voice", "off"))
        await step("3. voice off", en, {"heard": True, "translation": True, "voice": False, "tts_ran": False})
        print("\n-> dashboard: voice on, chat text off", switch("voice", "on"), switch("text", "off"))
        await step("4. chat text off", en, {"heard": False, "translation": False, "voice": True, "tts_ran": True})
        print("\n-> dashboard: text on (translation only), pause", switch("text", "on"), switch("interpreter", "pause"))
        await step("5. paused", en, {"heard": False, "translation": False, "voice": False, "tts_ran": False})
        print("\n-> dashboard: resume", switch("interpreter", "resume"))
        await step("6. resumed, translation only", ar, {"heard": False, "translation": True, "voice": True, "tts_ran": True})

        read_task.cancel()
    reads = [c for c in fake.calls if not c.endswith("send_chat_message")]
    if reads:
        problems.append(f"the bot called Attendee for something other than posting: {reads}")
    shown = [r for r in results if r["chat"]]
    if shown:
        first_chat = sorted(r["chat"][0][0] for r in shown)
        last_chat = sorted(r["chat"][-1][0] for r in shown)
        print(f"\nFirst chat line after the speaker stopped: median {first_chat[len(first_chat) // 2]}s, "
              f"translation: median {last_chat[len(last_chat) // 2]}s")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=Path("../deploy/config.docker-gpu.yaml"))
    ap.add_argument("--asr-model", help="override asr.cohere.model (a folder on this PC)")
    ap.add_argument("--ollama", default="http://localhost:11434")
    ap.add_argument("--english", required=True)
    ap.add_argument("--arabic", required=True)
    ap.add_argument("--workdir", type=Path, default=Path("../results"))
    args = ap.parse_args()

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    cfg["translator"]["ollama"]["base_url"] = args.ollama
    if args.asr_model:
        cfg["asr"]["cohere"]["model"] = args.asr_model
        cfg["asr"]["cohere"]["cache_dir"] = str(args.workdir.resolve() / "hf-cache")
    args.workdir.mkdir(parents=True, exist_ok=True)
    config_path = args.workdir / "e2e_config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    fake, engine = FakeAttendee(), Engine(config_path.resolve())
    try:
        engine.wait_ready()
        problems = asyncio.run(run(args, engine, fake))
    finally:
        engine.stop()
        fake.stop()
        (args.workdir / "e2e_engine.log").write_text("\n".join(engine.log), encoding="utf-8")
    print("\nALL SWITCHES BEHAVED" if not problems else "\nPROBLEMS:\n  " + "\n  ".join(problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
