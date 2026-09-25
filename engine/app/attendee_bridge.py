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

Zoom/Meet never send the bot its own audio, but its voice can still come back:
out of one participant's speaker and into another's microphone (two phones in
one room, a laptop without headphones). Translated again, that loops. So the
bridge is half-duplex - while the bot speaks, and for ECHO_TAIL_S after, the
meeting's audio is replaced with silence - and it drops a transcript that
matches something the bot said moments ago. People talking over the
interpreter are not heard; with consecutive interpretation they wait for it.

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

from fastapi import WebSocket, WebSocketDisconnect

from app.logging_utils import get_logger, log_event
from app.pipeline import Pipeline
from app.schema import EventType, PipelineEvent
from app.text_normalize import normalize_text

ATTENDEE_SAMPLE_RATE = 16000
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
    """Per-bot switches the dashboard and the phone flip mid-meeting."""

    def __init__(self) -> None:
        self._muted: set[str] = set()

    def set_muted(self, bot_id: str, muted: bool) -> None:
        if muted:
            self._muted.add(bot_id)
        else:
            self._muted.discard(bot_id)

    def is_muted(self, bot_id: str) -> bool:
        return bot_id in self._muted


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


def bot_output_messages(pcm16: bytes, sample_rate: int) -> list[str]:
    """Split one TTS clip into Attendee bot_output messages of OUTPUT_CHUNK_MS each."""
    audio = resample(pcm16, sample_rate, ATTENDEE_SAMPLE_RATE)
    chunk_bytes = ATTENDEE_SAMPLE_RATE * OUTPUT_CHUNK_MS // 1000 * 2
    return [
        json.dumps(
            {
                "trigger": "realtime_audio.bot_output",
                "data": {
                    "chunk": base64.b64encode(audio[offset : offset + chunk_bytes]).decode("ascii"),
                    "sample_rate": ATTENDEE_SAMPLE_RATE,
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
) -> None:
    """Serve one Attendee bot connection until it disconnects.

    pipeline_factory(accept_transcript=..., should_speak=...) builds the
    Pipeline, passing both hooks on to it.
    """
    logger = get_logger()
    controls = controls or BotControls()
    echoes = EchoGuard()
    bot_id = "unknown"
    speaking_until = 0.0  # monotonic time until which the meeting audio is ignored
    gated_frames = 0

    def accept_transcript(text: str, lang: str) -> bool:
        if echoes.is_echo(text, lang):
            log_event(logger, logging.INFO, "bot_echo_dropped", bot_id=bot_id, lang=lang)
            return False
        return True

    pipeline = pipeline_factory(
        accept_transcript=accept_transcript,
        should_speak=lambda: not controls.is_muted(bot_id),
    )
    frames: asyncio.Queue[bytes | None] = asyncio.Queue()
    outgoing: asyncio.Queue[str | None] = asyncio.Queue()

    async def send_paced() -> None:
        # Real-time pacing, so Attendee receives the bot's voice the way a live
        # microphone would deliver it, whatever its own buffering behaviour is.
        nonlocal speaking_until
        while (message := await outgoing.get()) is not None:
            if controls.is_muted(bot_id):
                continue  # muted mid-sentence: the rest of it is dropped, not delayed
            await ws.send_text(message)
            speaking_until = time.monotonic() + OUTPUT_CHUNK_MS / 1000 + ECHO_TAIL_S
            await asyncio.sleep(OUTPUT_CHUNK_MS / 1000)

    def handle(event: PipelineEvent) -> None:
        if event.type is EventType.AUDIO and event.audio:
            echoes.said(event.text, event.lang)
            for message in bot_output_messages(event.audio, event.audio_sample_rate or ATTENDEE_SAMPLE_RATE):
                outgoing.put_nowait(message)
        elif event.type in CAPTION_EVENT_TYPES:
            hub.publish(bot_id, event.model_dump_json(exclude={"audio"}))

    async def process() -> None:
        # Its own task, so the socket keeps being read while a turn is being
        # transcribed, translated and spoken (seconds, on CPU) - the meeting's
        # audio queues here in order instead of backing up inside Attendee.
        while (frame := await frames.get()) is not None:
            async for event in pipeline.process_frame(frame):
                handle(event)
        async for event in pipeline.flush():
            handle(event)

    sender = asyncio.create_task(send_paced())
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
            data = message["data"]
            chunk = resample(
                base64.b64decode(data["chunk"]), data.get("sample_rate", ATTENDEE_SAMPLE_RATE), pipeline_sample_rate
            )
            if time.monotonic() < speaking_until:
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
        finally:
            outgoing.put_nowait(None)
            try:
                await sender
            except (RuntimeError, WebSocketDisconnect):
                pass  # Attendee already closed the socket; nothing left to deliver to
            log_event(logger, logging.INFO, "attendee_bot_disconnected", bot_id=bot_id, chunks_ignored_while_speaking=gated_frames)
