"""End-to-end WebSocket tests for ``/ws/live`` using the mock provider.

Drives the FastAPI ``TestClient`` WebSocket through the ``PROTOCOL.md``
handshake and a turn, asserting the server messages and binary OUTPUT_AUDIO
frames conform to the contract. No network or API keys are used.
"""

from __future__ import annotations

import struct

from fastapi.testclient import TestClient

from app.main import create_app
from app.ws.frames import HEADER_SIZE, FrameTag, decode_frame, encode_frame


def _collect_until(ws, predicate, *, limit: int = 60):
    """Drain messages until ``predicate(msg)`` is true or ``limit`` reached.

    Returns the list of received messages. Each item is ``("json", obj)`` or
    ``("bytes", data)``.
    """
    received: list[tuple[str, object]] = []
    for _ in range(limit):
        data = ws.receive()
        if "text" in data and data["text"] is not None:
            import json

            obj = json.loads(data["text"])
            received.append(("json", obj))
            if predicate(("json", obj)):
                break
        elif "bytes" in data and data["bytes"] is not None:
            received.append(("bytes", data["bytes"]))
            if predicate(("bytes", data["bytes"])):
                break
    return received


def _handshake(ws) -> dict:
    """Send hello+config and return the parsed ``ready`` message."""
    ws.send_json(
        {
            "type": "hello",
            "protocolVersion": 1,
            "client": {"platform": "android", "appVersion": "1.0.0"},
            "device": {
                "kind": "phone",
                "id": "dev-1",
                "capabilities": ["audio_in", "video_in", "audio_out"],
            },
            "session": {},
        }
    )
    ws.send_json(
        {
            "type": "config",
            "audioIn": {"encoding": "pcm16", "sampleRate": 16000, "channels": 1},
            "videoIn": {"format": "jpeg", "fps": 1, "maxWidth": 1024},
            "audioOut": {"encoding": "pcm16", "sampleRate": 24000, "channels": 1},
        }
    )
    ready = ws.receive_json()
    return ready


def test_handshake_yields_ready() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ready = _handshake(ws)
            assert ready["type"] == "ready"
            assert ready["protocolVersion"] == 1
            assert "sessionId" in ready and ready["sessionId"]
            assert ready["model"]  # mock model label


def test_ping_pong() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _handshake(ws)
            # Drain the initial state message(s), then ping.
            ws.send_json({"type": "ping", "t": 123})
            msgs = _collect_until(
                ws, lambda m: m[0] == "json" and m[1].get("type") == "pong"
            )
            pongs = [m[1] for m in msgs if m[0] == "json" and m[1]["type"] == "pong"]
            assert pongs and pongs[0]["t"] == 123


def test_text_turn_produces_transcript_tool_and_audio() -> None:
    """A text turn drives transcript -> tool_call -> tool_result -> audio.

    The tool result is produced by a concurrent orchestrator task, so its
    ordering relative to the streamed audio frames is not deterministic. We
    therefore drain the whole turn (until both ``tool_result`` and ``audio_end``
    have been observed) and assert over the full set.
    """
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _handshake(ws)
            ws.send_json({"type": "text", "text": "remember to water plants"})

            seen: set[str] = set()

            def done(item) -> bool:
                if item[0] == "json":
                    seen.add(item[1].get("type"))
                return {"tool_result", "audio_end"}.issubset(seen)

            msgs = _collect_until(ws, done, limit=120)

            json_types = [m[1]["type"] for m in msgs if m[0] == "json"]
            assert "transcript" in json_types
            assert "tool_call" in json_types
            assert "tool_result" in json_types
            assert "audio_start" in json_types
            assert "audio_end" in json_types

            # The tool_call must be a canonical tool with the right shape.
            tool_calls = [
                m[1] for m in msgs if m[0] == "json" and m[1]["type"] == "tool_call"
            ]
            assert tool_calls[0]["name"] == "create_note"
            assert "args" in tool_calls[0]
            assert "id" in tool_calls[0]

            # The tool_result echoes id/name and reports success.
            results = [
                m[1]
                for m in msgs
                if m[0] == "json" and m[1]["type"] == "tool_result"
            ]
            assert results[0]["ok"] is True
            assert results[0]["name"] == "create_note"
            assert results[0]["result"]["id"] >= 1

            # Streamed OUTPUT_AUDIO binary frames must conform to PROTOCOL.md.
            audio_frames = [m[1] for m in msgs if m[0] == "bytes"]
            assert audio_frames, "expected OUTPUT_AUDIO binary frames"
            tag, _ts, payload = decode_frame(audio_frames[0])
            assert tag == FrameTag.OUTPUT_AUDIO
            assert len(audio_frames[0]) >= HEADER_SIZE
            assert len(payload) % 2 == 0  # PCM16 -> even byte count


def test_binary_audio_frame_drives_turn() -> None:
    """Sending an INPUT_AUDIO (0x01) frame triggers an assistant turn."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _handshake(ws)
            # 320 samples of silence (20 ms @ 16 kHz), PCM16 LE.
            pcm = struct.pack("<320h", *([0] * 320))
            ws.send_bytes(encode_frame(FrameTag.INPUT_AUDIO, pcm))

            msgs = _collect_until(
                ws,
                lambda m: m[0] == "json"
                and m[1].get("type") == "tool_result",
            )
            assert any(
                m[0] == "json" and m[1]["type"] == "tool_call" for m in msgs
            )


def test_interrupt_resets_state() -> None:
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            _handshake(ws)
            ws.send_json({"type": "interrupt"})
            msgs = _collect_until(
                ws,
                lambda m: m[0] == "json"
                and m[1].get("type") == "state"
                and m[1].get("value") == "listening",
            )
            states = [
                m[1]
                for m in msgs
                if m[0] == "json" and m[1]["type"] == "state"
            ]
            assert any(s["value"] == "listening" for s in states)


def _hello(provider: str | None = None) -> dict:
    msg = {
        "type": "hello",
        "protocolVersion": 1,
        "client": {"platform": "android", "appVersion": "1.0.0"},
        "device": {"kind": "phone", "id": "d", "capabilities": []},
        "session": {},
    }
    if provider is not None:
        msg["provider"] = provider
    return msg


def test_provider_selection_builds_requested_gateway() -> None:
    """hello.provider actually picks the gateway.

    Requesting ``gemini`` with no API key makes that gateway fail to connect —
    proving the *requested* provider was built (not the mock default). The
    session then falls back to the server default (mock here) and emits a
    NON-fatal ``provider_fallback`` notice instead of a dead session.
    """
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json(_hello(provider="gemini"))
            msg = ws.receive_json()
            # The fallback notice proves gemini was built + attempted (and
            # failed); a silent mock-default path would emit no such notice.
            assert msg["type"] == "error"
            assert msg["code"] == "provider_fallback"
            assert msg["fatal"] is False


def test_invalid_provider_falls_back_to_default() -> None:
    """An unknown provider falls back to the server default (mock) and works."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json(_hello(provider="bogus-xyz"))
            ready = ws.receive_json()
            assert ready["type"] == "ready"
            assert ready["model"]


def test_bad_first_message_is_rejected() -> None:
    """A non-hello first message yields an error and closes the session."""
    app = create_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"type": "text", "text": "no hello first"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert msg["code"] == "expected_hello"


async def test_notes_tasks_rest_endpoints(db_session) -> None:
    """The REST endpoints surface and manage what the agent created."""
    from app.tools.base import ToolContext
    from app.tools.notes import CreateNoteTool
    from app.tools.tasks import CreateTaskTool

    note = await CreateNoteTool().run(
        ToolContext(session=db_session), text="REST note"
    )
    task = await CreateTaskTool().run(
        ToolContext(session=db_session), title="REST task"
    )
    await db_session.commit()

    app = create_app()
    with TestClient(app) as client:
        notes = client.get("/notes").json()
        assert any(n["text"] == "REST note" for n in notes)

        tasks = client.get("/tasks").json()
        assert any(t["title"] == "REST task" and not t["done"] for t in tasks)

        # Mark the task done.
        r = client.post(f"/tasks/{task['id']}/done", params={"done": True})
        assert r.status_code == 200 and r.json()["done"] is True

        # Delete the note.
        r = client.delete(f"/notes/{note['id']}")
        assert r.status_code == 200 and r.json()["deleted"] is True
        notes2 = client.get("/notes").json()
        assert all(n["id"] != note["id"] for n in notes2)


def test_healthz_and_metrics_endpoints() -> None:
    app = create_app()
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "farryon_ws_connections_total" in metrics.text


def test_readyz_reports_ready_when_db_reachable() -> None:
    app = create_app()
    with TestClient(app) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        body = ready.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
