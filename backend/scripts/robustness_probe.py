"""Robustness probe: hostile-conditions test of a running backend.

Unlike ``e2e_smoke`` (one happy-path text turn), this connects like the real
app and then misbehaves the way a phone on bad Wi-Fi does. Run against a
backend already listening on 127.0.0.1:8000:

    python scripts/robustness_probe.py

Checks (each prints PASS/FAIL; exit 1 if any fail):
  T1  text turn         — handshake, one text turn, reply arrives; latency.
  T2  ping storm        — 20 pings in a burst; every one gets its pong back.
  T3  drop + reconnect  — hard-close mid-session; a fresh connect works.
  T4  parallel sockets  — two live connections at once (reconnect overlap);
                          both handshake, both answer pings, closing one
                          leaves the other alive.
  T5  malformed input   — junk JSON, unknown types, bogus binary tags,
                          oversized text: server must answer the next ping.
  T6  silent client     — connect, then send nothing for 25 s: the server
                          must keep the socket open (the app's mic can be
                          muted that long) and still answer a ping.
"""

from __future__ import annotations

import asyncio
import json
import struct
import sys
import time
from datetime import timedelta

import websockets

from app.config import get_settings
from app.core.security import encode_token


def _probe_uri() -> str:
    """Mint a short-lived access token the same way the auth module does.

    The backend (correctly) 403s a tokenless /ws/live when JWT_SECRET is
    configured, so the probe signs its own — same secret, same claims.
    """
    token = encode_token(
        settings=get_settings(),
        user_id=1,
        token_type="access",
        expires_delta=timedelta(minutes=30),
    )
    return f"ws://127.0.0.1:8000/ws/live?token={token}"


URI = _probe_uri()

HELLO = {
    "type": "hello",
    "protocolVersion": 1,
    "client": {"platform": "probe", "appVersion": "1.0.0"},
    "device": {
        "kind": "phone",
        "id": "robustness-probe",
        "capabilities": ["audio_in", "audio_out", "video_in"],
    },
}
CONFIG = {
    "type": "config",
    "audioIn": {"encoding": "pcm16", "sampleRate": 16000, "channels": 1},
    "videoIn": {"format": "jpeg", "fps": 1, "maxWidth": 1024},
    "audioOut": {"encoding": "pcm16", "sampleRate": 24000, "channels": 1},
}

RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def handshake(ws) -> bool:
    await ws.send(json.dumps(HELLO))
    await ws.send(json.dumps(CONFIG))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "ready":
            return True
        if msg.get("type") == "error" and msg.get("fatal"):
            return False
    return False


async def wait_pong(ws, t: int, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = deadline - time.monotonic()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(left, 0.05))
        except asyncio.TimeoutError:
            return False
        if isinstance(raw, bytes):
            continue
        msg = json.loads(raw)
        if msg.get("type") == "pong" and msg.get("t") == t:
            return True
    return False


async def t1_text_turn() -> None:
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            if not await handshake(ws):
                record("T1 text turn", False, "no ready")
                return
            t0 = time.monotonic()
            await ws.send(json.dumps({"type": "text", "text": "Say OK."}))
            got_reply = False
            first_reply_ms = None
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    if first_reply_ms is None:
                        first_reply_ms = int((time.monotonic() - t0) * 1000)
                    got_reply = True
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "transcript" and msg.get("role") != "user":
                    if first_reply_ms is None:
                        first_reply_ms = int((time.monotonic() - t0) * 1000)
                    got_reply = True
                if msg.get("type") == "state" and msg.get("value") == "listening" and got_reply:
                    break
                if msg.get("type") == "error" and msg.get("fatal"):
                    record("T1 text turn", False, f"fatal: {msg.get('message')}")
                    return
            record("T1 text turn", got_reply,
                   f"first reply {first_reply_ms}ms" if got_reply else "no reply in 30s")
    except Exception as exc:  # noqa: BLE001
        record("T1 text turn", False, repr(exc))


async def t2_ping_storm() -> None:
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            if not await handshake(ws):
                record("T2 ping storm", False, "no ready")
                return
            base = int(time.time() * 1000)
            for i in range(20):
                await ws.send(json.dumps({"type": "ping", "t": base + i}))
            got = 0
            deadline = time.monotonic() + 5
            while got < 20 and time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    continue
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    got += 1
            record("T2 ping storm", got == 20, f"{got}/20 pongs")
    except Exception as exc:  # noqa: BLE001
        record("T2 ping storm", False, repr(exc))


async def t3_drop_reconnect() -> None:
    try:
        ws = await websockets.connect(URI, max_size=None)
        if not await handshake(ws):
            record("T3 drop+reconnect", False, "no ready (1st)")
            return
        # Hard close with no goodbye — what a dying radio link looks like.
        await ws.close(code=1006)
    except Exception:  # noqa: BLE001 - 1006 may raise locally; that's fine
        pass
    try:
        async with websockets.connect(URI, max_size=None) as ws2:
            ok = await handshake(ws2)
            t = int(time.time() * 1000)
            await ws2.send(json.dumps({"type": "ping", "t": t}))
            ok = ok and await wait_pong(ws2, t)
            record("T3 drop+reconnect", ok, "fresh session usable" if ok else "2nd session broken")
    except Exception as exc:  # noqa: BLE001
        record("T3 drop+reconnect", False, repr(exc))


async def t4_parallel_sockets() -> None:
    try:
        ws_a = await websockets.connect(URI, max_size=None)
        ok_a = await handshake(ws_a)
        ws_b = await websockets.connect(URI, max_size=None)
        ok_b = await handshake(ws_b)
        t = int(time.time() * 1000)
        await ws_a.send(json.dumps({"type": "ping", "t": t}))
        pong_a = await wait_pong(ws_a, t)
        await ws_b.send(json.dumps({"type": "ping", "t": t + 1}))
        pong_b = await wait_pong(ws_b, t + 1)
        await ws_a.close()
        # B must survive A's death.
        await ws_b.send(json.dumps({"type": "ping", "t": t + 2}))
        survives = await wait_pong(ws_b, t + 2)
        await ws_b.close()
        ok = ok_a and ok_b and pong_a and pong_b and survives
        record("T4 parallel sockets", ok,
               f"a={ok_a and pong_a} b={ok_b and pong_b} b-after-a-close={survives}")
    except Exception as exc:  # noqa: BLE001
        record("T4 parallel sockets", False, repr(exc))


async def t5_malformed_input() -> None:
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            if not await handshake(ws):
                record("T5 malformed input", False, "no ready")
                return
            await ws.send("this is not json {{{")
            await ws.send(json.dumps({"type": "no_such_type", "x": 1}))
            await ws.send(json.dumps([1, 2, 3]))  # JSON but not an object
            await ws.send(struct.pack("<BQ", 0x7F, 0) + b"\x00" * 64)  # bogus tag
            await ws.send(json.dumps({"type": "text", "text": "x" * 100_000}))
            await ws.send(struct.pack("<B", 0x01))  # truncated header
            t = int(time.time() * 1000)
            await ws.send(json.dumps({"type": "ping", "t": t}))
            ok = await wait_pong(ws, t, timeout=8)
            record("T5 malformed input", ok,
                   "server alive after junk" if ok else "server stopped answering")
    except Exception as exc:  # noqa: BLE001
        record("T5 malformed input", False, repr(exc))


async def t6_silent_client() -> None:
    try:
        async with websockets.connect(URI, max_size=None) as ws:
            if not await handshake(ws):
                record("T6 silent client", False, "no ready")
                return
            # Drain-but-ignore for 25 s: a muted-mic phone in a pocket.
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(ws.recv(), timeout=deadline - time.monotonic())
                except (asyncio.TimeoutError, ValueError):
                    break
            t = int(time.time() * 1000)
            await ws.send(json.dumps({"type": "ping", "t": t}))
            ok = await wait_pong(ws, t)
            record("T6 silent client", ok,
                   "alive after 25s silence" if ok else "server dropped a quiet client")
    except Exception as exc:  # noqa: BLE001
        record("T6 silent client", False, repr(exc))


async def main() -> int:
    print(f"probe → {URI}")
    await t1_text_turn()
    await t2_ping_storm()
    await t3_drop_reconnect()
    await t4_parallel_sockets()
    await t5_malformed_input()
    await t6_silent_client()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
