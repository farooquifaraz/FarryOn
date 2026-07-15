# FarryOn — Live Translator Feature: Design Document

**Version:** 1.0 · **Date:** 2026-07-03 · **Author:** Solution Architecture (Claude + Faraz)
**Status:** Proposed — review ke baad implementation start karein

---

## 1. Executive Summary (सारांश)

FarryOn पहले से ही एक real-time, multimodal voice assistant है — phone/glasses का mic से 16kHz PCM16 audio backend WebSocket पर stream होता है, backend Gemini Live / OpenAI Realtime से बात करता है, और 24kHz PCM16 audio वापस आकर play होता है।

**Live Translator** feature का मतलब: user एक भाषा में बोले, app real-time में दूसरी भाषा में **बोला हुआ translation** (speech-to-speech) सुनाए — साथ में दोनों तरफ के text transcripts भी दिखाए।

**सबसे बड़ी good news:** June 2026 में Google ने Live API के अंदर एक dedicated translation model launch किया है — `gemini-3.5-live-translate-preview` — जो:

- 70+ भाषाओं के बीच continuous speech-to-speech translation करता है (हिन्दी, उर्दू, अरबी, English सब included)
- Input भाषा **auto-detect** करता है — सिर्फ target language बतानी होती है
- Audio format **exactly वही है जो FarryOn पहले से use करता है**: input 16kHz PCM16 mono, output 24kHz PCM16 mono, ~100ms chunks

इसका मतलब है कि FarryOn का पूरा existing audio pipeline (Flutter capture → WebSocket → backend → Gemini Live → WebSocket → PCM player) **बिना बदले reuse** हो जाता है। हमें मुख्य रूप से चाहिए: (a) backend में एक नया translate-mode provider adapter, (b) session-level "translator mode", (c) Flutter में एक Translate screen + language picker।

**अनुमानित लागत:** लगभग $0.02–$0.04 प्रति minute (नीचे Section 9 में detail; ये आँकड़े third-party sources से हैं — official pricing page से verify करना ज़रूरी है)।

---

## 2. Feature Definition — Translator के 3 Modes

| Mode | Description | Priority |
|---|---|---|
| **A. Conversation Mode** (आमने-सामने) | दो लोग एक phone पर बात करें। Person 1 हिन्दी बोले → English audio निकले; Person 2 English बोले → हिन्दी audio निकले। Auto language detection की वजह से एक ही session दोनों दिशाएँ handle कर सकता है (देखें Section 6.3)। | **P0** |
| **B. Listen / Interpreter Mode** | User सिर्फ सुनना चाहता है — lecture, announcement, TV, meeting। एक-तरफ़ा stream: जो भी भाषा आए → user की भाषा में audio + captions। Glasses के साथ यही killer use-case है (glasses का mic सुनता है, कान में translation आती है)। | **P0** |
| **C. Caption-only Mode** | Audio output बंद, सिर्फ live subtitles screen पर (silent environments, hearing-impaired users)। Technically Mode B का ही sub-case है — बस translated audio play नहीं करते, सिर्फ `outputTranscription` दिखाते हैं। | **P1** |

Success criteria (industry benchmark):
- First translated audio ≤ 2–3 seconds speaker के बोलने के बाद (model continuous है, "कुछ seconds पीछे" चलता है)
- Session में transcripts दोनों भाषाओं के real-time दिखें
- Network drop पर ≤ 3 second में transparent reconnect
- प्रति-user daily minutes cap (cost protection)

---

## 3. Industry Landscape — दो Architecture Approaches

### 3.1 Cascaded Pipeline (पुराना/classic तरीका)
```
Mic → STT (speech-to-text) → MT (machine translation) → TTS → Speaker
```
- **Pros:** हर stage observable/swappable है, transcript guaranteed, vendor lock-in कम
- **Cons:** 3 network hops → latency ज़्यादा (आमतौर पर 2–4s+), आवाज़ की emotion/tone खो जाती है, 3 services का orchestration + billing

### 3.2 Native Speech-to-Speech (S2S) — 2026 का industry standard
एक ही model audio-in → translated-audio-out करता है, streaming तरीके से।
- **Pros:** सबसे कम latency, आवाज़ का style/emotion कुछ हद तक preserve, एक ही WebSocket, simple architecture
- **Cons:** black-box (बीच में intervene नहीं कर सकते), provider-specific

2026 के independent benchmarks के अनुसार native S2S models latency और naturalness में जीतते हैं; cascaded सिर्फ तब चुनें जब transcript legally load-bearing हो या on-prem चाहिए। **FarryOn के लिए native S2S ही सही है** — app already Gemini Live/OpenAI Realtime (दोनों native S2S) पर बनी है।

### 3.3 Provider Comparison

| Provider | Model/Service | भाषाएँ | Latency (E2E first-token) | Pricing (approx)* | FarryOn Fit |
|---|---|---|---|---|---|
| **Google** | `gemini-3.5-live-translate-preview` (Live API) | 70+, auto-detect input | Continuous stream, कुछ sec पीछे; Gemini Live class ~1–3s | ~$0.023–0.037/min | ⭐ **Best** — existing `gemini.py` adapter, formats identical |
| **OpenAI** | GPT-Realtime-Translate / gpt-realtime-2 | major languages | ~0.8s first token (realtime class) | ~$0.034/min (translate model); full realtime $32/1M audio-in tokens | Good fallback — `openai_realtime.py` already मौजूद |
| **Microsoft** | Azure Speech Translation (streaming) | 100+ text, ~40 speech | Moderate; cascaded internally | $2.50/audio-hour ≈ $0.042/min | Enterprise option; नया SDK integration लगेगा |
| **Self-hosted** | Whisper + NLLB/MADLAD + XTTS | flexible | GPU पर 2–5s | infra cost only | P2 research — privacy/offline के लिए |

\* **Pricing disclaimer:** ये figures third-party sources (June–July 2026) से हैं और बदल सकते हैं। Launch से पहले official pricing pages से verify करें — मैं इन exact numbers को लेकर fully certain नहीं हूँ।

**निर्णय: Primary = Gemini Live Translate, Fallback = OpenAI Realtime (translation prompt के साथ)।** वजह: zero audio-pipeline change, सबसे सस्ता, 70+ languages auto-detect, और FarryOn का provider-factory pattern fallback को trivial बना देता है।

---

## 4. Current Architecture (जो आज मौजूद है)

```
┌─────────────────────────┐        WebSocket (frames.py protocol)        ┌──────────────────────────────┐
│  Flutter App             │  ── mic PCM16 16kHz (100ms chunks) ──▶      │  FastAPI Backend             │
│                          │                                              │                              │
│  capture/                │  ◀── AI audio PCM16 24kHz ──                 │  ws/live.py  (endpoint)      │
│   phone_capture_source   │  ◀── transcripts / events ──                 │  ws/session.py (lifecycle)   │
│   glasses_capture_source │                                              │  ws/frames.py (protocol)     │
│  playback/pcm_player     │                                              │        │                     │
│  state/live_controller   │                                              │  agent/orchestrator.py       │
│  data/live_client        │                                              │        │                     │
└─────────────────────────┘                                              │  ai/factory.py               │
                                                                          │   ├─ ai/gemini.py (Live)     │
        HeyCyan Glasses (SDK मौजूद,                                       │   ├─ ai/openai_realtime.py   │
        transport अभी wired नहीं)                                          │   ├─ ai/grok.py              │
                                                                          │   └─ ai/mock.py              │
                                                                          └──────────────────────────────┘
```

Key मौजूदा capabilities जो reuse होंगी:
- **Provider abstraction** (`ai/base.py` + `ai/factory.py` + `ALLOWED_PROVIDERS`, per-session `hello.provider`)
- **Streaming audio dono taraf** — flutter_sound `feedUint8FromStream()`, PCM16 16k→24k
- **Session cost controls** — `MAX_SESSION_SECONDS`, `IDLE_DISCONNECT_SECONDS`, context compression
- **Quota system** (`tools/quota.py`, `QUOTA_ENFORCEMENT_ENABLED`, plan limits)
- **Observability** — Prometheus metrics (`observability/metrics.py`) + Grafana (deploy/)
- **Auth** — JWT `?token=` on WebSocket

---

## 5. Proposed Architecture — Translator Mode

Core idea: **Translator एक नया session mode है, नया pipeline नहीं।**

```
hello frame (Flutter → backend):
{
  "mode": "translate",              // NEW: "agent" (default) | "translate"
  "provider": "gemini",             // existing field
  "translate": {                    // NEW block
    "target_language": "hi",        // BCP-47
    "echo_target_language": false,  // target भाषा already बोली जाए तो चुप रहे
    "captions_only": false          // Mode C
  }
}
```

```
                    mode == "translate"
Flutter ── mic ──▶ ws/live.py ──▶ ai/gemini_translate.py ──▶ Gemini Live API
   ▲                                │                        (gemini-3.5-live-translate-preview,
   │                                │                         translationConfig set)
   ├── translated audio 24kHz ◀─────┤
   ├── input_transcript  ◀──────────┤   (inputAudioTranscription)
   └── output_transcript ◀──────────┘   (outputAudioTranscription)
```

Translator mode में **tool engine, vision frames, system prompt — सब bypass** होते हैं (Google का translate model tools/instructions support ही नहीं करता, और ये cost भी बचाता है — कोई context re-billing, कोई camera frames नहीं)।

---

## 6. Backend Changes (FastAPI) — File-by-File

### 6.1 नया adapter: `backend/app/ai/gemini_translate.py`
`ai/base.py` का वही interface implement करे जो `gemini.py` करता है, लेकिन:

```python
# Setup message — gemini.py के setup से फर्क सिर्फ इतना:
setup = {
    "setup": {
        "model": "models/gemini-3.5-live-translate-preview",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            "translationConfig": {
                "targetLanguageCode": cfg.target_language,   # e.g. "hi"
                "echoTargetLanguage": cfg.echo_target_language,
            },
        },
    }
}
# Audio send: वही realtimeInput / audio/pcm;rate=16000 — gemini.py से reuse
# Receive: serverContent.inputTranscription / outputTranscription / modelTurn.parts[].inlineData
```

**क्यों अलग file, gemini.py में flag क्यों नहीं?** Translate model का contract काफी अलग है (no tools, no system prompt, no text input, no vision) — एक ही class में दो modes की if-else चौड़ी होकर bugs लाएगी। Shared WebSocket plumbing को `gemini_common.py` में निकाल लें।

### 6.2 `ai/factory.py` + `config.py`
```
# .env additions
TRANSLATE_PROVIDER=gemini_translate          # gemini_translate | openai_translate | mock
GEMINI_TRANSLATE_MODEL=gemini-3.5-live-translate-preview
TRANSLATE_ALLOWED_TARGET_LANGS=hi,en,ur,ar,es,fr,de,zh-Hans   # UI whitelist
TRANSLATE_MAX_SESSION_SECONDS=1800           # translator sessions लंबे चलते हैं
```
Factory में mode-aware resolution: `mode=="translate"` → translate provider map से adapter उठाओ। `mock` translate adapter भी बनाएँ (tests/demos deterministic रहें — FarryOn की existing philosophy)।

### 6.3 `ws/live.py` + `ws/session.py` + `ws/frames.py`
- `hello` frame schema में `mode` + `translate{}` block (frames.py में validation — घटिया language code पर clean error frame)
- Translator sessions में orchestrator/tool engine skip करो
- **दो नए outbound frames:** `translation_input_transcript` और `translation_output_transcript` (language code के साथ), ताकि UI dono transcripts अलग-अलग render करे
- Mode A (Conversation) के लिए simple version: auto-detect ही दोनों दिशाएँ संभालता है **जब दोनों speakers की भाषाएँ अलग हों और target एक ही हो** — e.g. target=en रखो: हिन्दी बोलने वाले का English बनेगा, English बोलने वाले का echo(false) पर silence। सच्चा two-way (hi⇄en दोनों तरफ audio) के लिए **दो parallel upstream sessions** चलाओ (एक target=hi, एक target=en) और mic audio दोनों को fan-out करो; output arbitration app में। इसे **P1** रखें — P0 में single-target Listen Mode ship करो।

### 6.4 Session resiliency (industry-scale ज़रूरी)
- Upstream (Gemini) socket drop → exponential backoff reconnect (1s, 2s, 4s; max 3), client को `provider_reconnecting` frame; fail होने पर `TRANSLATE_PROVIDER` fallback chain (जैसे web_search में primary/fallback pattern already है — वही यहाँ दोहराओ)
- Idempotent audio forwarding — reconnect पर in-flight chunks drop करना acceptable है (translation में re-send करने से duplicate speech आएगी)
- `MAX_SESSION_SECONDS` translator के लिए अलग tunable (ऊपर env)

### 6.5 Quota + billing hooks
- `tools/quota.py` में नया metered resource: `translate_minutes_per_day` (plan-wise: free=10 min, pro=120 min — plan_limits code-defaults pattern)
- Metering source: backend में audio-seconds counter (input chunks के bytes से derive करो — 16000 samples/sec × 2 bytes), Prometheus counter + DB में daily aggregate

### 6.6 Observability
Metrics add करें: `translate_sessions_active`, `translate_audio_seconds_total{direction}`, `translate_first_audio_latency_seconds` (histogram), `translate_provider_reconnects_total`, `translate_upstream_errors_total{provider}`। Grafana में एक Translator panel। Logs में **कभी transcript content log न करें** by default (privacy) — सिर्फ language codes, durations, session IDs।

---

## 7. Mobile Changes (Flutter)

### 7.1 नई feature folder: `mobile/lib/features/translate/`
- `translate_screen.dart` — split view: ऊपर "सुनी गई भाषा" transcript (auto-detected, language chip के साथ), नीचे translated transcript; बड़ा mic toggle; target-language picker (flag + native name, `TRANSLATE_ALLOWED_TARGET_LANGS` से)
- `translate_controller.dart` — `live_controller.dart` के pattern पर, लेकिन agent-specific चीज़ें (tools, camera) हटाकर
- Captions-only toggle (Mode C) — बस `pcm_player` को feed न करो

### 7.2 Reuse (कोई बदलाव नहीं / मामूली)
- `capture/phone_capture_source.dart`, `glasses_capture_source.dart` — जस के तस (वही 16k PCM16)
- `playback/pcm_player.dart` — जस का तस (वही 24k PCM16)
- `data/live_client.dart` — hello frame में `mode`/`translate` fields + दो नए transcript frames का parsing (`protocol/messages.dart`)

### 7.3 UX details (production-grade)
- Language picker की last choice `config_store.dart` में persist
- "Listening…" state में waveform/VU meter (user को भरोसा कि mic चालू है)
- Reconnect पर non-blocking toast; transcripts preserve
- Transcript का per-session local history (chat_history pattern), Share/Copy button
- Permission flow `state/permissions.dart` reuse

### 7.4 Glasses (HeyCyan) path
Glasses integration का transport layer अभी wired नहीं है — ये translator से **orthogonal** dependency है। Interim में: phone mic + glasses speaker (BLE audio route via OS) से Listen Mode काम करेगा। जब `glasses_capture_source` HeyCyan SDK से mic audio देने लगे, translator अपने-आप glasses-native हो जाएगा क्योंकि हम capture abstraction के पीछे हैं। (Glasses SDK wiring का अलग design doc बनता है।)

---

## 8. Security & Privacy

- **API keys server-side ही रहें** — current architecture (app → हमारा backend → Gemini) यही करती है। Google का ephemeral-token (client-direct) option मौजूद है लेकिन recommend नहीं — हमें metering, quotas, fallback के लिए बीच में रहना ज़रूरी है।
- WebSocket auth: existing JWT `?token=` enforce (prod में `JWT_SECRET` default से अलग होना mandatory — checklist item)
- **Transcripts by default store न करें**; user opt-in "Save transcript" पर ही DB में (models.py में `translation_sessions`, `translation_segments` tables — P1)
- Privacy policy update: "voice audio is processed by Google/OpenAI for translation" disclosure (app-store requirement)
- Rate limiting: `tools/ratelimit.py` pattern से per-user session-open rate limit (abuse: बार-बार connect/disconnect)

---

## 9. Cost Model (अनुमान — verify करें)

Assumptions: Gemini Live Translate ~$0.023–0.037/min (sources conflict करते हैं; conservative $0.04/min लेकर plan करें)।

| Scenario | Usage | Monthly cost/user (conservative) |
|---|---|---|
| Free plan | 10 min/day cap × ~15 active days | ~$6.00 |
| Pro plan | avg 30 min/day × 20 days | ~$24.00 |
| Heavy (cap 120/day) | worst-case 120 × 30 | ~$144 → इसीलिए hard cap + fair-use ज़रूरी |

Cost levers जो design में built-in हैं: daily minute quotas (6.5), idle disconnect, no vision frames in translate mode (Gemini Live का #1 cost driver यहाँ zero है), session max duration। **Action:** launch से पहले [official Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) से per-token/minute rate confirm करें — मैं third-party figures पर fully certain नहीं हूँ।

---

## 10. Known Limitations (Google docs से, honest list)

- Model **preview** status में है — breaking changes/GA pricing बदल सकती है; fallback provider इसीलिए ज़रूरी
- Voice replication inconsistent — लंबे pause के बाद आवाज़ बदल सकती है, rapid multi-speaker में एक voice पर अटक सकती है (Conversation Mode P1 में यही सबसे बड़ा UX risk)
- Language detection heavy accents / मिलती-जुलती भाषाओं (Hindi/Urdu spoken form!) में transcript-level गड़बड़ कर सकता है — docs कहते हैं final translation फिर भी accurate रहती है, पर हमें Hindi⇄Urdu pair का खुद QA करना होगा
- Background noise/music पूरी तरह filter नहीं होता; `echoTargetLanguage:true` पर artifacts
- Text input supported नहीं — text translation चाहिए तो अलग सस्ता path (normal Gemini Flash call) — P2

---

## 11. Delivery Plan

| Phase | Scope | Estimate |
|---|---|---|
| **P0 — Listen Mode MVP** | `gemini_translate.py` adapter + mock, hello mode/frames, quota metering, Flutter translate screen (single target, captions + audio), metrics, Hindi/English/Urdu QA | ~2 sprints |
| **P1 — Conversation Mode + polish** | dual-session two-way translation, transcript save/share, captions-only mode, fallback provider chain (OpenAI), Grafana dashboard, plan-based caps in billing | ~2 sprints |
| **P2 — Glasses-native + extras** | HeyCyan mic wiring के बाद glasses Listen Mode, text-input translation, offline/on-device exploration (Whisper-class), 2-device conversation (दो phones, एक session) | backlog |

P0 का सबसे पहला concrete task: `backend/app/ai/mock.py` की तर्ज़ पर `mock_translate` बनाकर ws-layer + Flutter screen को end-to-end बिना API key के चलाना — FarryOn की test philosophy के मुताबिक।

---

## 12. Sources

- [Live translation with Gemini Live API — official docs](https://ai.google.dev/gemini-api/docs/live-api/live-translate) (config, formats, languages, limitations)
- [Gemini 3.5 Live Translate launch — Google blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)
- [Gemini Live API overview](https://ai.google.dev/gemini-api/docs/live-api) · [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens)
- [Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/) · [Azure Speech translation overview](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-translation)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) · [OpenAI voice models announcement](https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/)
- Benchmarks/comparisons: [Coval STT 2026](https://www.coval.ai/blog/best-speech-to-text-providers-in-2026-independent-benchmarks-and-how-to-choose/) · [Softcery real-time vs turn-based](https://softcery.com/lab/ai-voice-agents-real-time-vs-turn-based-tts-stt-architecture) · [CloudPrice Gemini 3.5 Live Translate](https://cloudprice.net/models/google-gemini-3-5-live-translate)
