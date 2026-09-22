"""Entry point for the packaged Windows desktop app (see windows_launcher.spec).

Starts the engine server locally and opens the web client in the default
browser - the whole "download, double-click, start talking" experience for a
teammate who shouldn't need to touch Python/pip. Runs the exact same
app.server:app as `uvicorn app.server:app` (see README "Run it") - this file
only adds the auto-launch-browser convenience and a packaged entry point
PyInstaller can build an .exe around.
"""

from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

HOST = "0.0.0.0"
PORT = 8000


def _open_browser_when_ready() -> None:
    time.sleep(2.5)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


def main() -> None:
    # Import (not uvicorn's lazy "module:attr" string form) so PyInstaller's
    # static analysis traces it and the frozen bundle doesn't depend on
    # import-by-string resolving correctly against whatever CWD Explorer sets.
    from app.server import app as fastapi_app

    threading.Thread(target=_open_browser_when_ready, daemon=True).start()
    print(f"Interpreter engine starting - opening http://127.0.0.1:{PORT}/ in your browser...")
    print("Leave this window open while you use the app. Close it to stop the engine.")
    uvicorn.run(fastapi_app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
