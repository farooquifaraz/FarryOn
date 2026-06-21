"""Pin down the exact failing case: 'remind me in 2 minutes'.

Sends the real failing utterance to the live Gemini session and prints the
create_task due_date the model produced, plus the delta from the client time
we gave it. If the delta is not ~2 minutes, the model's clock math is the bug.

Backend must run on 127.0.0.1:8000. Exit 0 = due_date within tolerance.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta

import websockets

URI = "ws://127.0.0.1:8000/ws/live"
# A fixed, unambiguous client "now" with offset — same shape the app sends.
CLIENT_TIME = "2026-06-21T22:30:00+05:30"


async def _turn(ws, text: str) -> list[tuple[str, dict]]:
    await ws.send(json.dumps({"type": "text", "text": text}))
    calls: list[tuple[str, dict]] = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
            if o.get("type") == "tool_call":
                print(f"  tool_call {o.get('name')} args={o.get('args')}")
            if o.get("type") == "tool_result":
                calls.append((o.get("name", ""), o.get("result") or {}))
            if o.get("type") == "audio_end":
                break
    except asyncio.TimeoutError:
        pass
    return calls


async def run() -> int:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "hello", "protocolVersion": 1,
            "client": {"platform": "repro", "appVersion": "1"},
            "device": {"kind": "phone", "id": "r", "capabilities": []},
            "session": {}, "provider": "gemini", "clientTime": CLIENT_TIME,
        }))
        await ws.send(json.dumps({"type": "config"}))
        for _ in range(12):
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(raw, (bytes, bytearray)):
                continue
            if json.loads(raw).get("type") == "ready":
                break

        for phrase in [
            "Remind me in 2 minutes to test the app.",
            "Set a reminder for 30 seconds from now to drink water.",
            "Remind me in 5 minutes to call back.",
        ]:
            print(f"\n>>> {phrase}")
            calls = await _turn(ws, phrase)
            created = next((r for n, r in calls if n == "create_task"), None)
            if not created:
                print("  [FAIL] no create_task")
                continue
            due = created.get("due_date") or ""
            base = datetime.fromisoformat(CLIENT_TIME)
            try:
                got = datetime.fromisoformat(due)
                delta = (got - base).total_seconds()
                print(f"  due_date={due!r}  delta_from_client={delta:+.0f}s")
            except ValueError:
                print(f"  due_date={due!r}  (UNPARSEABLE)")
    return 0


def main() -> None:
    try:
        rc = asyncio.run(asyncio.wait_for(run(), timeout=240))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL exception: {exc!r}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
