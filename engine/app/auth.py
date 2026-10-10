"""Admin login for the dashboard and its API.

Everything is behind the login except: /healthz (Docker's health check), the
login page and its two calls, and /attendee/ws (the meeting bot's audio, which
has its own secret token). Not logged in: pages redirect to /login.html, API
calls get 401, websockets are closed.

The password is only ever stored as a salted PBKDF2 hash (see deploy/set_admin.py).
A session is a signed cookie (HttpOnly, SameSite=Strict). Too many wrong
passwords from one address locks it out for a minute.

The admin name and password hash come from the environment (INTERPRETER_ADMIN_USER and
INTERPRETER_ADMIN_PASSWORD_HASH - kept in the git-ignored .env file, never in the repository)
or, failing that, from the auth: section of the config.

Login is switched off (everything open, as before) while no password hash is set.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import quote

from app.config import AuthConfig

COOKIE = "interpreter_session"
ITERATIONS = 600_000
PUBLIC_PATHS = {"/healthz", "/api/ready", "/login.html", "/api/login", "/api/session", "/api/logout", "/favicon.ico"}
PUBLIC_PREFIXES = ("/attendee/ws",)
MAX_FAILURES, LOCKOUT_S, FAILURE_WINDOW_S = 5, 60.0, 300.0


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256:{iterations}:{base64.b64encode(salt).decode()}:{base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, digest = stored.split(":" if ":" in stored else "$")  # "$" was the first format
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations))
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


class Auth:
    def __init__(self, cfg: AuthConfig, clock=time.time) -> None:
        self.cfg = cfg.model_copy(update={
            "username": os.environ.get("INTERPRETER_ADMIN_USER") or cfg.username,
            "password_hash": os.environ.get("INTERPRETER_ADMIN_PASSWORD_HASH") or cfg.password_hash,
        })
        self._clock = clock
        self._secret = (os.environ.get("INTERPRETER_SESSION_SECRET") or secrets.token_urlsafe(32)).encode()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.password_hash)

    # --- credentials
    def check(self, username: str, password: str, client: str) -> bool:
        now = self._clock()
        if self._locked_until.get(client, 0.0) > now:
            return False
        # Both are always checked, so the time taken doesn't tell which one was wrong.
        user_ok = hmac.compare_digest(username.encode(), self.cfg.username.encode())
        pass_ok = verify_password(password, self.cfg.password_hash)
        if user_ok and pass_ok:
            self._failures.pop(client, None)
            return True
        recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW_S] + [now]
        self._failures[client] = recent
        if len(recent) >= MAX_FAILURES:
            self._locked_until[client] = now + LOCKOUT_S
            self._failures.pop(client, None)
        return False

    def locked(self, client: str) -> float:
        return max(0.0, self._locked_until.get(client, 0.0) - self._clock())

    # --- sessions
    def make_token(self) -> str:
        expires = int(self._clock() + self.cfg.session_hours * 3600)
        payload = f"{self.cfg.username}|{expires}"
        signature = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()

    def valid_token(self, token: str) -> bool:
        try:
            username, expires, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit("|", 2)
            expected = hmac.new(self._secret, f"{username}|{expires}".encode(), hashlib.sha256).hexdigest()
            return hmac.compare_digest(signature, expected) and int(expires) > self._clock() and username == self.cfg.username
        except (ValueError, TypeError):
            return False

    def request_ok(self, headers: dict[str, str]) -> bool:
        """Whether the request carries a valid login cookie."""
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and self.valid_token(value):
                return True
        return False


def _json(status: int, body: dict, headers: list[tuple[bytes, bytes]] | None = None) -> tuple[dict, dict]:
    payload = json.dumps(body).encode()
    start = {
        "type": "http.response.start", "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
                    (b"cache-control", b"no-store"), *(headers or [])],
    }
    return start, {"type": "http.response.body", "body": payload}


def safe_next(value: str | None) -> str:
    """Where to go after logging in: a path on this site, never another site."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/bot.html"


class AuthMiddleware:
    """Pure ASGI middleware, so websockets are covered as well as requests."""

    def __init__(self, app, auth: Auth) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket") or not self.auth.enabled:
            return await self.app(scope, receive, send)
        path = scope["path"]
        client = (scope.get("client") or ("?", 0))[0]
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}

        if scope["type"] == "http" and path in ("/api/login", "/api/session", "/api/logout"):
            return await self._auth_endpoint(scope, receive, send, path, client, headers)
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES) or self.auth.request_ok(headers):
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await receive()  # websocket.connect
            return await send({"type": "websocket.close", "code": 1008})
        if path.startswith("/api/"):
            start, body = _json(401, {"detail": "Log in first."})
            await send(start)
            return await send(body)
        target = path + (("?" + scope["query_string"].decode()) if scope.get("query_string") else "")
        await send({"type": "http.response.start", "status": 302,
                    "headers": [(b"location", f"/login.html?next={quote(target)}".encode()), (b"cache-control", b"no-store"),
                                (b"content-length", b"0")]})
        await send({"type": "http.response.body", "body": b""})

    async def _auth_endpoint(self, scope, receive, send, path, client, headers) -> None:
        auth = self.auth
        if path == "/api/session":
            ok = auth.request_ok(headers)
            start, body = _json(200, {"authenticated": ok, "username": auth.cfg.username if ok else None})
        elif path == "/api/logout":
            expired = f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict".encode()
            start, body = _json(200, {"ok": True}, [(b"set-cookie", expired)])
        elif scope["method"] != "POST":
            start, body = _json(405, {"detail": "POST only"})
        else:
            raw = b""
            while True:
                message = await receive()
                raw += message.get("body", b"")
                if not message.get("more_body"):
                    break
            try:
                data = json.loads(raw[:4096] or b"{}")
                username, password = str(data.get("username", "")), str(data.get("password", ""))
            except (ValueError, AttributeError):
                username = password = ""
            wait = auth.locked(client)
            if wait > 0:
                start, body = _json(429, {"detail": f"Too many wrong attempts. Try again in {int(wait) + 1} seconds."})
            elif auth.check(username, password, client):
                cookie = f"{COOKIE}={auth.make_token()}; Path=/; Max-Age={int(auth.cfg.session_hours * 3600)}; HttpOnly; SameSite=Strict".encode()
                start, body = _json(200, {"ok": True}, [(b"set-cookie", cookie)])
            else:
                wait = auth.locked(client)
                detail = f"Too many wrong attempts. Try again in {int(wait) + 1} seconds." if wait > 0 else "Wrong username or password."
                start, body = _json(429 if wait > 0 else 401, {"detail": detail})
        await send(start)
        await send(body)
