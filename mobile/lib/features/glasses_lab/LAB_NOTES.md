# Glasses Lab — findings log

> हर task के बाद यहाँ लिखो: क्या measure हुआ, क्या surprise निकला।
> यही file Stage B के decisions (audio path, latency budgets) का source है।

## Sprint 1 (module skeleton — stub mode)

- Date: 2026-07-05
- Bridge contract v1 defined (`bridge/glasses_channel.dart` ↔ `GlassesChannels.kt`).
- Stub SDK simulates: scan (1 device), connect (~0.8 s), battery/wear events,
  AI-photo thumbnail (~1.5 s, generated 320×240 JPEG), PCM stream
  (10 chunks/s @ "16000 Hz"), WiFi sync progress (0→100% in ~3 s).
- All six cards render and function against the stub. ✅

### Task V2 — stub smoke test on real phone (Galaxy S23 Ultra, 2026-07-05)

- Device: Samsung Galaxy S23 Ultra (SM-S918B), installed over wireless ADB;
  emulator plan dropped — Faraz tests on hardware only.
- Bug found on first open: Camera card button row overflowed 34 px on the
  S23 Ultra's width ("RIGHT OVERFLOWED BY 34 PIXELS" stripe). Fixed by
  switching the button Rows in camera_card / connection_card /
  media_sync_card to Wrap. analyze clean, 63/63 tests pass, re-installed.
- Checklist result on the fixed build: 6/6 pass (Faraz's run, event-console
  paste + screenshots as evidence):
  1. STUB MODE banner visible ✅
  2. Scan → L801-STUB → connect (green badge, battery 87%) ✅
  3. AI photo → thumbnail, elapsedMs=1501 (two runs, both 1501 ms) ✅
  4. Mic via SDK PCM → 188 chunks · 16000 Hz, Stop clean ✅
  5. WiFi sync 0→100%, 420 kB/s (simulated) ✅
  6. Event console logged everything (28 events), Copy all works ✅
- Note for future testers: "Mic via HFP" and "Mic via SDK PCM" are separate
  buttons — HFP emits only an `audio` status event, the chunk counter is
  PCM-only. First run of the checklist missed this.
- APK size note: fat debug APK is 213 MB; arm64-only debug is 86 MB — that is
  the floor for a debug build (Dart VM + JIT). The 19 MB arm64 release build
  hides the Lab by design, so test APKs stay debug/86 MB.

## Sprint 2 (real SDK wiring) — भरना बाकी

- [x] `.aar` version used: LIB_GLASSES_SDK-release_3.aar (HeyCyan Android SDK
      1.0.2, 2025-08-16, 1.9 MB) — dropped into app/libs/ 2026-07-05, debug
      build still green, stays git-ignored as intended.
- [x] Scan: L801 दिखा? नाम/MAC format: **"L802_2B1D"** (model prefix L802, not
      L801 — naming surprise), MAC `C0:97:B9:6D:2B:1D`, RSSI −45 dBm at desk
      distance. Scan also surfaces every named BLE device around (e.g. a Sony
      TV) — no vendor-prefix filter in the Lab, by design.
- [ ] Connect time (10 attempts): first hardware session: connect+services
      ≈ 3 s (2 samples, event-console timestamps); 10-cycle stress = Sprint 3.
- [x] Battery event codes observed: `addBatteryCallBack` fires periodically on
      its own (~every few s alongside the SDK heartbeat), pct=100 charging=false.
      Notify 0x05 path not yet seen in the wild (battery arrived via callback).
- [ ] Thumbnail: resolution / bytes / measured latency (5 samples):
- [ ] `voiceFromGlasses` PCM: sample rate / bit depth / channels / kis mode me:
- [ ] HFP recording quality (8k narrowband ya 16k wideband?):
- [ ] TTS on glasses speaker: clarity / volume:
- [ ] WiFi sync speed (kB/s), pairing UX:
- [ ] Gesture events: kaunsa gesture → kaunsa event code:
- [ ] Wear detection events:
- [ ] Surprises / vendor doc se alag behaviour:
  - 2026-07-05 (first hardware session, Task 2.3):
    - Device info: btFirmware `AM01L2_2.00.00_260114`, btHardware `AM01L2_V2.0`,
      **wifiFirmware/wifiHardware come back EMPTY** — likely the WiFi chip
      sleeps until a WiFi operation; re-check during Task 2.6.
    - `setNeedConnect(false)` + `disconnect()` does NOT disconnect — the SDK
      re-attaches within ~4 s. The real teardown is `unBindDevice()` (matches
      the sample's disconnect button). Fixed in HeyCyanGlassesSdk.
    - The SDK re-broadcasts service-discovered every ~2.5 s on a live link →
      "connected" spam in the console. Now deduped to transitions only (raw
      callbacks still in logcat, tag `GlassesLab`).
