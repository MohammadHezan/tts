"""Downloads the GPU setup's speech model (asr.cohere in config.docker-gpu.yaml,
~4GB) into its volume, once - docker-compose.gpu.yml's asr-pull service runs
this before the translator starts. Already there: returns in a second."""

import os
import sys
import time

from huggingface_hub import snapshot_download

sys.path.insert(0, "/srv/tts/engine")
from app.config import load_config  # noqa: E402

cfg = load_config().asr
if cfg.provider != "cohere":
    sys.exit(0)
c = cfg.cohere
if os.path.isabs(c.model) or c.model.startswith("/"):
    # A folder (the base Cohere model mounted from ./local-models). Already
    # there: nothing to do. Missing: the gated base model, with HF_TOKEN.
    if os.path.isfile(os.path.join(c.model, "config.json")):
        print(f"{c.model}: already there", flush=True)
        sys.exit(0)
    try:
        path = snapshot_download("CohereLabs/cohere-transcribe-03-2026", local_dir=c.model, allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.py"])
        print(f"downloaded CohereLabs/cohere-transcribe-03-2026 to {path}", flush=True)
        sys.exit(0)
    except Exception as error:
        sys.exit(
            f"{c.model} is missing and CohereLabs/cohere-transcribe-03-2026 couldn't be downloaded ({type(error).__name__}). "
            "Accept its terms on https://huggingface.co/CohereLabs/cohere-transcribe-03-2026, then either put its files in "
            "local-models/cohere-transcribe-03-2026 or set HF_TOKEN to your Hugging Face token."
        )
jobs = [(c.model, c.revision, ["*.json", "*.safetensors", "*.model", "*.txt", "*.py"])]
if c.processor:  # else the model's own repo already ships its processor
    # Only the processor files from this repo; its weights are the base model's.
    jobs.append((c.processor, c.processor_revision, ["*.json", "*.model", "*.txt"]))
for repo, revision, patterns in jobs:
    for attempt in range(1, 6):
        try:
            path = snapshot_download(repo, revision=revision, cache_dir=c.cache_dir, allow_patterns=patterns)
            print(f"{repo}: {path}", flush=True)
            break
        except Exception as error:
            if type(error).__name__ in ("GatedRepoError", "RepositoryNotFoundError"):
                sys.exit(
                    f"{repo} is gated: accept its terms on https://huggingface.co/{repo} with your Hugging Face account, "
                    "then give this container that account's token (HF_TOKEN)."
                )
            if attempt == 5:
                raise
            print(f"{repo}: attempt {attempt} failed ({error}), retrying", file=sys.stderr, flush=True)
            time.sleep(10 * attempt)
