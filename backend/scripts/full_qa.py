"""Full black-box + stress QA harness for the running FarryOn backend.

Exercises the system the way a real client does — over HTTP and the ``/ws/live``
WebSocket protocol — plus negative/edge cases and concurrency stress. Uses the
per-session ``provider=mock`` selection so functional/stress checks are
deterministic and put no load on the real AI providers.

Run against a backend already listening on 127.0.0.1:8000. Prints a results
table and exits 0 only if every case passes.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import time

# Windows consoles default to cp1252; force UTF-8 so arrows/× print cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
except Exception:  # noqa: BLE001
    pass

import httpx
import websockets

BASE = "http://127.0.0.1:8000"
URI = "ws://127.0.0.1:8000/ws/live"
_HDR = struct.Struct("<BQ")
_AUDIO, _VIDEO = 0x01, 0x02

results: list[tuple[str, str, bool, str]] = []  # (id, name, passed, detail)


def record(cid: str, name: str, passed: bool, detail: str = "") -> None:
    results.append((cid, name, passed, detail))


def _hello(provider: str = "mock", **extra) -> dict:
    msg = {
        "type": "hello", "protocolVersion": 1,
        "client": {"platform": "qa", "appVersion": "1.0.0"},
        "device": {"kind": "phone", "id": "qa", "capabilities": []},
        "session": {}, "provider": provider,
    }
    msg.update(extra)
    return msg


async def _ready(ws, timeout: float = 30) -> dict | None:
    for _ in range(15):
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        if isinstance(raw, (bytes, bytearray)):
            continue
        obj = json.loads(raw)
        if obj.get("type") == "ready":
            return obj
        if obj.get("type") == "error":
            return obj
    return None


async def _drain_turn(ws, timeout: float = 20, want_tool: bool = True) -> dict:
    """Drain a full turn.

    Tool calls run on a concurrent orchestrator task, so ``tool_call`` /
    ``tool_result`` can arrive AFTER ``audio_end``. We therefore keep reading
    until both audio_end and tool_result are seen, or a short lull follows
    audio_end (turns without a tool), bounded by ``timeout``.
    """
    loop = asyncio.get_event_loop()
    audio = 0
    types: set[str] = set()
    tool_ok = False
    saw_end = False
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 3.0))
        except asyncio.TimeoutError:
            if saw_end:
                break  # nothing more is coming
            continue
        if isinstance(raw, (bytes, bytearray)):
            audio += 1
            continue
        obj = json.loads(raw)
        t = obj.get("type")
        types.add(t)
        if t == "tool_result":
            tool_ok = obj.get("ok") is True
        if t == "audio_end":
            saw_end = True
        if saw_end and (not want_tool or "tool_result" in types):
            break
    return {"audio": audio, "types": types, "tool_ok": tool_ok}


# ── HTTP endpoints ────────────────────────────────────────────────────────
async def test_http() -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/healthz")
        record("F1", "GET /healthz 200 ok",
               r.status_code == 200 and r.json().get("status") == "ok",
               f"status={r.status_code}")
        r = await c.get(f"{BASE}/readyz")
        record("F2", "GET /readyz ready",
               r.status_code == 200 and r.json().get("status") == "ready", "")
        r = await c.get(f"{BASE}/metrics")
        record("F3", "GET /metrics prometheus",
               r.status_code == 200 and "farryon_ws_connections_total" in r.text,
               "")


# ── Functional WS (mock provider) ─────────────────────────────────────────
async def test_functional() -> None:
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(_hello("mock")))
        await ws.send(json.dumps({"type": "config"}))
        ready = await _ready(ws)
        record("F4", "WS handshake → ready (mock)",
               ready is not None and ready.get("type") == "ready"
               and ready.get("model") == "mock-1", str(ready))

        await ws.send(json.dumps({"type": "text", "text": "remember milk"}))
        turn = await _drain_turn(ws)
        need = {"transcript", "tool_call", "tool_result", "audio_start",
                "audio_end"}
        record("F5", "Text turn → transcript+tool+audio",
               need.issubset(turn["types"]) and turn["audio"] > 0
               and turn["tool_ok"],
               f"types={sorted(turn['types'])} audio={turn['audio']}")

        await ws.send(json.dumps({"type": "ping", "t": 4242}))
        pong = None
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
            if o.get("type") == "pong":
                pong = o
                break
        record("F6", "ping → pong echoes t",
               pong is not None and pong.get("t") == 4242, str(pong))

        await ws.send(json.dumps({"type": "interrupt"}))
        saw_listening = False
        for _ in range(20):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
            if o.get("type") == "state" and o.get("value") == "listening":
                saw_listening = True
                break
        record("F7", "interrupt → state listening", saw_listening, "")

        pcm = struct.pack("<320h", *([0] * 320))
        await ws.send(_HDR.pack(_AUDIO, 0) + pcm)
        turn2 = await _drain_turn(ws)
        record("F8", "Binary audio frame drives a turn (mock)",
               "tool_call" in turn2["types"], f"types={sorted(turn2['types'])}")


# ── Provider selection (black-box) ────────────────────────────────────────
async def test_providers() -> None:
    async def probe(provider: str) -> dict | None:
        async with websockets.connect(URI, max_size=None) as ws:
            await ws.send(json.dumps(_hello(provider)))
            await ws.send(json.dumps({"type": "config"}))
            return await _ready(ws)

    r = await probe("mock")
    record("P1", "provider=mock → ready",
           r and r.get("type") == "ready" and r.get("model") == "mock-1", str(r))
    r = await probe("gemini")
    record("P2", "provider=gemini → ready (real key) OR clean error",
           r is not None and r.get("type") in ("ready", "error"), str(r))
    r = await probe("bogus-xyz")
    record("P3", "invalid provider → falls back to default",
           r and r.get("type") == "ready", str(r))


# ── Negative / edge (black-box) ───────────────────────────────────────────
async def test_negative() -> None:
    # N1: first message not hello → expected_hello error.
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps({"type": "text", "text": "no hello"}))
        o = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        record("N1", "non-hello first msg → error expected_hello",
               o.get("type") == "error" and o.get("code") == "expected_hello",
               str(o))

    # N2: malformed JSON after handshake → bad_json error, session survives.
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(_hello("mock")))
        await _ready(ws)
        await ws.send("{not valid json")
        got_badjson = False
        for _ in range(10):
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            if isinstance(raw, (bytes, bytearray)):
                continue
            o = json.loads(raw)
            if o.get("type") == "error" and o.get("code") == "bad_json":
                got_badjson = True
                break
            if o.get("type") in ("state", "pong"):
                continue
        record("N2", "malformed JSON → bad_json, session alive", got_badjson, "")

    # N3: too-short binary frame is ignored, session still works.
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(_hello("mock")))
        await _ready(ws)
        await ws.send(b"\x01\x02\x03")  # < 9-byte header
        await ws.send(json.dumps({"type": "text", "text": "still alive?"}))
        turn = await _drain_turn(ws)
        record("N3", "short binary frame ignored, turn still works",
               "transcript" in turn["types"], f"types={sorted(turn['types'])}")

    # N4: unknown control type is ignored, session still works.
    async with websockets.connect(URI, max_size=None) as ws:
        await ws.send(json.dumps(_hello("mock")))
        await _ready(ws)
        await ws.send(json.dumps({"type": "totally_unknown", "x": 1}))
        await ws.send(json.dumps({"type": "text", "text": "ok"}))
        turn = await _drain_turn(ws)
        record("N4", "unknown control type ignored, turn works",
               "transcript" in turn["types"], "")


# ── Stress / concurrency ──────────────────────────────────────────────────
async def _one_mock_turn() -> bool:
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            await ws.send(json.dumps(_hello("mock")))
            await ws.send(json.dumps({"type": "config"}))
            if not await _ready(ws):
                return False
            await ws.send(json.dumps({"type": "text", "text": "hi"}))
            turn = await _drain_turn(ws)
            return turn["audio"] > 0 or "transcript" in turn["types"]
    except Exception:  # noqa: BLE001
        return False


async def test_stress() -> None:
    # S1: 30 concurrent sessions each complete a turn.
    n = 30
    t0 = time.monotonic()
    oks = await asyncio.gather(*[_one_mock_turn() for _ in range(n)])
    dt = time.monotonic() - t0
    record("S1", f"{n} concurrent sessions complete a turn",
           sum(oks) == n, f"{sum(oks)}/{n} ok in {dt:.1f}s")

    # S2: rapid connect/disconnect churn — backend stays healthy.
    async def churn() -> None:
        try:
            async with websockets.connect(URI, max_size=None) as ws:
                await ws.send(json.dumps(_hello("mock")))
        except Exception:  # noqa: BLE001
            pass
    await asyncio.gather(*[churn() for _ in range(50)])
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/healthz")
    record("S2", "50x connect/disconnect churn → healthz still ok",
           r.status_code == 200, f"status={r.status_code}")

    # S3: flood one session with 100 rapid binary frames — no crash.
    crashed = False
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            await ws.send(json.dumps(_hello("mock")))
            await _ready(ws)
            pcm = struct.pack("<160h", *([0] * 160))
            for _ in range(100):
                await ws.send(_HDR.pack(_AUDIO, 0) + pcm)
            await asyncio.sleep(1.0)
    except Exception:  # noqa: BLE001
        crashed = True
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{BASE}/healthz")
    record("S3", "100 rapid frames flood → no crash, healthz ok",
           not crashed and r.status_code == 200, "")

    # S4: large text payload (~50 KB) is handled.
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            await ws.send(json.dumps(_hello("mock")))
            await _ready(ws)
            await ws.send(json.dumps({"type": "text", "text": "x" * 50000}))
            turn = await _drain_turn(ws)
            ok = "transcript" in turn["types"]
    except Exception:  # noqa: BLE001
        ok = False
    record("S4", "50 KB text payload handled", ok, "")


async def main() -> int:
    print("Running full QA against", BASE)
    await test_http()
    await test_functional()
    await test_providers()
    await test_negative()
    await test_stress()

    width = max(len(n) for _, n, _, _ in results)
    print("\n" + "=" * (width + 22))
    print(f"{'ID':<4}{'CASE':<{width + 2}}{'RESULT':<8}")
    print("-" * (width + 22))
    passed = 0
    for cid, name, ok, detail in results:
        passed += ok
        mark = "PASS" if ok else "FAIL"
        print(f"{cid:<4}{name:<{width + 2}}{mark:<8}"
              + (f"  {detail}" if not ok and detail else ""))
    print("-" * (width + 22))
    total = len(results)
    print(f"TOTAL: {passed}/{total} passed"
          + ("  — ALL GREEN" if passed == total else "  — FAILURES ABOVE"))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
