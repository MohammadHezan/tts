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

import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from app.config import load_config
from app.logging_utils import configure_logging, get_logger, log_event
from app.pipeline import Pipeline
from app.providers.base import build_asr_provider, build_translator_provider, build_tts_provider

_STATIC_DIR = Path(__file__).resolve().parent / "static"

_cfg = load_config()
_logger = configure_logging(_cfg.logging)
_asr = build_asr_provider(_cfg.asr)
_translator = build_translator_provider(_cfg.translator)
_tts = build_tts_provider(_cfg.tts)  # None when tts.provider: none - captions only

app = FastAPI(title="Arabic<->English Speech Translation Engine")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
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
