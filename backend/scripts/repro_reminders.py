"""Live end-to-end test of the full task/reminder lifecycle via real Gemini.

Drives sequential voice-style text turns and checks the model calls the right
tools, the reminder time is a timezone-correct ISO due_date, and complete/
delete work by name:

  1. "Remind me to call mom tomorrow at 5pm"  -> create_task (ISO + offset)
  2. "What tasks do I have?"                  -> list_tasks
  3. "Mark the call-mom task done"            -> complete_task
  4. "Delete the call-mom task"               -> delete_task

Expects a backend on 127.0.0.1:8000 with AI_PROVIDER=gemini. Exit 0 = PASS.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import websockets

PROVIDER = os.environ.get("AI_PROVIDER", "?")
URI = "ws://127.0.0.1:8000/ws/live"
CLIENT_TIME = "2026-06-21T22:30:00+05:30"

results: list[tuple[str, bool, str]] = []


async def _turn(ws, text: str) -> list[tuple[str, dict]]:
    """Send one text turn; return the (tool_name, result) calls observed."""
    await ws.send(json.dumps({"type": "text", "text": text}))
    calls: list[tuple[str, dict]] = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=45)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
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

        # 1) Create with a reminder time.
        c = await _turn(ws, "Remind me to call mom tomorrow at 5pm.")
        created = next((r for n, r in c if n == "create_task"), None)
        due = (created or {}).get("due_date", "")
        ok = created is not None and due.startswith("2026-06-22") and "+05:30" in due
        results.append(("create_task w/ tz-correct due", ok, f"due={due!r}"))

        # 2) Recall.
        c = await _turn(ws, "What tasks do I have on my list?")
        ok = any(n == "list_tasks" for n, _ in c)
        results.append(("list_tasks recall", ok, str([n for n, _ in c])))

        # 3) Complete by name.
        c = await _turn(ws, "Mark the call mom task as done.")
        done = next((r for n, r in c if n == "complete_task"), None)
        ok = done is not None and done.get("done") is True
        results.append(("complete_task by name", ok, str(done)))

        # 4) Delete by name.
        c = await _turn(ws, "Delete the call mom task.")
        deleted = next((r for n, r in c if n == "delete_task"), None)
        ok = deleted is not None and deleted.get("deleted") is True
        results.append(("delete_task by name", ok, str(deleted)))

    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n--- reminder lifecycle ({PROVIDER}) ---")
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
              + (f"  {detail}" if not ok else ""))
    print(f"TOTAL: {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


def main() -> None:
    try:
        rc = asyncio.run(asyncio.wait_for(run(), timeout=240))
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL[{PROVIDER}] exception: {exc!r}")
        rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
