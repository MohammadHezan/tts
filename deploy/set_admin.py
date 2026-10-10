"""Sets the dashboard's admin login: writes the username and a salted hash of the
password (never the password) into the .env file in the repository's main folder.
.env is not in git, so the login stays out of the repository - copy that file to
another computer to use the same login there.

    python deploy/set_admin.py                       # asks for username and password
    python deploy/set_admin.py --username admin

For scripts: --password-env NAME reads the password from that environment variable.
Then restart the interpreter (Stop / Start Interpreter).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
from app.auth import hash_password  # noqa: E402


def set_lines(text: str, values: dict[str, str]) -> str:
    """`text` (a .env file) with these variables set, the others untouched. Single quotes: the value is literal."""
    lines = [line for line in text.splitlines() if line.split("=", 1)[0].strip() not in values]
    lines += [f"{key}='{value}'" for key, value in values.items()]
    return "\n".join(lines).strip("\n") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--username")
    ap.add_argument("--password-env", help="name of an environment variable holding the password")
    args = ap.parse_args()

    username = args.username or input("Admin username: ").strip()
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    else:
        password = getpass.getpass("Admin password: ")
        if password != getpass.getpass("Again: "):
            sys.exit("The two passwords differ.")
    if not username or len(password) < 8 or "'" in username:
        sys.exit("A username (no quote marks) and a password of at least 8 characters are needed.")
    existing = args.env_file.read_text(encoding="utf-8") if args.env_file.exists() else ""
    args.env_file.write_text(
        set_lines(existing, {"INTERPRETER_ADMIN_USER": username, "INTERPRETER_ADMIN_PASSWORD_HASH": hash_password(password)}),
        encoding="utf-8",
    )
    print(f"Admin login for '{username}' written to {args.env_file}. Restart the interpreter to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
