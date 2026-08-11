# Live translation — device test cases

Status as of 2026-08-11. The feature is **glasses-only**: the phone microphone
listens, the glasses speaker answers. Every case below assumes an L802 paired
and connected, because without one the screen refuses to start at all.

`✅` verified on a real device · `⬜` not yet run · `⚠️` known model behaviour we
cannot fix, only present honestly.

## What has been checked

| # | Case | How | Status |
|---|------|-----|--------|
| T1 | Starting a session at all — mic opens, glasses speak | S23 + L802 | ✅ |
| T2 | Farry comes back after leaving the screen | device + a regression test that fails on the old code | ✅ |
| T3 | Right-to-left languages render correctly | Urdu; the "bad translation" was left-aligned text, not a bad translation | ✅ |
| T4 | Sentences are not chopped mid-thought | punctuation-aware gap timing | ✅ |
| T5 | The phone's own output does not loop back into the mic | held mic + glasses-only | ✅ |
| T6 | Detected language, eight languages | see LAB_NOTES §6 | ⚠️ see below |
| T7 | A quiet session does not stream megabytes of silence | 888 KB → 276 KB for one sentence | ✅ |

## Still to run

| # | Case | What "pass" looks like | Why it matters |
|---|------|------------------------|----------------|
| T8 | **Speak the target language itself** — say something in Hindi with the target set to Hindi | The screen explains the silence in words | The model is silent *by design* here. With no explanation this reads as a dead feature, and it is the single most likely support question. |
| T9 | **Text only, no voice** toggle | Captions appear, glasses stay silent, and turning it back on resumes speech | The toggle is the whole point of the switch; nothing else covers it. |
| T10 | **Background the app mid-session** | Translation continues, the notification is present and accurate, returning to the app shows the transcript intact | People will put the phone in a pocket. This is the normal way to use it. |
| T11 | **WiFi drops and returns** | A banner appears, the session reconnects on its own, and existing transcript is not lost | Now more interesting than before: the socket no longer rebuilds on token renewal, so a genuine drop is the only thing that should rebuild it. |
| T12 | **Incoming phone call** | Translation pauses; after the call it resumes or ends cleanly — never keeps a dead mic | Android takes the mic away whether we cooperate or not. |
| T13 | **Ten minutes continuous** | No drift, no runaway memory, no silent death, quota metered correctly | Fifteen-minute sessions is exactly where the auth bug hid. Ten minutes of *translate* has not been run since that fix. |
| T14 | **Change the target language mid-session** | Upstream reconnects, the transcript already on screen stays | Documented behaviour that has never been exercised. |
| T15 | **Glasses disconnect mid-session** | The session ends with a plain message, not a hang | Glasses-only means there is no fallback to hide behind. |
| T16 | **Quota exhausted mid-session** | An explicit stop with a message | A silent death here is indistinguishable from a crash. |
| T17 | **Quota already exhausted before opening** | The screen refuses to open, with a reason | Never open and die mid-sentence. |
| T18 | **Two speakers alternating in different languages** | Both are picked up; the language label follows | The actual use case — a conversation, not a monologue. Untested. |

## Known model behaviour, not our bug

| # | Case | What happens |
|---|------|--------------|
| T6a | Urdu spoken input | Reported as Hindi and transcribed in Devanagari. Spoken Hindi and Urdu are near-identical; the translation is still correct. |
| T6b | Detection over a microphone | Measured 4/7 correct over the air versus 7/8 fed straight to the model. In the failures the model returned a **translation into a third language** in the "heard" slot — Russian came back as fluent Japanese, Urdu as fluent English, both meaning exactly what was said. |
| T6c | Last words clipped | The transcript can stop short of what was said while the translation stays correct. Reported against this model by others too. |

**In all seventeen utterances across both language runs the translation itself
was right.** What is unreliable is the "heard" line, not the output. Whether to
keep presenting that line as authoritative is a product decision, still open.

## How to run the language cases without a second speaker

`scratchpad/probe_multilang.py` makes the audio (the translate model is the only
speaker of most of these languages available locally) and checks detection over
the wire. For the over-the-air half, play the resulting WAVs at the phone and
read `gemini_translate.utterance` in the backend log — it records the detected
language and the character counts, and never the words.
