"""First-start setup for docker-compose.yml, so nobody has to create keys by hand.

Runs once per `docker compose up` (the `setup` service, in the translator image)
and writes into the shared `interpreter-setup` volume, mounted at /setup:

    attendee.env           Django secret + credentials-encryption key for Attendee
    attendee_api_key       the key the translator uses for Attendee's API; Attendee
                           registers it itself (attendee_provision.py)
    ca.pem / ca.key        a private certificate authority, trusted only by
                           Attendee's bot (attendee_run.sh adds it to its bundle)
    translator.pem/.key    the translator's certificate for the name "translator",
                           signed by that CA - Attendee's bot only connects to
                           wss:// URLs, and verifies the certificate
    attendee_*.sh/.py      the scripts the Attendee containers run (copied here so
                           Attendee's own image is used unmodified)

Everything except the copied scripts is generated only if missing, so restarts
keep the same keys. Nothing here leaves the Docker network: Attendee's port is
never published, and the translator's certificate is only for Attendee.
"""

from __future__ import annotations

import base64
import datetime
import ipaddress
import os
import secrets
import shutil
import string
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SETUP_DIR = Path(os.environ.get("SETUP_DIR", "/setup"))
SCRIPTS_DIR = Path(__file__).resolve().parent / "attendee"
TRANSLATOR_NAMES = ["translator", "localhost"]
RENEW_WITHIN = datetime.timedelta(days=30)


def _write(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(mode)
    tmp.replace(path)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def ensure_ca() -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key_path, cert_path = SETUP_DIR / "ca.key", SETUP_DIR / "ca.pem"
    if key_path.exists() and cert_path.exists():
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return key, x509.load_pem_x509_certificate(cert_path.read_bytes())

    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("Interpreter local CA"))
        .issuer_name(_name("Interpreter local CA"))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    _write(key_path, _key_pem(key), 0o600)
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)
    # A new CA invalidates any certificate signed by an old one.
    (SETUP_DIR / "translator.pem").unlink(missing_ok=True)
    return key, cert


def ensure_translator_cert(ca_key: ec.EllipticCurvePrivateKey, ca_cert: x509.Certificate) -> None:
    key_path, cert_path = SETUP_DIR / "translator.key", SETUP_DIR / "translator.pem"
    now = datetime.datetime.now(datetime.timezone.utc)
    if key_path.exists() and cert_path.exists():
        existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
        if existing.not_valid_after_utc - now > RENEW_WITHIN:
            return

    key = ec.generate_private_key(ec.SECP256R1())
    alt_names: list[x509.GeneralName] = [x509.DNSName(name) for name in TRANSLATOR_NAMES]
    alt_names.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name("translator"))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    _write(key_path, _key_pem(key), 0o600)
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM), 0o644)


def ensure_attendee_secrets() -> None:
    env_path = SETUP_DIR / "attendee.env"
    if not env_path.exists():
        # Same formats as Attendee's own init_env.py: a Fernet key and a Django secret.
        fernet_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
        env = f"CREDENTIALS_ENCRYPTION_KEY={fernet_key}\nDJANGO_SECRET_KEY={secrets.token_urlsafe(50)}\n"
        _write(env_path, env.encode("ascii"), 0o644)

    key_path = SETUP_DIR / "attendee_api_key"
    if not key_path.exists():
        alphabet = string.ascii_letters + string.digits
        _write(key_path, "".join(secrets.choice(alphabet) for _ in range(32)).encode("ascii"), 0o644)


def copy_scripts() -> None:
    for script in SCRIPTS_DIR.iterdir():
        if script.is_file():
            shutil.copyfile(script, SETUP_DIR / script.name)
            (SETUP_DIR / script.name).chmod(0o644)


def main() -> None:
    SETUP_DIR.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = ensure_ca()
    ensure_translator_cert(ca_key, ca_cert)
    ensure_attendee_secrets()
    copy_scripts()
    print(f"setup: ready in {SETUP_DIR}", flush=True)


if __name__ == "__main__":
    main()
