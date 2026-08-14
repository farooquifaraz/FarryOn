# Live translation — issues found while testing

**Status 2026-08-14, after the fixes.** I1, I2a, I2b and I2c are fixed and
covered by tests; none has been seen on a device since, because none has
been run on a device since — the next run is what closes them. I5 is
mitigated but explicitly unproven. I3 is not ours. I4 stays open on one
sighting.

| | Fault | State |
|---|---|---|
| I1 | Decimal point read as a full stop | **fixed**, needs a device run |
| I2a | Watchdog closes on a pause mid-list | **fixed** (via I2c + context) |
| I2b | Recogniser writes the full stop itself | **mitigated** — the translator now sees the previous fragment |
| I2c | Watchdog ignored the minimum length | **fixed**, and the minimum is now script-aware |
| I3 | Words misheard | not fixable here |
| I4 | First sentence missing, seen once | open, awaiting a second sighting |
| I5 | Recogniser speaks and we pay for it | **capped, unproven** — watch `gemini_asr.model_spoke` |

---


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

**Fixed** in `_last_sentence_end`. Two shapes are refused: a point with
digits on both sides, and a point with a digit before it and nothing yet
after it — because the transcript arrives a few characters at a time,
and "…more than 4." is what "4.9" looks like a moment before the 9
lands. Refusing to decide costs one delta; the watchdog still closes the
utterance if the speaker really did stop on a number. `str.isdigit()` is
Unicode-aware, so Arabic-Indic and Devanagari digits are covered by the
same test, and a comma inside a number was always safe.

A sentence that genuinely ends on a number still cuts — "…was 49. Then
it fell" is a test.

Worth noting for anyone tempted to blame the recogniser: fed real speech
saying "four point nine billion", it wrote **4.9 billion** correctly.
The number was never misheard. It was cut in half afterwards, by us.

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

### I2c — the watchdog ignored the minimum entirely

Found in T7 (Chinese → Hindi), and it turned out to be the mechanism
behind most of what I2a was blamed for. `_quiet_watchdog` closed whatever
was in the buffer regardless of length: `_MIN_SENTENCE_UNITS` was checked
on the sentence-ending path and nowhere else. Chinese made it visible
because a character is a word there — utterances of 2, 7 and 10
characters went out on their own, one of them so short the language could
not even be identified (`HEARD · UND`, containing "50").

### What was done

**Length is now measured in script-normalised units, not characters.**
A dense-script character counts for three, which is the Chinese-to-English
expansion our own logs show (20 characters became 68, 26 became 105, 30
became 89). The old thresholds were wrong in *both* directions for
Chinese: a complete ten-character sentence could never reach 25 and so
was never closed, while 25 characters of Chinese is a paragraph, so
nothing was ever held back either.

**The watchdog now respects that minimum**, giving a short fragment
`_SHORT_QUIET_GAP_S` (4.5 s) instead of 1.8 s before it gives up and
sends it alone. A scrap is far more likely to be the start of something
than all of one.

**The translator is given the previous utterance as context** — shown,
never translated. This is the only measure that covers I2b, because it
stops depending on the cut being in the right place at all. It cannot
un-break a sentence; it stops the second half being read in isolation.

Rejected: raising `_QUIET_GAP_S` across the board. It buys margin against
I2a, costs latency on every single utterance, and does nothing at all for
I2b.

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

### I3b — the first character after a pause goes missing

A repeated shape rather than a one-off, and worth separating because it
looks exactly like a cutting bug and is not one.

| Run | Spoken | Heard | Effect |
|---|---|---|---|
| T7, T8 Chinese | 每天 (every day) | 天 | "Today" instead of "every day" |
| T9 Chinese | 錢包/背包 (wallet/bag) | 包 | "bag," with no owner |

Both losses fall immediately after a sentence-final 。 — which is where
our own cut happens, so the suspicion is natural. It is not us:
`_close` hands everything after the ending to the next utterance and
drops nothing, and 此外 survived the same position once the watchdog
stopped closing scraps. What remains is the recogniser clipping the
onset of speech after a pause, and there is no lever for that here.

Chinese suffers worst because a lost character is a lost word. In Latin
script the same clipping loses a letter and the model reads through it.

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

**What was done, and what is still unknown.**

TEXT output was re-checked against the current model and is still
rejected outright — "the requested combination of response modalities
(TEXT) is not supported". The clean fix is not available.

So the budget was taken away instead: `max_output_tokens=1`. Against
real generated speech the transcript came back character-for-character
identical with and without the cap, so it costs nothing we want.

**It is not proven to work.** In that same experiment the model produced
no audio in *either* configuration, so there was nothing for the cap to
prevent. An eight-second clip evidently does not give it the opening
that a minutes-long session with pauses does. The cap is an upper bound
and must therefore reduce the waste, but "must" is an argument, not a
measurement.

`gemini_asr.model_spoke` now logs bytes and seconds as well as chunks.
**The next real session settles this**: if the warning is absent, it is
fixed; if it appears, the seconds figure finally says what it costs.

---

## I6 — The phone hears its own voice and translates it

**Severity: high.** Found 2026-08-14, the first time the feature was
used the way a person would actually use it.

Every earlier run played a recording from a second phone, so the only
voice in the room was the speaker's. Speaking into the phone directly,
with `speak_on_device` saying the translation out loud, closes a loop:

| | |
|---|---|
| Spoken | "और ताऊ जावेद" |
| Phone said | "and Uncle Javed" |
| Phone then heard | **"एंड अंकल जावेद नॉट"** |

That last line is English written in Devanagari — the recogniser
transcribing the phone's own speech back. It arrived with no language
label at all, which is the tell: it is not really any language, it is
our own output coming round again. It costs a translation call, it
costs the voice that speaks the result, and it puts a sentence on
screen that nobody said.

`AudioModeChannel` already puts the phone in voice-call mode so the
platform echo canceller has a playback reference, and it is plainly not
catching text-to-speech.

**Fix:** stop sending microphone audio to the recogniser while the phone
is speaking. Nothing has to be detected or guessed — the translation is
spoken by our own TTS, so we know exactly when it starts and when it
finishes. A short tail after it stops covers the room's reverberation.

The alternative — filtering the echo out of the transcript afterwards —
means recognising our own words in a different script, and by then the
call has already been paid for.

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
- **Latency.** Was 1.17–2.46 s. A client was being built for every
  sentence, which meant a TLS handshake before each translation:
  measured at 1021 ms per call against 674 ms reusing one client. With
  the client hoisted, the device run gives 528–1671 ms, median around
  950 ms — the first call is the slow one because that is when the
  connection is opened. Well inside what cascaded systems publish
  (1.4–1.7 s median).

  What remains is not the model. The largest wait is the speaker
  finishing their sentence, and no amount of speed touches that. Beating
  it means translating a sentence before it ends and correcting
  afterwards, which cannot be done with a voice — spoken words cannot be
  taken back.
- **On-device voice.** No cloud TTS call in any run; `speak_on_device`
  held throughout.
