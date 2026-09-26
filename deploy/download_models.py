"""Downloads the models into the translator image at build time (Dockerfile),
so starting the interpreter never involves downloading voice files by hand.

    /models/kokoro-v1.0.onnx, voices-v1.0.bin      English voice (Kokoro)
    /models/ar_JO-kareem-medium.onnx(.json)         Arabic voice (Piper)
    Hugging Face cache: Whisper "small"             speech recognition

The translation model is not here: Ollama keeps its own store, filled once by
docker-compose.yml's ollama-pull service.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path

MODELS_DIR = Path("/models")
KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ar/ar_JO/kareem/medium"
FILES = {
    "kokoro-v1.0.onnx": f"{KOKORO}/kokoro-v1.0.onnx",
    "voices-v1.0.bin": f"{KOKORO}/voices-v1.0.bin",
    "ar_JO-kareem-medium.onnx": f"{PIPER}/ar_JO-kareem-medium.onnx",
    "ar_JO-kareem-medium.onnx.json": f"{PIPER}/ar_JO-kareem-medium.onnx.json",
}
# asr.model of the config the image runs: config.docker.yaml (CPU) or
# config.docker-gpu.yaml (GPU) - the Dockerfile passes it in.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL") or "small"


def download(url: str, dest: Path, attempts: int = 5) -> None:
    for attempt in range(1, attempts + 1):
        try:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with urllib.request.urlopen(url, timeout=60) as response, tmp.open("wb") as out:
                while chunk := response.read(1 << 20):
                    out.write(chunk)
            tmp.replace(dest)
            print(f"downloaded {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)
            return
        except OSError as error:
            if attempt == attempts:
                raise
            print(f"{dest.name}: attempt {attempt} failed ({error}), retrying", file=sys.stderr, flush=True)
            time.sleep(5 * attempt)


def main() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        download(url, MODELS_DIR / name)

    from faster_whisper import download_model

    print(f"Whisper {WHISPER_MODEL}: {download_model(WHISPER_MODEL)}", flush=True)


if __name__ == "__main__":
    main()
