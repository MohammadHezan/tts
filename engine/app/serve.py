"""Runs the engine the way docker-compose.yml needs it: the same app on two ports.

    :8000  plain HTTP - the meeting-bot dashboard, the Android app, the web client
    :8443  TLS        - only for Attendee's bot, which refuses to connect to
                        anything but a wss:// URL (bots/serializers.py upstream)

One process, not two, because the bridge Attendee connects to and the dashboard
that shows its captions share in-memory state (the bot token, BotEventHub). The
TLS port starts only when TLS_CERT_FILE and TLS_KEY_FILE are set - the bundled
setup generates both on first start (deploy/setup_secrets.py).

    python -m app.serve        (from engine/)
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from collections.abc import Iterator

import uvicorn


class _Server(uvicorn.Server):
    # uvicorn.Server installs its own SIGINT/SIGTERM handlers, and with two of
    # them only the last one installed would ever hear a signal - leaving the
    # other port running until Docker gives up and kills the container.
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def build_configs(app: object) -> list[uvicorn.Config]:
    host = os.environ.get("HOST", "0.0.0.0")
    configs = [uvicorn.Config(app, host=host, port=int(os.environ.get("PORT", "8000")), lifespan="off")]
    cert, key = os.environ.get("TLS_CERT_FILE"), os.environ.get("TLS_KEY_FILE")
    if cert and key:
        configs.append(
            uvicorn.Config(
                app,
                host=host,
                port=int(os.environ.get("TLS_PORT", "8443")),
                ssl_certfile=cert,
                ssl_keyfile=key,
                lifespan="off",
            )
        )
    return configs


async def serve(app: object) -> None:
    servers = [_Server(config) for config in build_configs(app)]

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows event loops
            loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(server.serve() for server in servers))


def main() -> None:
    from app.server import app

    asyncio.run(serve(app))


if __name__ == "__main__":
    main()
