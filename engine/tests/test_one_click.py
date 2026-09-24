"""The pieces docker-compose.yml's bundled setup relies on: the generated
secrets and certificate, the translator serving the bot over TLS, the API key
read from a file, and failure reasons phrased for people."""

from __future__ import annotations

import os
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app.attendee_client import AttendeeSettings

ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_DIR.parent
FAKE_CONFIG = """\
audio: {sample_rate_hz: 16000, frame_ms: 30, channels: 1}
vad: {provider: silero, threshold: 0.5, min_speech_ms: 150, min_silence_ms: 700, speech_pad_ms: 200}
asr: {provider: fake}
translator: {provider: fake, glossary_path: null}
tts: {provider: fake}
"""


def test_api_key_comes_from_file_when_not_in_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key_file = tmp_path / "attendee_api_key"
    key_file.write_text("key-from-file\n")
    monkeypatch.setenv("ATTENDEE_BASE_URL", "http://attendee-app:8000/")
    monkeypatch.setenv("ATTENDEE_API_KEY", "")
    monkeypatch.setenv("ATTENDEE_API_KEY_FILE", str(key_file))
    assert AttendeeSettings.from_env() == AttendeeSettings("http://attendee-app:8000", "key-from-file")

    monkeypatch.setenv("ATTENDEE_API_KEY", "key-from-env")
    assert AttendeeSettings.from_env().api_key == "key-from-env"

    monkeypatch.setenv("ATTENDEE_API_KEY", "")
    monkeypatch.setenv("ATTENDEE_API_KEY_FILE", str(tmp_path / "missing"))
    assert AttendeeSettings.from_env() is None


def test_bot_failures_are_explained_in_plain_words() -> None:
    from app.server import _bot_problem

    assert _bot_problem({"events": []}) is None
    assert _bot_problem({"events": [{"type": "leave_requested", "sub_type": "user_requested"}]}) is None
    denied = {"events": [{"type": "join_requested"}, {"type": "could_not_join", "sub_type": "request_to_join_denied"}]}
    assert _bot_problem(denied) == "Someone in the meeting declined the bot's request to join."
    assert _bot_problem({"events": [{"type": "fatal_error", "sub_type": "some_new_code"}]}) == "Some new code."


def test_serve_adds_the_tls_port_only_with_a_certificate(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.serve import build_configs

    monkeypatch.delenv("TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("TLS_KEY_FILE", raising=False)
    assert [c.port for c in build_configs(object())] == [8000]
    monkeypatch.setenv("TLS_CERT_FILE", "/setup/translator.pem")
    monkeypatch.setenv("TLS_KEY_FILE", "/setup/translator.key")
    configs = build_configs(object())
    assert [c.port for c in configs] == [8000, 8443]
    assert configs[1].ssl_certfile == "/setup/translator.pem"


@pytest.fixture(scope="module")
def setup_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pytest.importorskip("cryptography")
    out = tmp_path_factory.mktemp("setup")
    env = {**os.environ, "SETUP_DIR": str(out)}
    subprocess.run([sys.executable, str(REPO_ROOT / "deploy" / "setup_secrets.py")], env=env, check=True)
    return out


def test_setup_generates_everything_once(setup_dir: Path) -> None:
    from cryptography import x509
    from cryptography.fernet import Fernet

    expected = {
        "attendee.env", "attendee_api_key", "ca.key", "ca.pem", "translator.key", "translator.pem",
        "attendee_run.sh", "attendee_setup.sh", "attendee_provision.py",
    }
    assert expected <= {p.name for p in setup_dir.iterdir()}
    env = dict(line.split("=", 1) for line in (setup_dir / "attendee.env").read_text().splitlines())
    Fernet(env["CREDENTIALS_ENCRYPTION_KEY"])  # raises unless it's a valid Fernet key
    assert len(env["DJANGO_SECRET_KEY"]) >= 50
    assert len((setup_dir / "attendee_api_key").read_text()) == 32

    cert = x509.load_pem_x509_certificate((setup_dir / "translator.pem").read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert "translator" in names

    before = {name: (setup_dir / name).read_bytes() for name in ("attendee.env", "attendee_api_key", "ca.pem", "translator.pem")}
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "deploy" / "setup_secrets.py")],
        env={**os.environ, "SETUP_DIR": str(setup_dir)},
        check=True,
    )
    assert before == {name: (setup_dir / name).read_bytes() for name in before}


def test_bot_reaches_the_translator_over_tls(setup_dir: Path, tmp_path: Path) -> None:
    """What Attendee's bot does (websockets.sync.client.connect on a wss:// URL,
    default certificate checks) against `python -m app.serve`, trusting only the
    CA attendee_run.sh adds to its bundle."""
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    config = tmp_path / "config.yaml"
    config.write_text(FAKE_CONFIG)
    http_port, tls_port = 18791, 18792
    env = {
        **os.environ,
        "ENGINE_CONFIG_PATH": str(config),
        "PORT": str(http_port),
        "TLS_PORT": str(tls_port),
        "TLS_CERT_FILE": str(setup_dir / "translator.pem"),
        "TLS_KEY_FILE": str(setup_dir / "translator.key"),
        "ATTENDEE_BRIDGE_TOKEN": "right-token",
    }
    server = subprocess.Popen([sys.executable, "-m", "app.serve"], cwd=ENGINE_DIR, env=env)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"http://127.0.0.1:{http_port}/healthz", timeout=1).json()["service"] == "interpreter":
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.fail("app.serve did not start")

        trusted = ssl.create_default_context(cafile=str(setup_dir / "ca.pem"))
        url = f"wss://127.0.0.1:{tls_port}/attendee/ws?token="
        with connect(url + "right-token", ssl=trusted, server_hostname="translator", open_timeout=10) as ws:
            ws.ping().wait(5)
        with pytest.raises(InvalidStatus):
            connect(url + "wrong", ssl=trusted, server_hostname="translator", open_timeout=10)
        with pytest.raises(ssl.SSLCertVerificationError):
            connect(url + "right-token", ssl=ssl.create_default_context(), server_hostname="translator", open_timeout=10)
        # Still serving everyone else afterwards.
        assert httpx.get(f"http://127.0.0.1:{http_port}/healthz", timeout=5).status_code == 200
    finally:
        server.terminate()
        server.wait(timeout=15)
