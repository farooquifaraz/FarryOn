# The photo that arrives after the question

> **Status 2026-09-23: built.** Faraz reported it in plain words — "the photo
> is taken, and Farry says the glasses didn't take it". The live log had both
> causes: a native 8 s capture watchdog firing before a late shot (22 Sept
> 15:10, 23 Sept 16:27), and the tool engine's 20 s default killing a tool
> whose photo had landed at 16 s (22 Sept 12:09). What shipped:
>
> - Vision tools on the glasses wait `glasses_photo_patience_seconds` (12 s),
>   then the model says in one sentence that the photo is on its way — no
>   guess, it has not seen it.
> - The question is kept open for `glasses_late_photo_seconds` (40 s, above the
>   app's 38 s backstop). When the photo lands it is put in the model's context,
>   described, and the model answers; if it never lands the model says so once.
>   `app/agent/late_photo.py`, wired in `app/ws/session.py`.
> - A `capture_timeout` from the app no longer ends the question (the photo can
>   still come); every other failure code does.
> - Native capture watchdog 8 s → 15 s; the BLE link asks for high connection
>   priority for the length of a photo and gives it back after.
> - `identify_image` / `capture_photo` carry a 30 s tool ceiling so a photo in
>   hand is never cut off mid-describe.
>
> Rejected, with reasons: the Live API's own NON_BLOCKING functions (on this
> native-audio model the model answers from a guess before the result —
> googleapis/python-genai#1894, closed "not planned"); speculative capture
> while the user is still talking, the way Meta does it (in call-mode the photo
> pauses the headset mic, so it would cut the user's question off, and every
> false trigger files a photo in the chat and gallery).
>
> The sections below are the analysis from 2026-08-21, kept as written except
> where marked.

## What happens today

A glasses photo can arrive long after the model gave up waiting for it. When it
does, it is emitted as a frame, saved to the gallery, and never connected back
to the question that asked for it. The model has already answered — and its
answer is that it could not see.

Measured on an L802 in a live voice session, 2026-08-21:

| | asked | photo delivered | gap |
|---|---|---|---|
| first attempt | 23:15:44 | 23:18:05 | **141 s** |
| second attempt | 00:08:00 | 00:08:28 | **28 s** |

The 28 s case is now covered: `glasses_frame_wait_seconds` is 32 s and the Dart
backstop is 38 s (see `backend/app/config.py` and
`mobile/lib/capture/glasses_capture_config.dart`, both pinned by tests).

The 141 s case is not covered, and should not be. Nothing sensible holds a
spoken conversation for over two minutes.

## Why it is slow

The glasses take the picture quickly — 2.2-2.4 s, firmware-fixed. The cost is
the transfer. The thumbnail comes over BLE in chunks, and the gap between them
is what dominates:

```
00:08:04.100  chunk 0 requested
00:08:04.285–04.297  data arrives          ← 12 ms
00:08:04.902  chunk 0 complete             ← 605 ms later
00:08:05.736  chunk 1 complete             ← ~830 ms
00:08:06.570  chunk 2 complete             ← ~830 ms
```

Nineteen chunks at ~830 ms. The radio is not the bottleneck: each chunk's data
lands in about 12 ms and then roughly 800 ms passes with nothing happening.

> **Correction 2026-09-23:** not true of the SDK in use now
> (`LIB_GLASSES_SDK-release-20260709_8.aar`). Decompiled: the thumbnail chunk
> request (`LargeDataHandler.syncPictureThumbnails`) is queued with the
> two-argument `BleDataBean`, whose sleep is 0. Each chunk is requested only
> after the previous one arrives, so the pace is the link's — which is why the
> link now asks for high connection priority during a photo. The paragraph
> below describes `release_3`.

That pause is inside the vendor SDK. `BleConsumer` calls
`Thread.sleep(BleDataBean.getSleepTime())` before every queued BLE write, and
the value is chosen by vendor code in `AlbumHandle`. It is shipped as bytecode
in `LIB_GLASSES_SDK-release_3.aar` — we cannot change it, and we should not
patch around it by racing the queue.

**This is a question for the vendor**: why is there a fixed ~800 ms delay
between thumbnail chunks, and is it tunable? A 17 KB photo moving at ~1.1 KB/s
over a link with a 517-byte MTU is roughly two orders of magnitude below what
the radio can do.

## The fix that is still needed

Widening a timeout cannot help when the spread runs from 28 s to 141 s. The
photo should stop being thrown away instead:

1. When a capture times out, the app already tells the backend
   (`CaptureFailedMessage`), and the model says it could not see. Keep that —
   it is honest at the time it is said.
2. When the photo *does* arrive afterwards, it already reaches the backend as a
   frame. Add a message alongside it saying this is the picture that question
   was waiting for.
3. The model then answers the original question, unprompted: *"That's a Nol
   card — sorry, the glasses were slow."*

This changes conversation behaviour — Farry would speak again half a minute
after a question she has already answered — so it needs a decision from Faraz
before it is built, not just an implementation.

## What not to do

- **Do not shorten the budgets to make failures faster.** The failure is the
  problem, not the waiting.
- **Do not widen `RECENT_FRAME_SECONDS`** (currently 2 s in
  `backend/app/agent/orchestrator.py`) to catch the late frame. That window
  exists so a question uses a picture taken *for it*; widening it brings back
  the stale-frame bug where a second question is answered from the first
  question's photo.
