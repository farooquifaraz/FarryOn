# The photo that arrives after the question

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
