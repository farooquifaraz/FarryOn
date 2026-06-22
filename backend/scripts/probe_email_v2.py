"""Verify the model uses read_emails categories/range + send_email (confirm)."""

from __future__ import annotations

import asyncio
import json

import websockets

URI = "ws://127.0.0.1:8000/ws/live"


async def turn(ws, text):
    await ws.send(json.dumps({"type": "text", "text": text}))
    calls = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
            if o.get("type") == "tool_call":
                calls.append((o["name"], o.get("args")))
            if o.get("type") == "audio_end":
                break
    except asyncio.TimeoutError:
        pass
    return calls


async def run():
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "hello", "protocolVersion": 1,
            "client": {"platform": "p", "appVersion": "1"},
            "device": {"kind": "phone", "id": "p", "capabilities": []},
            "session": {}, "provider": "gemini",
            "email": {"address": "me@gmail.com", "appPassword": "fake"},
        }))
        await ws.send(json.dumps({"type": "config"}))
        for _ in range(15):
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if not isinstance(raw, (bytes, bytearray)) and \
                    json.loads(raw).get("type") == "ready":
                break

        for phrase in [
            "Show me my promotional emails",
            "Any important unread emails this week?",
        ]:
            print(f"\n>>> {phrase}")
            for n, a in await turn(ws, phrase):
                print(f"   {n}  {a}")

        # send flow — confirm step
        print("\n>>> Email faraz@example.com saying I'll be late")
        for n, a in await turn(ws, "Email faraz@example.com saying I'll be late"):
            print(f"   {n}  {a}")
        print(">>> yes send it")
        for n, a in await turn(ws, "Yes, send it"):
            print(f"   {n}  {a}")


asyncio.run(asyncio.wait_for(run(), timeout=240))
