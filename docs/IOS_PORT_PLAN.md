# FarryOn — iOS Port Plan (full functionality clone)

Date: 2026-08-09. Based on a full review of `mobile/` (71 Dart files, ~18,800 lines),
the native Android layer (8 Kotlin files, ~4,000 lines), the backend contract
(`PROTOCOL.md` + `backend/app`), and the HeyCyan iOS SDK
(`AI Glasses SDK/HeyCyan_iOS_SDK_V1.0.0_20251205`).

## Verdict

**Yes — a full-functionality iOS port is plannable, and it is NOT a rewrite.**
FarryOn is a Flutter app: the entire UI, state management, live-session pipeline,
WebSocket protocol, REST layer, auth, notes/tasks, finder, email, subscription,
and settings are cross-platform Dart and carry over to iOS as-is.

What must actually be built for iOS:

1. **A Swift native layer** replacing 8 Kotlin files — three platform channels
   whose Dart-side contract is already frozen (`com.farryon/glasses` + its event
   channel, `com.farryon/audio_mode`, `com.farryon/media`).
2. **The HeyCyan glasses integration on the iOS SDK** (QCSDK.framework — present
   in the repo, and in several ways *cleaner* than the Android SDK).
3. **~8 small Dart fixes** where the code is currently Android-only
   (notifications, permissions, secure-storage options, Google Sign-In config).
4. **The iOS project scaffold itself** — `mobile/ios` today has no Xcode project,
   no AppDelegate, no Podfile; only a hand-customized Info.plist.

Two genuine functional gaps need decisions/vendor input (see §6): the iOS SDK has
no raw device-notification firehose (wear-state / touch gestures / storage-full),
and iOS media sync joins the glasses' own WiFi AP, which drops the phone off the
internet mid-sync (Android's WiFi-Direct did not).

---

## 1. Complete feature inventory (what "no missed functionality" means)

### Cross-platform Dart — ports unchanged
| Area | Files | Notes |
|---|---|---|
| Live voice+vision session | `state/live_controller.dart` (2,166 ln), `data/live_client.dart`, `protocol/*` | WS `/ws/live`, binary frames (PCM16 16 kHz up / 24 kHz down, JPEG 1 fps), echo guard, mic energy gate, barge-in, reconnect w/ resume |
| Capture abstraction | `capture/*` | Phone mic/camera + glasses sources behind one interface |
| TTS playback | `playback/pcm_player.dart` | flutter_sound `startPlayerFromStream` 24 kHz |
| Auth | `features/auth/*`, `state/auth.dart`, `data/auth_api.dart` | Email/password, 2FA, Google SSO, rotating refresh tokens |
| Your Stuff | `features/data/*` | Notes, reminders, conversations; offline cache + outbox |
| Finder | `features/finder/*`, `data/finder_api.dart` | Landmark/product identify via `POST /detect` |
| Settings | `features/settings/*` | Server, provider, languages, hands-free, glasses card, 2 email accounts, web-search keys, subscription/usage + Stripe checkout |
| Email | `core/config.dart`, `data/email_probe.dart` | Credentials sent per-session in `hello`; IMAP probe is a raw TLS socket (works on iOS) |
| Voice tools (client-executed) | `live_controller.dart` | zoom, mute, camera on/off/rotate, capture_photo, identify, record_video, stop_recording, enable_bluetooth, connect/disconnect glasses, end_session, WhatsApp/SMS open, contact resolution (on-device), reminders |
| Debug | `features/debug/*`, `core/log_store.dart` | Log share via share_plus |
| Glasses UI | `features/glasses/*`, `features/glasses_lab/*` | Connect flow, Lab bench — Dart side unchanged; talks to the channel contract |

### Native Android → must be rebuilt in Swift
| Kotlin file | iOS replacement |
|---|---|
| `MainActivity.kt` | `AppDelegate.swift` registering the 3 channels |
| `AudioModeChannel.kt` | `AudioModeChannel.swift` — AVAudioSession `.playAndRecord` + `.voiceChat` + speaker override; skip when route is BT/wired (`skipped_external_route`) |
| `MediaChannel.kt` | `MediaChannel.swift` — PHPhotoLibrary save into a "Farry" album, return localIdentifier string |
| `GlassesChannels.kt` | `GlassesChannels.swift` — same 23 methods, 14+ event types, verbatim strings |
| `GlassesSdk.kt` (+ stub) | `GlassesSdk.swift` protocol + `StubGlassesSdk.swift` (port the stub too — it's what makes the simulator usable) |
| `HeyCyanGlassesSdk.kt` (2,743 ln) | `HeyCyanGlassesSdk.swift` on QCSDK.framework + a `GlassesCentralManager.swift` (port of the vendor demo's `QCCentralManager` — scan/connect/reconnect is demo code, not framework code) |
| `GlassesForegroundService.kt` | **No iOS equivalent.** CoreBluetooth state restoration (`CBCentralManagerOptionRestoreIdentifierKey`) + `bluetooth-central` background mode |
| `SessionMicService.kt` | **No iOS equivalent.** `audio` background mode + keeping the AVAudioSession active; `startMicService`/`stopMicService` become audio-session activate/deactivate so Dart stays unchanged |

### The frozen contract (zero Dart churn if respected)
Channel names `com.farryon/glasses`, `com.farryon/glasses/events`, `com.farryon/media`,
`com.farryon/audio_mode`; all 23 glasses method names; argument keys `mac`,
`timeoutMs`, `enabled`, `days`, `seconds`, `mode`, `type`, `level`, `bytes`, `name`;
event types `connectionState`, `battery`, `wearState`, `gesture`, `deviceInfo`,
`thumbnail`, `captureFailed`, `videoState`, `syncedPhoto`, `pcmChunk`, `mediaCount`,
`syncProgress`, `audio`, `error`, `deviceEvent`; the `videoState`/`captureFailed`
reason vocabularies; `"applied" | "skipped_external_route" | "unavailable"`;
binary payloads (`jpeg`, `data`) must be `FlutterStandardTypedData(bytes:)` —
Dart checks `is Uint8List` and silently drops anything else.

`takeAiPhoto` and `startVideoRecording` are the only methods returning a value
(a correlation requestId); everything else acks `null` and answers via events.

---

## 2. Backend — no changes required

The wire contract is platform-neutral. The client already sends
`hello.client.platform: "ios"` on iOS (backend only logs it). Items worth touching
opportunistically (not blockers):

- `backend/app/tools/device.py` ~line 164: prompt says "on Android the user sees
  a quick system 'Allow' prompt" — spoken to iOS users; make it platform-neutral.
- `session_expired` server event is unhandled in Dart (both platforms) — handle it.
- `/detect` is unauthenticated and cost-bearing — unrelated to iOS, but noted.

---

## 3. Dart changes required (small, all platform-gating)

1. **`core/notifications.dart`** — the most Android-coupled file. Add
   `DarwinInitializationSettings`, `DarwinNotificationDetails` to all four detail
   objects, iOS permission request via the plugin. Accept: iOS has no exact-alarm
   guarantee (`UNCalendarNotificationTrigger`), no progress-bar notification, no
   `ongoing` notification for glasses activity (in-app status or drop; Live
   Activity is optional later).
2. **`features/glasses_lab/glasses_permissions.dart`** — currently returns `true`
   unconditionally off-Android. Add `Permission.bluetooth` handling for iOS.
3. **`core/config_store.dart`** — add
   `IOSOptions(accessibility: KeychainAccessibility.first_unlock)` so tokens are
   readable after reboot before first unlock.
4. **`state/auth.dart` / Google Sign-In** — keep `serverClientId` (Web client id)
   AND add an iOS OAuth client: `GIDClientID` + reversed-client-id URL scheme in
   Info.plist. (Trap from Android setup: Web vs platform client id confusion.)
5. **`enable_bluetooth` tool path** — iOS cannot prompt to enable BT; native
   returns a status the assistant can speak ("turn it on in Control Centre").
6. **`debug_logs_screen.dart`** — share_plus on iPad needs `sharePositionOrigin`.
7. **`pubspec.yaml`** — `flutter_launcher_icons: ios: true` (icons currently
   Android-only).
8. **Optional:** handle `session_expired`; keep `startMicService` calls as-is
   (already try/caught — native maps them to audio-session management).

---

## 4. iOS project scaffold (currently missing)

`mobile/ios` has **no Xcode project** — only Info.plist (customized), generated
plugin registrant, and build artifacts. Required:

1. `flutter create --platforms=ios .` in `mobile/`, then **re-apply the existing
   Info.plist customizations** (it will be overwritten): display name "Farry",
   camera + mic usage strings, `NSAllowsLocalNetworking`.
2. Add to Info.plist:
   - `NSBluetoothAlwaysUsageDescription`, `NSLocalNetworkUsageDescription`,
     `NSBonjourServices` (`_http._tcp`, `_http._udp`),
     `NSPhotoLibraryAddUsageDescription`, `NSPhotoLibraryUsageDescription`
     (image_picker gallery), `NSLocationWhenInUseUsageDescription`,
     `NSContactsUsageDescription`
   - `UIBackgroundModes`: `audio`, `bluetooth-central`
   - `LSApplicationQueriesSchemes`: `whatsapp`, `sms`, `tg`
   - `CFBundleURLTypes` with the Google reversed client id; `GIDClientID`
   - ATS: keep `NSAllowsLocalNetworking` + exception for `192.168.31.1`
     (the glasses' AP address, per the vendor demo)
3. Entitlement: `com.apple.developer.networking.HotspotConfiguration`
   (needed for `NEHotspotConfiguration` to join the glasses' AP).
4. Embed `QCSDK.framework` (+ nested `JLAudioUnitKit`, `JLLogHelper`) — it's
   Objective-C, module-enabled, importable from Swift directly.
5. Bundle id, signing, App Store assets. Release note: Android disables Impeller
   (black-surface-on-resume bug); on iOS Impeller is the only backend — retest
   resume behaviour explicitly.

**Build environment reality check:** iOS builds require macOS + Xcode. From this
Windows machine the options are: a Mac (even a base Mac mini), a cloud Mac, or
CI (Codemagic / GitHub Actions macOS runners) with a physical iPhone for device
testing. Glasses testing additionally needs the L801 unit near the iPhone.

---

## 5. The glasses port in detail (the real work)

The HeyCyan iOS SDK (QCSDK V1.0.0, 2025-12-05) has **parity or better** for
almost everything FarryOn uses:

**Better on iOS:**
- Video is explicit `Video` / `VideoStop` modes — Android's single ambiguous
  toggle (and all the `workTypeIng` interpretation, corrective-toggle and
  `already_recording` machinery) collapses into simple calls. Keep the
  `videoState` event contract and the precondition ladder (busy/low_battery/
  bad_duration/not_connected), drop the toggle heuristics.
- `getVideoInfo` returns the supported-durations list (Android guessed; demo
  configures 360 s, above Android's 240 cap).
- Typed volume model (`QCVolumeInfoModel`) instead of a raw 10-int block.
- Per-file delete with a real success/fail callback (`deleleteMedia:` — the typo
  is the vendor's) instead of a separately-registered ack listener.
- Documented voice-wake API; AI-photo thumbnail arrives as one
  `didReceiveAIChatImageData:` callback — no manual ~1013-byte BLE chunk
  reassembly (keep an overall timeout instead of the per-chunk watchdog).
- Live glasses-mic PCM: `didReceiveAIChatVoiceData:` — same 16 kHz/16-bit/mono.

**Different on iOS (design decisions):**
- **No MAC addresses.** iOS identifies peripherals by per-app
  `CBPeripheral.identifier` UUID. Keep the `mac` field in the channel contract
  and carry the UUID string in it (zero Dart churn); fetch the real MAC
  post-connect (`getDeviceMacAddressSuccess:`) for display only.
  `bondedGlasses()` maps to `retrieveConnectedPeripherals(withServices:)`.
- **No classic-BT control.** Android reflectively connects/disconnects A2DP for
  TTS-on-glasses. iOS cannot: user pairs once in iOS Settings, iOS routes media
  automatically; route TTS back to speaker via AVAudioSession overrides.
  `pairClassicBt` becomes guidance ("pair in Settings"), emitted as an `audio`
  status event.
- **Ready signal** is `QCSDKManager addPeripheral:finished:` success (analogue of
  Android's `onServiceDiscovered`). Auto-reconnect policy is app-side (port the
  demo's reconnect + `willRestoreState` + saved identifier).
- **Media sync joins the glasses' WiFi AP** (`openWifiWithMode:` → SSID/password →
  `NEHotspotConfiguration` → HTTP download from 192.168.31.1) instead of
  WiFi-Direct. **The phone loses internet during sync, so the live WebSocket
  will drop.** Design decision required — recommended: block sync during a live
  session (mirror of the existing "no sync while recording" rule), show clear UX,
  auto-reconnect the session after sync. Stall recovery: overall watchdog +
  `setDeviceMode:NoPowerP2P` (the P2P reset exists on iOS too).
- A 30 s clip ≈ 40 MB at ~3.6 MB/s (hardware-measured on Android) — expect
  similar payloads over the AP.

**Gaps to raise with the vendor before implementation (see §6):**
- No raw device-notification firehose in the public headers. Android's notify
  stream (`load[6]` reports) powers: wear on/off (0x09 → wear-to-talk),
  long-press mic state (0x03), wearer's own button capture (0x01 →
  auto-sync trigger), storage-full (0x0e → retention), volume-slide (0x12),
  and the whole `deviceEvent {hex}` diagnostics trail. The likely hook is the
  `OdmNotifyD2P` NSNotification — needs verification with HeyCyan.
- No per-file transfer speed callback (cosmetic — `syncProgress.speedKbps`).
- Recorded audio may arrive as Opus (`convertOpusToPcm` helper exists).

**Port verbatim (hardware-earned defensive logic, all OS-neutral):**
single-in-flight guards for photo/video/sync; generation counter on thumbnail
fetches; duplicate-notify dedupe; watchdogs with typed failure reasons on every
async op; firmware-owned video duration (never app timers); terminal
`syncProgress pct=100` convention; media-count probe before import; two-stage
lazy media recount (+3 s/+10 s); retention deletes only after the file is safely
on the phone; remove-before-add listener idempotence; the retention pending-queue
(`name|millis` set, now in UserDefaults). Make the stub fallback **loud** — the
silent stub fallback caused two "STUB FRAME" release bugs on Android.

---

## 6. Questions to send HeyCyan now (long lead time — send before coding)

1. How does an iOS app receive raw device notifications (wear state, touch
   gestures, wearer-initiated capture, storage full)? Is `OdmNotifyD2P` the
   supported hook, and what is the payload format?
2. Is there an iOS callback equivalent to Android's
   `voiceFromGlassesStatus(1/2)` (mic session start/end), or is inferring the
   end from chunk silence the intended pattern?
3. Does `startToDownloadMediaResourceWithProgress:` expose transfer speed, and
   is there any way to cancel a running download?
4. Confirm recorded-audio format on iOS (Opus?) and the role of
   `convertOpusToPcm`.
5. Confirm the AP-mode IP (192.168.31.1) and whether BLE control (battery,
   notifications) stays alive while the phone is on the glasses' AP.

---

## 7. Phased delivery plan

### Phase 0 — Environment + scaffold (2–4 days)
Mac/CI access; Apple Developer account; `flutter create --platforms=ios`;
re-apply Info.plist; Podfile; icons (`flutter_launcher_icons` ios: true);
signing; **milestone: app builds and shows the login screen in the simulator.**

### Phase 1 — Full phone-only app on iOS (1–2 weeks)
- Swift: `AppDelegate` + `AudioModeChannel` + `MediaChannel` + glasses channel
  registered with **StubGlassesSdk** (Lab reports `stub`, everything degrades
  gracefully exactly as designed).
- Dart: notifications Darwin support, secure-storage IOSOptions, Google
  Sign-In iOS config, LSApplicationQueriesSchemes, iPad share origin.
- Verify the whole non-glasses app on a real iPhone: live session (echo
  behaviour with VoiceProcessingIO!), background audio with screen locked,
  auth + Google SSO, notes/reminders (schedule + fire + cancel), finder,
  WhatsApp/SMS/contacts flow, email test, subscription, gallery save.
- **Milestone: feature parity with Android minus glasses, device-tested.**

### Phase 2 — Glasses on iOS (2–4 weeks, hardware-gated)
- `GlassesCentralManager.swift` (scan/connect/reconnect/restoration) →
  `HeyCyanGlassesSdk.swift` in contract order:
  1. scan/connect/disconnect/auto-reconnect + battery/deviceInfo (UUID-as-mac)
  2. AI photo → `thumbnail`/`captureFailed` events + retry semantics
  3. live PCM (`pcmChunk`) → wear-to-talk end-to-end via backend
  4. video record (explicit start/stop) → `videoState` events
  5. media counts, WiFi-AP sync → `syncProgress`/`syncedPhoto`, gallery export
  6. retention (delete-after-sync + pending queue), volume, video duration
- Resolve vendor answers (wear-state hook is required for wear-to-talk parity).
- **Milestone: Glasses Lab all-green on iPhone + L801; live session with glasses
  mic + photo + video + sync, device-tested.**

### Phase 3 — Background/lifecycle hardening + release (1–2 weeks)
- CoreBluetooth state restoration; audio session interruptions (phone call,
  Siri, route changes); sync-vs-session UX for the AP problem; Impeller resume
  test; TestFlight; App Store review prep (BT + background-audio justifications,
  privacy manifest/nutrition labels for mic/camera/location/contacts).

Total: roughly **5–8 weeks** of focused work, dominated by Phase 2 hardware
iteration (Android's glasses layer took weeks of on-device debugging; iOS
benefits from all of it — the protocol quirks are documented — but sync,
backgrounding and audio routing are genuinely different).

## 8. Risk register (top 5)

| Risk | Impact | Mitigation |
|---|---|---|
| Vendor iOS SDK lacks the raw notify stream | Wear-to-talk, auto-sync trigger, storage-full retention degrade | Vendor question #1 now; fallback: poll battery/media-counts, drop wear-to-talk on iOS v1 |
| WiFi-AP sync drops the live session | Sync mid-conversation kills the assistant | Block sync during sessions; auto-resume after; clear UX |
| iOS backgrounding kills the mic/session | "Talk with screen off" parity | `audio` background mode + never-deactivated session; test early in Phase 1 |
| Echo behaviour differs (VoiceProcessingIO vs Android AEC) | Assistant hears itself → fake turns | The Dart echo guard + gate is platform-neutral; test speaker mode on device in Phase 1 |
| App Store review (background BT + audio + contacts) | Launch delay | Honest usage strings, demo video for review notes |
