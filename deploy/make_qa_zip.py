"""Builds the QA package: one zip with the project (everything in git), the models
(local-models/) and a login, laid out like the official Windows download -
the two .bat files on top, the project under app\\.

    set QA_PASSWORD=...        (the password QA will log in with; at least 8 characters)
    python deploy/make_qa_zip.py --out Interpreter-QA.zip

The login is written only into the zip's own .env (a salted hash - the password is
not stored). The repository's .env, meeting-data and any tokens are never included.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from app.auth import hash_password  # noqa: E402

TOP = "Interpreter-QA"
LEFT_OUT = ("android/", ".github/")  # the phone app needs a login screen first; CI is not for QA
TOP_LEVEL = {"Start Interpreter.bat", "Stop Interpreter.bat"}
BIG = (".safetensors", ".gguf", ".onnx", ".bin", ".model")  # already compressed: stored as they are


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT.parent / "Interpreter-QA.zip")
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password-env", default="QA_PASSWORD")
    ap.add_argument("--no-models", action="store_true", help="leave local-models out (the stack then downloads the plain models)")
    args = ap.parse_args()

    password = os.environ.get(args.password_env, "")
    if len(password) < 8:
        sys.exit(f"Set {args.password_env} to the QA password (at least 8 characters).")
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("Warning: uncommitted changes are in the working tree; the zip has what is on disk, not the commit.\n" + dirty, file=sys.stderr)
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    files = [f for f in files if not f.startswith(LEFT_OUT)]

    env = f"INTERPRETER_ADMIN_USER='{args.username}'\nINTERPRETER_ADMIN_PASSWORD_HASH='{hash_password(password)}'\n"
    models = ROOT / "local-models"
    if not args.no_models and not (models / "Modelfile").exists():
        sys.exit("local-models/ is missing (the speech model and the Modelfile). Use --no-models to leave it out.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in files:
            src = ROOT / rel
            if not src.is_file():
                continue
            inner = f"{TOP}/{rel}" if rel in TOP_LEVEL else f"{TOP}/app/{rel}"
            z.write(src, inner)
            count += 1
        for name, inner in (("QA-README.txt", "README.txt"), ("qa_collect_logs.bat", "Collect Logs.bat")):
            raw = (ROOT / "deploy" / name).read_bytes()
            text = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # Windows line endings
            z.writestr(f"{TOP}/{inner}", text)
        z.writestr(f"{TOP}/app/.env", env)
        if not args.no_models:
            for src in sorted(models.rglob("*")):
                if src.is_file() and ".cache" not in src.parts:
                    kind = zipfile.ZIP_STORED if src.suffix in BIG else zipfile.ZIP_DEFLATED
                    z.write(src, f"{TOP}/app/local-models/{src.relative_to(models).as_posix()}", compress_type=kind)
                    count += 1
    print(f"{count} files -> {args.out} ({args.out.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
