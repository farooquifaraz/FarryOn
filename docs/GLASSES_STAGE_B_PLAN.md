# FarryOn Glasses — Stage B Integration Plan
### v1.0 — 7 July 2026 · Author: Claude (per sprint package: "वो plan Claude बनाएगा")
**Input:** Stage A LAB_NOTES.md (signed off 2026-07-07) — every number and
constraint below is hardware-verified there, not assumed.

## Goal

Graduate the proven Lab bridge into production FarryOn: the user picks
**Smart glasses** as the capture device and every voice-first feature plus
Photo-Trigger Vision runs hands-free through the L801/L802. Backend,
protocol, AI gateway and prompts stay untouched — the glasses slot in under
the existing `CaptureSource` seam.

## Proven foundations (Stage A)

| Capability | Verified number |
|---|---|
| Mic input | SDK PCM 16 kHz/16-bit/mono over plain BLE, gesture start/stop |
| Vision fast path | AI photo → 512×384 thumbnail, median 3.8 s, AI-recognition 5/5 |
| Vision full path | WiFi-P2P full-res 6560×4928 @ ~4.2 MB/s, auto gallery export |
| Output | TTS via A2DP to glasses speaker, app-side volume |
| Link | 13/13 auto-reconnects; foreground service survives background |
| Wear | 0x0a events after wearCheck enable |
| Battery | ~0%/21 min moderate use |

## Phases (~6–8 weeks total)

### B0 — Bridge graduation prep — DONE (2026-07-07)
- B0.1 ✅ `pcmChunk` events now carry the real PCM bytes (voice pipeline
  can consume audio) — commit c6ecc52.
- **Decision (Faraz, 2026-07-07): the bridge SERVICE-SPLIT moves into B1.**
  Reason: the 1385-line `HeyCyanGlassesSdk` is hardware-verified as-is;
  splitting it in a vacuum forces a full device-regression for zero new
  behaviour. In B1 the real consumer (GlassesCaptureSource) reveals where
  the seams actually belong, so the split is cheaper and correct there.
- Tina's answers (remote mic `aiVoiceWake`, charging-WiFi, P2P reset) fold
  in whenever they arrive; B1+ designs fallbacks assuming NO vendor fixes.

### B1 — Bridge split + capture seam + GlassesCaptureSource (2 wk)
- **First: split `HeyCyanGlassesSdk` into services** (folded from B0) —
  `GlassesConnection` (scan/connect/reconnect/watchdogs/BT-state),
  `GlassesAudio` (PCM/HFP/TTS/volume), `GlassesVision` (photo/thumbnail/
  WiFi-sync/gallery). Shared event bus + main-thread marshalling stay
  central. Lab keeps working against the same services (debug bench).
  Gate: Lab hardware regression green before touching the seam.
- **Design change (Faraz, 2026-07-07): "capture device" is not one device.**
  Split the seam into three independent selectors, mix-and-match:
  - **Audio in:** Phone mic / BT earbuds (system route — works through the
    existing phone path already) / Glasses PCM
  - **Vision:** Phone camera (continuous 1 fps) / Glasses photo-trigger
  - **Audio out (TTS):** system route (phone/earbuds) / Glasses speaker
    (explicit A2DP opt-in)
- Flagship combos: **all-glasses** (long-press-to-talk) and
  **earbuds-voice + glasses-camera** (hands-free listening via existing
  mode; glasses stay unbonded → no assistant-chooser, camera-only battery).
- Implement `lib/capture/glasses_capture_source.dart` providing the
  glasses-backed options; Live screen in glasses-vision mode shows a status
  card (connected/worn/battery + last AI photo) instead of a preview.
- Audio: PCM stream → existing 16 kHz pipeline (zero resampling).
- Auto-connect on selection (saved MAC, no scan), foreground service on.
- Exit: live session runs with each combo (all-phone, all-glasses,
  earbuds+glasses); phone-only mode untouched.

### B2 — Voice pipeline (1.5 wk)
- Long-press gesture (0x03) = talk trigger: mic-on event → stream PCM to
  backend → Whisper → intent; mic-off (status 2) = end of utterance.
- Server-side denoise/AGC before STT (match HFP clarity — RNNoise or
  equivalent; A/B against raw).
- TTS out: A2DP route when classic-bonded; else phone speaker. **Default:
  do NOT classic-bond** (avoids the Android assistant-chooser collision);
  bond is an explicit "glasses speaker" opt-in in Settings.
- Exit: "Farry, …" full loop hands-free; assistant chooser never appears in
  default config.

### B3 — Photo-Trigger Vision (1.5 wk)
- Voice intents (scan/identify/read) → `takeAiPhoto` → thumbnail →
  `identify_image`/Gemini → spoken answer. Budget: ≤5 s photo-to-answer
  (3.8 s thumbnail + inference).
- Document tier: intent classifier flags receipts/documents → after
  thumbnail answer, background WiFi sync pulls full-res for OCR-grade
  re-processing; user gets a refined follow-up.
- Reuse Stage A guards verbatim: photo watchdog, sync watchdog, 0-count
  skip, charging-block message, media-count UX.
- Exit: 8 Camera-to-Action tools work by voice on glasses.

### B4 — Ambient UX (1 wk)
- Wear events: take-off → pause listening/session; put-on → resume — and
  **auto device-switch**: wear-on flips audio/vision to glasses, wear-off
  falls back to phone/earbuds (silent mode change, one-line status toast).
- Voice mode-switch intent: "Farry, glasses mode / phone mode" → backend
  tool → hot-swap the selectors at runtime.
- Volume voice-commands ("volume badhao") → cached setVolumeControl path.
- Auto-sync policy: on connect + on memory-full (0x0e) + nightly; synced
  media → gallery (already built).
- Battery events → status chip + low-battery voice warning.
- Exit: full day of passive wear without touching the phone.

### B5 — Hardening + release gating (1 wk)
- Stress: 7-day soak, call/notification interplay, multi-reconnect chaos.
- Release build: glasses features ship; **Lab stays kDebugMode-only**.
- Regression: full suite + phone-mode parity; battery profile under heavy
  continuous use (the untested case from 3.7).
- Exit: `FarryOn-Glasses-v1.0` APK to Faraz.

### iOS (after Android v1.0, ~3–4 wk)
QCSDK framework (Obj-C). Federated plugin design from B0 keeps the Dart
side unchanged.

## Risks & mitigations (all observed in Stage A)

1. **Glasses WiFi off while charging / stale P2P after interrupts** —
   watchdogs + user messaging exist; auto-retry once after glasses
   power-cycle prompt. Vendor answer may remove entirely.
2. **No remote mic-open** (aiVoiceWake ack'd but inert) — Stage B assumes
   long-press-to-talk; if vendor unlocks it, wake-word upgrade is a bonus.
3. **Taps invisible to app** — design uses only long-press + slide + wear;
   no dependency on taps.
4. **BLE thumbnail 3.8 s median** — within budget; thumbnailSize 0x01
   experiment queued in B3 if headroom needed.
5. **SDK callback quirks** (persistent callbacks, single slots, chunked
   streams, silent failures) — all catalogued in LAB_NOTES; bridge wraps
   every one behind guards already written in Stage A.

## Working protocol (same as Stage A — it worked)

Local commits per task, no push without Faraz's tested go; analyze clean +
tests green every change; every hardware claim measured via the `GlassesLab`
logcat tag; findings appended to LAB_NOTES.md; wireless-ADB install to the
S23 Ultra; Claude monitors logs live during device tests.
