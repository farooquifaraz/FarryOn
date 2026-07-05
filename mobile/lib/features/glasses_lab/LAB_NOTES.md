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
- [x] Connect time (10 attempts): **11/11 success** (2026-07-05, logcat
      timestamps, connect cmd → connected event):
      2.3, 3.1, 2.06, 3.06, 1.85, 5.05, 2.51, 2.34, 2.01, 2.84, 3.75 s →
      **median 2.51 s, worst 5.05 s** — all well under the 10 s target.
      Disconnect via unBindDevice sticks (23 s observed, no self-reconnect).
      One phantom "connected" 34 ms after a disconnect (SDK's re-broadcast
      raced unBind) → fixed with a userDisconnected guard + re-unbind.
- [x] Battery event codes observed: `addBatteryCallBack` fires periodically on
      its own (~every few s alongside the SDK heartbeat), pct=100 charging=false.
      Notify 0x05 path not yet seen in the wild (battery arrived via callback).
- [ ] Thumbnail: resolution / bytes / measured latency (5 samples):
      Transport: `getPictureThumbnails` STREAMS the JPEG in ~1013-byte BLE
      chunks (boolean=false per chunk, true on the final one) — must
      accumulate; the PDF never mentions chunking.
      5 samples (2026-07-05, thumbnailSize=0x02):
      | bytes  | total ms | (capture ≈2.2–2.4 s + BLE transfer rest) |
      | 22 865 | 4564 |
      | 17 263 | 4082 |
      | 18 898 | 4095 |
      | 24 368 | 4520 |
      | 15 850 | 3650 |
      **Median 4095 ms, range 3650–4564 ms** — above the 3 s card target but
      inside the Integration Assessment's 3–5 s Camera-to-Action budget.
      Transfer ≈10 kB/s ⇒ thumbnailSize 0x00/0x01 could shave ~1 s; capture
      time (~2.2 s) is firmware-fixed. Verdict: Photo-Trigger Vision viable.
- [ ] Wear/touch support probe (`wearFunctionSupport`, model=21):
      **wear=true**, translation=true, **volume=false** (yet volume-slide
      0x12 events DO arrive — support flag vs behaviour mismatch, noted).
      `wearCheck(true, true)` → open=true — wear reporting now enabled;
      which notify code wear on/off uses is still unconfirmed (0x0a suspect).
- [x] `voiceFromGlasses` PCM: **16 000 Hz / 16-bit / mono — CONFIRMED**
      (2026-07-05). Evidence: 473 600 B in a 15.0 s wall-clock session =
      31 571 B/s ≈ 32 000 B/s; decoded as 16 kHz 16-bit mono the duration is
      14.8 s (matches); RMS 4380 / peak 32768 = real speech; ZCR ~2 000/s.
      Trigger: glasses long-press (mic gesture) → voiceFromGlassesStatus(1)
      → PCM chunks → status(2) on release. Works over BLE alone — NO classic
      BT pairing needed. Whisper-ready with zero resampling. Files:
      lab_1783280376381.pcm (+ WAV copies sent to Faraz).
- [~] HFP recording quality: AudioRecord @16 kHz wideband worked (319 488 B
      / 10.0 s exact), real audio — BUT classic-BT bond never confirmed
      (no BOND_BONDED event in 5 pair attempts), so the SCO source may have
      been the PHONE mic. Verdict pending bond confirmation.
- [ ] TTS on glasses speaker: TTS played on the media route; whether it came
      out of the glasses or the phone speaker needs Faraz's ear-witness
      (classic BT bond unconfirmed).
- **Audio A/B verdict (Stage B input path): SDK PCM WINS.** 16 kHz/16-bit/
  mono raw stream over plain BLE, no pairing ceremony, gesture-triggered
  start/stop signals included. HFP stays as fallback only if TTS output also
  needs classic BT anyway.
- [ ] WiFi sync speed (kB/s), pairing UX:
- [ ] Gesture events: kaunsa gesture → kaunsa event code (2026-07-05 session):
      - slide on temple → `0x12` volumeChange (music 0/16 curr 15, call 0/15
        curr 15, system 0/16 curr 10, mode 03) — slides ARE the volume control
      - long press (earlier session) → `0x03` mic-on/start-speaking
      - `0x0a` loadData=`bc 73 02 00 c6 d0 0a 01` → **NOT in the vendor doc**,
        seen once, not yet reproduced — still unmapped
      - **single tap / double tap → NO event reaches the app** (67 s window,
        nothing logged) — taps are likely handled on-device only
- [ ] Wear detection events: **wear on/off emitted NOTHING by default.**
      Hypothesis: reporting must be enabled first via
      `LargeDataHandler.wearCheck(enable, ...)` (API exists in the .aar) —
      test in a follow-up session; matters for Stage B auto-sleep.
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
