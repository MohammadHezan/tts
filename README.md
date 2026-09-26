# Arabic ↔ English Meeting Interpreter

A Galaxy-AI-style live interpreter for meetings. A bot joins a Google Meet or
Teams call as its own participant, hears Arabic and English, and speaks each
phrase in the other language into the call. Nobody else installs anything.
You send the bot in from the PC dashboard or from the Android app.

## Quick start

1. **PC** (Windows/Linux/Mac, 16 GB memory, Docker Desktop):
   - Windows: [Interpreter-Windows.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-Windows.zip).
     Unzip it and double-click `Start Interpreter`.
   - Linux/Mac: [Interpreter-PC.zip](https://github.com/MohammadHezan/tts/releases/download/pc-latest/Interpreter-PC.zip).
     Unzip it and run `./start.sh`.
   - The first start downloads about 15 GB (20-40 minutes). After that it
     starts in about a minute.
2. Open **http://localhost:8765/bot.html**, paste a meeting link, press send,
   and admit "AI Interpreter" in the meeting.
3. **Phone** (optional): install [Interpreter-debug.apk](https://github.com/MohammadHezan/tts/releases/download/android-latest/Interpreter-debug.apk),
   open *Meeting Bot*. It finds the PC on the Wi-Fi by itself.

Stop it with `Stop Interpreter` / `./stop.sh`.

Zoom links need Zoom app credentials in Attendee. Google Meet and Teams work
as they are.

## Graphics card (NVIDIA)

The start scripts test `docker run --gpus all ... nvidia-smi`. If Docker can
reach the card, they add `docker-compose.gpu.yml`:

| | Graphics card | Processor |
|---|---|---|
| Speech recognition | Whisper large-v3-turbo | Whisper small (int8) |
| Translation | Gemma 3 12B | Llama 3.1 8B |
| Config | `deploy/config.docker-gpu.yaml` | `deploy/config.docker.yaml` |

The status line at the top of the dashboard says which one is in use and why.
Set `INTERPRETER_CPU=1` before starting to stay on the processor.

## How it works

```
Meet / Teams call  <->  Attendee bot (headless browser, joins the call)
                           |  wss:// meeting audio in, bot voice out
                           v
                     translator:  VAD -> Whisper -> Ollama -> neural voice
                           ^
       dashboard http://<pc>:8765/bot.html   and   Android app "Meeting Bot"
```

`docker-compose.yml` runs everything on one PC. Only port 8765 is published.

| Service | Role |
|---|---|
| `translator` | this repo's engine (`engine/app/serve.py`): dashboard, API, the audio bridge to Attendee |
| `setup` | one-shot: Attendee's secrets, API key, and a private certificate (`deploy/setup_secrets.py`) |
| `ollama`, `ollama-pull` | the translation model |
| `attendee-*`, `postgres`, `redis` | [Attendee](https://github.com/attendee-labs/attendee) v1.79.2, the open-source meeting bot (Elastic License 2.0), built unmodified |

One meeting, in `engine/app/`:

- `attendee_bridge.py` takes the meeting audio in and streams the bot's
  voice out. It drops the bot's own voice coming back (echo guard) and handles
  mute.
- `vad.py` cuts speech into phrases of about 10 words, so the bot starts
  translating while the speaker carries on.
- `pipeline.py` drops quiet or unvoiced noise, then runs Whisper
  (`providers/asr_faster_whisper.py`), the translator
  (`providers/translator_ollama.py`, prompt in `prompts.py`) and the voice
  (`providers/tts_neural.py`) in order, in the background.
- `meeting_chat.py`: people in the meeting can type `mute` / `unmute`
  (or `اسكت` / `تكلم`) in the chat.

## Settings you might change

In `deploy/config.docker.yaml` (processor) or `deploy/config.docker-gpu.yaml`
(graphics card):

- `vad.phrase_min_ms`: remove it to translate whole sentences instead of phrases.
- `tts.neural.voices`, `tts.neural.rates`: the voices and how fast they speak.
- `translator.ollama.model`: the translation model.

`glossary.yaml`: terms that must always translate the same way
(e.g. walnut = خشب الجوز). They also help Whisper hear them.

## Voices and licenses

The bot speaks with Microsoft's neural voices (`ar-JO-TaimNeural`,
`en-US-AndrewNeural`) through [edge-tts](https://github.com/rany2/edge-tts)
(LGPL-3.0). They need internet but no key. It is Edge's read-aloud service,
not a supported Microsoft API; the supported route to the same voices is
Azure Speech. If they fail, the bot falls back to local voices:
[Kokoro](https://github.com/hexgrad/kokoro) for English (Apache-2.0) and
[Piper](https://github.com/OHF-Voice/piper1-gpl) for Arabic (GPL-3.0, called
as a library, unmodified).

## Repository

```
engine/app/        the translator (FastAPI) and its providers; static/ is the dashboard
deploy/            start script for Windows, setup, Docker configs, Attendee's run script
android/           the Android app (see android/README.md)
docker-compose*.yml, Dockerfile, start.sh, stop.sh, *.bat
config.yaml, glossary.yaml
```

CI (`.github/workflows/`) builds the images, publishes them to GHCR, and
republishes the PC zips and the Android APK on every push.
