"""Thin client for Attendee's REST API - only the calls the dashboard needs.

Configured from the environment (.env is loaded by app.config.load_config):
    ATTENDEE_BASE_URL       e.g. http://localhost:8000 for a self-hosted Attendee
    ATTENDEE_API_KEY        created in Attendee's own web UI ("API Keys"), or
    ATTENDEE_API_KEY_FILE   a file holding it - docker-compose.yml's bundled
                            Attendee gets its key generated into one on first start
                            (deploy/setup_secrets.py), so nobody has to create one.

The API key stays server-side: the browser dashboard and the Android app talk
to our /api/bots endpoints, never to Attendee directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class AttendeeError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Attendee API returned {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass
class AttendeeSettings:
    base_url: str
    api_key: str

    @classmethod
    def from_env(cls) -> AttendeeSettings | None:
        base_url = os.environ.get("ATTENDEE_BASE_URL", "").rstrip("/")
        api_key = os.environ.get("ATTENDEE_API_KEY", "") or _read_key_file(os.environ.get("ATTENDEE_API_KEY_FILE", ""))
        if not base_url or not api_key:
            return None
        return cls(base_url=base_url, api_key=api_key)


def _read_key_file(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class AttendeeClient:
    def __init__(
        self, settings: AttendeeSettings, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            headers={"Authorization": f"Token {settings.api_key}"},
            timeout=timeout,
            transport=transport,
        )

    async def create_bot(self, meeting_url: str, bot_name: str, audio_ws_url: str, sample_rate: int) -> dict[str, Any]:
        body = {
            "meeting_url": meeting_url,
            "bot_name": bot_name,
            "websocket_settings": {"audio": {"url": audio_ws_url, "sample_rate": sample_rate}},
            # The interpreter only needs the live audio stream. Without this,
            # Attendee also records and encodes the call's video on the same
            # machine that is running Whisper and the translation model.
            "recording_settings": {"format": "none"},
        }
        return await self._request("POST", "/api/v1/bots", body)

    async def check(self) -> None:
        """Raises AttendeeError/OSError unless Attendee is up and accepts our key."""
        await self._request("GET", "/api/v1/bots")

    async def get_bot(self, bot_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/v1/bots/{bot_id}")

    async def chat_messages(self, bot_id: str, updated_after: str, cursor: str | None = None) -> dict[str, Any]:
        """The meeting chat since `updated_after` (ISO 8601): {"results": [...], "next": url | None}."""
        params = {"updated_after": updated_after}
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", f"/api/v1/bots/{bot_id}/chat_messages", params=params)

    async def send_chat_message(self, bot_id: str, message: str) -> dict[str, Any]:
        """Posts `message` in the meeting chat as the bot (Attendee: no emoji)."""
        return await self._request("POST", f"/api/v1/bots/{bot_id}/send_chat_message", {"message": message})

    async def leave_bot(self, bot_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/v1/bots/{bot_id}/leave")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        response = await self._client.request(method, path, json=body, params=params)
        if response.is_error:
            raise AttendeeError(response.status_code, response.text)
        return response.json() if response.content else {}
