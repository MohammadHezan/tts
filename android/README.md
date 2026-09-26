# Interpreter - Android app

Talk into your phone, hear the translation back - no server required. The
app opens in **Standalone mode** by default: on-device speech recognition,
on-device translation, and the phone's own text-to-speech, entirely local
after a one-time setup. More modes are a tap away: **Meeting Bot** (sends an
interpreter bot into a Zoom/Meet call that hears everyone *and* speaks the
translation into it - the bot runs on a computer, controlled from the phone)
and **Engine mode** (talk to the `engine/` WebSocket service on your LAN for
higher-quality, glossary/context-aware translation). Built for Galaxy Z Fold +
Galaxy Buds 3 Pro, but works on any Android 12+ (API 31+) phone.

## Standalone mode (default - no server, no Windows machine)

This is the direct answer to "why do I need to connect to a server just to
talk to my phone?" - you don't. Standalone mode is built entirely from
on-device Android platform APIs:

| Stage | API | Notes |
|---|---|---|
| Speech -> text | `android.speech.SpeechRecognizer` (`createOnDeviceSpeechRecognizer`) | Guaranteed on-device on API 31+, zero network |
| Text -> text | ML Kit Translate (`com.google.mlkit:translate`) | Free, Apache-2.0, Google's on-device NMT; downloads a ~30MB model per language **once** (needs internet that one time), fully offline after |
| Text -> speech | `android.speech.tts.TextToSpeech` | Built into every Android device, no download for English/Arabic |

Source: `app/src/main/kotlin/com/interpreter/app/standalone/` -
`OnDeviceSpeechRecognizer.kt`, `MlKitTranslator.kt`, `OnDeviceTts.kt`,
`StandaloneViewModel.kt`, `StandaloneScreen.kt`.

**How to use it:** pick which language you're about to speak (the chip row
at the top), tap the mic, talk, tap again to stop (or just pause - the
recognizer ends the turn on its own after a moment of silence). Your speech
is transcribed, translated, and spoken back automatically. The top-bar button
cycles modes: Standalone → Meeting Bot → Engine mode → Standalone.

**Trade-offs vs. Engine mode** - this is a deliberate simplicity-for-quality
trade, not a bug:
- You pick the speaker's language each turn; there's no automatic language
  detection like the Whisper-based engine does.
- Translation is generic ML Kit NMT - no glossary injection, no
  conversation-context memory across turns.
- First use of each language pair needs one internet connection to fetch its
  ~30MB ML Kit model; every use after that is fully offline.

## Meeting Bot (Zoom/Meet, full two-way - via your computer)

The phone-side answer to "make it hear the call *and* speak into it": the
phone doesn't do the audio at all. It sends an **interpreter bot** into the
Zoom/Meet call - the bot runs on your computer (the translator server from the
root README's "Meeting Interpreter" section) and joins as its own
participant, so it can hear everyone and speak the translation into the call.
That is the only way to get both directions: Android blocks any app from
capturing another app's call audio or acting as its microphone
(`AudioPlaybackCaptureConfiguration` excludes the `USAGE_VOICE_COMMUNICATION`
streams Zoom/Meet use), but a separate participant needs neither.

**Use it:** start the interpreter on the computer (`Start Interpreter` /
`./start.sh`, root README "Meeting Interpreter"). On the phone: Standalone →
"Meeting Bot" in the top bar. The screen finds the computer on the Wi-Fi by
itself (`ServerFinder.kt` asks every address on the phone's subnet for
`/healthz` on port 8765 and remembers the one that answers as the
interpreter); if it can't, it offers a box to type the address. Paste the
meeting link (*Paste* button), tap *Send interpreter into meeting*, admit
"AI Interpreter" in the meeting. The screen shows the bot's status in plain
words, why it couldn't join if it can't, and each sentence it heard and said,
live. Leaving the screen doesn't pull the bot out - *Remove interpreter* does.

Source: `app/src/main/kotlin/com/interpreter/app/meetingbot/` -
`MeetingBotApi.kt` (the server's `/api/bots` REST + caption websocket),
`ServerFinder.kt`, `MeetingBotViewModel.kt`, `MeetingBotScreen.kt`; the subnet
math is `core`'s `SubnetScan.kt`. Captions go through core's
existing `ConversationState` reducer unchanged - the server sends the same
`PipelineEvent` JSON Engine mode already parses.

## Two modules

```
android/
├── core/   # pure Kotlin/JVM - wire protocol, state, audio framing, subnet math.
│           # No Android dependency.
└── app/    # the Android app - UI, AudioRecord/AudioTrack, Bluetooth
            # routing, foreground service. Needs the Android SDK (Android Studio).
```

**`:core`**

- `PipelineEvent.kt` / `EventType.kt` - mirror `engine/app/schema.py`,
  parsed with kotlinx.serialization.
- `ConversationState.kt` - a pure reducer over the event stream (partial
  caption, completed turns with their translations).
- `AudioFraming.kt` - PCM16 frame helpers matching `engine/app/audio_utils.py`.
- `SubnetScan.kt` - the addresses the Meeting Bot screen asks for the PC.

**`:app`**

- `standalone/` - the default, server-free mode (see above).
- `meetingbot/` - the Meeting Bot screen (see above).
- `network/EngineWebSocketClient.kt` - OkHttp WebSocket client for Engine mode.
- `audio/MicCapture.kt`, `audio/AudioPlayback.kt` - AudioRecord/AudioTrack
  wrappers.
- `audio/BluetoothAudioRouting.kt` - prefers LE Audio (LC3) over classic
  Bluetooth via `AudioManager.setCommunicationDevice` (API 31+, why minSdk is
  31): about 50-100 ms round trip on the Buds 3 Pro versus 120-200 ms on AAC.
- `service/InterpreterForegroundService.kt` - owns the session (mic + WS +
  playback) so backgrounding the app doesn't drop it.
- `ui/` - Compose UI, dark theme.

CI (`.github/workflows/build-android.yml`) builds the debug APK and publishes
it to the [android-latest pre-release](https://github.com/MohammadHezan/tts/releases/tag/android-latest).

## Building

1. Install [Android Studio](https://developer.android.com/studio) (this gets you the Android SDK too).
2. Open the `android/` folder as a project.
3. Let Gradle sync - it'll download the Android SDK components and all
   dependencies from Google's Maven repo automatically.
4. Run on your Z Fold (USB debugging, or a Wi-Fi-connected device).

## Connecting to your engine (optional - Engine mode only)

Standalone mode (the default) needs none of this. Engine mode is a thin
client to the Python engine - **it does not run ASR/translation/TTS itself**.
Start the engine (see the root `README.md`) on a machine on the same network
as your phone:

```bash
cd ../engine
uvicorn app.server:app --host 0.0.0.0 --port 8000
```

Then in the app, set "Engine address" to `ws://<that machine's LAN IP>:8000/ws`
(e.g. `ws://192.168.1.42:8000/ws` - find the IP with `ip addr` / `ifconfig` on
the engine machine) and tap Start.

## Permissions

Requested on first launch: microphone (capture, needed by both modes),
Bluetooth (routing to the Buds, Engine mode only), notifications (Engine
mode's foreground service's persistent "listening" notification, required by
Android while it holds the mic in the background). Standalone mode never
starts that foreground service, so it never shows that notification.
