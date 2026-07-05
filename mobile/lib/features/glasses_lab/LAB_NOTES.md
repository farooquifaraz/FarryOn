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

## Sprint 2 (real SDK wiring) — भरना बाकी

- [ ] `.aar` version used:
- [ ] Scan: L801 दिखा? नाम/MAC format:
- [ ] Connect time (10 attempts):
- [ ] Battery event codes observed:
- [ ] Thumbnail: resolution / bytes / measured latency (5 samples):
- [ ] `voiceFromGlasses` PCM: sample rate / bit depth / channels / kis mode me:
- [ ] HFP recording quality (8k narrowband ya 16k wideband?):
- [ ] TTS on glasses speaker: clarity / volume:
- [ ] WiFi sync speed (kB/s), pairing UX:
- [ ] Gesture events: kaunsa gesture → kaunsa event code:
- [ ] Wear detection events:
- [ ] Surprises / vendor doc se alag behaviour:
