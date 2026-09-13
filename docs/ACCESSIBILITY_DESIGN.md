# FarryOn — Accessibility Modes: Design Document

**Version:** 1.0 · **Date:** 2026-09-13 · **Author:** Solution Architecture (Claude + Faraz)
**Status:** Phase 1 implemented (Live captions, Speak for me); Phase 2 (Guide mode)
and Phase 3 (sign language) proposed — review before implementation starts.

Pair this with [`LIVE_TRANSLATOR_DESIGN.md`](./LIVE_TRANSLATOR_DESIGN.md) (the
translate pipeline these modes reuse), [`DEVICE_ADAPTER.md`](./DEVICE_ADAPTER.md)
and [`PROTOCOL.md`](../PROTOCOL.md).

---

## 1. Executive summary

FarryOn is asked to serve three groups of people of determination, each with a
different pairing of the glasses and the phone:

| # | Who | What they need | Who wears the glasses |
|---|-----|----------------|-----------------------|
| **A** | **Blind / low-vision** | Walk around with a companion that says what is ahead, reads signs and finds things | The user |
| **B** | **Non-speaking** ("mute") | Tell a hearing person what they want, out loud, in a voice | The *other* person — or nobody; the phone speaks |
| **C** | **Deaf / hard of hearing** | Hear what people around them say, as text they can read | The user (mic on the face) — or nobody; the phone mic works |

The most important finding from reading the code: **two of the three are almost
free.** FarryOn already has a transcription-only path with no server voice
(`speakOnDevice` + captions-only), and an on-device text-to-speech engine that
plays on whatever Bluetooth route the glasses are on. Mode C is that path with
the glasses requirement relaxed; Mode B is a text box in front of that engine.
Both are built in this change, with no backend modification.

Mode A is real work: a new prompt profile, per-session camera cadence, a
safety posture, and device testing on a real route before it goes near a user
who cannot see the mistakes. It is specified here and left for Phase 2.

**Sign language** (a non-speaking user *signs* and the glasses speak it) is
the one thing the current pipeline genuinely cannot do, and the state of the
art cannot do reliably either. It is scoped honestly in §7 as research, with a
fingerspelling prototype as the first honest milestone.

---

## 2. What the hardware and the pipeline can do today

Everything below is from the code, not the brochure. Paths are given so it can
be re-checked.

| Capability | State | Where |
|---|---|---|
| Glasses **speaker** | Yes — a Bluetooth audio sink. Anything the phone plays on its media/voice route lands in the wearer's ear. There is **no** "send audio to glasses" SDK call. | `HeyCyanGlassesSdk.kt` `startTtsSample`, `AudioModeChannel.kt` |
| Glasses **microphone** | Yes — continuous 16 kHz PCM over the call-mode (HFP) route; press-to-talk over BLE | `glasses_capture_source.dart` `handsFreeTransport` |
| Glasses **camera** | **Photo only**, one JPEG on request, median 3.8 s to arrive. No video stream. | `glasses_capture_source.dart:74-78` |
| Glasses **display** | **None.** Nothing can be shown on the lens; text always goes to the phone screen. | no display API in `GlassesSdk.kt` |
| Glasses **gestures** the app can see | Long-press on the temple, volume slide, worn / taken off. Single taps never reach the app. | `HeyCyanGlassesSdk.kt:1072-1155` |
| Phone camera to the model | ~1 fps captured; the cost gate forwards one frame every 2 s (continuous) or 6 s (on-turn). Global setting, not per session. | `config.py:218-234`, `session.py:_should_forward_frame` |
| Transcription with **no server voice** | Yes — cascade pipeline with `speakOnDevice: true` emits `transcript` events and returns before TTS | `cascade_translate.py:290-293`, `gemini_asr.py` |
| On-device text-to-speech | Yes, free, instant; reports when the phone has no voice for a language | `playback/device_voice.dart` |
| Typed turn to the assistant | Yes — `{"type":"text"}`; the reply comes back as spoken audio | `session.py:986-1003`, `live_controller.dart:sendText` |
| Per-user preferences on the server | None. Preferences live on the phone (`AppConfig` + `ConfigStore`). | `core/config.dart`, `core/config_store.dart` |
| Screen-reader support in the app | None today (no `Semantics`, no text scaling). | — |

---

## 3. Mode C — Live captions (deaf / hard of hearing) — **built**

### 3.1 What the user gets

Open **Accessibility → Live captions**. The phone (or the glasses, if worn)
listens to the room and every sentence appears on the phone screen as large
text, in the speaker's language, with a translation underneath when the
speaker is not using the user's language. Nothing is spoken. It works with no
glasses at all, because there is nothing to loop back into the microphone.

### 3.2 Why this is the translate screen and not a new one

Live translation already does exactly this in "Text only" mode: mic → Gemini
streaming ASR → `transcript` events → `_TurnTile`. What stopped a deaf user
from using it was one rule: **the translate controller refuses to start without
the glasses.** That rule is correct for spoken translation — on the phone
loudspeaker one English sentence came back as fourteen translations of itself
(device-proven 2026-08-10) — and irrelevant when nothing is played.

So the change is the smallest one that is true:

- `TranslateController.start()` skips the glasses gate when `captionsOnly` is
  on. The microphone-permission check, the audio-route handling and the
  reconnect logic are untouched.
- A glasses drop mid-session no longer holds a captions session unless the
  glasses *were* the microphone. With the phone mic listening and nothing
  playing, the drop changes nothing.
- `TranslateScreen.open(context, captions: true)` opens the same screen as a
  captions preset: title "Live captions", captions forced on, the "Text only"
  switch hidden, larger type, and the "glasses needed" panel gone. The user's
  normal translate preference (`translateCaptionsOnly`) is **not** overwritten
  by opening the preset.

### 3.3 Cost

Captions bill as translate minutes (`_meter_translate`), which is the cheapest
path in the product: the ASR model plus a text translation, no server audio.
No new metering is needed.

### 3.4 Not done yet (P1)

- **Speaker turns.** The ASR gives sentences, not speakers. "Who said that?"
  is the single most requested captioning feature and needs diarisation the
  cascade does not have. Candidate: Gemini's `speaker_diarization` on the ASR
  model once it is exposed on the Live path; otherwise a visual "new voice"
  hint from energy/pitch change on the phone.
- **Same-language captions with no translation card.** Today when the speaker
  already uses the target language the tile shows the "already in X" note.
  For captions that note is noise; hide it in the captions preset.
- **Text size preference.** Captions use a fixed larger size. A slider, and
  honouring the OS text scale, belongs with the app-wide accessibility pass
  (§8).

---

## 4. Mode B — Speak for me (non-speaking users) — **built**

### 4.1 What the user gets

Open **Accessibility → Speak for me**. A large text box, a row of quick
phrases ("Yes", "Thank you", "Please speak slowly, I'm reading"), and a
**Speak** button. Whatever is typed is said out loud by the phone's own voice
in the chosen language. If the glasses are bonded, the sound plays in the
glasses — which is the "give the glasses to the other person" scenario: the
non-speaking user types on the phone, the hearing person hears it in their
ear. Without glasses, the phone speaker does the job.

Every line spoken is kept on screen; tap one to say it again; long-press to
keep it as a quick phrase. A **Show** button flips the last line to
full-screen large text for showing the phone to someone in a noisy place.

### 4.2 How it works

`SpeakForMeController` wraps `DeviceVoice.speak(text, language)`. That is the
same engine live translation already uses, and it plays on Android's media
route, so the glasses receive it over A2DP with no new glasses code. There is
no server round-trip and no cost.

Opening the screen **disconnects the assistant** and closing it reconnects,
the same as live translation, for the same reason: the assistant's microphone
would otherwise hear the phone's voice as the user speaking.

The language defaults to the user's primary language from Settings, mapped to
a BCP-47 code; a picker on the screen overrides it and the choice is saved
(`speakLanguage` in `AppConfig`). When the phone has no voice for that
language, `DeviceVoice` says so once and the screen offers the installer.

### 4.3 Not done yet (P1)

- **Predictive phrases.** A small on-device suggestion list based on what the
  user says most. Frequency counting is enough; no model needed.
- **Voice choice.** Android offers several voices per language; letting the
  user pick one (and a speaking rate) is a `FlutterTts.setVoice` call plus a
  setting.
- **Two-way with captions.** The obvious next step is a split screen: captions
  of what the other person says on top, the speak box below. Both pipelines
  exist; the work is the screen and the microphone hand-off between them
  (captions must pause while the phone speaks, exactly as the translate echo
  guard does).

---

## 5. Mode A — Guide (blind / low-vision) — **proposed, Phase 2**

### 5.1 What the user should get

The user wears the glasses and walks. Farry:

- says what is ahead when asked ("what's in front of me?"), or on a long-press
  of the temple;
- reads signs, labels, menus, screens and money on request;
- finds things ("where is the door?", "is there a chair?");
- warns, unprompted, about a small set of hazards it can see: steps, kerbs,
  a road edge, a vehicle in the path, an obstacle at head height;
- keeps every answer **short, spatial and ordered by danger**: "Kerb down, one
  step ahead. Then a parked bike on your right."

### 5.2 What exists

Everything except the prompt, the cadence and the safety posture. The
assistant already sees the camera, calls `capture_photo`/`identify_image`,
and speaks. A typed or spoken "describe what's ahead" works today on the
phone camera, with a general-purpose answer.

### 5.3 What has to change

**Backend**

1. **A prompt profile.** `build_system_prompt(profile="guide")` appends a
   guide block: answer in one or two sentences; lead with hazards; use clock
   positions and step counts; never guess a sign's text — say "I can't read
   it" instead; never say "safe to cross". Selected by a new
   `hello.profile` field (default `"assistant"`), validated like `mode` is.
2. **Per-session camera cadence.** The frame gate reads global settings. A
   guide session needs `continuous` at 1–2 s from the *phone* camera when the
   user is moving; `hello.vision` carries `{"mode": "continuous",
   "minIntervalS": 1.5}` and the session clamps it to a server-side floor so a
   client cannot buy itself 10 fps. This is also where the cost lives — see
   §5.5.
3. **Hazard nudges.** With continuous frames the model only speaks when asked.
   A guide must speak unprompted for hazards. Cheapest honest mechanism: a
   periodic silent instruction every N frames — "if the last frame shows a
   step, kerb, vehicle or obstacle in the walking path, say so in one short
   sentence; otherwise say nothing" — the same nudge machinery
   `test_vad_and_nudge.py` already covers for quiet speech.

**Mobile**

4. **Guide screen.** Camera preview is irrelevant to the user; the screen is
   a big status ("Guiding · glasses mic on"), a big stop button, and the
   transcript in large type for a sighted helper. Connects with
   `profile: "guide"` and the phone camera as the video source (the glasses
   camera cannot stream).
5. **Long-press on the temple** (`deviceEvent 0x03`) as the "what's ahead?"
   trigger, so the user never has to find a button.
6. **Screen-reader pass** on that screen first (§8).

### 5.4 Safety posture — not optional

A blind user cannot see the model being wrong. Before this ships:

- The prompt forbids the model from asserting safety ("clear to cross", "no
  cars"). It may describe; it may not clear.
- Hazard warnings are marked in the transcript and audited in
  `tool_audit` so misses and false alarms can be counted from real walks.
- The first screen the mode ever shows says what it is: a description aid,
  not a mobility aid, and not a replacement for a cane or a guide dog. That
  is what every comparable product (Be My Eyes, Seeing AI, Envision) says,
  and they say it because it is true.
- Device testing on a real route, with a sighted observer, before any user
  test. `PHONE_TEST.md` gets a Guide section.

### 5.5 Cost

This is the expensive mode. At 1 frame every 1.5 s, a ten-minute walk sends
400 frames, and every frame is re-billed on every later turn in a Live API
session. Two mitigations, both required:

- **Frames ride only with a turn or a nudge.** Keep `on_turn` semantics but
  make the heartbeat short (2–3 s) rather than opening the continuous tap.
- **Session rotation.** Cut the Live session every ~2 minutes and reconnect,
  so the re-billed context never grows past two minutes of frames. The
  reconnect machinery exists; the transcript is client-side and survives it.

Estimated at Gemini Live rates: ~$0.10–0.20 per ten-minute walk with
rotation, several times that without. **These figures are my estimate from
the token pricing, not a measurement — measure on the mock provider's frame
counter and then on a real session before setting a plan cap.**

---

## 6. The Accessibility hub

One entry point, reachable from the Live screen's action bar and from
Settings: **Accessibility**. It lists the three modes with who they are for
and opens the ones that exist. Guide shows what it will be and, until then,
points the user at the assistant's existing "what's in front of me?".

Nothing about the hub is a "mode" on the server. Every mode is a screen the
phone opens with the right session configuration; the hub is navigation.

---

## 7. Sign language — honest scope

The request "the glasses read the signing and speak it" is a research problem,
not a feature:

- **Motion, not stills.** Handshape, movement, location and facial grammar
  carry meaning over a few seconds. The pipeline delivers one still every 2 s
  from the phone and one photo every ~4 s from the glasses. Neither can read
  signing, and streaming 15–30 fps to the Live API would be both unaffordable
  and still the wrong tool.
- **Which language.** ASL, BSL, Indian, Pakistani and Emirati sign languages
  are different languages. Public data and models exist mostly for ASL
  (WLASL, Google's ASL Signs). Anything else means collecting data.
- **State of the art.** Isolated-sign recognition works in demos. Continuous
  sentence recognition in real conditions does not yet work reliably enough
  to speak on someone's behalf. I am not certain of the accuracy any model
  would reach here; treat every published number as a demo figure until it
  is reproduced on our data.

A path that respects that:

1. **On-device landmarks.** MediaPipe Hands + Pose on the phone at 30 fps,
   producing 21 points per hand plus pose. Nothing leaves the device.
2. **Fingerspelling first.** A small classifier over landmark sequences for
   the fingerspelled alphabet. Letters → words → the Speak-for-me box → voice.
   This is a weekend-scale prototype, proves the whole loop, and is genuinely
   useful for names, places and numbers.
3. **Isolated signs** for a vocabulary of a few hundred, mapped to the quick
   phrases. Same loop.
4. **Continuous signing** stays research until 1–3 are measured.

Camera for all of this: the phone, held by the hearing person or on a stand,
facing the signer. The glasses camera cannot do it.

---

## 8. App-wide accessibility pass (P1, all modes)

The app has no `Semantics`, no live-region announcements and no text scaling.
For a product that now says it is for people of determination, that has to be
fixed regardless of mode:

- Every icon-only button gets a label (`tooltip` already exists on most; add
  `Semantics(label:)` where it does not).
- Transcript and caption lists announce new lines to TalkBack
  (`SemanticsService.announce`).
- All hard-coded font sizes go through `MediaQuery.textScalerOf(context)`;
  the captions screen is the first to comply.
- The Live orb and status chips get text alternatives.
- A `flutter test` that walks the widget tree for unlabeled tappables.

---

## 9. Security and privacy

- Captions and spoken lines are people's words. Nothing is saved unless the
  user taps save (captions reuse the translate note flow; Speak-for-me keeps
  its list in memory and only pinned phrases in preferences).
- Speak-for-me makes **no network call**. Captions send only microphone audio,
  as translation already does; no camera frame leaves the phone in that mode.
- Guide mode will send camera frames continuously. The Guide screen must say
  so, once, before the first session; and the frames are never stored
  server-side beyond the live session (already true of the frame cache).

---

## 10. Delivery plan

| Phase | Scope | State |
|---|---|---|
| **P1 — this change** | Accessibility hub · Live captions preset (glasses optional) · Speak for me with quick phrases, saved phrases, language pick, big-text show · tests | **done in code; needs a phone run** |
| **P1.1** | Captions: hide same-language note in preset, text-size preference · Speak: voice + rate picker · Accessibility labels on both screens · PROTOCOL.md catch-up (`mode`, `translate`, `transcript.lang/utterance` are undocumented) | next |
| **P2 — Guide** | `hello.profile`, guide prompt block, per-session cadence with server floor, hazard nudge, session rotation, Guide screen, temple long-press, safety copy, route test with observer | after review of §5 |
| **P3 — Two-way** | Split screen captions + speak, with the microphone hand-off | after P1.1 |
| **R — Sign language** | MediaPipe landmarks → fingerspelling prototype → measured accuracy → decide | research track |

### What to test on the phone for P1

1. Live captions with **no** glasses: starts, phone mic listens, sentences
   appear, nothing plays, Farry comes back on exit.
2. Live captions with glasses worn: glasses mic listens; take the glasses off
   mid-session — the session holds and resumes when they reconnect, as
   translation does.
3. Speak for me with glasses bonded: typed line is heard **in the glasses**,
   not the phone. Without glasses: phone speaker. The route chip on screen
   says which.
4. Speak for me in a language the phone has no voice for: one clear message
   and the "Add voices" button, not silence.
5. Both screens: the assistant is quiet while open and reconnects on exit.
