"""Two-device meeting simulation: the real interpreter in a simulated call.

Only what can't be reached from a test runner is simulated - Zoom/Meet and
Attendee. Everything else is what actually ships: the translator server
(normally the docker compose stack), its dashboard, Whisper, Ollama,
Kokoro/Piper, the /attendee/ws bridge.

The call has two devices taking turns:
    Device A - Sarah, speaks English
    Device B - Omar, speaks Arabic
Their voices are their own Piper voices, not the bot's. A stand-in for
Attendee answers the translator's "send a bot" request and then does what
Attendee + Zoom/Meet do once a bot is in the call:
    - streams everyone's mixed audio to the bot, 20ms at a time, in real time
      (the bot never hears itself, same as in a real call)
    - plays the bot's voice to both devices
Each device's recording is what that person would hear in the call.

Checks, per turn:
    heard       the bot transcribed the speaker in the right language
    translated  the translation came out in the other language
    spoke       the bot's voice reached the listener's device
    understood  a separate Whisper listens to the listener's recording of the
                bot and must pick out the sentence's key words - the Arabic
                the bot speaks is translated back to English for this check
    latency     speaker stops -> listener starts hearing the translation

Usage (from engine/):
    # against a running translator, e.g. `docker compose up` (see
    # .github/workflows/meeting-simulation.yml for the exact setup)
    python -m scripts.simulate_meeting --engine-url http://localhost:8765 \\
        --voices-dir ../sim-voices --out ../sim-output

    # wiring only, no models needed: starts its own engine with fake providers
    python -m scripts.simulate_meeting --fake-engine --out /tmp/sim-output
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, HTTPException, Request

ENGINE_DIR = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 320  # 20ms - a live call's cadence
BOT_ID = "bot_meeting_sim"
MEETING_URL = "https://meet.google.com/sim-ulat-ion"


@dataclass(frozen=True)
class Line:
    device: str  # "A" or "B"
    lang: str
    text: str
    meaning_en: str  # what it means in English - the reference for "understood"
    keys: tuple[str, ...]  # regexes the listener-side English must contain
    terms: tuple[str, ...]  # regexes the bot's translation text itself must contain


SCRIPT = (
    Line(
        "A", "en",
        "Good morning Omar, thank you for joining the call today.",
        "Good morning Omar, thank you for joining the call today.",
        (r"morning", r"thank", r"call|join|meeting"),
        (r"صباح", r"شكر"),
    ),
    Line(
        "B", "ar",
        "صباح النور، يسعدني أن أعرض عليكم مجموعتنا الجديدة من الكنب الفاخرة.",
        "Good morning, I am pleased to show you our new collection of luxury sofas.",
        (r"morning", r"collection|range|new", r"sofa|couch|furniture"),
        (r"morning", r"collection"),
    ),
    Line(
        "A", "en",
        "We would like to order twenty dining chairs in walnut wood.",
        "We would like to order twenty dining chairs in walnut wood.",
        (r"twenty|\b20\b", r"chair", r"walnut|nut|wood"),
        (r"20|عشرين|عشرون", r"كرسي|كراسي", r"الجوز"),
    ),
    Line(
        "B", "ar",
        "ممتاز، نستطيع تسليم الطلب خلال ثلاثة أسابيع.",
        "Excellent, we can deliver the order within three weeks.",
        (r"deliver", r"three|\b3\b", r"week"),
        (r"deliver", r"three|\b3\b", r"week"),
    ),
)
DEVICES = {"A": "Device A - Sarah (speaks English)", "B": "Device B - Omar (speaks Arabic)"}
OTHER = {"A": "B", "B": "A"}
SPEAKER_VOICES = {"en": "en_US-lessac-medium.onnx", "ar": "ar_JO-kareem-low.onnx"}


# --- voices ------------------------------------------------------------------


def to_16k(pcm16: np.ndarray, rate: int) -> np.ndarray:
    if rate == SAMPLE_RATE:
        return pcm16
    converted, _ = audioop.ratecv(pcm16.tobytes(), 2, 1, rate, SAMPLE_RATE, None)
    return np.frombuffer(converted, dtype=np.int16)


class SpeakerVoice:
    """A meeting participant's voice: Piper if the voice file is there, else espeak-ng."""

    def __init__(self, lang: str, voices_dir: Path | None) -> None:
        self.lang = lang
        self._piper = None
        model = voices_dir / SPEAKER_VOICES[lang] if voices_dir else None
        if model and model.exists():
            from piper import PiperVoice

            self._piper = PiperVoice.load(str(model))
            self.name = f"Piper {model.stem}"
        elif shutil.which("espeak-ng"):
            self.name = f"espeak-ng ({lang})"
        else:
            raise RuntimeError(f"no voice for {lang}: put {SPEAKER_VOICES[lang]} in --voices-dir or install espeak-ng")

    def say(self, text: str) -> np.ndarray:
        if self._piper is not None:
            chunks = list(self._piper.synthesize(text))
            pcm = np.frombuffer(b"".join(c.audio_int16_bytes for c in chunks), dtype=np.int16)
            return to_16k(pcm, chunks[0].sample_rate)
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "line.wav"
            voice = "en-us" if self.lang == "en" else "ar"
            subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", str(wav), text], check=True, capture_output=True)
            data, rate = sf.read(wav, dtype="int16")
            return to_16k(data if data.ndim == 1 else data[:, 0], rate)


class ListenerEars:
    """An independent Whisper standing in for the listener's ears (not the bot's)."""

    def __init__(self, model: str) -> None:
        from faster_whisper import WhisperModel

        self.model_name = model
        self._model = WhisperModel(model, device="cpu", compute_type="int8")

    def _run(self, pcm16: np.ndarray, language: str, task: str) -> str:
        audio = (pcm16.astype(np.float32) / 32768.0).clip(-1, 1)
        segments, _ = self._model.transcribe(audio, language=language, task=task, beam_size=5)
        return "".join(s.text for s in segments).strip()

    def transcribe(self, pcm16: np.ndarray, language: str) -> str:
        return self._run(pcm16, language, "transcribe")

    def in_english(self, pcm16: np.ndarray, language: str) -> str:
        return self._run(pcm16, language, "translate" if language != "en" else "transcribe")


# --- the call ----------------------------------------------------------------


@dataclass
class _Utterance:
    device: str
    pcm: np.ndarray
    pos: int = 0
    started: asyncio.Event = field(default_factory=asyncio.Event)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    start_time: float = 0.0
    end_time: float = 0.0


class Meeting:
    """Attendee's REST API, plus what Attendee + the call do once the bot is in."""

    def __init__(self, api_key: str) -> None:
        self.chat_sent: list[str] = []
        self.api_key = api_key
        self.t0 = time.monotonic()
        self.heard: dict[str, list[tuple[int, np.ndarray]]] = {"A": [], "B": []}  # (sample pos, audio)
        self.bot_chunks: list[tuple[float, np.ndarray]] = []  # (arrival, audio)
        self._bot_cursor = 0
        self._queue: deque[_Utterance] = deque()
        self.create_body: dict[str, Any] | None = None
        self.state = "none"
        self.error: str | None = None
        self.connected = asyncio.Event()
        self._leave = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self.app = self._build_app()

    def now_pos(self) -> int:
        return int((time.monotonic() - self.t0) * SAMPLE_RATE)

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/v1/bots", status_code=201)
        async def create_bot(request: Request) -> dict[str, Any]:
            if request.headers.get("Authorization") != f"Token {self.api_key}":
                raise HTTPException(401, "bad token")
            self.create_body = await request.json()
            self.state = "joining"
            self._tasks.append(asyncio.create_task(self._join(self.create_body["websocket_settings"]["audio"]["url"])))
            return {"id": BOT_ID, "state": self.state, "meeting_url": self.create_body["meeting_url"]}

        # The meeting chat (engine app/meeting_chat.py): nobody types anything here,
        # but the bot says hello and announces mute changes.
        @app.get("/api/v1/bots/{bot_id}/chat_messages")
        async def chat_messages(bot_id: str) -> dict[str, Any]:
            return {"next": None, "previous": None, "results": []}

        @app.post("/api/v1/bots/{bot_id}/send_chat_message")
        async def send_chat_message(bot_id: str, request: Request) -> dict[str, Any]:
            self.chat_sent.append((await request.json())["message"])
            return {}

        # The dashboard's readiness check (engine app/server.py _attendee_status).
        @app.get("/api/v1/bots")
        async def list_bots(request: Request) -> dict[str, Any]:
            if request.headers.get("Authorization") != f"Token {self.api_key}":
                raise HTTPException(401, "bad token")
            return {"results": []}

        @app.get("/api/v1/bots/{bot_id}")
        async def get_bot(bot_id: str) -> dict[str, Any]:
            return {"id": bot_id, "state": self.state}

        @app.post("/api/v1/bots/{bot_id}/leave")
        async def leave_bot(bot_id: str) -> dict[str, Any]:
            self._leave.set()
            return {"id": bot_id, "state": "leaving"}

        return app

    async def say(self, device: str, pcm: np.ndarray) -> _Utterance:
        utterance = _Utterance(device, pcm)
        self._queue.append(utterance)
        await utterance.finished.wait()
        return utterance

    def leave(self) -> None:
        self._leave.set()

    async def _join(self, ws_url: str) -> None:
        from websockets.asyncio.client import connect

        try:
            async with connect(ws_url, max_size=None) as ws:
                self.state = "joined_recording"
                self.connected.set()
                receiver = asyncio.create_task(self._receive_bot_voice(ws))
                await self._stream_call_audio(ws)
                await asyncio.sleep(1.0)
                receiver.cancel()
        except Exception as error:  # reported by the checks, never swallowed
            self.error = repr(error)
            self.connected.set()
        finally:
            self.state = "ended"

    async def _stream_call_audio(self, ws: Any) -> None:
        tick = CHUNK_SAMPLES / SAMPLE_RATE
        next_send = time.monotonic()
        silence = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
        index = 0
        while not self._leave.is_set():
            chunk = silence
            if self._queue:
                utterance = self._queue[0]
                if utterance.pos == 0:
                    utterance.start_time = time.monotonic()
                    self.heard[OTHER[utterance.device]].append((self.now_pos(), utterance.pcm))
                    utterance.started.set()
                chunk = utterance.pcm[utterance.pos : utterance.pos + CHUNK_SAMPLES]
                utterance.pos += CHUNK_SAMPLES
                if utterance.pos >= len(utterance.pcm):
                    self._queue.popleft()
                    utterance.end_time = time.monotonic() + len(chunk) / SAMPLE_RATE
                    utterance.finished.set()
                if len(chunk) < CHUNK_SAMPLES:
                    chunk = np.pad(chunk, (0, CHUNK_SAMPLES - len(chunk)))
            await ws.send(json.dumps({
                "bot_id": BOT_ID,
                "trigger": "realtime_audio.mixed",
                "data": {
                    "chunk": base64.b64encode(chunk.tobytes()).decode("ascii"),
                    "sample_rate": SAMPLE_RATE,
                    "timestamp_ms": index * 20,
                },
            }))
            index += 1
            next_send += tick
            await asyncio.sleep(max(0.0, next_send - time.monotonic()))

    async def _receive_bot_voice(self, ws: Any) -> None:
        async for raw in ws:
            message = json.loads(raw)
            if message.get("trigger") != "realtime_audio.bot_output":
                continue
            data = message["data"]
            pcm = to_16k(np.frombuffer(base64.b64decode(data["chunk"]), dtype=np.int16), data["sample_rate"])
            pos = max(self.now_pos(), self._bot_cursor)  # the bot's voice is one continuous stream
            self._bot_cursor = pos + len(pcm)
            for device in ("A", "B"):  # everyone in the call hears the bot
                self.heard[device].append((pos, pcm))
            self.bot_chunks.append((time.monotonic(), pcm))

    def bot_still_talking(self) -> bool:
        return self._bot_cursor > self.now_pos()

    def render(self, device: str | None) -> np.ndarray:
        """What `device` heard, or (None) the whole call: both speakers and the bot."""
        if device is not None:
            segments = self.heard[device]
        else:  # A's track plus Omar-side audio A didn't hear (Sarah's own voice); the bot only once
            bot_ids = {id(pcm) for _, pcm in self.bot_chunks}
            segments = self.heard["A"] + [(pos, pcm) for pos, pcm in self.heard["B"] if id(pcm) not in bot_ids]
        length = max((pos + len(pcm) for pos, pcm in segments), default=0)
        mix = np.zeros(length, dtype=np.int32)
        for pos, pcm in segments:
            mix[pos : pos + len(pcm)] += pcm
        return np.clip(mix, -32768, 32767).astype(np.int16)


class CaptionFeed:
    """The dashboard's caption socket: what the bot heard and said, as it happens."""

    def __init__(self) -> None:
        self.events: list[tuple[float, dict[str, Any]]] = []
        self.error: str | None = None

    async def run(self, url: str) -> None:
        from websockets.asyncio.client import connect

        try:
            async with connect(url) as ws:
                async for raw in ws:
                    self.events.append((time.monotonic(), json.loads(raw)))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.error = repr(error)


def room_noise(seconds: float, seed: int = 7) -> np.ndarray:
    """Nobody talking: a room's hum, keyboard clicks and a few breaths - what a
    call carries between sentences, and what Whisper likes to "hear" as
    "شكراً" / "Thank you" if nothing stops it."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    hum = np.cumsum(rng.standard_normal(n))  # brown noise: low rumble
    hum = (hum - np.convolve(hum, np.ones(400) / 400, mode="same")) * 25  # drop the DC drift, about -45 dBFS
    audio = hum.astype(np.float64)
    for at in rng.uniform(0.5, seconds - 0.5, size=int(seconds * 1.5)):  # clicks, -20 dBFS peaks
        start = int(at * SAMPLE_RATE)
        audio[start : start + 80] += rng.standard_normal(80) * 3000 * np.exp(-np.arange(80) / 15)
    for at in rng.uniform(1.0, seconds - 1.0, size=3):  # breaths: 0.4s of shaped noise
        start, length = int(at * SAMPLE_RATE), int(0.4 * SAMPLE_RATE)
        envelope = np.sin(np.linspace(0, np.pi, length)) ** 2
        audio[start : start + length] += rng.standard_normal(length) * 600 * envelope
    return np.clip(audio, -32768, 32767).astype(np.int16)


async def check_silence(meeting: Meeting, feed: CaptionFeed, seconds: float, idle_s: float) -> tuple[bool, str]:
    """Plays only noise into the call; the bot must neither caption nor speak."""
    bot_chunks_before, events_before = len(meeting.bot_chunks), len(feed.events)
    await meeting.say("A", room_noise(seconds))
    await asyncio.sleep(idle_s + 4)  # long enough for a phantom turn to be translated and spoken
    spoken = sum(len(pcm) for _, pcm in meeting.bot_chunks[bot_chunks_before:]) / SAMPLE_RATE
    heard = [e.get("text") for _, e in feed.events[events_before:] if e.get("type") in ("final", "translation") and e.get("text")]
    ok = spoken == 0 and not heard
    detail = "bot stayed silent" if ok else f"bot spoke {spoken:.1f}s, captioned {heard!r}"
    return ok, detail


# --- one turn ------------------------------------------------------------------


@dataclass
class Turn:
    line: Line
    speech_seconds: float = 0.0
    heard: str = ""
    heard_lang: str = ""
    said: str = ""
    said_lang: str = ""
    errors: list[str] = field(default_factory=list)
    bot_audio: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))
    voice_after_s: float | None = None  # speaker stops -> listener starts hearing the bot
    text_after_s: float | None = None  # speaker stops -> translation text on the dashboard
    timed_out: bool = False
    listener_heard: str = ""  # listener-side Whisper, in the language the bot spoke
    listener_english: str = ""  # ... and in English, for the key-word check
    wer: float | None = None
    terms_missing: list[str] = field(default_factory=list)
    length_ratio: float | None = None
    keys_found: list[str] = field(default_factory=list)
    checks: dict[str, bool | None] = field(default_factory=dict)


async def run_turn(meeting: Meeting, feed: CaptionFeed, line: Line, pcm: np.ndarray, idle_s: float, timeout_s: float) -> Turn:
    turn = Turn(line, speech_seconds=len(pcm) / SAMPLE_RATE)
    first_event = len(feed.events)
    first_chunk = len(meeting.bot_chunks)
    utterance = await meeting.say(line.device, pcm)
    deadline = time.monotonic() + timeout_s

    while True:
        await asyncio.sleep(0.25)
        events = feed.events[first_event:]
        chunks = meeting.bot_chunks[first_chunk:]
        answered = any(e["type"] in ("translation", "error") for _, e in events)
        heard_nothing = any(e["type"] == "final" and not e["text"] for _, e in events)
        last_activity = max([t for t, _ in events] + [t for t, _ in chunks] + [utterance.end_time])
        quiet = time.monotonic() - last_activity > idle_s and not meeting.bot_still_talking()
        if (answered or heard_nothing) and quiet:
            break
        if time.monotonic() > deadline:
            turn.timed_out = True
            break

    events = feed.events[first_event:]
    chunks = meeting.bot_chunks[first_chunk:]
    finals = [e for _, e in events if e["type"] == "final" and e["text"]]
    translations = [(t, e) for t, e in events if e["type"] == "translation"]
    turn.heard = " ".join(e["text"] for e in finals)
    turn.heard_lang = finals[0]["lang"] if finals else ""
    turn.said = " ".join(e["text"] for _, e in translations)
    turn.said_lang = translations[0][1]["lang"] if translations else ""
    turn.errors = [e.get("error") or "error" for _, e in events if e["type"] == "error"]
    if chunks:
        turn.bot_audio = np.concatenate([pcm for _, pcm in chunks])
        turn.voice_after_s = chunks[0][0] - utterance.end_time
    if translations:
        turn.text_after_s = translations[0][0] - utterance.end_time
    return turn


# --- checks ------------------------------------------------------------------

_AR_DIACRITICS = re.compile(r"[ً-ْـ]")
_NUMBERS = {"20": "twenty", "3": "three"}


def normalize(text: str, lang: str, spell_numbers: bool = True) -> str:
    text = text.lower()
    if lang == "ar":
        text = _AR_DIACRITICS.sub("", text)
        text = re.sub("[أإآ]", "ا", text).replace("ة", "ه").replace("ى", "ي")
    text = re.sub(r"[^\w\s]", " ", text)
    words = [_NUMBERS.get(w, w) if spell_numbers else w for w in text.split()]
    return " ".join(words)


def word_error_rate(reference: str, hypothesis: str, lang: str) -> float:
    import jiwer

    ref, hyp = normalize(reference, lang), normalize(hypothesis, lang)
    return float(jiwer.wer(ref, hyp)) if hyp else 1.0


def arabic_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum("؀" <= c <= "ۿ" for c in letters) / len(letters) if letters else 0.0


def check_turn(turn: Turn, ears: ListenerEars | None, fake_engine: bool) -> None:
    line = turn.line
    target = OTHER_LANG[line.lang]
    if fake_engine:  # fake ASR/translator: only the wiring is meaningful
        turn.checks["heard"] = bool(turn.heard)
        turn.checks["translated"] = bool(turn.said)
    else:
        turn.wer = word_error_rate(line.text, turn.heard, line.lang)
        turn.checks["heard"] = turn.heard_lang == line.lang and turn.wer <= (0.3 if line.lang == "en" else 0.5)
        share = arabic_share(turn.said)
        in_target = share > 0.6 if target == "ar" else (bool(turn.said) and share < 0.1)
        turn.checks["translated"] = turn.said_lang == target and in_target
        # Right key terms (walnut must not become cedar), and nothing tacked on
        # that nobody said - the bot speaks this into the meeting.
        said = normalize(turn.said, target, spell_numbers=False)
        turn.terms_missing = [t for t in line.terms if not re.search(t, said)]
        turn.length_ratio = len(turn.said) / max(1, len(line.text))
        turn.checks["faithful"] = not turn.terms_missing and turn.length_ratio <= 2.0
    turn.checks["spoke"] = len(turn.bot_audio) >= SAMPLE_RATE // 2
    turn.checks["no errors"] = not turn.errors and not turn.timed_out

    if ears is None or not turn.checks["spoke"] or fake_engine:
        turn.checks["understood"] = None  # not checked
        return
    turn.listener_heard = ears.transcribe(turn.bot_audio, target)
    turn.listener_english = turn.listener_heard if target == "en" else ears.in_english(turn.bot_audio, target)
    english = turn.listener_english.lower()
    turn.keys_found = [key for key in line.keys if re.search(key, english)]
    turn.checks["understood"] = len(turn.keys_found) >= len(line.keys) - 1


OTHER_LANG = {"en": "ar", "ar": "en"}


# --- report ------------------------------------------------------------------


def _mark(value: bool | None) -> str:
    return "n/a" if value is None else ("PASS" if value else "FAIL")


def write_report(
    out: Path, turns: list[Turn], meeting: Meeting, feed: CaptionFeed, info: dict[str, str], other_checks_ok: bool = True
) -> bool:
    passed = all(v is not False for t in turns for v in t.checks.values()) and len(turns) == len(SCRIPT)
    passed = passed and meeting.error is None and feed.error is None and other_checks_ok
    voice_delays = [t.voice_after_s for t in turns if t.voice_after_s is not None]
    lines = [
        f"# Two-device meeting simulation: {'PASS' if passed else 'FAIL'}",
        "",
        f"- **Call**: {DEVICES['A']} and {DEVICES['B']}, taking turns; the interpreter bot joins as a third participant.",
    ]
    lines += [f"- **{key}**: {value}" for key, value in info.items()]
    if voice_delays:
        lines.append(
            f"- **Delay** (speaker stops -> listener hears the translation): "
            f"median {statistics.median(voice_delays):.1f}s, worst {max(voice_delays):.1f}s"
        )
    if meeting.error:
        lines.append(f"- **Meeting connection error**: `{meeting.error}`")
    if feed.error:
        lines.append(f"- **Caption feed error**: `{feed.error}`")
    lines += ["", "| # | Speaker | They said | Bot heard | Bot said | Listener heard (separate Whisper) | Voice after | Checks |", "|---|---|---|---|---|---|---|---|"]
    for i, t in enumerate(turns, 1):
        checks = ", ".join(f"{name} {_mark(v)}" for name, v in t.checks.items())
        heard_note = f" *(WER {t.wer:.0%})*" if t.wer is not None else ""
        listener = t.listener_heard + (f" *({t.listener_english})*" if t.listener_english and t.listener_english != t.listener_heard else "")
        voice_after = f"{t.voice_after_s:.1f}s" if t.voice_after_s is not None else "-"
        cells = [
            str(i), DEVICES[t.line.device].split(" - ")[1], t.line.text, (t.heard or "-") + heard_note,
            t.said or "-", listener or "-", voice_after, checks,
        ]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    for i, t in enumerate(turns, 1):
        if t.checks.get("faithful") is False:
            lines.append(
                f"\nTurn {i} not faithful: missing {t.terms_missing or 'nothing'}, "
                f"translation {t.length_ratio:.1f}x the length of what was said"
            )
        if t.errors or t.timed_out:
            lines.append(f"\nTurn {i} problems: {'timed out; ' if t.timed_out else ''}{'; '.join(t.errors)}")
    lines += [
        "",
        "Recordings (in this run's `meeting-simulation` artifact):",
        "- `device_A_sarah_hears.wav` - what Sarah hears: Omar's Arabic, and the bot's English and Arabic",
        "- `device_B_omar_hears.wav` - what Omar hears: Sarah's English, and the bot's Arabic and English",
        "- `whole_call.wav` - everyone",
        "- `dashboard.png` - the translator dashboard at the end of the call (when a browser was available)",
    ]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps({
        "passed": passed,
        "info": info,
        "meeting_error": meeting.error,
        "caption_feed_error": feed.error,
        "create_bot_request": meeting.create_body,
        "turns": [
            {
                "speaker": t.line.device, "lang": t.line.lang, "said": t.line.text, "meaning_en": t.line.meaning_en,
                "bot_heard": t.heard, "bot_heard_lang": t.heard_lang, "wer": t.wer,
                "bot_said": t.said, "bot_said_lang": t.said_lang, "errors": t.errors, "timed_out": t.timed_out,
                "bot_voice_seconds": round(len(t.bot_audio) / SAMPLE_RATE, 2),
                "voice_after_s": t.voice_after_s, "text_after_s": t.text_after_s,
                "listener_heard": t.listener_heard, "listener_english": t.listener_english,
                "keys": list(t.line.keys), "keys_found": t.keys_found,
                "terms": list(t.line.terms), "terms_missing": t.terms_missing, "length_ratio": t.length_ratio,
                "checks": t.checks,
            }
            for t in turns
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return passed


# --- orchestration -------------------------------------------------------------


async def open_dashboard(engine_url: str, chromium_path: str | None) -> tuple[Any, Any, Any]:
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(executable_path=chromium_path)
    page = await browser.new_page(viewport={"width": 1100, "height": 1400})
    await page.goto(f"{engine_url}/bot.html")
    await page.fill("#meeting-url", MEETING_URL)
    await page.fill("#bot-name", "AI Interpreter")
    await page.click("#send-bot")  # the same button a person presses
    return playwright, browser, page


async def simulate(args: argparse.Namespace, engine_url: str, api_key: str) -> bool:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    voices_dir = Path(args.voices_dir) if args.voices_dir else None
    voices = {lang: SpeakerVoice(lang, voices_dir) for lang in ("en", "ar")}
    lines_audio = [voices[line.lang].say(line.text) for line in SCRIPT]
    print(f"Speaker voices: English = {voices['en'].name}, Arabic = {voices['ar'].name}", flush=True)

    meeting = Meeting(api_key)
    server = uvicorn.Server(uvicorn.Config(meeting.app, host=args.meeting_host, port=args.meeting_port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)

    # Async client only: the Attendee stand-in the translator calls back into
    # runs on this same event loop, so a blocking request would deadlock it.
    client = httpx.AsyncClient(base_url=engine_url, timeout=30)
    config = (await client.get("/api/bots/config")).json()
    print(f"Translator config: {config}", flush=True)

    dashboard = None
    if args.dashboard:
        try:
            dashboard = await open_dashboard(engine_url, args.chromium_path)
            print("Bot sent from the dashboard (bot.html), like a person would", flush=True)
        except Exception as error:  # no browser here - the API is what the dashboard calls anyway
            print(f"Dashboard unavailable ({error!r}); sending the bot through the API instead", flush=True)
    if dashboard is None:
        response = await client.post("/api/bots", json={"meeting_url": MEETING_URL, "bot_name": "AI Interpreter"})
        if response.status_code != 200:
            raise RuntimeError(f"translator refused to send the bot: {response.status_code} {response.text}")

    feed = CaptionFeed()
    events_url = engine_url.replace("http", "ws", 1) + f"/api/bots/{BOT_ID}/events"
    feed_task = asyncio.create_task(feed.run(events_url))

    await asyncio.wait_for(meeting.connected.wait(), timeout=60)
    if meeting.error:
        raise RuntimeError(f"bot never connected to the call: {meeting.error}")
    print(f"Bot joined the call; letting it settle for {args.settle_s:.0f}s (loads Whisper, warms up translation)", flush=True)
    await asyncio.sleep(args.settle_s)

    print(f"\nNobody speaks for {args.noise_s:.0f}s - only room noise, clicks and breaths in the call", flush=True)
    silence_ok, silence_detail = await check_silence(meeting, feed, args.noise_s, args.idle_s)
    print(f"  {'PASS' if silence_ok else 'FAIL'}: {silence_detail}", flush=True)

    turns: list[Turn] = []
    for i, (line, pcm) in enumerate(zip(SCRIPT, lines_audio, strict=True), 1):
        print(f"\nTurn {i}: {DEVICES[line.device]} says: {line.text}", flush=True)
        turn = await run_turn(meeting, feed, line, pcm, args.idle_s, args.turn_timeout_s)
        print(f"  bot heard ({turn.heard_lang or '-'}): {turn.heard or '-'}", flush=True)
        print(f"  bot said  ({turn.said_lang or '-'}): {turn.said or '-'}", flush=True)
        voice_after = f"{turn.voice_after_s:.1f}s" if turn.voice_after_s is not None else "never"
        print(f"  bot voice: {len(turn.bot_audio) / SAMPLE_RATE:.1f}s, reached {DEVICES[OTHER[line.device]]} {voice_after} after the speaker stopped", flush=True)
        for error in turn.errors:
            print(f"  ERROR: {error}", flush=True)
        turns.append(turn)

    if dashboard is not None:
        _, browser, page = dashboard
        try:
            await page.screenshot(path=str(out / "dashboard.png"), full_page=True)
        except Exception as error:
            print(f"Dashboard screenshot failed: {error!r}", flush=True)

    # Remove the bot the way a person would, then hang up.
    try:
        await client.post(f"/api/bots/{BOT_ID}/leave")
    except httpx.HTTPError as error:
        print(f"leave request failed: {error!r}", flush=True)
    meeting.leave()
    await asyncio.sleep(2)
    feed_task.cancel()
    if dashboard is not None:
        playwright, browser, _ = dashboard
        await browser.close()
        await playwright.stop()
    await client.aclose()
    server.should_exit = True
    await server_task

    for name, device in (("device_A_sarah_hears", "A"), ("device_B_omar_hears", "B"), ("whole_call", None)):
        sf.write(out / f"{name}.wav", meeting.render(device), SAMPLE_RATE, subtype="PCM_16")

    ears = None
    if args.listener_model and not args.fake_engine:
        print(f"\nListening back to each device's recording with a separate Whisper ({args.listener_model})...", flush=True)
        ears = ListenerEars(args.listener_model)
    for turn in turns:
        check_turn(turn, ears, args.fake_engine)

    info = {
        "Translator": engine_url,
        "Speaker voices": f"Sarah = {voices['en'].name}, Omar = {voices['ar'].name}",
        "Listener check": f"faster-whisper {ears.model_name}" if ears else "not run",
    }
    info["Noise only, nobody speaking"] = f"{'PASS' if silence_ok else 'FAIL'} - {silence_detail} ({args.noise_s:.0f}s of room noise, clicks, breaths)"
    info.update(dict(item.split("=", 1) for item in args.info))
    passed = write_report(out, turns, meeting, feed, info, other_checks_ok=silence_ok)
    print("\n" + (out / "report.md").read_text(encoding="utf-8"), flush=True)
    return passed


def build_meeting_fake_config(tmp_dir: Path) -> Path:
    """Fake ASR/translation/voice, but the shipped meeting config's speech
    detection (deploy/config.docker.yaml), so the noise check tests what ships."""
    import yaml

    shipped = yaml.safe_load((ENGINE_DIR.parent / "deploy" / "config.docker.yaml").read_text(encoding="utf-8"))
    config = {
        "audio": shipped["audio"],
        "vad": shipped["vad"],
        "asr": {"provider": "fake"},
        "translator": {"provider": "fake", "glossary_path": None},
        "tts": {"provider": "fake"},
    }
    path = tmp_dir / "config.meeting-fake.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def start_fake_engine(tmp_dir: Path, port: int, meeting_port: int, api_key: str) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "ENGINE_CONFIG_PATH": str(build_meeting_fake_config(tmp_dir)),
        "ATTENDEE_BASE_URL": f"http://127.0.0.1:{meeting_port}",
        "ATTENDEE_API_KEY": api_key,
        "ATTENDEE_CALLBACK_WS_URL": f"ws://127.0.0.1:{port}/attendee/ws",
    }
    engine = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.server:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ENGINE_DIR,
        env=env,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200:
                return engine
        except httpx.TransportError:
            time.sleep(0.3)
    engine.terminate()
    raise RuntimeError("fake engine did not start")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine-url", default="http://localhost:8765", help="the running translator")
    parser.add_argument("--api-key", default=os.environ.get("ATTENDEE_API_KEY", "sim-key"), help="must match the translator's ATTENDEE_API_KEY")
    parser.add_argument("--meeting-host", default="0.0.0.0", help="where the Attendee stand-in listens")
    parser.add_argument("--meeting-port", type=int, default=8796)
    parser.add_argument("--voices-dir", default=None, help=f"folder with the speakers' Piper voices: {', '.join(SPEAKER_VOICES.values())}")
    parser.add_argument("--listener-model", default="small", help="Whisper model for the listener check ('' to skip)")
    parser.add_argument("--settle-s", type=float, default=20.0, help="silence after the bot joins, before anyone speaks")
    parser.add_argument("--idle-s", type=float, default=6.0, help="bot quiet this long after answering = turn over")
    parser.add_argument("--noise-s", type=float, default=20.0, help="room noise with nobody talking, before the first turn; the bot must stay silent")
    parser.add_argument("--turn-timeout-s", type=float, default=300.0)
    parser.add_argument("--dashboard", action="store_true", help="send the bot from bot.html in a headless browser and screenshot it")
    parser.add_argument("--chromium-path", default=None)
    parser.add_argument("--fake-engine", action="store_true", help="start an engine with fake providers (wiring check only)")
    parser.add_argument("--info", action="append", default=[], help="KEY=VALUE line for the report header")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    engine = None
    tmp = tempfile.TemporaryDirectory()
    engine_url = args.engine_url.rstrip("/")
    if args.fake_engine:
        port = 8798
        engine = start_fake_engine(Path(tmp.name), port, args.meeting_port, args.api_key)
        engine_url = f"http://127.0.0.1:{port}"
    try:
        passed = asyncio.run(simulate(args, engine_url, args.api_key))
    finally:
        if engine is not None:
            engine.terminate()
            engine.wait(timeout=10)
        tmp.cleanup()
    print("PASS" if passed else "FAIL", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
