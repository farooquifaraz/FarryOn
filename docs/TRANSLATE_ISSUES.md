# Live translation — open issues found while testing

Every problem seen on the device during the cross-language runs of
2026-08-14, with what caused it and what it would take to fix. Kept
separate from the test plan so the discussion has one list to work from.

Nothing here is a translation-quality problem. In five runs the meaning
and the numbers survived every time and every translation landed in
1.2–2.5 s. Both open faults live in step one of the cascade — the
recogniser in `backend/app/ai/gemini_asr.py` — and both are about
**where a sentence is cut**, not about what was heard or said.

---

## I1 — A decimal point ends the sentence

**Severity: high.** Corrupts numbers, which is exactly what people quote
a translation for.

`_last_sentence_end()` scans for any character in `_SENTENCE_ENDINGS`,
and `.` is in that set. In "4.9 billion" the point between the digits
looks identical to a full stop, so the utterance is closed there.

Seen twice:

| Run | Heard as | Came out as |
|---|---|---|
| T2 English → Hindi | "…more than 4." / "9 billion people…" | two cards, "4" and "9 billion" |
| T3 Arabic → Urdu | "…يتصل اكثر من 4." / "9 مليار شخص…" | "more than 4 people" then "9 billion" |

It did **not** fire in T4/T5 (Hindi source). The buffer at that moment
held only "हर दिन 4." — ten characters, below `_MIN_SENTENCE_CHARS`
(25), so the cut was refused. The protection is accidental: the same
sentence with a few more words in front of the number would split.

**Fix:** refuse the cut when the character is `.` and both neighbours are
digits. Cheap, local, no behaviour change anywhere else. Note that a
comma inside a number ("4,900") is already safe — `,` is not a sentence
ending — and Arabic-Indic digits need the same digit test, so use a
Unicode-aware digit check rather than `str.isascii()`.

---

## I2 — A pause inside a list ends the sentence

**Severity: medium.** The meaning survives; the reading experience does
not. **Two independent causes** — fixing one leaves the other.

### I2a — the quiet watchdog

`_quiet_watchdog` closes an unfinished utterance after `_QUIET_GAP_S`
(1.8 s) of transcript silence. A speaker enumerating items pauses at the
commas, and that pause is indistinguishable from the end of a thought.

Seen in both Hindi runs, at the same comma:

> **T4:** "हर दिन 4.9 बिलियन से ज्यादा लोग काम करने" / "पढ़ाई करनी या दोस्तों से…"
> **T5:** "Every day more than 4.9 billion people work" / "Log in to the internet to study or connect with friends"

English shows the damage most clearly: *"people work"* parses as a
complete sentence and says something the speaker never said.

### I2b — the recogniser punctuates the pause itself

Found in T6 (Spanish → Hindi) and it changes the diagnosis. The split
came at a **full stop the model itself wrote**, mid-enumeration:

> "…se conectan a internet para trabajar**.**" / "Estudiar o comunicarse con amigos."
> "…6 horas y 58 minutos diarios en línea**.**" / "navegando por un flujo constante…"

The Spanish original has commas in both places. No watchdog was
involved — `_last_sentence_end()` found a genuine `.` in the transcript
and cut correctly on bad input. Any fix that only touches the watchdog
cannot see this at all.

**Fix options, cheapest first:**

1. Raise `_QUIET_GAP_S`. Buys margin against I2a, costs latency on every
   utterance, does nothing for I2b. Not a fix.
2. Refuse to close on a trailing conjunction or comma — covers I2a and
   the "trabajar. / Estudiar" shape of I2b only if the fragment is short
   enough to look unfinished. Partial.
3. Give the translator the previous fragment as context, so a
   continuation reads as one sentence even when the cut was wrong. This
   is the only option that covers **both** causes, because it stops
   depending on the cut being right. One more moving part; the prompt
   already takes a system instruction, so the cost is a few hundred
   tokens per utterance.

(3) is now the one worth doing. (2) is a cheap complement.

---

## I3 — Words misheard

**Severity: low, and not ours to fix.** The recogniser's own accuracy.
Logged so the pattern is visible if it grows.

| Run | Spoken | Heard | Effect |
|---|---|---|---|
| T1 Arabic → English | 7:40 | "47" | wrong time |
| T1 | "my flight" | "my family" | wrong noun |
| T5 Hindi → English | "हाल की स्टडीज़" | "ऑल की स्टडीज" | "All key studies" instead of "Recent studies" |

Three slips across roughly 35 sentences. Nothing in the pipeline can
correct these; a different or larger ASR model is the only lever, and it
costs latency.

---

## I5 — The recogniser speaks, and we pay for it

**Severity: high, and it is a money bug rather than a quality one.**
Nothing reaches the user's ears; the audio is discarded on arrival.

`_SILENT_INSTRUCTION` tells the model it is a silent transcriber. It is
not obeying. `_spoke_anyway` counts the audio chunks that arrive anyway,
and every session of 2026-08-14 has a non-zero count:

| Session | Chunks spoken |
|---|---|
| 14:48 | 82 |
| 14:52 | 104 |
| 15:09 | 1075 |
| 15:10 | 321 |
| 15:20 | 256 |

Speech output is billed at roughly six times what listening costs, so
this eats into the saving that moving TTS on-device was meant to
deliver. It went unnoticed because the counter is only logged in
`close()` — a warning nobody sees until the session ends, and only then
as a bare number with no cost attached.

**Two things to do, in order:**

1. **Measure it.** The chunk count alone does not give a bill. Log the
   byte total as well, convert to seconds at the output sample rate, and
   put a figure on one session. Until that number exists, the severity
   above is an inference, not a measurement.
2. **Stop it.** `response_modalities=["AUDIO"]` is set because TEXT is
   rejected by the models that transcribe well — that was measured. But
   it has not been retried since the model changed to
   `gemini-2.5-flash-native-audio-latest`. Retry TEXT first; it is the
   clean fix. If it is still rejected, the fallback is to stop reading
   `model_turn` parts at all and, if the API allows, cap or disable the
   speech budget on the config.

---

## I4 — First sentence missing (seen once, not reproduced)

**Severity: unknown.** T1's opening sentence never appeared on screen.
It has not recurred in T2–T5. Left open deliberately: if it returns,
the shape of the second occurrence is what will identify it. Do not
close this without a second data point.

---

## Not issues

Recorded so they are not re-investigated:

- **English pivot in the label.** The translate model routes non-English
  targets through English. It is visible in the input-transcription slot
  but does not affect output, and the cascade no longer relies on that
  slot for the source text.
- **Latency.** 1.17–2.46 s across all five runs, worst case on the
  longest sentence. This is at the good end of what cascaded systems
  publish (1.4–1.7 s median).
- **On-device voice.** No cloud TTS call in any run; `speak_on_device`
  held throughout.
