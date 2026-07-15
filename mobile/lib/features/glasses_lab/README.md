# Glasses Lab — L801 hardware test bench (Stage A)

Isolated test module for the HeyCyan L801 smart glasses. One card per SDK
capability; every device event lands in the on-screen console. This module is
the rehearsal for Stage B, where the proven bridge graduates into
`lib/capture/glasses_capture_source.dart`.

## Isolation guarantees

- Debug builds only: the settings entry is behind `kDebugMode`; release builds
  never show or run the Lab.
- Self-contained: owns its controller + platform bridge; imports nothing from
  `live/`, `capture/`, or `data/`, and nothing imports it except the single
  settings `ListTile` in `live_screen.dart`.
- Crash-safe: every bridge call is guarded; a missing/broken native side shows
  a status banner instead of throwing.

## Architecture

```
GlassesLabScreen (cards UI)
  └─ GlassesLabController (ChangeNotifier — all state, unit-tested)
       └─ GlassesBridgeApi (Dart contract)
            └─ GlassesChannel — MethodChannel 'com.farryon/glasses'
                               EventChannel  'com.farryon/glasses/events'
                 └─ Kotlin: GlassesChannels → GlassesSdk interface
                      ├─ StubGlassesSdk (default; simulated L801)
                      └─ HeyCyanGlassesSdk (Sprint 2; needs vendor .aar)
```

Commands go Dart → native; device DATA always comes back as events
(mirrors BLE reality, so stub → real SDK is a drop-in swap).

## Running

- Any phone/emulator, no hardware: `flutter run` (debug) → Settings →
  Glasses Lab. Banner shows **STUB MODE**.
- Real glasses (Sprint 2): drop the vendor `.aar` into `android/app/libs/`
  (see the README there), implement `HeyCyanGlassesSdk`, switch the factory in
  `GlassesChannels.createSdk()`. Note: real BLE needs `BLUETOOTH_SCAN` /
  `BLUETOOTH_CONNECT` permissions in the manifest + runtime prompts — that is
  part of Sprint 2, deliberately not added in stub-only Sprint 1.

## Tests

`flutter test test/glasses_lab_controller_test.dart` — controller logic against
a fake bridge (no platform channels involved).

## LAB_NOTES.md

Every sprint task appends findings (measured latencies, PCM formats, event
codes, surprises) to `LAB_NOTES.md` — that file is the input for Stage B
decisions. Keep it honest; "didn't work" is a finding.
