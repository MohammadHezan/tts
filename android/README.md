# Interpreter - Android app

Talk into your phone, hear the translation back - no server required. The
app opens in **Standalone mode** by default: on-device speech recognition,
on-device translation, and the phone's own text-to-speech, entirely local
after a one-time setup. More modes are a tap away: **Caption Bridge**
(translates Zoom/Google Meet's own Live Captions while you're in a call -
listening only), **Meeting Bot** (sends an interpreter bot into a Zoom/Meet
call that hears everyone *and* speaks the translation into it - runs on your
computer, controlled from the phone), and **Engine mode** (talk to the
`engine/` WebSocket service on your LAN for higher-quality,
glossary/context-aware translation). Built for Galaxy Z Fold + Galaxy Buds
3 Pro, but works on any Android 12+ (API 31+) phone.

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
is transcribed, translated, and spoken back automatically. Tap "Engine mode"
in the top bar to switch to the WebSocket client instead.

**Trade-offs vs. Engine mode** - this is a deliberate simplicity-for-quality
trade, not a bug:
- You pick the speaker's language each turn; there's no automatic language
  detection like the Whisper-based engine does.
- Translation is generic ML Kit NMT - no glossary injection, no
  conversation-context memory across turns.
- First use of each language pair needs one internet connection to fetch its
  ~30MB ML Kit model; every use after that is fully offline.

## Caption Bridge (Zoom/Meet, listening only)

Translates whatever Zoom or Google Meet's own **Live Captions** feature is
already showing on screen, and speaks the translation. Built after directly
confirming this is the only legitimate way to get call audio-derived text on
Android at all: `AudioPlaybackCaptureConfiguration` (the API for one app to
record another app's audio) explicitly excludes `USAGE_VOICE_COMMUNICATION`
streams, and Zoom/Meet's call audio is documented as using exactly that -
there is no way to capture their audio directly, on-device, without root.
Reading their *captions* instead is a completely different, fully
OS-sanctioned mechanism (`AccessibilityService` - the same API screen
readers use to read on-screen text), so this is real and legal where audio
capture isn't.

**What it does**: watches for on-screen text in Zoom (`us.zoom.videomeetings`)
and Meet (`com.google.android.apps.meetings`) only - nothing else on your
phone - reads their caption text as it updates, translates it, and speaks
the translation.

**What it does not do**: send anything back into the call. This is listening
only. To reply, speak for yourself, or switch to Standalone mode to
translate what *you* say and say that translation yourself. Building a
version of this that also talks back into the call would need Zoom's
official Meeting SDK bot path (raw audio, but gated behind Zoom's own 4-6
week external app-review process to join meetings you don't host) or,
for Meet, a desktop browser-automation bot (Meet has no bot API at all) -
both are desktop-only regardless.

**Setup:**
1. Grant the accessibility permission: open Caption Bridge in the app, tap
   "Open accessibility settings", find "Interpreter" in the list, turn it on.
   Android requires this to be a manual step in system Settings for this
   permission class - it cannot be requested like a normal runtime permission.
2. In your Zoom/Meet call, turn on that app's own Live Captions/Live
   Transcript feature (the CC icon in their call controls).
3. Pick which language the *other person* is speaking in Caption Bridge's
   chip row. Translated lines appear and are spoken automatically as their
   captions update.

**Honest caveat**: Zoom/Meet don't publish a stable "this is the caption
text" identifier, so `CaptionAccessibilityService.findLikelyCaptionText()`
uses a heuristic (a view-id keyword match, falling back to the longest text
in the bottom half of the screen, where captions are conventionally
overlaid) that could not be verified against a live call in the sandbox this
was built in - there's no Zoom/Meet account or live call available there.
The screen has a "raw detected text" debug panel so you can confirm on your
actual device whether it's reading the right thing; if it isn't, that
heuristic in `captionbridge/CaptionAccessibilityService.kt` is exactly what
needs adjusting; describing what it grabs instead would pin down the fix.

Source: `app/src/main/kotlin/com/interpreter/app/captionbridge/` -
`CaptionAccessibilityService.kt`, `CaptionBridgeViewModel.kt`,
`CaptionBridgeScreen.kt`. Reuses `MlKitTranslator`/`OnDeviceTts` from
`standalone/` unchanged - only the input side (accessibility-read text
instead of `SpeechRecognizer` audio) is new.

## Meeting Bot (Zoom/Meet, full two-way - via your computer)

The phone-side answer to "make it hear the call *and* speak into it": the
phone doesn't do the audio at all. It sends an **interpreter bot** into the
Zoom/Meet call - the bot runs on your computer (the translator server from the
root README's "Meeting Interpreter" section) and joins as its own
participant, so it can hear everyone and speak the translation into the call.
That is the only way to get both directions: Android blocks any app from
capturing another app's call audio or acting as its microphone (see Caption
Bridge above), but a separate participant needs neither.

**Use it:** Caption Bridge → "Meeting Bot" in the top bar. Enter your
computer's address on the same Wi-Fi (e.g. `http://192.168.1.50:8765`), paste
the meeting link, tap *Send interpreter into meeting*, admit the bot from the
meeting's waiting room. The screen shows each sentence it heard and what it
said, live. Leaving the screen doesn't pull the bot out - *Remove bot* does.

Source: `app/src/main/kotlin/com/interpreter/app/meetingbot/` -
`MeetingBotApi.kt` (the server's `/api/bots` REST + caption websocket),
`MeetingBotViewModel.kt`, `MeetingBotScreen.kt`. Captions go through core's
existing `ConversationState` reducer unchanged - the server sends the same
`PipelineEvent` JSON Engine mode already parses.

## Two modules, on purpose

```
android/
├── core/   # pure Kotlin/JVM - wire protocol, state, audio framing. No Android
│           # dependency, builds+tests with plain Gradle (no Android SDK needed).
└── app/    # the actual Android app - UI, AudioRecord/AudioTrack, Bluetooth
            # routing, foreground service. Needs the Android SDK (Android Studio).
```

This split exists because `:core` was built and unit-tested in a
network-sandboxed environment that has Java/Gradle/Maven Central but **not**
`dl.google.com` (where the Android SDK and AndroidX libraries live) - so it's
the one part of this app that's been genuinely compiled and tested, not just
written to spec. Run it yourself:

```bash
cd android
./gradlew :core:test    # or: gradle :core:test  (11 tests, real JVM execution)
```

`:app` needs Android Studio (or a machine with the Android SDK installed) to
build - see [Building](#building) below.

## What's in `:core` (tested here)

- `PipelineEvent.kt` / `EventType.kt` - mirrors `engine/app/schema.py` exactly,
  parsed via kotlinx.serialization with a snake_case↔camelCase naming bridge.
- `ConversationState.kt` - a pure reducer over the event stream (partial
  caption, completed turns with their translations) - the same shape a
  ViewModel exposes to the UI, fully testable without Android.
- `AudioFraming.kt` - PCM16 frame-size/slicing helpers matching
  `engine/app/audio_utils.py`'s `iter_frames`.

## What's in `:app` (written to spec, not compiled here)

- `standalone/` - the default, server-free mode (see above): on-device ASR,
  ML Kit translation, platform TTS, and the ViewModel/UI wiring them
  together. No dependency on anything else in this repo.
- `network/EngineWebSocketClient.kt` - OkHttp WebSocket client; every API call
  in it (`WebSocket.send(ByteString)`, `WebSocketListener` callback
  signatures, `ByteString.of(...)`) was checked against the real OkHttp/Okio
  4.12.0/3.9.0 jars downloaded from Maven Central and inspected with `javap`
  in this sandbox - not guessed.
- `audio/MicCapture.kt`, `audio/AudioPlayback.kt` - AudioRecord/AudioTrack
  wrappers, mirroring `engine/cli/translate_mic.py`'s capture/playback loop.
- `audio/BluetoothAudioRouting.kt` - **prefers LE Audio (LC3) over classic
  Bluetooth** via `AudioManager.setCommunicationDevice`/
  `availableCommunicationDevices` (API 31+, why minSdk is 31). This is the
  single biggest lever for closing the latency gap with Samsung's own
  Interpreter mode on the same Buds 3 Pro - LC3 round-trips at ~50-100ms
  versus ~120-200ms on classic AAC Bluetooth.
- `service/InterpreterForegroundService.kt` - owns the whole session (mic +
  WS + playback) so backgrounding the app mid-conversation doesn't drop it.
- `ui/` - single-screen Compose UI: connection status, editable engine
  address, big live caption, scrollback of completed turns with their
  translations. Dark theme, per spec.

This part genuinely cannot be verified without the Android SDK (`dl.google.com`
is blocked in the sandbox this was built in - see the main README's
"Validation notes" for the same constraint on the Python side). It was
written carefully against well-established, stable Android APIs, and every
API I was *not* fully certain of was checked against real downloaded
artifacts rather than guessed - two real mistakes were caught this way
(a nonexistent `stringResourceCompat` typo, and a nonexistent
`ByteArray.toByteString()` extension that doesn't actually exist in Okio's
public API). Expect at most minor first-build fixups, not a rewrite.

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
