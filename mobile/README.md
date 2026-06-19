# FarryOn — Mobile App (Flutter)

The Flutter (Android + iOS) client for **FarryOn**, a real-time multimodal AI
assistant. It streams your **camera** (~1 fps JPEG) and **microphone**
(PCM16 16 kHz) to the backend over a single WebSocket, plays back streamed TTS
audio (PCM16 24 kHz) with low latency, shows live transcripts, and renders the
agent's tool activity (notes, tasks, web search, messages).

The wire contract is **`../PROTOCOL.md`** — this app conforms to it exactly. If
the protocol changes, change it there and bump `protocolVersion`.

---

## Run it

```bash
cd mobile
flutter pub get
flutter run            # talks to ws://localhost:8000/ws/live by default
```

Requires Flutter 3.19+ / Dart 3.3+, plus the platform toolchains
(Android SDK / Xcode) for the device you target.

### Configure the backend host

The backend location is **not hard-coded**. You can set it three ways
(highest precedence first):

1. **At runtime** — tap the ⚙️ (settings) icon in the app and enter host, port,
   and the TLS toggle. The client reconnects immediately.

2. **At build/run time** via `--dart-define`:

   ```bash
   flutter run \
     --dart-define=FARRYON_HOST=192.168.1.50 \
     --dart-define=FARRYON_PORT=8000 \
     --dart-define=FARRYON_SECURE=false \
     --dart-define=FARRYON_TOKEN=<optional-jwt>
   ```

   * `FARRYON_HOST` — backend IP/DNS (default `localhost`).
   * `FARRYON_PORT` — backend port (default `8000`).
   * `FARRYON_SECURE` — `true` ⇒ `wss://`, `false` ⇒ `ws://` (default `false`).
   * `FARRYON_TOKEN` — optional short-lived auth token, sent as `?token=`.

3. **Defaults** — `ws://localhost:8000/ws/live`.

> **Emulator tip:** the Android emulator reaches your host machine at
> `10.0.2.2`, not `localhost`. Use
> `--dart-define=FARRYON_HOST=10.0.2.2` (or set it in settings). The iOS
> simulator can use `localhost` directly. On a physical device, use the host's
> LAN IP and make sure both are on the same network.

The resolved endpoint is always `ws[s]://<host>:<port>/ws/live`.

---

## Permissions

FarryOn needs the **microphone** and **camera**:

* **Android** — declared in `android/app/src/main/AndroidManifest.xml`
  (`RECORD_AUDIO`, `CAMERA`, `INTERNET`). Requested at runtime on first connect.
* **iOS** — `NSMicrophoneUsageDescription` and `NSCameraUsageDescription` in
  `ios/Runner/Info.plist`. `NSAllowsLocalNetworking` is enabled so a dev
  `ws://` backend on the LAN works; for production, prefer `wss://` and tighten
  App Transport Security.

If a permission is denied the app shows a rationale; if permanently denied it
offers to open system Settings.

---

## Architecture

```
lib/
  main.dart / app.dart        ProviderScope + Material 3 → LiveScreen
  core/
    config.dart               backend host/port/TLS/token (env + runtime)
    protocol_url.dart          pure /ws/live URI builder (unit-tested)
    logger.dart                lightweight logging
  protocol/                   ── the shared wire contract, in Dart ──
    protocol.dart              tags, sample rates, msg-type & tool-name consts
    frames.dart                9-byte binary header codec (tag + uint64 LE ts)
    messages.dart              typed models for every JSON message
  data/
    live_client.dart          WebSocketLiveClient: handshake, heartbeat,
                              exponential-backoff reconnect, resumeId,
                              JSON+binary multiplexing
  capture/                    ── universal device-adapter layer ──
    capture_source.dart        abstract CaptureSource (audio16k, jpegFrames)
    phone_capture_source.dart  phone camera + mic (real)
    glasses_capture_source.dart smart-glasses stub (transport TODO)
    device_registry.dart       pick/switch the active CaptureSource
  playback/
    pcm_player.dart            low-latency PCM16 24 kHz player, flush() for barge-in
  state/
    live_controller.dart       wires CaptureSource → LiveClient → PcmPlayer
    live_state.dart            immutable UI state snapshot
    permissions.dart           mic/camera permission flow
    providers.dart             Riverpod providers + LiveNotifier
  features/live/
    live_screen.dart           camera preview, mic/interrupt/text controls,
                              device + settings sheets
    widgets/                   status_indicator, transcript_view,
                              tool_activity_view, camera_preview_view
test/                          frames, messages, URL, client, controller tests
```

### Data flow

1. Socket opens → client sends `hello` + `config` → server replies `ready`.
2. `CaptureSource` emits `audio16k` and `jpegFrames`; the controller wraps each
   in a binary frame (`0x01` audio, `0x02` video) and sends it.
3. Server streams `0x03` OUTPUT_AUDIO frames → `PcmPlayer` plays them.
4. JSON events (`transcript`, `tool_call`, `tool_result`, `state`, …) update the
   UI.
5. Tapping the mic while the assistant is speaking sends `interrupt` and flushes
   playback (barge-in).

### Reconnection

Per `PROTOCOL.md` §7: exponential backoff with full jitter (0.5 → 8 s, reset on
`ready`), a `ping` every 15 s, and a drop+reconnect if no `pong` arrives within
10 s. On reconnect the previous `sessionId` is replayed as `session.resumeId`.

---

## The universal smart-glasses adapter (`CaptureSource`)

The whole app depends on the **`CaptureSource`** abstraction and *never* on the
phone camera/mic directly. A `CaptureSource` exposes exactly what the wire needs:

```dart
abstract class CaptureSource {
  Stream<Uint8List> get audio16k;   // PCM16 LE mono 16 kHz chunks (20–100 ms)
  Stream<Uint8List> get jpegFrames; // JPEG stills ~1 fps, ≤ 1024 px
  DeviceInfo get info;              // for hello.device
  Future<void> initialize();
  Future<void> startAudio(); Future<void> stopAudio();
  Future<void> startVideo(); Future<void> stopVideo();
  Future<void> dispose();
}
```

* `PhoneCaptureSource` is the real phone implementation (camera + `flutter_sound`
  mic, with on-device JPEG downscaling).
* `GlassesCaptureSource` is a compiling, documented **stub**: it implements the
  same interface and is selectable in the device switcher, but its transport is
  marked `TODO`.

### Adding smart glasses (or any device)

1. Implement `CaptureSource` for your device in `lib/capture/`. Your only job is
   to normalize the device's output into the contract: **PCM16 LE mono 16 kHz**
   audio chunks on `audio16k`, and **≤ 1024 px JPEG** frames on `jpegFrames`.
   Typical transports: BLE/GATT, Wi-Fi/RTSP/WebRTC, or a vendor SDK
   (see the detailed notes in `glasses_capture_source.dart`).
2. Register it in `DeviceRegistry` (extend `CaptureDeviceKind` / the factory).
3. Done — the data, state, and UI layers need **no** changes; the new device
   appears in the in-app device switcher and feeds the same pipeline.

---

## Audio stack choice

`flutter_sound` is used for both capture and playback because it streams raw
**PCM16** directly to/from Dart with no file round-trip: the recorder pushes
16 kHz mono PCM to a `Sink` (→ `0x01` frames), and the player consumes streamed
24 kHz mono PCM (← `0x03` frames) with `flush()` for barge-in. That gives the
exact rates the protocol needs behind a single dependency. (The alternative,
`mic_stream` + a separate PCM player, would mean two audio packages and manual
Int16 framing.)

---

## Tests

```bash
flutter test
```

* `frames_test.dart` — binary header round-trip + known little-endian byte
  vectors (must match the backend byte-for-byte).
* `messages_test.dart` — (de)serialization of every protocol message.
* `protocol_url_test.dart` — endpoint URI building.
* `live_client_test.dart` — handshake, frame send/receive, reconnect + resumeId
  (uses an in-memory fake WebSocket).
* `live_controller_test.dart` — end-to-end orchestration (capture → wire,
  OUTPUT_AUDIO → player, barge-in, tool/transcript state).
```
