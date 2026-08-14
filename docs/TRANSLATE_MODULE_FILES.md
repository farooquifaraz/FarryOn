# Live translation — every file it is made of

Generated from the repository on 2026-08-11, not from memory.

The feature is deliberately **isolated**: it runs on its own WebSocket, its own
controller and its own audio player, so that if it broke entirely the assistant
would carry on working. That shows up here as a long list of files that are
*only* translation, and a short list of shared ones it had to touch.

---

## Backend — files that are only translation

| File | What it is |
|---|---|
| `backend/app/ai/cascade_translate.py` | **The current pipeline.** Hear with the live model, translate with a text model, speak with a streaming TTS model. Written because one model doing all three got Hindi wrong. |
| `backend/app/ai/gemini_translate.py` | The single-model adapter. Still used — the cascade uses it as its *listening* step — and still selectable whole via `TRANSLATE_PIPELINE=direct`. |
| `backend/app/ai/mock_translate.py` | Deterministic twin. The whole feature runs end to end with no API key, which is how the test suite covers it offline. |
| `backend/alembic/versions/0007_translate_usage.py` | Adds `translate_seconds` to daily usage. |

### Backend tests

| File | What it protects |
|---|---|
| `backend/tests/test_cascade_translate.py` | The wiring between the three steps, and that one failing step does not end the session. |
| `backend/tests/test_gemini_translate_adapter.py` | What the real model actually does — no turn boundaries, delta transcripts, endless silence padding, GoAway. Every rule was a bug first. |
| `backend/tests/test_ws_translate.py` | The isolation promises: no tools, no camera, and never a silent fall back to the assistant. |
| `backend/tests/test_translate_quota.py` | That translate minutes are metered separately from voice. |

---

## Backend — shared files it touches

Ordered by how much of each file is about translation.

| File | Involvement |
|---|---|
| `backend/app/ws/session.py` | **Heavy.** `hello.mode`, the translate gateway, per-session metering, the 1-hour cap, and the rule that a translate session never becomes an assistant. |
| `backend/app/config.py` | **Heavy.** Provider, model, allowed target languages, session cap, pipeline choice, text/TTS models, language hints. |
| `backend/app/ai/factory.py` | `build_translate_gateway` — picks mock, direct, or cascade. |
| `backend/app/observability/metrics.py` | Five translate metrics: sessions, active, audio seconds, first-audio latency, errors, upstream rollovers. |
| `backend/app/ai/events.py` | The `lang` field on a transcript event, which is how the heard language reaches the phone. |
| `backend/app/db/models.py` · `backend/app/db/repo.py` | `translate_seconds` on daily usage. |

---

## Mobile — files that are only translation

| File | What it is |
|---|---|
| `mobile/lib/features/translate/translate_screen.dart` | The screen: heard pane, translated pane, language picker, captions toggle, save button. |
| `mobile/lib/features/translate/translate_controller.dart` | Its **own** socket and **own** player, so the assistant's half-duplex mic logic never applies. Glasses handling lives here too. |
| `mobile/lib/features/translate/translate_state.dart` | The state and one turn of conversation. |
| `mobile/lib/features/translate/translate_providers.dart` | Riverpod wiring. |
| `mobile/lib/features/translate/translate_languages.dart` | 78 languages from Google's own table, search, and which ones are right-to-left. |
| `mobile/lib/features/translate/translate_language_picker.dart` | The searchable full-page picker. |
| `mobile/lib/features/translate/translate_transcript.dart` | Renders a session into the note that gets saved. |

### Mobile tests

`mobile/test/translate_controller_test.dart` ·
`mobile/test/translate_languages_test.dart` ·
`mobile/test/translate_screen_test.dart` ·
`mobile/test/translate_transcript_test.dart`

---

## Mobile — shared files it uses

**Modified for translation:**

| File | Why |
|---|---|
| `mobile/lib/data/live_client.dart` | `TranslateSessionConfig` and the lean `mode: "translate"` handshake — no mailboxes, no web-search keys sent to a session that cannot use them. |
| `mobile/lib/core/config.dart` · `config_store.dart` | Target language and captions-only, persisted. |
| `mobile/lib/protocol/messages.dart` | The two translate message types. |
| `mobile/lib/features/live/live_screen.dart` | The entry icon, and reconnecting Farry when you leave. |
| `mobile/lib/data/data_api.dart` | `createNote` — how a saved translation is stored. |

**Used unchanged:**

`capture/capture_source.dart` · `capture/device_registry.dart` ·
`playback/pcm_player.dart` · `playback/voice_audio_mode.dart` ·
`core/notifications.dart` · `core/theme.dart` · `core/logger.dart` ·
`protocol/frames.dart` · `protocol/protocol.dart` ·
`state/permissions.dart` · `state/providers.dart` ·
`features/glasses_lab/bridge/glasses_channel.dart`

---

## Documents

| File | What it holds |
|---|---|
| `docs/TRANSLATE_TEST_PLAN.md` | Every device test case, what passed, and what the English-pivot investigation found. |
| `docs/LIVE_TRANSLATOR_DESIGN.md` | The original design (2026-07-03). Parts are superseded — it assumed translation would be a mode *inside* the live session. |
| `mobile/lib/features/glasses_lab/LAB_NOTES.md` §6 | What the model actually does, measured. |

---

## The marketing website (a separate, much smaller thing)

| File | What it is |
|---|---|
| `backend/app/web/index.html` | The whole landing page. |
| `backend/app/web/router.py` | Serves `/`, the icon, and the APK download. |
| `backend/app/web/__init__.py` | Package marker. |
| `backend/app/web/farry-icon.png` | Icon. |
| `apk/README.md` | How the APKs get built and put here. |
| `apk/*.apk` | The builds it hands out — **currently a 29 June build**, six weeks stale. |

Wired in at `backend/app/main.py` (`include_router(site_router)`).
