# Interpreter - Android app

Single-screen bidirectional interpreter client for the `engine/` WebSocket
service: mic in, live captions + spoken translation out to your earbuds.
Built for Galaxy Z Fold + Galaxy Buds 3 Pro, but works on any Android 12+
(API 31+) phone.

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

## Connecting to your engine

The app is a thin client - **it does not run ASR/translation/TTS itself**.
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

Requested on first launch: microphone (capture), Bluetooth (routing to the
Buds), notifications (the foreground service's persistent "listening"
notification, required by Android while it holds the mic in the background).
