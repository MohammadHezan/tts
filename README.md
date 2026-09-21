# Arabic ↔ English Real-Time Speech Translation

Galaxy AI "Interpreter"-style live speech translation. Delivered in phases;
**Phases 1+2 are done**: a bidirectional (EN↔AR) engine with streaming
captions *and* spoken audio output, a CLI test harness (WAV + live mic, now
with a working listen→translate→speak loop), and a latency/WER/audio-budget
benchmark. Android, iOS, foldable UX, and desktop meeting mode are **not**
in this delivery - see [Status](#status) and [Native apps](#native-apps-android--ios--desktop-not-in-this-delivery).

## Project tree

```
tts/
├── config.yaml              # single source of truth for provider selection + params
├── glossary.yaml            # EN<->AR term glossary, injected into the translator prompt
├── .env.example              # copy to .env - API keys only, never committed
├── engine/                   # Python 3.11, FastAPI + WebSocket
│   ├── requirements.txt
│   ├── pyproject.toml         # pytest config
│   ├── app/
│   │   ├── config.py           # typed config.yaml/.env loader
│   │   ├── schema.py           # PipelineEvent wire schema (partial|final|translation|audio)
│   │   ├── logging_utils.py    # structured JSON logging + per-stage LatencyTracker
│   │   ├── audio_utils.py      # WAV I/O, resampling, frame slicing
│   │   ├── vad.py              # Silero VAD streaming endpointer
│   │   ├── segmenter.py        # sentence/semantic segmenter
│   │   ├── glossary.py         # loads glossary.yaml, renders it into the system prompt
│   │   ├── prompts.py          # shared translator domain system-prompt + glossary + context
│   │   ├── pipeline.py         # VAD -> ASR -> segmenter -> translator -> TTS, bidirectional
│   │   ├── server.py           # FastAPI WebSocket app (ws://.../ws)
│   │   └── providers/
│   │       ├── base.py                  # ASR/Translator/TTS ABCs + factories
│   │       ├── asr_faster_whisper.py    # local-agreement streaming ASR (bidirectional)
│   │       ├── asr_fake.py              # deterministic fake for tests/dry-run
│   │       ├── translator_ollama.py     # local LLM translator (default)
│   │       ├── translator_claude.py     # cloud LLM translator (optional)
│   │       ├── translator_fake.py       # deterministic fake for tests/dry-run
│   │       ├── tts_kokoro.py            # English voice (Apache-2.0)
│   │       ├── tts_piper.py             # Arabic voice (GPL-3.0 - see Licensing)
│   │       ├── tts_multi.py             # routes by language: Kokoro EN / Piper AR
│   │       └── tts_fake.py              # deterministic fake for tests/dry-run
│   ├── cli/
│   │   ├── translate_wav.py    # python -m cli.translate_wav --file audio.wav
│   │   ├── translate_mic.py    # python -m cli.translate_mic  (live bidirectional loop)
│   │   ├── audio_playback.py   # sequential TTS playback helper
│   │   └── formatting.py
│   ├── bench/
│   │   ├── benchmark.py        # python -m bench.benchmark
│   │   └── samples.py          # auto-generates an espeak-ng smoke sample set
│   └── tests/                  # pytest: VAD, segmenter, schema, glossary, integration
└── README.md
```

## Hardware/env assumptions (spec placeholders were left blank)

The brief's `HARDWARE / ENV` block wasn't filled in, so per the working rules
I chose these defaults - **update them for your machine**:

| Placeholder | Default assumed | Why |
|---|---|---|
| Laptop OS | Linux (dev/test), cross-platform code | No OS given; nothing here is Linux-only except the mic CLI's PortAudio note |
| GPU | **None assumed** (CPU fallback is the baseline) | Safer default; GPU path is fully supported, see below |
| RAM | No hard minimum enforced | `small.en`/`base.en` need <1GB; `large-v3-turbo` needs ~2-3GB; TTS models are all <1GB |
| Internet | Required for first-run model downloads (ASR/TTS weights) and for `translator.provider: claude` | Silero VAD's weights ship inside its pip package - **no network needed for VAD** |
| Paid APIs | **Off by default** ($0 to run) | Default provider stack is 100% local: faster-whisper + Ollama + Kokoro + Piper. Claude cloud translation is opt-in |

### Where quality/latency depends on hardware

| Stage | GPU path | CPU fallback |
|---|---|---|
| VAD (Silero, ONNX) | n/a - always CPU, ~0.5ms per 32ms window (measured) | same - negligible cost either way |
| ASR (faster-whisper) | `large-v3-turbo`, `device: cuda`, `compute_type: float16` - needs ~6GB VRAM, easily beats the 2s budget | `asr.model: small.en`/`base.en` + `compute_type: int8`. `large-v3-turbo` on CPU-only will likely miss the <=2s caption budget for longer utterances |
| Translator (LLM) | Ollama also runs on GPU if available; not required | `translator.provider: ollama` + a small quantized model runs acceptably on CPU; switch to `translator.provider: claude` for guaranteed low latency without local compute |
| TTS (Kokoro/Piper) | Both run fine on CPU already (ONNX runtime, small models) - GPU isn't the bottleneck here | Default; no change needed. If audio latency is tight, the biggest lever is usually cutting `translator` latency, not TTS |

`asr.device`/`compute_type` default to `auto` (GPU if `ctranslate2` detects one,
else CPU+int8) - you generally don't need to touch this.

## Setup

```bash
cd engine
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# System deps:
#   Linux:   apt install libportaudio2 espeak-ng   # PortAudio for mic capture, espeak-ng only for bench's auto smoke samples
#   macOS:   brew install portaudio espeak-ng
#   Windows: PortAudio ships inside the sounddevice wheel; install espeak-ng from https://github.com/espeak-ng/espeak-ng/releases for the auto smoke benchmark

cp ../.env.example ../.env   # fill in only what you need (see config.yaml below)
```

Nothing here needs a GPU to install. **Model weights are not bundled** (same
story for ASR and TTS) - download what you plan to use:

| Model | Where | Points at |
|---|---|---|
| faster-whisper (`large-v3-turbo` etc.) | Downloads automatically on first use via Hugging Face Hub | `asr.model` in `config.yaml` (just the model name) |
| Kokoro (English TTS) | https://github.com/thewh1teagle/kokoro-onnx/releases - grab `kokoro-v1.0.onnx` + `voices-v1.0.bin` | `tts.kokoro.model_path` / `voices_path` |
| Piper (Arabic TTS) | https://huggingface.co/rhasspy/piper-voices/tree/main/ar - pick any `ar_*` voice (`.onnx` + `.onnx.json`) | `tts.piper.model_path` |
| Ollama translator model | `ollama pull llama3.1:8b-instruct-q4_K_M` (or your own choice) | `translator.ollama.model` |

Put downloaded files under `engine/models/` (gitignored) or anywhere else -
just update the paths in `config.yaml`.

## Configure: `config.yaml`

Every stage is selected by **provider name**, resolved by
`app/providers/base.py`'s factories - no code changes needed to switch:

```yaml
asr:
  provider: faster_whisper   # or "fake" for a model-free dry run
  language: auto              # auto-detect en/ar - this is what makes it bidirectional
translator:
  provider: ollama            # or "claude" (cloud) or "fake"
  glossary_path: glossary.yaml
  context_turns: 6
tts:
  provider: none               # none (captions only) | multi_voice (Kokoro+Piper) | fake
```

- **Local, $0 (default):** `asr.provider: faster_whisper` + `translator.provider: ollama`
  + `tts.provider: multi_voice` once you've downloaded the model files above.
- **Cloud translator:** `translator.provider: claude` + `ANTHROPIC_API_KEY` in `.env`.
  ASR/TTS stay local either way.
- **Captions only, no audio:** leave `tts.provider: none` (the safe default -
  no TTS model downloads required to run at all).
- **No models at all (`fake` providers):** every CLI/bench tool accepts
  `--dry-run`, which forces ASR/translator/TTS to `fake` - validates the whole
  pipeline (VAD, streaming, event schema, latency tracking, audio event
  wiring) with zero downloads and zero network calls. This is how this repo
  was validated in this sandbox (see [Validation notes](#validation-notes-what-was-and-wasnt-run-here)).

### Licensing note on the Arabic TTS voice

`tts.kokoro` (English) uses [Kokoro](https://github.com/hexgrad/kokoro), **Apache-2.0**.
`tts.piper` (Arabic) uses [Piper](https://github.com/OHF-Voice/piper1-gpl), **GPL-3.0-or-later**
- it's the only well-maintained, good-quality *offline* neural Arabic TTS we
found with a real Python API; Kokoro's phonemizer (misaki) has no Arabic
support at all, and the closest Apache/MIT-licensed alternatives we're aware
of (e.g. Meta's MMS-TTS) are CC-BY-NC (non-commercial - the exact category
the brief said to avoid). Piper is free for commercial *use*; GPL's copyleft
obligations concern distributing *modified Piper source*, not calling it as
an external tool the way `tts_piper.py` does. If your org needs an
Apache/MIT-only dependency tree, swap the Arabic provider in `tts_piper.py`/
`tts_multi.py` - that's the entire plug point - once a suitable model exists,
or fall back to `espeak-ng` (already a dev dependency here, also GPL, but
usable as a last-resort robotic-voice fallback).

## Run it

All commands run from `engine/`.

**WebSocket server** (`ws://host:port/ws`, PCM16 mono 16kHz frames in, JSON
`PipelineEvent`s out incl. base64 `audio` when TTS is enabled - see `app/schema.py`):
```bash
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

**CLI: WAV file**
```bash
python -m cli.translate_wav --file path/to/audio.wav              # real providers, real-time paced
python -m cli.translate_wav --file path/to/audio.wav --dry-run    # no models/network
python -m cli.translate_wav --file path/to/audio.wav --play-audio # speak the translation out loud
python -m cli.translate_wav --file path/to/audio.wav --save-audio-dir out/
```

**CLI: live bidirectional interpreter (mic in, speaker/earbuds out)**
```bash
python -m cli.translate_mic --list-devices                    # find your input/output device indices
python -m cli.translate_mic                                   # Ctrl+C to stop
python -m cli.translate_mic --device 3 --output-device 4      # pick specific devices
```
Speak English, hear Arabic; speak Arabic, hear English - direction is
automatic. **This is the "listen → translate → pronounce it, vice versa" loop
you asked for**, running today from a laptop: point `--device`/`--output-device`
at your Bluetooth-paired Galaxy Buds (`--list-devices` lists every device the
OS knows about, Buds included, once paired at the OS level) and you have live
bidirectional voice translation through the earbuds - no native app needed to
validate the core UX. Not runnable in *this* cloud sandbox (no audio
hardware); validated here via `translate_wav.py --play-audio`/`--save-audio-dir`
and the WS+audio round-trip instead (see Validation notes) - run this on your
own machine to hear it.

**Benchmark: per-stage latency + WER + audio budget**
```bash
python -m bench.benchmark                     # auto-generates an espeak-ng smoke set
python -m bench.benchmark --with-audio         # also synthesize + budget-check TTS latency (<=3.5s)
python -m bench.benchmark --manifest my.json   # your own recorded audio, format: [{"wav": "...", "reference": "..."}]
python -m bench.benchmark --dry-run            # validates the harness only
```

**Tests**
```bash
pytest -q
```

## Architecture notes / trade-offs

- **Bidirectional by construction**: `asr.language: auto` means faster-whisper
  detects en/ar per utterance; `Pipeline` then picks translation direction as
  "whichever of `translator.source_lang`/`target_lang` ASR did NOT detect" -
  one config pair (`en`/`ar`) covers both directions, no per-direction config.
- **Local-agreement streaming ASR** (`app/providers/asr_faster_whisper.py`):
  faster-whisper only decodes whole buffers, not incrementally. To stream
  partial captions without O(utterance²) recompute, we re-decode only the
  still-unconfirmed audio tail every `chunk_ms`, and confirm a word once two
  consecutive decodes agree on it (LocalAgreement-2), then permanently advance
  past its audio using its own word timestamp. `finalize()` re-decodes the
  *whole* utterance once at VAD speech-end for the authoritative transcript.
- **TTS runs per sentence**, immediately after that sentence's TRANSLATION
  event - same granularity as the translator, so audio starts streaming
  without waiting for the whole utterance's translation to finish. Kokoro
  (English) and Piper (Arabic) are both ONNX-runtime based (CPU-fast, no
  torch needed at inference time).
- **Context memory**: `Pipeline` keeps a `deque(maxlen=translator.context_turns)`
  of prior `(source_text, translated_text)` turns, passed to the translator as
  alternating user/assistant messages - gives the LLM cross-turn consistency
  (pronouns, terminology) without re-sending full history every call.
- **Glossary**: `app/glossary.py` loads `glossary.yaml` and renders it as an
  "use these exact translations" block appended to the system prompt,
  direction-aware (en→ar or ar→en) via `Glossary.format_for_prompt()`.
- **VAD** (`app/vad.py`) wraps `silero_vad.VADIterator` (the reference
  streaming state machine) rather than reimplementing threshold/hangover
  logic, and re-chunks arbitrary network frame sizes into the model's fixed
  512-sample window. Weights ship inside the pip package - fully offline.
- **Segmenter** (`app/segmenter.py`) is a small guarded regex, not an NLP
  model: it only needs to avoid splitting on common abbreviations and decimal
  numbers/currency, which covers retail/business speech well without adding a
  model dependency for a rarely-ambiguous task. Handles Arabic sentence
  boundaries (`؟`, Arabic letter range) the same way.
- **Latency accounting**: every `PipelineEvent.latency_ms` for `final`/
  `translation`/`audio` events is measured from the VAD speech-end timestamp
  (the spec's actual KPIs: "<=2s caption", "<=3.5s audio"), not from
  stage-internal timers. `Pipeline.latency_for_turn()` additionally exposes a
  full per-stage breakdown (`asr_final`, `translate[i]`, `tts[i]`) for the
  benchmark script.

## Native apps (Android / iOS / desktop) - not in this delivery

You asked for all three platforms plus Bluetooth integration. Two constraints
shaped what's actually buildable next:

- **Android matches your stated hardware** (Z Fold + Buds); **iOS wasn't in
  the original spec**, and this sandbox is Linux - it physically cannot
  compile or run Swift/Xcode projects, on any turn, regardless of priority.
  Recommended order: Android next (Kotlin/Compose, per the brief's Phase 3/4),
  Desktop after (Phase 5), iOS once the UX is proven on real hardware.
- **Bluetooth reality**: Android/iOS cannot capture audio already flowing
  over Bluetooth (calls, media) - only the phone's own mic or an earbud's mic
  input. The native apps' job is exactly what `cli/translate_mic.py` already
  does: capture mic input, run it through this engine (over the WebSocket
  server), play translated audio back out to the Buds via A2DP/LE Audio.
  Nothing here changes that; the CLI proves the audio routing model works.

Say the word (and confirm the platform order above, or redirect it) and I'll
scaffold the Android app against this engine's WebSocket API next.

## Status

**Done (Phase 1+2):** VAD endpointing, bidirectional streaming ASR with
local-agreement partials, sentence segmentation (en/ar), bidirectional LLM
translation (local Ollama or cloud Claude) with glossary + multi-turn context
memory, TTS (Kokoro EN + Piper AR) wired into the pipeline as AUDIO events,
FastAPI WebSocket server, WAV + live-bidirectional-mic CLI harnesses,
latency/WER/audio-budget benchmark, structured logging, 31 passing tests.

**Not yet built**: Android app, iOS app, foldable UX, desktop meeting mode
(system audio loopback + overlay + virtual mic), Docker, CI.

## Validation notes: what was and wasn't run here

This sandbox has **no GPU** and **no outbound access to huggingface.co**
(pypi.org/files.pythonhosted.org are reachable, `huggingface.co` returns a
proxy 403), so faster-whisper/Kokoro/Piper model weights can't be downloaded
here, and Ollama isn't installed. What *was* verified in this environment:

- Full pip install of `engine/requirements.txt` (incl. `kokoro-onnx`, `piper-tts`).
- All 31 pytest tests pass (`pytest -q`), including one that runs the real
  bundled Silero VAD ONNX model against synthesized speech, and dedicated
  tests for AR→EN direction resolution, glossary loading (both directions),
  context-memory accumulation across turns, and AUDIO event wiring.
- The complete bidirectional pipeline (VAD → ASR → segmenter → translator →
  TTS) end-to-end via `--dry-run`/`fake` providers against both a WAV file and
  synthesized audio, through `cli/translate_wav.py` (incl. `--save-audio-dir`
  writing valid WAV files), `bench/benchmark.py --with-audio`, and a live
  `uvicorn` server hit over a real WebSocket client - including decoding the
  base64 `audio` field back into valid PCM16 with the correct sample rate.
- Measured: Silero VAD inference is ~0.46ms per 512-sample (32ms) window on
  this sandbox's 4-core CPU - confirms VAD is never the bottleneck.
- Bugs caught and fixed by this live testing (Phase 1): `FakeAsr` wasn't
  respecting the decode cadence, and `PipelineEvent`'s `use_enum_values=True`
  silently broke `is EventType.X` identity checks in `bench/benchmark.py`.

**Not verified here** (needs your hardware + network): real faster-whisper/
Kokoro/Piper quality and latency, real Ollama/Claude translation quality, and
live mic capture + earbud playback. Run `python -m bench.benchmark --with-audio`
on your target machine (after downloading the models above) for real numbers
against the <=2s caption / <=3.5s audio budgets, and `cli/translate_mic.py`
to hear it end to end.

---

Phase 1+2 complete. Stopping here for confirmation on platform order (Android
→ Desktop → iOS, as above) before scaffolding a native app.
