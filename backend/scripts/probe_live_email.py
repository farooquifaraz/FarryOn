"""Probe the LIVE cloud backend: is the read_emails tool wired and called?

Sends fake email creds (so IMAP login will fail) and asks about today's mail.
We only check that the model CALLS read_emails and that the tool returns a
graceful result — that confirms the server pipeline works end to end.
"""

from __future__ import annotations

import asyncio
import json

import websockets

URI = "wss://farryon-backend.onrender.com/ws/live"


async def run() -> None:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "hello", "protocolVersion": 1,
            "client": {"platform": "probe", "appVersion": "1"},
            "device": {"kind": "phone", "id": "p", "capabilities": []},
            "session": {}, "provider": "gemini",
            "clientTime": "2026-06-21T23:00:00+05:30",
            "email": {"address": "test@gmail.com", "appPassword": "fake-app-pw"},
        }))
        await ws.send(json.dumps({"type": "config"}))
        # wait for ready
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            if isinstance(raw, (bytes, bytearray)):
                continue
            if json.loads(raw).get("type") == "ready":
                print("[ok] backend ready")
                break

        await ws.send(json.dumps(
            {"type": "text", "text": "What emails did I get today?"}))
        called = False
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                if isinstance(raw, (bytes, bytearray)):
                    continue
                o = json.loads(raw)
                t = o.get("type")
                if t == "tool_call" and o.get("name") == "read_emails":
                    called = True
                    print(f"[ok] model CALLED read_emails  args={o.get('args')}")
                if t == "tool_result" and o.get("name") == "read_emails":
                    print(f"[ok] tool_result: {json.dumps(o.get('result') or o.get('error'))}")
                if t == "audio_end":
                    break
        except asyncio.TimeoutError:
            print("[..] no more events (timeout)")
        print("\nRESULT:",
              "read_emails pipeline WORKS on live backend"
              if called else "model did NOT call read_emails")


asyncio.run(asyncio.wait_for(run(), timeout=240))
