"""Downloads the GPU setup's speech model (asr.cohere in config.docker-gpu.yaml,
~4GB) into its volume, once - docker-compose.gpu.yml's asr-pull service runs
this before the translator starts. Already there: returns in a second."""

import sys
import time

from huggingface_hub import snapshot_download

sys.path.insert(0, "/srv/tts/engine")
from app.config import load_config  # noqa: E402

cfg = load_config().asr
if cfg.provider != "cohere":
    sys.exit(0)
c = cfg.cohere
jobs = [
    (c.model, c.revision, ["*.json", "*.safetensors", "*.model", "*.txt"]),
    # Only the processor files from this repo; its weights are the base model's.
    (c.processor, c.processor_revision, ["*.json", "*.model", "*.txt"]),
]
for repo, revision, patterns in jobs:
    for attempt in range(1, 6):
        try:
            path = snapshot_download(repo, revision=revision, cache_dir=c.cache_dir, allow_patterns=patterns)
            print(f"{repo}: {path}", flush=True)
            break
        except Exception as error:
            if attempt == 5:
                raise
            print(f"{repo}: attempt {attempt} failed ({error}), retrying", file=sys.stderr, flush=True)
            time.sleep(10 * attempt)
