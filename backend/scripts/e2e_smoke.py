"""End-to-end realtime smoke test (runs in CI, not a unit test).

Starts nothing itself — expects a backend already listening on 127.0.0.1:8000.
Connects a real WebSocket client to ``/ws/live``, performs the protocol
handshake, sends one text turn, and verifies the configured provider streams
back a real response (audio and/or transcript) with no fatal error.

Exit code 0 = PASS, 1 = FAIL. Provider is taken from ``AI_PROVIDER``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets

PROVIDER = os.environ.get("AI_PROVIDER", "?")
URI = "ws://127.0.0.1:8000/ws/live"

HELLO = {
    "type": "hello",
    "protocolVersion": 1,
    "client": {"platform": "e2e", "appVersion": "1.0.0"},
    "device": {
        "kind": "test",
        "id": "e2e",
        "capabilities": ["audio_in", "audio_out", "video_in"],
    },
    "session": {},
}
CONFIG = {
    "type": "config",
    "audioIn": {"encoding": "pcm16", "sampleRate": 16000, "channels": 1},
    "audioOut": {"encoding": "pcm16", "sampleRate": 24000, "channels": 1},
}


async def run() -> int:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(HELLO))
        await ws.send(json.dumps(CONFIG))

        # 1) Expect a `ready` (provider connected) before anything else.
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

        # 2) Send one text turn and collect the response.
        await ws.send(
            json.dumps(
                {"type": "text", "text": "Reply with a short, friendly hello."}
            )
        )

        audio_bytes = 0
        audio_frames = 0
        transcripts: list[str] = []
        fatal_err = None
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=45)
                if isinstance(raw, (bytes, bytearray)):
                    audio_frames += 1
                    audio_bytes += max(0, len(raw) - 9)  # minus frame header
                    continue
                obj = json.loads(raw)
                t = obj.get("type")
                if t == "transcript" and obj.get("text"):
                    transcripts.append(obj["text"])
                elif t == "error":
                    print(f"  error frame: {obj}")
                    if obj.get("fatal"):
                        fatal_err = obj
                        break
                elif t == "audio_end":
                    break  # got a full spoken turn
                elif t == "state" and obj.get("value") == "listening":
                    if audio_frames or transcripts:
                        break  # turn complete with content
        except asyncio.TimeoutError:
            pass

        got_response = (audio_frames > 0) or bool(transcripts)
        print(
            f"RESULT[{PROVIDER}] audio_frames={audio_frames} "
            f"audio_bytes={audio_bytes} transcripts={transcripts!r} "
            f"fatal_err={fatal_err}"
        )
        if got_response and fatal_err is None:
            print(f"PASS[{PROVIDER}] realtime turn completed with a response")
            return 0
        print(f"FAIL[{PROVIDER}] no usable response")
        return 1


def main() -> None:
    try:
        rc = asyncio.run(asyncio.wait_for(run(), timeout=150))
    except Exception as exc:  # noqa: BLE001 - smoke test top-level
        print(f"FAIL[{PROVIDER}] exception: {exc!r}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
