# Arabic ↔ English Real-Time Speech Translation

Galaxy AI "Interpreter"-style live speech translation. Delivered in phases;
**Phases 1+2 (engine), a web client, and an Android app are done**: a
bidirectional (EN↔AR) engine with streaming captions *and* spoken audio
output, a CLI test harness (WAV + live mic, with a working
listen→translate→speak loop), a latency/WER/audio-budget benchmark, a
zero-install **browser client that runs on literally any device** (Windows,
Mac, Linux, iPhone, Android - see [Web client](#web-client-any-device-any-platform)),
a Kotlin/Compose Android client with LE Audio routing for the Buds 3 Pro,
and **Meeting Interpreter**: an interpreter bot that joins a real Zoom/Google
Meet call and speaks the translation into it, sent from a web dashboard or the
Android app (see [Meeting Interpreter](#meeting-interpreter-a-bot-that-joins-zoommeet-and-interprets)).
Native iOS and foldable UX are **not** in this delivery - see
[Status](#status) and [Native apps](#native-apps).

## Quick start: the Meeting Interpreter

1. **Computer** (16 GB memory, Docker installed):
   - Windows: [Interpreter-Windows.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-Windows.zip) -
     unzip, open the `Interpreter` folder, double-click `Start Interpreter`.
   - Linux/Mac: [Interpreter-PC.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-PC.zip) -
     unzip, run `./start.sh`.
2. **Phone**: install [Interpreter-debug.apk](https://github.com/MohammadHezan/tts/releases/download/android-latest/Interpreter-debug.apk),
   open *Meeting Bot*, paste a Google Meet link, send.

Details: [Meeting Interpreter](#meeting-interpreter-a-bot-that-joins-zoommeet-and-interprets).

## Download prebuilt (Windows + Linux + Android)

Built by CI on real Windows/Linux/Android runners (this repo's own dev
environment can't cross-compile any of them) - see `.github/workflows/`.
Windows and Linux both build from the exact same `engine/desktop_launcher.spec`
(PyInstaller doesn't cross-compile, so each still needs its own native runner).

1. Go to the **Actions** tab: [Windows build](https://github.com/MohammadHezan/tts/actions/workflows/build-windows.yml) / [Linux build](https://github.com/MohammadHezan/tts/actions/workflows/build-linux.yml) / [Android build](https://github.com/MohammadHezan/tts/actions/workflows/build-android.yml)
2. Open the latest run with a green check
3. Scroll to **Artifacts** at the bottom and download:
   - **`interpreter-windows`** → unzip, run `Interpreter.exe`. It starts the
     engine and opens the web client in your browser automatically. First
     launch: Windows SmartScreen will warn "unknown publisher" (unsigned
     binary) - click "More info" → "Run anyway".
   - **`interpreter-linux`** → unzip, `chmod +x Interpreter` (the zip should
     preserve this, but set it again if your unzip tool stripped it), then
     `./Interpreter` from a terminal. Same auto-opens-your-browser behavior
     as Windows. Needs `libportaudio2` installed system-wide for live mic
     capture (`sudo apt install libportaudio2` on Debian/Ubuntu - see
     "Setup" below).
   - **`interpreter-android-debug`** → unzip, install the `.apk` on your
     phone. It's a debug build (not on Play Store), so Android will ask you
     to allow "install from unknown sources" once. **Easier on the phone:**
     every Android build also publishes the APK to the
     [android-latest pre-release](https://github.com/MohammadHezan/tts/releases/tag/android-latest) -
     open that page on the phone and tap `Interpreter-debug.apk`, no login or
     unzipping.

Downloading Actions artifacts requires being signed in to GitHub (the
release links above don't).

**Live two-way translation inside an actual Zoom/Google Meet call**: see
[Call mode (Windows/Linux + Zoom/Meet)](#call-mode-windowslinux--zoommeet)
below. Android **cannot** do this - see that section's "Why not Android"
note before asking why.

## Project tree

```
tts/
├── config.yaml              # single source of truth for provider selection + params
├── glossary.yaml            # EN<->AR term glossary, injected into the translator prompt
├── .env.example              # copy to .env - API keys only, never committed
├── Dockerfile                # translator image (Meeting Interpreter), voices + Whisper built in
├── docker-compose.yml        # the whole Meeting Interpreter: translator + Ollama + Attendee
├── start.sh / stop.sh        # one-click start/stop (Linux/Mac)
├── Start Interpreter.bat     # one-click start (Windows) -> deploy/start.ps1
├── Stop Interpreter.bat
├── deploy/
│   ├── config.docker.yaml     # CPU config used inside the container
│   ├── setup_secrets.py       # first start: Attendee secrets + API key, translator certificate
│   ├── attendee/              # scripts the bundled Attendee containers run
│   ├── download_models.py     # voices + Whisper into the image
│   └── verify_stack.py        # CI check of the running stack with the real Attendee
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
│   │   ├── server.py           # FastAPI app: ws://.../ws, /api/bots, /attendee/ws, static files
│   │   ├── serve.py            # runs server.py on HTTP :8000 + TLS :8443 (for Attendee's wss:// bot)
│   │   ├── attendee_bridge.py  # Attendee meeting-bot audio <-> Pipeline (Meeting Interpreter)
│   │   ├── attendee_client.py  # Attendee REST: create / get / remove bots
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
│   ├── static/                  # web client - served by server.py at http://host:port/
│   │   ├── index.html / style.css
│   │   ├── app.js               # WebSocket + getUserMedia + AudioContext playback
│   │   ├── pcm-worklet.js       # AudioWorklet: resamples mic input to 16kHz PCM16
│   │   └── bot.html / bot.js    # Meeting Interpreter dashboard (send bot, live captions)
│   ├── scripts/
│   │   ├── verify_web_client.py # real-browser (Playwright) end-to-end check
│   │   └── verify_meeting_bot.py # dashboard + bridge end-to-end against a fake Attendee
│   ├── cli/
│   │   ├── translate_wav.py    # python -m cli.translate_wav --file audio.wav
│   │   ├── translate_mic.py    # python -m cli.translate_mic  (live bidirectional loop)
│   │   ├── translate_call.py   # python -m cli.translate_call (two-way Zoom/Meet call mode)
│   │   ├── meeting_bot.py      # python -m cli.meeting_bot    (joins Zoom/Meet as its own participant)
│   │   ├── pipeline_runner.py  # shared one-Pipeline-against-two-devices loop (translate_call/meeting_bot)
│   │   ├── audio_playback.py   # sequential TTS playback helper
│   │   └── formatting.py
│   ├── bench/
│   │   ├── benchmark.py        # python -m bench.benchmark
│   │   └── samples.py          # auto-generates an espeak-ng smoke sample set
│   └── tests/                  # pytest: VAD, segmenter, schema, glossary, integration
├── android/                  # Kotlin/Compose native client - see android/README.md
│   ├── core/                   # pure Kotlin, built+tested here (./gradlew :core:test)
│   └── app/                    # the Android app - needs Android Studio to build
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

**WebSocket server + web client** (`ws://host:port/ws` for the API, `http://host:port/`
for a ready-to-use browser UI - see [Web client](#web-client-any-device-any-platform)):
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

## Web client (any device, any platform)

**This is the direct answer to "make it work on both my device and the
Australian person's device"**: `uvicorn app.server:app` serves a browser UI
at `http://host:port/` alongside the WebSocket API - no app install, no
platform-specific build. Open it in Chrome/Edge/Firefox on Windows/Mac/Linux,
or Safari/Chrome on iPhone/Android, and it works. One person runs the engine
(on the i7+RTX 3060 machine); everyone else just opens a URL.

How it works (`app/static/`, ~400 lines of dependency-free vanilla JS):
`getUserMedia` captures the mic, an `AudioWorklet` (`pcm-worklet.js`)
resamples it to 16kHz mono PCM16 on the audio thread and streams it over the
WebSocket, incoming `PipelineEvent`s render live captions, and `AUDIO` events
play back sequentially via `AudioContext`/`AudioBufferSourceNode`.

**This was verified in a real browser, not just written to spec** -
`scripts/verify_web_client.py` launches actual Chromium via Playwright with a
synthesized WAV fed in as the fake microphone (`--use-file-for-fake-audio-capture`),
clicks Start, and asserts captions/translation/audio genuinely render in the
DOM:
```bash
pip install -r requirements-dev.txt   # adds playwright (optional, dev-only)
python -m scripts.verify_web_client
```
This test is exactly how a real cross-platform bug was caught and fixed:
pydantic's `ser_json_bytes="base64"` encodes with the **URL-safe** base64
alphabet (`-`/`_`), which browser `atob()` silently rejects - and, it turned
out, so does Android's `Base64.DEFAULT`. Both clients now explicitly decode
URL-safe base64 (`app.js`'s `base64ToInt16Array`, the Android service's
`Base64.URL_SAFE` flag); `tests/test_schema.py` locks the wire format's
alphabet in place so this can't silently regress.

### HTTPS for the web client

Browsers only grant microphone access on a "secure context" - `https://` or
`http://localhost`. `http://<lan-ip>:8000/` (what the Australian counterpart
would open) **will not get microphone permission** without TLS. For a
trusted LAN, a self-signed certificate is enough:
```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"
uvicorn app.server:app --host 0.0.0.0 --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
```
Then open `https://<engine-ip>:8000/` and accept the one-time browser
warning (self-signed, not a public CA). For a real public/production
deployment, put a reverse proxy (Caddy, nginx) with a real certificate
(e.g. Let's Encrypt) in front instead.

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

## Native apps

**Android app is in `android/`** - see `android/README.md` for full details.
It opens in **Standalone mode** by default: talk into the phone, it
transcribes, translates, and speaks the translation back, using only
on-device Android platform APIs (`SpeechRecognizer`, ML Kit Translate,
`TextToSpeech`). No server, no Windows/laptop machine, no network dependency
beyond one one-time per-language model download. This is the mode for "I
talk to it and it responds in another language" with nothing else running.

**Meeting Bot** is one tap away: sends the interpreter bot into a Zoom/Meet
call - see [Meeting Interpreter](#meeting-interpreter-a-bot-that-joins-zoommeet-and-interprets).

**Engine mode** is one tap away in the same app for when you want it: a thin
WebSocket client to this repo's `engine/` (domain-tuned glossary, multi-turn
context memory, LE Audio (LC3) routing preference for the Buds 3 Pro - see
"How Samsung does it" below). Trade higher quality for needing a machine on
the same network running the engine. The [web client](#web-client-any-device-any-platform)
(any device, zero install) talks to that same engine too - use it for the
Australian counterpart, or anyone without a Galaxy phone.

Split into two Gradle modules on purpose: `android/core/` (pure Kotlin, no
Android dependency - the wire protocol, conversation state, audio framing)
was actually built and unit-tested in this sandbox (`./gradlew :core:test` -
11 real passing tests), same rigor as the Python side. `android/app/` (the
actual UI/AudioRecord/Bluetooth code) needs the Android SDK, which lives on
`dl.google.com` - blocked here the same way `huggingface.co` is - so it's
written carefully against verified real APIs (every uncertain OkHttp/Okio
call was checked against the actual downloaded jars, not guessed) but needs
Android Studio on your machine to compile and run on the Z Fold.

**iOS is not built yet** - it wasn't in the original spec, and building it
needs Xcode/macOS, which this Linux sandbox physically cannot run regardless
of priority. Desktop meeting mode (live two-way translation inside an actual
Zoom/Google Meet call) **is** built - see [Call mode](#call-mode-windowslinux--zoommeet)
below.

**Bluetooth reality, unchanged**: Android/iOS cannot capture audio already
flowing over Bluetooth (calls, media) - only the phone's own mic or an
earbud's mic input. The app's job is exactly what `cli/translate_mic.py`
already proved: capture mic input, run it through the engine, play translated
audio back out to the Buds. The CLI validated that audio routing model before
any native code was written.

## Meeting Interpreter (a bot that joins Zoom/Meet and interprets)

The recommended way to interpret a real Zoom or Google Meet call. An
**interpreter bot joins the meeting as its own participant**: it hears
everyone, and speaks the translation into the call (English ↔ Arabic, direction
auto-detected per sentence). Nobody else in the call installs anything. You
send it in from a web dashboard or from the Android app.

```
Zoom / Meet call  <->  Attendee bot (headless browser / Zoom SDK, joins the call)
                           |  websocket: meeting audio in, bot voice out
                           v
                     translator (this repo) -> VAD -> Whisper ASR -> Ollama translate -> Kokoro/Piper TTS
                           ^
       dashboard (http://<pc>:8765/bot.html)  and  Android app "Meeting Bot" screen
```

**Why Attendee for the bot part** (not built from scratch here):

| Option | Bot can *speak* into the call? | Cost / license | |
|---|---|---|---|
| [Attendee](https://github.com/attendee-labs/attendee) | Yes - bidirectional realtime-audio websocket | Free to self-host; Elastic License 2.0 (source-available - fine for your own business use, can't be resold as a hosted service) | **used** |
| Vexa | No evidence it can - transcription-focused | Apache 2.0 | doesn't meet the core need |
| Meeting BaaS speaking bot | Yes | Needs paid Meeting BaaS + OpenAI + Cartesia API keys | not free |

Attendee keeps up with Zoom/Meet's join flows (the part that breaks whenever
those apps change their UI) so this repo doesn't have to. This repo supplies
the translator it streams audio to: `engine/app/attendee_bridge.py`
(`realtime_audio.mixed` in → Pipeline → `realtime_audio.bot_output` out),
`engine/app/attendee_client.py` (creating/removing bots - the Attendee API key
never leaves the server), and the dashboard at `/bot.html`.

### Test it now - one download, one start

Everything runs on one computer (Windows, Linux or Mac with 16 GB of memory):
the translator, the translation model, **and Attendee itself**, bundled in
`docker-compose.yml` and set up automatically - no Attendee install, no API key,
no `.env`, no IP address, no model downloads by hand.

1. Install **Docker Desktop** (Windows/Mac) or Docker (Linux:
   `curl -fsSL https://get.docker.com | sh`).
2. Download and unzip **[Interpreter-Windows.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-Windows.zip)**
   (Windows: just the starters, everything else in `app\`) or
   **[Interpreter-PC.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-PC.zip)**
   (Linux/Mac) - or use this repo, same files.
3. **Windows:** double-click `Start Interpreter` (not `start.sh` - that's for
   Linux/Mac). **Linux/Mac:** `./start.sh`. Windows first-time Docker Desktop
   notes (accept its agreement, restart, `wsl --update`) are in
   `deploy/README-Windows.txt`, and the start script prints them if Docker's
   engine won't start.
   The first start downloads about 15 GB (20-40 minutes); after that it starts
   in about a minute. It opens `http://localhost:8765/bot.html` when ready.
4. Start a **Google Meet** and copy its link. On the phone: Interpreter app →
   *Meeting Bot* (it finds the computer on the Wi-Fi by itself) → paste → *Send
   interpreter into meeting*. Or paste it on the computer's page.
5. In Meet, **let "AI Interpreter" in**, and talk.

Google Meet and Microsoft Teams work as they are. Zoom bots need a Zoom
developer app's credentials in Attendee (see [Attendee's docs](https://github.com/attendee-labs/attendee#obtaining-zoom-oauth-credentials)) -
use Meet for testing. Two phones in one room: use earbuds or separate rooms, or
each phone's microphone hears the other.

**What the start scripts and the first start do for you:**
- `start.sh` / `deploy/start.ps1` (behind `Start Interpreter.bat`): start Docker
  Desktop if needed; on Windows, offer to raise Docker's memory cap (WSL gives
  it half the PC's memory by default - too little for a 16 GB PC) and to let
  phones on the Wi-Fi reach port 8765; pass this computer's Wi-Fi address to
  the dashboard; wait until everything is ready.
- `setup` (deploy/setup_secrets.py): generates Attendee's secrets, its API key,
  and a private certificate authority + certificate for the translator.
  Attendee refuses to stream audio to anything but a `wss://` address, so the
  bot connects to `wss://translator:8443`, trusting only that private CA
  (deploy/attendee/attendee_run.sh). The dashboard and phones use plain HTTP
  on 8765 (engine/app/serve.py runs the same app on both ports).
- `attendee-setup`: creates Attendee's database and registers the API key
  (deploy/attendee/attendee_provision.py - no Attendee account needed).
- `ollama-pull`: downloads the translation model. The voices and Whisper are
  built into the translator image (deploy/download_models.py).
Only port 8765 is published. Attendee, its database and Redis are reachable
only inside the stack. Bots are created with recording off - the interpreter
only needs the live audio, and encoding video would compete with Whisper and
the translation model for the CPU.

The Attendee image is Attendee's tagged release built unmodified (Elastic
License 2.0) and published by `.github/workflows/one-click-stack.yml`, so a
first start downloads it instead of building it. Using an Attendee you run
elsewhere instead: `docker-compose.external-attendee.yml`.

**NVIDIA GPU mode (automatic):** the start scripts check whether Docker can use
an NVIDIA GPU (`docker run --gpus all ... nvidia-smi`) and if so add
`docker-compose.gpu.yml`: the translator's GPU image
(`interpreter-translator:gpu`, CUDA cuBLAS + cuDNN 9) running Whisper
**large-v3-turbo** (`deploy/config.docker-gpu.yaml` - far better Arabic than
`small`), and Ollama on the GPU. Without a usable GPU everything stays on the
CPU (`deploy/config.docker.yaml`: Whisper `small`, int8). If the GPU can't
run Whisper after all, the translator falls back to `small` on the CPU instead
of going silent (`asr.cpu_fallback_model`). The dashboard says which one it's
using. `INTERPRETER_CPU=1` forces the CPU. CI builds the GPU image and checks
its CUDA libraries load, but no GPU is available there - GPU mode hasn't been
run on a real GPU yet.

**What keeps it from talking when nobody spoke** (Whisper "hears" stock
phrases like "شكراً" / "Thank you" / "ترجمة نانسي قنقر" in silence and noise):
- speech detection tuned for call audio (`vad` in `deploy/config.docker*.yaml`);
- utterances quieter than -50 dBFS, or with under 45 ms of *voiced* sound
  (vocal cords at a pitch - `engine/app/voicing.py`; breaths, clicks and
  keyboard noise have none) never reach Whisper;
- Whisper runs with its own speech filter and without conditioning, segments
  it rates as likely non-speech are dropped, and its stock phrases are dropped
  when they're the whole utterance (`providers/asr_faster_whisper.py`);
- the bot's own voice can come back through someone's speaker into another
  microphone (two phones in one room) and would loop: the bridge ignores the
  call's audio while the bot speaks and for 1.5s after, and drops a transcript
  matching something it just said (`attendee_bridge.py`). People talking over
  the interpreter aren't heard - with consecutive interpretation they wait.
The two-device simulation plays 20s of room noise, clicks and breaths before
the first turn and fails if the bot says anything.

**Mute**: *Mute interpreter* on the dashboard or in the app keeps the bot in the
meeting but silent (it stops mid-sentence, and skips synthesizing); captions
keep coming. *Unmute* brings its voice back (`POST /api/bots/{id}/mute`).

**How it behaves in a call**: everyone hears the bot's translation after each
sentence (consecutive interpretation, not simultaneous) - speaker finishes, a
few seconds later the bot says it in the other language. The bot never hears
itself (Zoom/Meet don't send you your own audio), so it can't loop. Each bot
loads its own Whisper model, so two bots at once means two models in memory.
Attendee's defaults decide when it leaves on its own: 60s after everyone else
has left, or after 10 minutes of silence once it has been in for 20 minutes.

**What's verified vs. not**: the Attendee side was checked against Attendee's
source (v1.79.2): the `realtime_audio.mixed` / `realtime_audio.bot_output`
messages, `websocket_settings.audio` (`url`, `sample_rate`), the leave endpoint,
API-key authentication, and the `wss://`-only rule - which the earlier manual
setup (a `ws://` callback) would have failed on. `.github/workflows/one-click-stack.yml`
runs the whole stack with `./start.sh` on every change, with the real bundled
Attendee: `deploy/verify_stack.py` sends a bot to a Meet link through the
dashboard's API, and checks Attendee accepts the request, its worker launches
the bot's browser, and the failure reason (there's no real meeting behind the
link) comes back as a sentence; a check from inside Attendee's worker container
confirms the bot trusts the translator's certificate. `engine/scripts/verify_meeting_bot.py`
runs our side with a stand-in Attendee over `wss://` - dashboard in a real
Chromium, speech in, translated speech back as `bot_output`. **Not verified:
joining a real call**, which needs a real meeting - the first real test is yours.
If Attendee rejects a request or a bot can't join, the dashboard and the phone
show the reason.

### Tested: a two-device meeting with the real models

`.github/workflows/meeting-simulation.yml` runs the **actual `docker compose`
stack** (real Whisper, Ollama llama3.1 8B, Kokoro, Piper) in a simulated call,
set up exactly as in "Test it now" above. The script is
`engine/scripts/simulate_meeting.py`:

- **Device A** (Sarah) speaks English and **Device B** (Omar) speaks Arabic,
  each in their own Piper voice (not the bot's), taking turns about an order.
- A stand-in for Attendee + Zoom/Meet accepts the "send a bot" request that
  the dashboard makes (a real Chromium clicks the button), streams everyone's
  audio to the bot in real time (20ms chunks), and plays the bot's voice to both
  devices.
- Per turn, it checks four things:
  - the bot heard the speaker correctly;
  - the translation is in the other language, has the key terms and nothing
    added (walnut must be خشب الجوز, and the translation can be at most 2x the
    length of what was said);
  - the bot's voice reached the other device;
  - a separate Whisper listening to that device's recording understands it.
- The report is on each run's page (Actions → *Meeting Simulation* → the run).
  The `meeting-simulation` artifact holds what each device heard
  (`device_A_sarah_hears.wav`, `device_B_omar_hears.wav`), the whole call, and a
  dashboard screenshot. To run it again: *Run workflow* on that page, optionally
  with `whisper_model: medium`.

Latest result on a GitHub runner (4 CPU cores, **no GPU**), all checks passing:

| Speaker | Said | Bot said |
|---|---|---|
| Sarah (EN) | Good morning Omar, thank you for joining the call today. | صباح الخير عمر، شكراً لك على الانضمام إلى المكالمة اليوم. |
| Omar (AR) | صباح النور، يسعدني أن أعرض عليكم مجموعتنا الجديدة من الكنب الفاخرة. | Good morning, I'm delighted to present to you our new collection of luxury furniture. |
| Sarah (EN) | We would like to order twenty dining chairs in walnut wood. | نرغب في طلب 20 كرسيًا طعامًا من خشب الجوز. |
| Omar (AR) | ممتاز، نستطيع تسليم الطلب خلال ثلاثة أسابيع. | Excellent, I can have the order delivered within three weeks. |

What the simulation found and fixed, and what it still shows:
- **Fixed:** the bot spoke an invented follow-up question nobody asked.
  Translation requests now name the direction and forbid replying, and
  `clean_translation()` drops sentences tacked on.
- **Fixed:** "walnut wood" was translated as cedar. The glossary now has the
  furniture materials.
- **Fixed:** translation was 2-3x slower than necessary on CPU, because the
  system prompt changed with every direction flip.
- **Still weak: Arabic speech recognition with Whisper `small`** (the Docker
  default). It heard "الكنب" (sofas) as noise, hence "furniture" above, and got
  "نستطيع" slightly wrong. English recognition was word-perfect. Whisper
  `medium` (same run, `whisper_model: medium`) heard the sofas sentence
  perfectly, but misheard "ثلاثة" (three), and the bot said "a fortnight" for
  three weeks. The check caught it and failed the run. It was also ~3x slower on
  CPU (11-19s per sentence instead of ~4s, median delay 23s instead of 13s), so
  `small` stays the CPU default. With an NVIDIA GPU, run the translator natively
  with `large-v3-turbo` (see above). The speakers here are synthetic voices
  (Omar's is Piper's lower-quality Arabic voice); real voices may do better or
  worse.
- **Delay on CPU only:** about 12-14s from when someone stops talking to when
  the other side hears the translation. The first sentence took ~35s while the
  model was still loading. The split is Whisper ~4s, translation ~6-7s,
  voice 0.5-2.5s. Translation is the part a GPU removes (`docker-compose.gpu.yml`).

## Call mode (Windows/Linux + Zoom/Meet)

Two live translation directions running at once, so you speak your language
and the other person hears the translation *through the actual call* - and
whatever they say comes back to you translated too. `cli/translate_call.py`
runs two complete, independent pipelines concurrently (each its own VAD/ASR/
translator/TTS - `AsrProvider` is explicitly one-instance-per-pipeline, so
each direction needs its own):

```
OUTGOING:  your real mic  -> translate -> virtual cable's input  -> Zoom/Meet reads this as "your Microphone"
INCOMING:  Zoom/Meet's own audio output -> translate -> your real headphones/speakers
```

This works with **zero special integration with Zoom or Meet** - both already
let you pick any input/output device for a call, and as far as either app is
concerned, our translated audio is just another microphone. The only thing
you need to install is one *virtual audio cable* (a device that exists purely
to pipe audio from one app to another on the same machine):

**Windows:**
1. Install [VB-CABLE](https://vb-audio.com/Cable/) (free) - adds a device pair
   named "CABLE Input" / "CABLE Output".
2. In Zoom/Meet's audio settings, set **Microphone → CABLE Input**. Leave
   Speaker as your real headphones/speakers - untouched.
3. Run `python -m cli.translate_call --list-devices` and note the index for:
   your real mic, "CABLE Input" (that's `--mic-out-device`), your real
   speakers/headphones (`--speaker-device`), and a **WASAPI loopback**
   device for your speakers (`--loopback-device` - `sounddevice` exposes
   this as an input-capable entry for your output device; look for your
   speaker/headphone name appearing in the input list too - that's the
   loopback capture of whatever Zoom/Meet is actually playing, no cable
   needed for this direction).

**Linux (PipeWire/PulseAudio - no install needed, it's already there):**
1. Create a null-sink to act as the virtual cable:
   `pactl load-module module-null-sink sink_name=interpreter_mic sink_properties=device.description=interpreter_mic`
2. In Zoom/Meet's audio settings, set **Microphone → interpreter_mic** (or
   "Monitor of interpreter_mic" depending on how your app lists it). Leave
   Speaker as your real output - untouched.
3. Run `python -m cli.translate_call --list-devices`. `--mic-out-device` is
   `interpreter_mic`. `--loopback-device` is your real output device's
   **monitor** source (PipeWire/PulseAudio exposes one for every sink
   automatically, listed as "Monitor of \<your speakers\>" - no null-sink
   needed for this direction, that's what a monitor source already is).

Then, both OSes:

```bash
cd engine
python -m cli.translate_call \
    --mic-device <your real mic> \
    --mic-out-device <virtual cable input> \
    --loopback-device <your speaker's monitor/loopback> \
    --speaker-device <your real speakers/headphones>
```

Needs the same one-time setup as any engine run (Ollama or a Claude API key,
TTS voices - see [Setup](#setup)/[Run it](#run-it)) - `cli/translate_call.py`
is a thin wrapper around the exact same `Pipeline` used everywhere else in
this repo, just run twice concurrently against different devices. Running
two directions at once roughly doubles CPU/RAM use versus a single
`cli/translate_mic.py` session (two full ASR models loaded, not one) - if
your machine can't keep up in real time, set a smaller `asr.model` in
`config.yaml` (e.g. `small` or `base` instead of `large-v3-turbo`).

**Not yet wired into the desktop `.exe`/binary launcher or a GUI** - this is
a CLI harness today (device indices, not a dropdown), the same tier of
"proven, not yet polished" as `cli/translate_mic.py` was before the web
client existed. A proper Call mode screen in the web UI (device pickers
instead of `--list-devices` + copy-pasted indices) is the natural next step
if this proves out for you.

**Why not Android**: this whole design works because Zoom/Meet on desktop
let *any* app register as an input/output device, and OS-level virtual audio
cables are a normal, sanctioned thing to install. Neither is true on
Android - `AudioPlaybackCaptureConfiguration` (the API for one app to record
another app's audio) explicitly excludes `USAGE_VOICE_COMMUNICATION` streams
(calls/VoIP - exactly what Zoom/Meet use) by OS policy, and there is no
mechanism at all for a third-party app to act as another app's microphone
without root. That's not a gap in this project, it's Android deliberately
not allowing what this feature does - the same protection that stops a
malicious app from silently recording your calls. Android's own live
translation (Standalone mode, see [Native apps](#native-apps)) works great
for talking to the phone directly; it can't reach into Zoom/Meet's audio.

### Meeting bot (automated, Linux, joins on its own)

For most uses, prefer [Meeting Interpreter](#meeting-interpreter-a-bot-that-joins-zoommeet-and-interprets)
above - same idea, but with Attendee maintaining the Zoom/Meet join flows
instead of this script's unverified ones. This is the no-extra-service
fallback.

`cli/meeting_bot.py` is a different way to reach the same result: instead of
Call mode's setup on the machine you're already using for the call, this
launches a headless Chromium (Playwright) that joins the meeting **as its
own participant** - a bot with a name like "AI Interpreter" that everyone
else in the call can just see and hear, no software or device setup needed
on their end. This is the same general approach real meeting-bot products
build for themselves (browser automation + OS audio routing), not an
official Zoom/Google feature - see the Call mode intro above for why Zoom's
actual official bot path (Meeting SDK, real raw audio) is gated behind their
own 4-6 week external review to join meetings you don't host, and Google
Meet has no bot API at all. A browser is just another participant either
app already knows how to host.

Only one `Pipeline` is needed (not two) - the bot is a single participant
listening to the meeting's mixed audio and speaking translations back into
it, and `Pipeline` already auto-detects the spoken language and flips
translation direction per utterance, so one instance handles both directions
of a two-person conversation flowing through it.

```bash
cd engine
pip install -r requirements-dev.txt   # adds playwright
playwright install chromium            # skip if you already have Chromium; pass --chromium-path instead
python -m cli.meeting_bot --url "https://meet.google.com/xxx-xxxx-xxx" --name "AI Interpreter" --headed
```

Linux only - it uses `pactl`/PulseAudio (or PipeWire's `pactl` compatibility
layer) to create two null-sinks, points Chromium's default mic/speaker at
them, and runs the same translation loop as Call mode against those virtual
devices. Windows would need a different virtual-audio mechanism (VB-CABLE,
same as Call mode above) - not built here.

**What's genuinely verified vs. not**, stated plainly rather than implied:
outbound access to zoom.us/meet.google.com is blocked in the sandbox this
was built in, so the actual join flow (`_join_zoom`/`_join_meet` - filling
in a name, clicking Join) **could not be run against a real meeting page**.
It's written defensively (matches buttons/fields by accessible role and
text pattern, not brittle hardcoded CSS selectors, with generous timeouts),
but that's a design choice to make it *likely* to work, not proof that it
does. What **was** verified for real here: the Chromium launch itself
(`find_chromium`, the exact launch args and context permissions this script
uses, page navigation, clean shutdown) against the sandbox's real
pre-installed Chromium - that part runs. The PulseAudio null-sink routing
couldn't be tested either (no PulseAudio in this sandbox at all). Run with
`--headed` the first time so you can see exactly where it stalls if it
doesn't join cleanly, and treat `_join_meet`/`_join_zoom` in
`cli/meeting_bot.py` as the place to fix if so.

## How Samsung's Interpreter mode gets so fast (and what we borrowed)

Three things stack together, not one "magic model": (1) a dedicated NPU
(12-80 TOPS depending on chip generation) doing AI math no laptop CPU can
match; (2) tiny purpose-built models (~350MB/language pair) instead of a
general LLM; (3) Bluetooth LE Audio (LC3 codec) on the Buds 3 Pro, which
round-trips at ~50-100ms versus ~120-200ms on classic AAC Bluetooth - a pure
transport-latency win, unrelated to AI. (1) isn't replicable on a laptop-hosted
engine; (2) is exactly the model-tiering this README already documents
per-hardware; (3) is fully implemented in `android/app/.../BluetoothAudioRouting.kt`
- explicit LE Audio preference, not left to whatever the OS defaults to.

## Status

**Done (Phase 1+2, engine):** VAD endpointing, bidirectional streaming ASR
with local-agreement partials, sentence segmentation (en/ar), bidirectional
LLM translation (local Ollama or cloud Claude) with glossary + multi-turn
context memory, TTS (Kokoro EN + Piper AR) wired into the pipeline as AUDIO
events, FastAPI WebSocket server, WAV + live-bidirectional-mic CLI harnesses,
latency/WER/audio-budget benchmark, structured logging, 32 passing tests.

**Done (web client, this delivery):** browser UI served by the engine itself
(`app/static/`) - works on any device with a modern browser, no install.
Verified end-to-end in a real Chromium instance via Playwright
(`scripts/verify_web_client.py`), which caught and led to fixing a real
cross-client base64-encoding bug (see "Web client" above).

**Done (Android, this delivery):** Kotlin/Compose app with three modes -
**Standalone** (default: on-device `SpeechRecognizer` + ML Kit Translate +
`TextToSpeech`, no server), **Meeting Bot** (sends the interpreter bot into a
Zoom/Meet call and shows its captions), and **Engine** (WS client, mic capture
+ earbud playback, LE Audio routing preference, foreground service). All build
as a real debug APK on GitHub Actions CI (`.github/workflows/build-android.yml`,
`ubuntu-latest`, unrestricted `dl.google.com` access unlike this sandbox);
`:core` additionally has 11 real passing unit tests run in this sandbox.
(Caption Bridge, which translated Zoom/Meet's own on-screen captions, was
removed - the Meeting Bot covers calls both ways.)
Iterated against real device feedback: an on-device-only ASR attempt failing
silently on hardware with no on-device Arabic model (fixed with an automatic
fallback to the standard system recognizer), one-tap-per-sentence replaced
with continuous listening, and TTS audio not reaching Bluetooth earbuds
(fixed - `SpeechRecognizer` was leaving the session on the earbuds' Bluetooth
SCO call-audio link instead of the normal A2DP media link TTS needs).

**Done (desktop, this delivery):** `engine/desktop_launcher.py` +
`desktop_launcher.spec` package the *exact same* engine (real faster-whisper
ASR, real Ollama/Claude translation, real Kokoro/Piper TTS - not a
simplified on-device stand-in like Android's standalone mode) into a
double-click desktop app via PyInstaller, for both Windows and Linux from
the one spec file. CI-built and verified green on real `windows-latest` and
`ubuntu-latest` runners (`.github/workflows/build-windows.yml`,
`build-linux.yml`) - see "Download prebuilt" above. One real bug caught and
fixed by that CI: `silero-vad`'s own package metadata pulls in `torch`, and
pip's default Linux wheel for `torch` drags in the full CUDA toolkit as
several GB of unused `nvidia-*` packages, which exhausted the Linux runner's
disk - fixed by pinning the CPU-only PyTorch build on both platforms (this
project never uses torch directly, only silero-vad's ONNX runtime export).

**Done (call mode, this delivery):** `engine/cli/translate_call.py` runs two
concurrent `Pipeline` instances against different audio devices - a virtual
cable feeding Zoom/Meet's microphone input, and a loopback capture of
Zoom/Meet's own output audio - for live two-way translation inside an actual
call, with no special integration with either app (see
[Call mode](#call-mode-windowslinux--zoommeet)). Not runnable in this
sandbox (no audio hardware, same constraint as `translate_mic.py`); verified
here via module import, argparse/`--list-devices`/`--help` execution, and
by construction - it reuses `Pipeline`/`AudioPlayer`/the provider factories
unchanged, just wired to two devices instead of one, the same pattern
`translate_mic.py` already uses for a single direction. Android **cannot**
do this - it's an OS-level restriction, not a missing feature; see that
section's "Why not Android."

**Done (meeting bot, this delivery):** `engine/cli/meeting_bot.py` - a
headless-Chromium bot (Playwright) that joins a Zoom/Meet meeting as its own
participant and runs one auto-direction-detecting `Pipeline` against
PulseAudio virtual devices wired to that browser, so no one else in the
call needs any special setup at all. Genuinely mixed verification, stated
plainly: the Chromium launch itself (exact executable path resolution,
launch args, context permissions, navigation, shutdown) was run for real
against this sandbox's pre-installed Chromium and works. The actual
join-a-meeting flow could not be - zoom.us/meet.google.com are blocked by
this sandbox's egress policy - and the PulseAudio null-sink routing
couldn't be tested either (no PulseAudio installed here at all). See
[Meeting bot](#meeting-bot-automated-linux-joins-on-its-own) for exactly
what that means for you running it for real.

**Done (Meeting Interpreter, this delivery):** an Attendee-based interpreter
bot for real Zoom/Meet calls - `engine/app/attendee_bridge.py` +
`attendee_client.py`, the `/bot.html` dashboard, a Docker image + compose file
for the translator (with Ollama), and an Android *Meeting Bot* screen that
sends the bot and shows its captions. Our whole side verified for real by
`scripts/verify_meeting_bot.py` (real Chromium, real VAD, stand-in Attendee),
which caught two real bugs while being written: captions lost when the
dashboard connected after the bot's first sentence (the event feed now
replays history), and the caption socket blocking server shutdown. Since
then bundled into a one-click stack with Attendee itself, checked against
Attendee's source and run with the real Attendee in CI - see the section's
"What's verified vs. not". CI also runs the Python suite and both browser
checks on every push (`test-engine.yml`).

**Not yet built**: iOS app, foldable UX (split view / Flex mode / cover
screen), a GUI for call mode (device dropdowns instead of `--list-devices` +
copied indices), GPU support inside the Docker image.

## Validation notes: what was and wasn't run here

This sandbox has **no GPU** and **no outbound access to huggingface.co**
(pypi.org/files.pythonhosted.org are reachable, `huggingface.co` returns a
proxy 403), so faster-whisper/Kokoro/Piper model weights can't be downloaded
here, and Ollama isn't installed. What *was* verified in this environment:

- Full pip install of `engine/requirements.txt` (incl. `kokoro-onnx`, `piper-tts`).
- All 32 pytest tests pass (`pytest -q`), including one that runs the real
  bundled Silero VAD ONNX model against synthesized speech, and dedicated
  tests for AR→EN direction resolution, glossary loading (both directions),
  context-memory accumulation across turns, AUDIO event wiring, and the
  base64 wire-format alphabet (see below).
- The complete bidirectional pipeline (VAD → ASR → segmenter → translator →
  TTS) end-to-end via `--dry-run`/`fake` providers against both a WAV file and
  synthesized audio, through `cli/translate_wav.py` (incl. `--save-audio-dir`
  writing valid WAV files), `bench/benchmark.py --with-audio`, and a live
  `uvicorn` server hit over a real WebSocket client.
- **The web client, in a real browser**: `scripts/verify_web_client.py`
  launches actual Chromium (Playwright) with a synthesized WAV as a fake
  microphone, clicks Start, and confirms captions/translation/audio genuinely
  render in the DOM - not just "the server responds," the whole
  mic→AudioWorklet→WebSocket→VAD→pipeline→WebSocket→DOM chain, for real.
- Measured: Silero VAD inference is ~0.46ms per 512-sample (32ms) window on
  this sandbox's 4-core CPU - confirms VAD is never the bottleneck.
- Bugs caught and fixed by this live testing: `FakeAsr` wasn't respecting the
  decode cadence (Phase 1); `PipelineEvent`'s `use_enum_values=True` silently
  broke `is EventType.X` identity checks (Phase 1); pydantic's
  `ser_json_bytes="base64"` uses the URL-safe alphabet, which both `atob()`
  and Android's `Base64.DEFAULT` mis-decode (caught by the browser test,
  fixed in both clients, locked in by a new schema test).

**Not verified here** (needs your hardware + network): real faster-whisper/
Kokoro/Piper quality and latency, real Ollama/Claude translation quality, and
live mic capture + earbud playback. Run `python -m bench.benchmark --with-audio`
on your target machine (after downloading the models above) for real numbers
against the <=2s caption / <=3.5s audio budgets, and `cli/translate_mic.py`
to hear it end to end. Android Standalone mode's actual recognition/
translation/speech quality on-device (as opposed to it compiling and the APK
installing) also needs your Z Fold - it depends on the on-device speech model
and ML Kit language pack Google ships for your exact device/region.

---

Engine (Phase 1+2), web client, and Android app complete - the web client
alone covers "works on both my device and the Australian person's device"
for any browser on any platform, today. Next up: Desktop meeting mode
(Phase 5) or native iOS - say which, or redirect.
