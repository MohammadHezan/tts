# Arabic ↔ English Real-Time Speech Translation

Galaxy AI "Interpreter"-style live speech translation. This repo is delivered in
phases (see the project brief); **Phase 1 (this delivery) is the engine MVP**:
EN→AR streaming captions, a CLI test harness (WAV + live mic), and a
latency/WER benchmark script. TTS, AR→EN, the Android app, foldable UX, and
desktop meeting mode are **not** in this phase - see [Status](#status).

## Project tree (Phase 1)

```
tts/
├── config.yaml            # single source of truth for provider selection + params
├── .env.example            # copy to .env - API keys only, never committed
├── engine/                 # Python 3.11, FastAPI + WebSocket
│   ├── requirements.txt
│   ├── pyproject.toml       # pytest config
│   ├── app/
│   │   ├── config.py         # typed config.yaml/.env loader
│   │   ├── schema.py         # PipelineEvent wire schema (partial|final|translation|audio)
│   │   ├── logging_utils.py  # structured JSON logging + per-stage LatencyTracker
│   │   ├── audio_utils.py    # WAV I/O, resampling, frame slicing
│   │   ├── vad.py            # Silero VAD streaming endpointer
│   │   ├── segmenter.py      # sentence/semantic segmenter
│   │   ├── prompts.py        # shared translator domain system-prompt
│   │   ├── pipeline.py       # orchestrates VAD -> ASR -> segmenter -> translator
│   │   ├── server.py         # FastAPI WebSocket app (ws://.../ws)
│   │   └── providers/
│   │       ├── base.py                  # ASRProvider/TranslatorProvider/TtsProvider ABCs + factories
│   │       ├── asr_faster_whisper.py    # local-agreement streaming ASR
│   │       ├── asr_fake.py              # deterministic fake for tests/dry-run
│   │       ├── translator_ollama.py     # local LLM translator (default)
│   │       ├── translator_claude.py     # cloud LLM translator (optional)
│   │       └── translator_fake.py       # deterministic fake for tests/dry-run
│   ├── cli/
│   │   ├── translate_wav.py  # python -m cli.translate_wav --file audio.wav
│   │   ├── translate_mic.py  # python -m cli.translate_mic
│   │   └── formatting.py
│   ├── bench/
│   │   ├── benchmark.py      # python -m bench.benchmark
│   │   └── samples.py        # auto-generates an espeak-ng smoke sample set
│   └── tests/                # pytest: VAD, segmenter, schema, integration
└── README.md
```

## Hardware/env assumptions (spec placeholders were left blank)

The brief's `HARDWARE / ENV` block wasn't filled in, so per the working rules
I chose these defaults - **update them for your machine**:

| Placeholder | Default assumed | Why |
|---|---|---|
| Laptop OS | Linux (dev/test), cross-platform code | No OS given; nothing here is Linux-only except the mic CLI's PortAudio note |
| GPU | **None assumed** (CPU fallback is the baseline) | Safer default; GPU path is fully supported, see below |
| RAM | No hard minimum enforced | `small.en`/`base.en` need <1GB; `large-v3-turbo` needs ~2-3GB |
| Internet | Required for first-run model downloads (faster-whisper pulls weights from Hugging Face) and for `translator.provider: claude` | Silero VAD's weights ship inside its pip package - **no network needed for VAD** |
| Paid APIs | **Off by default** ($0 to run) | Default provider stack is 100% local: faster-whisper + Ollama. Claude cloud translation is opt-in via config.yaml |

### Where quality/latency depends on hardware

| Stage | GPU path | CPU fallback |
|---|---|---|
| VAD (Silero, ONNX) | n/a - always CPU, ~0.5ms per 32ms window (measured) | same - negligible cost either way |
| ASR (faster-whisper) | `large-v3-turbo`, `device: cuda`, `compute_type: float16` - needs ~6GB VRAM, easily beats the 2s budget | Set `asr.model: small.en` (or `base.en`) and `asr.compute_type: int8` in `config.yaml`. `large-v3-turbo` on CPU-only will likely miss the <=2s partial-caption budget for longer utterances |
| Translator (LLM) | N/A (Ollama also runs LLMs on GPU if available; not required) | `translator.provider: ollama` with a small quantized model (e.g. `llama3.1:8b-instruct-q4_K_M`) runs acceptably on CPU; for guaranteed low latency without local compute, switch to `translator.provider: claude` (cloud, needs `ANTHROPIC_API_KEY` + internet, small per-call cost) |

`asr.device` and `asr.compute_type` default to `auto` (GPU if `ctranslate2` detects
one, else CPU+int8), so you generally don't need to touch this - it's here for
when you *do* need to force one path.

## Setup

```bash
cd engine
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# System deps:
#   Linux:   apt install libportaudio2 espeak-ng   # PortAudio for mic capture, espeak-ng only for bench's auto-generated smoke samples
#   macOS:   brew install portaudio espeak-ng
#   Windows: PortAudio ships inside the sounddevice wheel; install espeak-ng from https://github.com/espeak-ng/espeak-ng/releases if you want the auto smoke benchmark

cp ../.env.example ../.env   # fill in only what you need (see config.yaml below)
```

Nothing here needs a GPU to install; `ctranslate2`/`faster-whisper` will use
CUDA automatically if present (`asr.device: auto` in `config.yaml`).

## Configure: `config.yaml`

Every stage is selected by **provider name**, resolved by
`app/providers/base.py`'s factories - no code changes needed to switch:

```yaml
asr:
  provider: faster_whisper   # or "fake" for a model-free dry run
  model: large-v3-turbo      # swap to small.en/base.en for CPU-only machines
translator:
  provider: ollama           # or "claude" (cloud, needs ANTHROPIC_API_KEY) or "fake"
```

- **Local (default, $0):** `asr.provider: faster_whisper` + `translator.provider: ollama`.
  Requires [Ollama](https://ollama.com) running locally with the configured model pulled:
  `ollama pull llama3.1:8b-instruct-q4_K_M`.
- **Cloud translator:** set `translator.provider: claude`, put `ANTHROPIC_API_KEY`
  in `.env` (copy from `.env.example`). ASR stays local either way (no cloud ASR
  provider in Phase 1 - Deepgram/Azure are documented extension points in `.env.example`
  but not implemented yet).
- **No models at all (`fake` providers):** every CLI/bench tool accepts `--dry-run`,
  which forces `asr.provider`/`translator.provider` to `fake` - validates the whole
  pipeline (VAD, streaming, event schema, latency tracking) with zero downloads
  and zero network calls. This is how Phase 1 was validated in this sandbox (see
  [Validation notes](#validation-notes-what-was-and-wasnt-run-here)).

## Run it

All commands run from `engine/`.

**WebSocket server** (`ws://host:port/ws`, PCM16 mono 16kHz frames in, JSON
`PipelineEvent`s out - see `app/schema.py`):
```bash
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

**CLI: WAV file**
```bash
python -m cli.translate_wav --file path/to/audio.wav            # real providers, real-time paced
python -m cli.translate_wav --file path/to/audio.wav --dry-run  # no models/network
python -m cli.translate_wav --file path/to/audio.wav --fast     # ignore real-time pacing
```

**CLI: live mic**
```bash
python -m cli.translate_mic --list-devices   # find your input device index
python -m cli.translate_mic                  # Ctrl+C to stop
```
Not runnable in this cloud sandbox (no audio hardware) - run it on your own
machine to confirm mic capture; the pipeline logic it exercises is identical
to `translate_wav.py`, which *was* validated here.

**Benchmark: per-stage latency + WER**
```bash
python -m bench.benchmark                     # auto-generates an espeak-ng smoke set
python -m bench.benchmark --manifest my.json   # your own recorded audio, format: [{"wav": "...", "reference": "..."}]
python -m bench.benchmark --dry-run            # validates the harness only
```

**Tests**
```bash
pytest -q
```

## Architecture notes / trade-offs

- **Local-agreement streaming ASR** (`app/providers/asr_faster_whisper.py`):
  faster-whisper only decodes whole buffers, not incrementally. To stream
  partial captions without O(utterance²) recompute, we re-decode only the
  still-unconfirmed audio tail every `chunk_ms`, and confirm a word once two
  consecutive decodes agree on it (LocalAgreement-2), then permanently advance
  past its audio using its own word timestamp. `finalize()` re-decodes the
  *whole* utterance once at VAD speech-end for the authoritative transcript -
  the stitched partials are for live captions only.
- **VAD** (`app/vad.py`) wraps `silero_vad.VADIterator` (the reference
  streaming state machine) rather than reimplementing threshold/hangover
  logic, and re-chunks arbitrary network frame sizes into the model's fixed
  512-sample window. Weights ship inside the pip package - fully offline.
- **Segmenter** (`app/segmenter.py`) is a small guarded regex, not an NLP
  model: it only needs to avoid splitting on common abbreviations and decimal
  numbers/currency, which covers retail/business speech well without adding a
  model dependency for a rarely-ambiguous task.
- **Latency accounting**: every `PipelineEvent.latency_ms` for `final`/
  `translation` events is measured from the VAD speech-end timestamp (the
  spec's actual KPI: "first translated caption <=2s after speech ends"), not
  from stage-internal timers. `Pipeline.latency_for_turn()` additionally
  exposes a full per-stage breakdown for the benchmark script.
- **Provider abstraction**: Phase 1 only needs one ASR direction (EN) and one
  translator direction (EN→AR), but the ABCs in `providers/base.py` and the
  `TurnContext`/context-message plumbing in `app/prompts.py` are already
  shaped for Phase 2 (AR→EN, glossary, context window) - additive, not a
  rewrite.

## Status

**Done (Phase 1):** VAD endpointing, streaming ASR with local-agreement
partials, sentence segmentation, EN→AR LLM translation (local Ollama or cloud
Claude), FastAPI WebSocket server, WAV + mic CLI harnesses, latency/WER
benchmark, structured logging, 24 passing tests (unit + integration).

**Not in Phase 1** (later phases per the brief): TTS/audio output, AR→EN
direction, glossary + multi-turn context memory, config.yaml `tts:` section is
a stub, Android app, foldable UX, desktop meeting mode, Docker.

## Validation notes: what was and wasn't run here

This sandbox has **no GPU** and **no outbound access to huggingface.co**
(pypi.org/files.pythonhosted.org are reachable, `huggingface.co` returns a
proxy 403), so faster-whisper's model weights can't be downloaded here, and
Ollama isn't installed. What *was* verified in this environment:

- Full pip install of `engine/requirements.txt`.
- All 24 pytest tests pass (`pytest -q`), including one that runs the real
  bundled Silero VAD ONNX model against synthesized speech.
- The complete pipeline (VAD → ASR → segmenter → translator) end-to-end via
  `--dry-run` (fake ASR/translator) against both a WAV file and synthesized
  audio, through `cli/translate_wav.py`, `bench/benchmark.py`, and a live
  `uvicorn` server hit over a real WebSocket client (frames in, streaming
  `partial`/`final`/`translation` JSON events out).
- Measured: Silero VAD inference is ~0.46ms per 512-sample (32ms) window on
  this sandbox's 4-core CPU - confirms VAD is never the bottleneck.
- Two real bugs were caught and fixed by this live testing: `FakeAsr` wasn't
  respecting the decode cadence (flooded one partial per frame instead of per
  chunk), and `PipelineEvent`'s `use_enum_values=True` silently turned
  `event.type` into a plain string, breaking `is EventType.X` identity checks
  in `bench/benchmark.py`.

**Not verified here** (needs your hardware + network): real faster-whisper
transcription quality/latency, real Ollama/Claude translation quality, and
live mic capture. Run `python -m bench.benchmark` (after `ollama pull
llama3.1:8b-instruct-q4_K_M`, or switching `translator.provider: claude`) on
your target machine for real numbers, and compare against the <=2s
caption / <=3.5s audio budget from the spec.

---

Phase 1 complete. Per the brief, stopping here for confirmation before
starting Phase 2 (AR→EN, TTS, provider abstraction completion, glossary +
context memory).
