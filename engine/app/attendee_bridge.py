"""Bridge between an Attendee meeting bot and our translation Pipeline.

Attendee (https://attendee.dev - self-hosted meeting-bot API, Elastic License
2.0) runs the headless browser / Zoom SDK client that actually sits in the
Zoom or Google Meet call. With `websocket_settings.audio` set on the bot, it
opens a websocket to us and streams the meeting's mixed audio in; we stream
the bot's voice back out. Protocol (per Attendee's realtime-audio docs):

    in:  {"bot_id": "...", "trigger": "realtime_audio.mixed",
          "data": {"chunk": <base64 PCM16 mono>, "sample_rate": 16000, "timestamp_ms": ...}}
    out: {"trigger": "realtime_audio.bot_output",
          "data": {"chunk": <base64 PCM16 mono>, "sample_rate": 16000}}

One Pipeline per bot connection. It already auto-detects the spoken language
and flips translation direction per utterance, so one bot handles both sides
of an EN<->AR conversation.

The Pipeline runs in background mode: it keeps listening and transcribing
while earlier phrases are translated and spoken, in order.

Zoom/Meet never send the bot its own audio, but its voice can still come back:
out of one participant's speaker and into another's microphone (two phones in
one room, a laptop without headphones). Translated again, that loops. So the
bridge drops a transcript that matches something the bot said moments ago
(EchoGuard). With half_duplex it also ignores the meeting while the bot
speaks, and for ECHO_TAIL_S after - for whole-sentence (consecutive)
interpretation, where people wait for the bot. Phrase by phrase
(vad.phrase_min_ms) the bot speaks while the speaker carries on, so the
meeting is never ignored: that would cut them off mid-sentence.

BotControls holds what the dashboard and the phone can switch mid-meeting:
muted, the bot stops speaking (and skips synthesizing), captions keep going.

Caption events are also published per bot_id (see BotEventHub) so the web
dashboard and the Android app can show what the bot heard and said.
"""

from __future__ import annotations

import asyncio
import audioop
import base64
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from difflib import SequenceMatcher

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect

from app.chat_captions import ChatCaptioner, caption_line
from app.logging_utils import get_logger, log_event
from app.meeting_record import MeetingRecorder
from app.pipeline import Pipeline
from app.schema import EventType, PipelineEvent
from app.text_normalize import normalize_text

ATTENDEE_SAMPLE_RATE = 16000  # the meeting audio we receive
# The bot's voice goes out at the neural voices' own rate, so it isn't
# resampled at all (going down to 16 kHz without a filter folded the "s"
# sounds into a harsh hiss). Attendee repeats each sample to reach the
# meeting's 48 kHz (1.79.2, realtime_audio_output_manager.py) - from 24 kHz
# its side effects land higher, where they're far less audible. (Sending 48 kHz
# would skip that, but Attendee 1.79.2 mishandles an already-matching rate.)
OUTPUT_SAMPLE_RATE = 24000
# Softer "s": flat to 4 kHz, gently down to -6 dB at 7 kHz, gone by 8.5 kHz
# - so what Attendee's sample repeating mirrors upwards lands above ~15.5 kHz.
DEESS_POINTS_HZ_DB = ((0, 0.0), (4000, 0.0), (5500, -3.0), (7000, -6.0), (7800, -9.0), (8500, -40.0))
OUTPUT_CHUNK_MS = 100
# How long after the bot's last sound the meeting audio stays ignored: the
# round trip of its voice through the call to someone's speaker and back in
# through a microphone.
ECHO_TAIL_S = 1.5
# A transcript this similar to something the bot said within ECHO_WINDOW_S,
# in the same language, is its own voice coming back.
ECHO_SIMILARITY = 0.6
ECHO_WINDOW_S = 30.0
# What the dashboard/app render. Partials would churn the replay history
# during a long meeting without ever being shown.
CAPTION_EVENT_TYPES = {EventType.FINAL, EventType.TRANSLATION, EventType.ERROR}


def _put_dropping_oldest(queue: asyncio.Queue[str], message: str) -> None:
    if queue.full():
        queue.get_nowait()  # a stalled viewer drops its oldest caption; it never blocks the bot
    queue.put_nowait(message)


class BotEventHub:
    """In-process fan-out of caption events to dashboard/app listeners, per bot.

    Keeps a bounded history per bot and replays it on subscribe, so a viewer
    that connects late (or refreshes mid-meeting) still sees the conversation
    so far instead of only what's said after it connected.
    """

    def __init__(self, history: int = 200) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)
        self._history: dict[str, deque[str]] = defaultdict(lambda: deque(maxlen=history))

    def subscribe(self, bot_id: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        for message in self._history.get(bot_id, ()):
            _put_dropping_oldest(queue, message)
        self._subscribers[bot_id].add(queue)
        return queue

    def unsubscribe(self, bot_id: str, queue: asyncio.Queue[str]) -> None:
        self._subscribers[bot_id].discard(queue)

    def publish(self, bot_id: str, message: str) -> None:
        self._history[bot_id].append(message)
        for queue in list(self._subscribers.get(bot_id, ())):
            _put_dropping_oldest(queue, message)


class BotControls:
    """Per-bot switches the dashboard, the phone and the meeting chat flip
    mid-meeting, and when each bot's audio was last heard.

    voice (TTS): off = the bot stays silent AND no voice is synthesized.
    text: what is typed in the meeting chat - "off", "translation" or "both"
        (what was heard as well); a language can be switched off on its own.
    hold: the voice is made while someone talks but spoken once they stop.
    paused: the bot ignores the meeting entirely (no transcription, no
        translation, no voice) until resumed - for a private aside."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        voice_default: bool = True,
        text_default: str = "translation",
        hold_default: bool = True,
    ) -> None:
        self._muted: set[str] = set()
        self._unmuted: set[str] = set()  # explicitly on, when voice_default is off
        self._voice_default = voice_default
        # Languages the bot doesn't speak for now. The meeting mixes the bot's
        # voice for everyone, so nobody can mute it for themselves alone - but
        # each side only needs the translations into its own language, so
        # silencing one language is how one side turns it off for itself.
        self._silent_languages: dict[str, set[str]] = defaultdict(set)
        self._text_default = text_default
        self._text_mode: dict[str, str] = {}
        self._text_off_languages: dict[str, set[str]] = defaultdict(set)
        self._paused: set[str] = set()
        self._hold_default = hold_default
        self._hold: dict[str, bool] = {}
        self._heard: dict[str, float] = {}
        self._clock = clock

    def set_muted(self, bot_id: str, muted: bool) -> None:
        if muted:
            self._muted.add(bot_id)
            self._unmuted.discard(bot_id)
        else:
            self._muted.discard(bot_id)
            self._unmuted.add(bot_id)

    def is_muted(self, bot_id: str) -> bool:
        return bot_id in self._muted or (not self._voice_default and bot_id not in self._unmuted)

    def set_language_muted(self, bot_id: str, lang: str, muted: bool) -> None:
        if muted:
            self._silent_languages[bot_id].add(lang)
        else:
            self._silent_languages[bot_id].discard(lang)

    def muted_languages(self, bot_id: str) -> set[str]:
        return set(self._silent_languages.get(bot_id, ()))

    def speaks(self, bot_id: str, lang: str) -> bool:
        return not self.is_muted(bot_id) and lang not in self._silent_languages.get(bot_id, ())

    def text_mode(self, bot_id: str) -> str:
        return self._text_mode.get(bot_id, self._text_default)

    def set_text_mode(self, bot_id: str, mode: str) -> None:
        self._text_mode[bot_id] = mode

    def set_text_language_off(self, bot_id: str, lang: str, off: bool) -> None:
        if off:
            self._text_off_languages[bot_id].add(lang)
        else:
            self._text_off_languages[bot_id].discard(lang)

    def text_off_languages(self, bot_id: str) -> set[str]:
        return set(self._text_off_languages.get(bot_id, ()))

    def types_text(self, bot_id: str, lang: str) -> bool:
        """Whether translations into `lang` are typed in the meeting chat."""
        return self.text_mode(bot_id) != "off" and lang not in self._text_off_languages.get(bot_id, ())

    def set_paused(self, bot_id: str, paused: bool) -> None:
        if paused:
            self._paused.add(bot_id)
        else:
            self._paused.discard(bot_id)

    def is_paused(self, bot_id: str) -> bool:
        return bot_id in self._paused

    def set_hold(self, bot_id: str, hold: bool) -> None:
        self._hold[bot_id] = hold

    def holds_voice(self, bot_id: str) -> bool:
        """Whether the voice waits for the speaker to stop before it speaks."""
        return self._hold.get(bot_id, self._hold_default)

    def heard_from(self, bot_id: str) -> None:
        self._heard[bot_id] = self._clock()

    def seconds_since_heard(self, bot_id: str) -> float | None:
        """None if this bot's audio hasn't reached this server since it started."""
        heard = self._heard.get(bot_id)
        return None if heard is None else self._clock() - heard


class EchoGuard:
    """Remembers what the bot said recently, to recognize it when it comes back."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._said: deque[tuple[float, str, str]] = deque(maxlen=20)

    def said(self, text: str, lang: str) -> None:
        self._said.append((self._clock(), lang, normalize_text(text)))

    def is_echo(self, text: str, lang: str) -> bool:
        heard = normalize_text(text)
        now = self._clock()
        return any(
            now - at <= ECHO_WINDOW_S and said_lang == lang and SequenceMatcher(None, heard, said).ratio() >= ECHO_SIMILARITY
            for at, said_lang, said in self._said
        )


def resample(pcm16: bytes, src_rate: int, dst_rate: int) -> bytes:
    if src_rate == dst_rate:
        return pcm16
    converted, _ = audioop.ratecv(pcm16, 2, 1, src_rate, dst_rate, None)
    return converted


def voice_for_meeting(pcm16: bytes, sample_rate: int) -> bytes:
    """One whole TTS clip, de-essed and at OUTPUT_SAMPLE_RATE - both done in
    the frequency domain, so any source rate (Piper's 22050) is resampled
    cleanly too."""
    samples = np.frombuffer(pcm16, dtype=np.int16).astype(np.float64)
    if len(samples) < 2:
        return pcm16
    spectrum = np.fft.rfft(samples)
    freqs = np.fft.rfftfreq(len(samples), 1.0 / sample_rate)
    points_hz, points_db = zip(*DEESS_POINTS_HZ_DB)
    spectrum *= 10 ** (np.interp(freqs, points_hz, points_db, right=-120.0) / 20)
    n_out = round(len(samples) * OUTPUT_SAMPLE_RATE / sample_rate)
    if n_out != len(samples):
        resized = np.zeros(n_out // 2 + 1, dtype=complex)
        keep = min(len(resized), len(spectrum))
        resized[:keep] = spectrum[:keep]
        spectrum = resized * (n_out / len(samples))
    out = np.fft.irfft(spectrum, n_out)
    return np.clip(np.round(out), -32768, 32767).astype(np.int16).tobytes()


def bot_output_messages(pcm16: bytes, sample_rate: int) -> list[str]:
    """Split one TTS clip into Attendee bot_output messages of OUTPUT_CHUNK_MS each."""
    audio = voice_for_meeting(pcm16, sample_rate)
    chunk_bytes = OUTPUT_SAMPLE_RATE * OUTPUT_CHUNK_MS // 1000 * 2
    return [
        json.dumps(
            {
                "trigger": "realtime_audio.bot_output",
                "data": {
                    "chunk": base64.b64encode(audio[offset : offset + chunk_bytes]).decode("ascii"),
                    "sample_rate": OUTPUT_SAMPLE_RATE,
                },
            }
        )
        for offset in range(0, len(audio), chunk_bytes)
    ]


async def run_bridge(
    ws: WebSocket,
    pipeline_factory: Callable[..., Pipeline],
    pipeline_sample_rate: int,
    frame_bytes: int,
    hub: BotEventHub,
    controls: BotControls | None = None,
    half_duplex: bool = True,
    chat: ChatCaptioner | None = None,
    hold_release_s: float = 0.3,
    hold_max_s: float = 30.0,
    recorder: MeetingRecorder | None = None,
    on_disconnect: Callable[[str], None] | None = None,
) -> None:
    """Serve one Attendee bot connection until it disconnects.

    pipeline_factory(accept_transcript=..., should_speak=..., background=True)
    builds the Pipeline, passing these on to it.
    """
    logger = get_logger()
    controls = controls or BotControls()
    echoes = EchoGuard()
    bot_id = "unknown"
    speaking_until = 0.0  # monotonic time until which the meeting audio is ignored
    gated_frames = 0
    closing = False  # the meeting is over: nothing is held any more

    def accept_transcript(text: str, lang: str) -> bool:
        if echoes.is_echo(text, lang):
            log_event(logger, logging.INFO, "bot_echo_dropped", bot_id=bot_id, lang=lang)
            return False
        return True

    pipeline = pipeline_factory(
        accept_transcript=accept_transcript,
        should_speak=lambda lang: controls.speaks(bot_id, lang),
        background=True,
    )
    frames: asyncio.Queue[bytes | None] = asyncio.Queue()
    outgoing: asyncio.Queue[list[str] | None] = asyncio.Queue()  # one item per clip of the bot's voice

    async def wait_for_quiet() -> None:
        """The voice is ready; hold it while someone is talking, then speak once
        they have been quiet for hold_release_s. Gives up after hold_max_s, so
        a long monologue is still interpreted."""
        started = quiet_since = time.monotonic()
        while not closing and time.monotonic() - started < hold_max_s:
            if pipeline.speaker_active():
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= hold_release_s:
                return
            await asyncio.sleep(0.05)

    async def send_paced() -> None:
        # Real-time pacing, so Attendee receives the bot's voice the way a live
        # microphone would deliver it, whatever its own buffering behaviour is.
        nonlocal speaking_until
        while (clip := await outgoing.get()) is not None:
            if controls.holds_voice(bot_id):
                await wait_for_quiet()
            for message in clip:
                if controls.is_muted(bot_id):
                    break  # muted mid-sentence: the rest of it is dropped, not delayed
                await ws.send_text(message)
                speaking_until = time.monotonic() + OUTPUT_CHUNK_MS / 1000 + ECHO_TAIL_S
                await asyncio.sleep(OUTPUT_CHUNK_MS / 1000)

    def handle(event: PipelineEvent) -> None:
        if event.type is EventType.AUDIO and event.audio:
            echoes.said(event.text, event.lang)
            outgoing.put_nowait(bot_output_messages(event.audio, event.audio_sample_rate or OUTPUT_SAMPLE_RATE))
        elif event.type in CAPTION_EVENT_TYPES:
            hub.publish(bot_id, event.model_dump_json(exclude={"audio", "speech_ms", "merged_turn_ids"}))
            if recorder is not None:
                recorder.record(bot_id, event)  # just written down: nothing is summarized while the meeting runs
            if chat is not None and event.text and event.type is not EventType.ERROR and controls.types_text(bot_id, event.lang):
                # Typed in the meeting's chat the moment it exists: a translation
                # (always, unless text is off), what was heard too in "both" mode.
                if event.type is EventType.TRANSLATION or controls.text_mode(bot_id) == "both":
                    chat.post(bot_id, caption_line(event.lang, event.text))

    async def process() -> None:
        # Its own task, so the socket keeps being read while a phrase is being
        # transcribed (seconds, on CPU) - the meeting's audio queues here in
        # order instead of backing up inside Attendee.
        while (frame := await frames.get()) is not None:
            async for event in pipeline.process_frame(frame):
                handle(event)
        async for event in pipeline.flush():  # also waits for the translation queue
            handle(event)

    async def interpret() -> None:
        async for event in pipeline.background_events():
            handle(event)

    sender = asyncio.create_task(send_paced())
    interpreter = asyncio.create_task(interpret())
    processor = asyncio.create_task(process())
    log_event(logger, logging.INFO, "attendee_bot_connected")
    buffer = bytearray()
    try:
        while True:
            message = json.loads(await ws.receive_text())
            if processor.done():
                break  # processing crashed - the finally below re-raises why
            if message.get("trigger") != "realtime_audio.mixed":
                continue
            bot_id = message.get("bot_id", bot_id)
            controls.heard_from(bot_id)
            data = message["data"]
            chunk = resample(
                base64.b64decode(data["chunk"]), data.get("sample_rate", ATTENDEE_SAMPLE_RATE), pipeline_sample_rate
            )
            if controls.is_paused(bot_id) or (half_duplex and time.monotonic() < speaking_until):
                # Silence rather than dropping the audio, so the endpointer's
                # timing stays true and an open utterance still ends.
                chunk = bytes(len(chunk))
                gated_frames += 1
            buffer += chunk
            while len(buffer) >= frame_bytes:
                frames.put_nowait(bytes(buffer[:frame_bytes]))
                del buffer[:frame_bytes]
    except WebSocketDisconnect:
        pass
    finally:
        frames.put_nowait(None)
        try:
            await processor  # finish what was already heard, so its captions still reach the dashboard
            await interpreter
        finally:
            interpreter.cancel()  # only still running if processing crashed
            closing = True
            pipeline.close()
            if chat is not None:
                await chat.close(bot_id)
            outgoing.put_nowait(None)
            try:
                await sender
            except (RuntimeError, WebSocketDisconnect):
                pass  # Attendee already closed the socket; nothing left to deliver to
            if on_disconnect is not None and bot_id != "unknown":
                on_disconnect(bot_id)
            log_event(logger, logging.INFO, "attendee_bot_disconnected", bot_id=bot_id, chunks_ignored_while_speaking=gated_frames)
