"""Reproduction harness for the "stuck on Thinking / no 2nd reply / thinking
text leaks into the transcript" report.

Mirrors the live app: it streams JPEG video frames continuously (~5 fps) while
sending TWO sequential text turns. For each turn it verifies:

  * a real response arrives (audio and/or transcript),
  * the turn actually completes (server returns to ``listening``),
  * the transcript contains the spoken words, NOT the model's private
    chain-of-thought (no "my thoughts", "I've concluded", etc.).

Expects a backend already listening on 127.0.0.1:8000 with AI_PROVIDER=gemini.
Exit 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
import sys

import websockets

PROVIDER = os.environ.get("AI_PROVIDER", "?")
URI = "ws://127.0.0.1:8000/ws/live"

# A tiny but valid JPEG (1x1) — content is irrelevant; we only need the model to
# accept a real image frame on the realtime channel while we drive text turns.
_JPEG_1x1 = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AfwD/2Q=="
)

_HEADER = struct.Struct("<BQ")
_INPUT_VIDEO = 0x02

# Markers that should NEVER appear in a clean spoken transcript — they are
# hallmarks of leaked reasoning/thinking text.
_THINKING_MARKERS = (
    "my thoughts",
    "i've concluded",
    "i have concluded",
    "i'm aiming",
    "i am aiming",
    "centering on",
    "let me think",
    "the user wants",
    "i should",
)

HELLO = {
    "type": "hello",
    "protocolVersion": 1,
    "client": {"platform": "repro", "appVersion": "1.0.0"},
    "device": {"kind": "test", "id": "repro",
               "capabilities": ["audio_in", "audio_out", "video_in"]},
    "session": {},
}
CONFIG = {
    "type": "config",
    "audioIn": {"encoding": "pcm16", "sampleRate": 16000, "channels": 1},
    "videoIn": {"format": "jpeg", "fps": 1, "maxWidth": 1024},
    "audioOut": {"encoding": "pcm16", "sampleRate": 24000, "channels": 1},
}


async def _stream_video(ws, stop: asyncio.Event) -> None:
    """Continuously push JPEG frames like the phone camera until told to stop."""
    ts = 0
    while not stop.is_set():
        frame = _HEADER.pack(_INPUT_VIDEO, ts) + _JPEG_1x1
        try:
            await ws.send(frame)
        except Exception:  # noqa: BLE001 - socket closing
            return
        ts += 1
        await asyncio.sleep(0.2)  # ~5 fps, more aggressive than production


async def _collect_turn(ws, label: str) -> dict:
    """Drive one turn to completion; return what we observed."""
    audio_frames = 0
    transcripts: list[str] = []
    completed = False
    fatal = None
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(raw, (bytes, bytearray)):
                audio_frames += 1
                continue
            obj = json.loads(raw)
            t = obj.get("type")
            if t == "transcript" and obj.get("role") == "assistant":
                if obj.get("text"):
                    transcripts.append(obj["text"])
            elif t == "error":
                print(f"  [{label}] error frame: {obj}")
                if obj.get("fatal"):
                    fatal = obj
                    break
            elif t == "state" and obj.get("value") == "listening":
                # Turn returned to listening after producing content.
                if audio_frames or transcripts:
                    completed = True
                    break
    except asyncio.TimeoutError:
        pass

    # The assistant transcript is cumulative; the last emit is the full line.
    final_text = transcripts[-1] if transcripts else ""
    leaked = [m for m in _THINKING_MARKERS if m in final_text.lower()]
    return {
        "label": label,
        "audio_frames": audio_frames,
        "text": final_text,
        "completed": completed,
        "leaked": leaked,
        "fatal": fatal,
    }


async def run() -> int:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(HELLO))
        await ws.send(json.dumps(CONFIG))

        # Wait for ready.
        ready = None
        for _ in range(12):
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            if isinstance(raw, (bytes, bytearray)):
                continue
            obj = json.loads(raw)
            if obj.get("type") == "ready":
                ready = obj
                break
            if obj.get("type") == "error":
                print(f"FAIL[{PROVIDER}] error before ready: {obj}")
                return 1
        if ready is None:
            print(f"FAIL[{PROVIDER}] never received 'ready'")
            return 1
        print(f"READY[{PROVIDER}] model={ready.get('model')}")

        stop = asyncio.Event()
        video_task = asyncio.create_task(_stream_video(ws, stop))
        try:
            # Turn 1 — like asking about the scene.
            await ws.send(json.dumps(
                {"type": "text", "text": "What do you see right now?"}))
            r1 = await _collect_turn(ws, "turn1")
            print(f"  turn1: audio={r1['audio_frames']} completed="
                  f"{r1['completed']} leaked={r1['leaked']} "
                  f"text={r1['text'][:80]!r}")

            # Turn 2 — the message that previously got no reply.
            await ws.send(json.dumps({"type": "text", "text": "hi"}))
            r2 = await _collect_turn(ws, "turn2")
            print(f"  turn2: audio={r2['audio_frames']} completed="
                  f"{r2['completed']} leaked={r2['leaked']} "
                  f"text={r2['text'][:80]!r}")
        finally:
            stop.set()
            video_task.cancel()
            try:
                await video_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        ok = True
        for r in (r1, r2):
            got = r["audio_frames"] > 0 or bool(r["text"])
            if not got:
                print(f"FAIL[{PROVIDER}] {r['label']}: no response")
                ok = False
            if not r["completed"]:
                print(f"FAIL[{PROVIDER}] {r['label']}: turn never completed "
                      "(stuck)")
                ok = False
            if r["leaked"]:
                print(f"FAIL[{PROVIDER}] {r['label']}: thinking leaked into "
                      f"transcript: {r['leaked']}")
                ok = False
            if r["fatal"]:
                print(f"FAIL[{PROVIDER}] {r['label']}: fatal {r['fatal']}")
                ok = False

        if ok:
            print(f"PASS[{PROVIDER}] both turns completed, clean transcripts, "
                  "no stuck state")
            return 0
        return 1


def main() -> None:
    try:
        rc = asyncio.run(asyncio.wait_for(run(), timeout=180))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL[{PROVIDER}] exception: {exc!r}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
