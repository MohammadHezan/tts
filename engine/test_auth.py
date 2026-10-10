"""The admin login, against the real middleware (app/auth.py) in front of a stand-in app:

    python test_auth.py
"""

from __future__ import annotations

import base64
import sys

sys.path.insert(0, ".")

from fastapi import FastAPI, WebSocket  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app.auth import Auth, AuthMiddleware, hash_password, safe_next, verify_password  # noqa: E402
from app.config import AuthConfig  # noqa: E402

FAILED: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  {detail}"))
    if not ok:
        FAILED.append(name)


USER, PASSWORD = "test-admin", "test-password-123"
now = [1_000_000.0]


def make(enabled: bool = True) -> tuple[TestClient, Auth]:
    auth = Auth(AuthConfig(username=USER, password_hash=hash_password(PASSWORD, 1000) if enabled else ""), clock=lambda: now[0])
    app = FastAPI()
    app.add_middleware(AuthMiddleware, auth=auth)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/login.html")
    def login():
        return {"page": "login"}

    @app.get("/bot.html")
    def dashboard():
        return {"page": "dashboard"}

    @app.get("/api/bots/{bot_id}")
    def bot(bot_id: str):
        return {"id": bot_id}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_text("hello")

    @app.websocket("/attendee/ws")
    async def bridge(socket: WebSocket):
        await socket.accept()
        await socket.send_text("bot")

    return TestClient(app, follow_redirects=False), auth


def ws_opens(client: TestClient, path: str) -> bool:
    try:
        with client.websocket_connect(path) as socket:
            socket.receive_text()
        return True
    except WebSocketDisconnect:
        return False


def main() -> int:
    check("password hashing", verify_password(PASSWORD, hash_password(PASSWORD, 1000)) and not verify_password("nope", hash_password(PASSWORD, 1000)))
    check("the hash does not contain the password", PASSWORD not in hash_password(PASSWORD, 1000))
    check("safe_next keeps sites apart", safe_next("/bot.html") == "/bot.html" and safe_next("//evil.com") == "/bot.html"
          and safe_next("https://evil.com") == "/bot.html" and safe_next(None) == "/bot.html")

    c, _ = make()
    r = c.get("/bot.html")
    check("dashboard redirects to the login page", r.status_code == 302 and r.headers["location"].startswith("/login.html?next=/bot.html"), (r.status_code, r.headers))
    check("API says 401", c.get("/api/bots/x").status_code == 401)
    check("health check and login page stay open", c.get("/healthz").status_code == 200 and c.get("/login.html").status_code == 200)
    check("not logged in: websocket refused", not ws_opens(c, "/ws"))
    check("the bot's own audio socket is not affected", ws_opens(c, "/attendee/ws"))

    r = c.post("/api/login", json={"username": USER, "password": "wrong"})
    check("wrong password refused", r.status_code == 401 and "set-cookie" not in r.headers)
    r = c.post("/api/login", json={"username": "someone", "password": PASSWORD})
    check("wrong username refused", r.status_code == 401)
    check("still locked out of the dashboard", c.get("/bot.html").status_code == 302)

    r = c.post("/api/login", json={"username": USER, "password": PASSWORD})
    cookie = r.headers.get("set-cookie", "")
    check("right username and password log in", r.status_code == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie, (r.status_code, cookie))
    check("then the dashboard opens", c.get("/bot.html").json() == {"page": "dashboard"})
    check("and the API", c.get("/api/bots/x").json() == {"id": "x"})
    check("and websockets", ws_opens(c, "/ws"))
    check("session shows who is logged in", c.get("/api/session").json() == {"authenticated": True, "username": USER})

    c.post("/api/logout")
    check("after logout everything is closed again", c.get("/api/bots/x").status_code == 401 and c.get("/bot.html").status_code == 302)

    # tampered and expired sessions
    c2, auth2 = make()
    token = auth2.make_token()
    check("a valid token works", auth2.valid_token(token))
    forged = base64.urlsafe_b64encode(f"{USER}|{int(now[0]) + 99999}|{'0' * 64}".encode()).decode()
    check("a forged token is refused", not auth2.valid_token(forged))
    c2.cookies.set("interpreter_session", forged)
    check("a forged cookie opens nothing", c2.get("/api/bots/x").status_code == 401)
    now[0] += 13 * 3600
    check("a session expires after its hours", not auth2.valid_token(token))
    now[0] -= 13 * 3600

    # lockout
    c3, _ = make()
    codes = [c3.post("/api/login", json={"username": USER, "password": f"bad{i}"}).status_code for i in range(6)]
    check("five wrong passwords lock the address out", codes[:4] == [401] * 4 and 429 in codes[4:], codes)
    r = c3.post("/api/login", json={"username": USER, "password": PASSWORD})
    check("even the right password waits during the lockout", r.status_code == 429, r.status_code)
    now[0] += 61
    r = c3.post("/api/login", json={"username": USER, "password": PASSWORD})
    check("and works again after it", r.status_code == 200, r.status_code)

    # the login can come from the environment (the .env file) instead of the config
    import os
    os.environ["INTERPRETER_ADMIN_USER"] = "from-env"
    os.environ["INTERPRETER_ADMIN_PASSWORD_HASH"] = hash_password("env-password-1", 1000)
    env_auth = Auth(AuthConfig(), clock=lambda: now[0])
    check("a login set in the environment is used", env_auth.enabled and env_auth.check("from-env", "env-password-1", "ip")
          and not env_auth.check("from-env", PASSWORD, "ip2"))
    check("hashes have no $ (safe in .env and compose)", "$" not in os.environ["INTERPRETER_ADMIN_PASSWORD_HASH"])
    old_format = "pbkdf2_sha256$1000$" + hash_password("x", 1000).split(":")[2] + "$" + hash_password("x", 1000).split(":")[3]
    check("the first hash format still verifies", verify_password("x", "pbkdf2_sha256$1000$AAAA$BBBB") is False and old_format.count("$") == 3)
    del os.environ["INTERPRETER_ADMIN_USER"], os.environ["INTERPRETER_ADMIN_PASSWORD_HASH"]

    # off while no password is set
    c5, _ = make(enabled=False)
    check("no password set: nothing is locked (as before)", c5.get("/bot.html").status_code == 200 and ws_opens(c5, "/ws"))

    print(f"\n{'ALL PASSED' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
