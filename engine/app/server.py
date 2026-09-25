"""FastAPI WebSocket server: PCM16 frames in, JSON PipelineEvents out. Also
serves the browser client (app/static/) from the same process, so one
command gets you both the API and a usable UI - open http://host:port/ in
any modern browser, on any platform (see app/static/ + README "Web client").

    ws://host:port/ws  - binary WS messages are 16kHz mono PCM16 frames
                          (20-30ms each); the server streams back one JSON
                          text message per PipelineEvent (see app/schema.py).
                          When tts.provider != "none", AUDIO events carry
                          base64 PCM16 in `audio` + `audio_sample_rate`.

Run with (from the engine/ directory):
    uvicorn app.server:app --host 0.0.0.0 --port 8000

For any client other than localhost, browsers require HTTPS to grant
microphone access - see README "HTTPS for the web client" for a self-signed
cert + `--ssl-keyfile`/`--ssl-certfile` uvicorn flags.

One Pipeline (and its VAD/ASR session state) is created per connection, since
speech endpointing is inherently per-stream. The ASR and Translator provider
instances are created once at process startup and shared across connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.attendee_bridge import ATTENDEE_SAMPLE_RATE, BotControls, BotEventHub, run_bridge
from app.attendee_client import AttendeeClient, AttendeeError, AttendeeSettings
from app.config import load_config
from app.logging_utils import configure_logging, get_logger, log_event
from app.meeting_chat import MUTED, UNMUTED, announce, watch_chat
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider, build_tts_provider

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_cfg = load_config()
_logger = configure_logging(_cfg.logging)
_asr = None  # for /ws only - built on first use, so a meeting-bot-only server never loads a model it doesn't need
_asr_lock = asyncio.Lock()
# One ASR instance loaded ahead of time for the next meeting bot (INTERPRETER_PRELOAD_ASR,
# set by docker-compose.yml): the bot's first sentence doesn't wait for Whisper to
# load, and the dashboard can tell right away whether it runs on the GPU.
_spare_asr: Any = None
_spare_asr_lock = threading.Lock()


def _preload_spare_asr() -> None:
    global _spare_asr
    try:
        asr = build_asr_provider(_cfg.asr)
    except Exception as error:  # the bot will try again when it connects, and log why
        log_event(_logger, logging.ERROR, "asr_preload_failed", error=repr(error))
        return
    with _spare_asr_lock:
        _spare_asr = asr
    log_event(_logger, logging.INFO, "asr_preloaded")


def _take_spare_asr() -> Any:
    global _spare_asr
    with _spare_asr_lock:
        asr, _spare_asr = _spare_asr, None
    return asr


if os.environ.get("INTERPRETER_PRELOAD_ASR") == "1":
    threading.Thread(target=_preload_spare_asr, name="asr-preload", daemon=True).start()
_translator = build_translator_provider(_cfg.translator)
_tts = build_tts_provider(_cfg.tts)  # None when tts.provider: none - captions only

# Anyone who can reach /attendee/ws could otherwise speak into the meeting
# through the bot, so the URL handed to Attendee carries a secret.
_BRIDGE_TOKEN = os.environ.get("ATTENDEE_BRIDGE_TOKEN") or secrets.token_urlsafe(24)
_bot_hub = BotEventHub()
_bot_controls = BotControls()

app = FastAPI(title="Arabic<->English Speech Translation Engine")


class CreateBotRequest(BaseModel):
    meeting_url: str
    bot_name: str = "AI Interpreter"


class MuteRequest(BaseModel):
    muted: bool


def _attendee_client() -> AttendeeClient:
    settings = AttendeeSettings.from_env()
    if settings is None:
        raise HTTPException(503, "Attendee is not configured - set ATTENDEE_BASE_URL and ATTENDEE_API_KEY in .env")
    return AttendeeClient(settings)


# Shown when Attendee can't be reached at all. With docker-compose.yml's bundled
# Attendee that almost always means it is still starting (first start runs its
# database migrations), so say that rather than a raw connection error.
_ATTENDEE_UNREACHABLE = "The meeting service is still starting. Wait a minute and try again."


def _bridge_ws_url(request: Request) -> str:
    """Where Attendee should connect back to. ATTENDEE_CALLBACK_WS_URL wins; otherwise
    derived from however this server was reached, which only works for Attendee if
    that address is reachable from wherever Attendee runs (not localhost inside Docker)."""
    base = os.environ.get("ATTENDEE_CALLBACK_WS_URL", "").rstrip("/")
    if not base:
        scheme = "wss" if request.url.scheme == "https" else "ws"
        base = f"{scheme}://{request.url.netloc}/attendee/ws"
    return f"{base}?token={_BRIDGE_TOKEN}"


# Rejections a person can act on, phrased for them. Attendee's own wording
# points at its web UI (e.g. "add Zoom credentials at http://localhost:8000/..."),
# which the bundled setup doesn't expose.
_ATTENDEE_REJECTIONS = {
    "Zoom App credentials are required": (
        "Zoom meetings need a Zoom developer app set up first. Google Meet and Microsoft Teams "
        "work right away - send the interpreter to a Meet or Teams link instead."
    ),
    "exceeded the maximum number of concurrent bots": (
        "The interpreter is already in another meeting. Remove it from that meeting first."
    ),
}


def _explain_attendee_rejection(detail: str) -> str | None:
    for marker, message in _ATTENDEE_REJECTIONS.items():
        if marker in detail:
            return message
    return None


async def _call_attendee(call: Any) -> dict[str, Any]:
    client = _attendee_client()
    try:
        return await call(client)
    except AttendeeError as error:
        friendly = _explain_attendee_rejection(error.detail)
        if friendly:
            raise HTTPException(400, friendly) from error
        raise HTTPException(502, f"Attendee rejected the request ({error.status_code}): {error.detail}") from error
    except (OSError, httpx.TransportError) as error:
        log_event(_logger, logging.WARNING, "attendee_unreachable", base_url=os.environ.get("ATTENDEE_BASE_URL"), error=repr(error))
        raise HTTPException(503, _ATTENDEE_UNREACHABLE) from error
    finally:
        await client.aclose()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    # "service" lets the Android app tell this server apart from anything else
    # answering on port 8765 while it scans the Wi-Fi for it.
    return {"status": "ok", "service": "interpreter"}


async def _attendee_status() -> tuple[bool, str | None]:
    """(ready, problem) - problem is a plain sentence for the dashboard/app, or None."""
    settings = AttendeeSettings.from_env()
    if settings is None:
        return False, "Attendee is not configured: set ATTENDEE_BASE_URL and ATTENDEE_API_KEY."
    client = AttendeeClient(settings, timeout=5.0)
    try:
        await client.check()
        return True, None
    except AttendeeError as error:
        if error.status_code in (401, 403):
            return False, "Attendee refused this server's API key."
        return False, f"Attendee answered with an error ({error.status_code})."
    except (OSError, httpx.TransportError):
        return False, _ATTENDEE_UNREACHABLE
    finally:
        await client.aclose()


@app.get("/api/bots/config")
async def bots_config(request: Request) -> dict[str, Any]:
    callback = _bridge_ws_url(request).split("?", 1)[0]
    auto_derived = not os.environ.get("ATTENDEE_CALLBACK_WS_URL")
    ready, problem = await _attendee_status()
    return {
        "attendee_configured": AttendeeSettings.from_env() is not None,
        "attendee_ready": ready,
        "attendee_problem": problem,
        "tts_enabled": _tts is not None,
        "callback_ws_url": callback,
        # Only worth warning about when guessed from how this page was opened -
        # an explicit localhost setting is a deliberate same-machine setup.
        "callback_is_localhost": auto_derived and any(host in callback for host in ("localhost", "127.0.0.1")),
        # Attendee rejects any other scheme when the bot is created.
        "callback_is_secure": callback.startswith("wss://"),
        # Set by start.sh / "Start Interpreter.bat" to this computer's Wi-Fi address.
        "phone_url": os.environ.get("INTERPRETER_PHONE_URL") or None,
        # What speech recognition runs on: configured, or - once it has loaded -
        # what actually loaded (the GPU may have fallen back to the CPU).
        "speech_on_gpu": _speech_on_gpu(),
        "hardware": await _hardware_report(),
    }


async def _hardware_report() -> dict[str, Any]:
    """Where each model runs and, when it's not the graphics card, why - for the
    dashboard, so a screenshot of it is enough to see what happened."""
    report: dict[str, Any] = {
        "gpu_check": os.environ.get("INTERPRETER_GPU_STATUS") or None,  # the start scripts' check
        "speech_model": None,
        "speech_gpu_error": None,
        "translation_model": _cfg.translator.ollama.model if _cfg.translator.provider == "ollama" else _cfg.translator.provider,
        "translation_on_gpu": None,
        "voices": _cfg.tts.provider,
    }
    if _cfg.tts.provider == "neural":
        from app.providers import tts_neural

        report["voices"] = {"provider": "neural", "spoken": dict(tts_neural.spoken), "last_error": tts_neural.last_error}
    if _cfg.asr.provider == "faster_whisper":
        from app.providers import asr_faster_whisper

        report["speech_model"] = asr_faster_whisper.last_loaded
        report["speech_gpu_error"] = asr_faster_whisper.last_gpu_error
    if _cfg.translator.provider == "ollama":
        try:
            async with httpx.AsyncClient(base_url=_cfg.translator.ollama.base_url, timeout=2.0) as client:
                loaded = (await client.get("/api/ps")).json().get("models") or []
            model = next((m for m in loaded if m.get("name") == _cfg.translator.ollama.model or m.get("model") == _cfg.translator.ollama.model), None)
            if model is not None:  # only known once it has translated something
                report["translation_on_gpu"] = (model.get("size_vram") or 0) > 0
        except (httpx.HTTPError, ValueError):
            pass
    return report


def _speech_on_gpu() -> bool:
    if _cfg.asr.provider != "faster_whisper":
        return False
    from app.providers import asr_faster_whisper

    if asr_faster_whisper.last_loaded is not None:
        return asr_faster_whisper.last_loaded.endswith(" on cuda")
    return _cfg.asr.device == "cuda"


# Keeps fire-and-forget tasks referenced until they finish (asyncio holds only weak references).
_background_tasks: set[asyncio.Task[None]] = set()


def _in_background(coro: Any) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.post("/api/bots")
async def create_bot(body: CreateBotRequest, request: Request) -> dict[str, Any]:
    ws_url = _bridge_ws_url(request)
    bot = await _call_attendee(
        lambda client: client.create_bot(body.meeting_url, body.bot_name, ws_url, ATTENDEE_SAMPLE_RATE)
    )
    if bot.get("id"):
        # "mute" / "unmute" typed in the meeting's chat (app/meeting_chat.py)
        _in_background(watch_chat(bot["id"], _bot_controls))
    return bot


# Attendee's reason codes (bots/models.py BotEventSubTypes upstream) for why a
# bot couldn't join or had to leave, as something to tell the person in front
# of the dashboard or the phone. Codes not listed fall back to the code itself.
_BOT_PROBLEMS = {
    "meeting_not_found": "That meeting link doesn't work. Check the link and try again.",
    "meeting_not_started_waiting_for_host": "The meeting hasn't started yet. Start it, then send the bot again.",
    "request_to_join_denied": "Someone in the meeting declined the bot's request to join.",
    "waiting_room_timeout_exceeded": "Nobody let the bot in from the waiting room in time.",
    "login_required": "This meeting only lets signed-in users join. Allow guests in the meeting's settings.",
    "blocked_by_captcha": "The meeting service asked the bot to solve a captcha. Try again in a few minutes.",
    "unable_to_connect_to_meeting": "The bot couldn't connect to the meeting. Check this computer's internet connection.",
    "zoom_authorization_failed": "Zoom bots need Zoom developer credentials. Use Google Meet or Teams instead.",
    "unpublished_zoom_app": "Zoom bots need Zoom developer credentials. Use Google Meet or Teams instead.",
    "zoom_app_cannot_join_anonymously": "This Zoom meeting doesn't allow bots to join. Use Google Meet or Teams instead.",
    "meeting_ended_before_bot_joined": "The meeting ended before the bot got in.",
    "auto_leave_only_participant_in_meeting": "The bot left because everyone else had left.",
    "auto_leave_silence": "The bot left after a long silence.",
    "process_terminated": "The bot stopped unexpectedly. This computer may be out of memory.",
    "heartbeat_timeout": "The bot stopped responding. This computer may be out of memory.",
    "bot_not_launched": "The bot didn't start. Restart the interpreter and try again.",
}


def _bot_problem(bot: dict[str, Any]) -> str | None:
    for event in reversed(bot.get("events") or []):
        code = event.get("sub_type")
        if code and code not in ("user_requested", "leave_requested_before_bot_joined"):
            return _BOT_PROBLEMS.get(code, code.replace("_", " ").capitalize() + ".")
    return None


@app.get("/api/bots/{bot_id}")
async def get_bot(bot_id: str) -> dict[str, Any]:
    bot = await _call_attendee(lambda client: client.get_bot(bot_id))
    bot["problem"] = _bot_problem(bot)
    bot["muted"] = _bot_controls.is_muted(bot_id)
    return bot


@app.post("/api/bots/{bot_id}/mute")
async def mute_bot(bot_id: str, body: MuteRequest) -> dict[str, Any]:
    """Stops (or restarts) the bot's voice in the meeting; its captions keep going."""
    changed = _bot_controls.is_muted(bot_id) != body.muted
    _bot_controls.set_muted(bot_id, body.muted)
    log_event(_logger, logging.INFO, "attendee_bot_muted" if body.muted else "attendee_bot_unmuted", bot_id=bot_id)
    if changed:  # so everyone in the call knows why it went quiet, and how to undo it
        _in_background(announce(bot_id, MUTED if body.muted else UNMUTED))
    return {"id": bot_id, "muted": body.muted}


@app.post("/api/bots/{bot_id}/leave")
async def leave_bot(bot_id: str) -> dict[str, Any]:
    return await _call_attendee(lambda client: client.leave_bot(bot_id))


@app.websocket("/api/bots/{bot_id}/events")
async def bot_events(ws: WebSocket, bot_id: str) -> None:
    await ws.accept()
    queue = _bot_hub.subscribe(bot_id)

    async def forward() -> None:
        while True:
            await ws.send_text(await queue.get())

    forwarder = asyncio.create_task(forward())
    try:
        # This socket only ever sends, so the viewer leaving is only noticed by
        # listening for it - otherwise the handler waits on queue.get() forever
        # and holds up server shutdown.
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await forwarder
        _bot_hub.unsubscribe(bot_id, queue)


@app.websocket("/attendee/ws")
async def attendee_bridge(ws: WebSocket) -> None:
    if not secrets.compare_digest(ws.query_params.get("token", ""), _BRIDGE_TOKEN):
        await ws.close(code=1008)
        return
    await ws.accept()
    # Load the translation model while the bot is still being admitted, so the
    # first sentence of the meeting doesn't also pay for it.
    warm_up = asyncio.create_task(_warm_up_translator())
    # AsrProvider holds per-utterance state, so each bot gets its own instance;
    # constructing one loads the ASR model, which must not block the event loop.
    asr = _take_spare_asr() or await asyncio.to_thread(build_asr_provider, _cfg.asr)
    frame_bytes = _cfg.audio.frame_samples * 2
    await run_bridge(
        ws,
        lambda **hooks: Pipeline(_cfg, asr, _translator, _tts, emit_partials=False, **hooks),
        _cfg.audio.sample_rate_hz,
        frame_bytes,
        _bot_hub,
        _bot_controls,
    )
    warm_up.cancel()


async def _warm_up_translator() -> None:
    try:
        await _translator.translate("Hello.", _cfg.translator.source_lang, _cfg.translator.target_lang)
    except Exception as error:  # the real first sentence will surface a persistent failure
        log_event(_logger, logging.WARNING, "translator_warm_up_failed", error=repr(error))


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    global _asr
    await ws.accept()
    async with _asr_lock:
        if _asr is None:
            _asr = await asyncio.to_thread(build_asr_provider, _cfg.asr)
    pipeline = Pipeline(_cfg, _asr, _translator, _tts)
    log_event(_logger, logging.INFO, "ws_connected")
    try:
        while True:
            pcm16 = await ws.receive_bytes()
            async for event in pipeline.process_frame(pcm16):
                await ws.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        pass
    finally:
        try:
            async for event in pipeline.flush():
                await ws.send_text(event.model_dump_json())
        except RuntimeError:
            pass  # socket already closed while flushing the last utterance
        log_event(get_logger(), logging.INFO, "ws_disconnected")


# Mounted last so it never shadows /healthz or /ws above - Starlette matches
# routes in registration order, and StaticFiles(html=True) would otherwise
# happily "handle" any path under "/" itself.
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
