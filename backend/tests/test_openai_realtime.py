"""Unit tests for the OpenAI Realtime gateway — no SDK, no network.

The adapter normalizes OpenAI's Realtime event stream into the gateway's
:class:`GatewayEvent` vocabulary and renders outbound items. We exercise:
  * ``_handle_event`` for every server event ``type`` it understands,
  * tool-definition rendering,
  * the ``send_*`` / ``interrupt`` / ``close`` item creation against a fake conn,
  * ``connect`` failing fast with no API key.

Events are read straight off the gateway's internal queue, so no live websocket
is needed. The Grok adapter is a thin subclass, so this also covers its path.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from app.ai.base import ToolSpec
from app.ai.events import EventType
from app.ai.openai_realtime import OpenAIRealtimeGateway

# asyncio_mode=auto (pytest.ini) runs the coroutine tests; the two sync tests
# (tool-definition shape) stay unmarked to avoid a spurious asyncio warning.


def _gw(tools: list[ToolSpec] | None = None) -> OpenAIRealtimeGateway:
    return OpenAIRealtimeGateway(system_prompt="sys", tools=tools or [])


def _ev(etype: str, **kw) -> SimpleNamespace:
    return SimpleNamespace(type=etype, **kw)


async def _drain(gw: OpenAIRealtimeGateway) -> list:
    """Collect every event currently on the queue (skipping the close sentinel)."""
    out = []
    while not gw._queue.empty():
        ev = gw._queue.get_nowait()
        if ev is not None:
            out.append(ev)
    return out


def _b64(pcm: bytes) -> str:
    return base64.b64encode(pcm).decode("ascii")


async def test_audio_delta_emits_start_then_chunk_once() -> None:
    """First audio delta opens the stream; later deltas only add chunks."""
    gw = _gw()
    await gw._handle_event(_ev("response.output_audio.delta", delta=_b64(b"\x01\x02")))
    kinds = [e.type for e in await _drain(gw)]
    assert kinds[0] == EventType.AUDIO_START
    assert EventType.AUDIO_CHUNK in kinds

    await gw._handle_event(_ev("response.audio.delta", delta=_b64(b"\x03\x04")))
    kinds2 = [e.type for e in await _drain(gw)]
    assert EventType.AUDIO_START not in kinds2  # not re-opened
    assert kinds2 == [EventType.AUDIO_CHUNK]


async def test_audio_done_emits_end_only_when_open() -> None:
    """AUDIO_END fires once for an open stream, and is a no-op otherwise."""
    gw = _gw()
    gw._audio_open = True
    await gw._handle_event(_ev("response.output_audio.done"))
    assert [e.type for e in await _drain(gw)] == [EventType.AUDIO_END]
    # Stream already closed → a second done is ignored.
    await gw._handle_event(_ev("response.audio.done"))
    assert await _drain(gw) == []


async def test_transcript_deltas_accumulate_and_finalize_on_done() -> None:
    """Streaming deltas are cumulative; response.done finalizes + completes turn."""
    gw = _gw()
    await gw._handle_event(_ev("response.output_audio_transcript.delta", delta="Hel"))
    await gw._handle_event(_ev("response.audio_transcript.delta", delta="lo"))
    await gw._handle_event(_ev("response.done"))
    events = await _drain(gw)

    tx = [e for e in events if e.type == EventType.TRANSCRIPT]
    assert [t.text for t in tx] == ["Hel", "Hello", "Hello"]
    assert tx[0].final is False and tx[-1].final is True
    assert all(t.role == "assistant" for t in tx)
    assert any(e.type == EventType.TURN_COMPLETE for e in events)
    assert gw._assistant_buf == ""  # buffer reset for the next turn


async def test_user_input_transcription_is_user_role_final() -> None:
    """Completed ASR is surfaced as a finalized user transcript."""
    gw = _gw()
    await gw._handle_event(
        _ev(
            "conversation.item.input_audio_transcription.completed",
            transcript="what is this",
        )
    )
    [t] = await _drain(gw)
    assert t.type == EventType.TRANSCRIPT
    assert t.role == "user" and t.final is True and t.text == "what is this"


async def test_function_call_event_parses_json_args() -> None:
    """A function call is decoded into a ToolCallEvent with parsed args."""
    gw = _gw()
    await gw._handle_event(
        _ev(
            "response.function_call_arguments.done",
            call_id="call_1",
            name="add_note",
            arguments='{"text": "buy milk"}',
        )
    )
    [tc] = await _drain(gw)
    assert tc.type == EventType.TOOL_CALL
    assert tc.id == "call_1" and tc.name == "add_note"
    assert tc.args == {"text": "buy milk"}


async def test_function_call_bad_json_yields_empty_args() -> None:
    """Malformed argument JSON degrades to empty args instead of crashing."""
    gw = _gw()
    await gw._handle_event(
        _ev(
            "response.function_call_arguments.done",
            call_id="c",
            name="x",
            arguments="{not valid",
        )
    )
    [tc] = await _drain(gw)
    assert tc.args == {}


async def test_error_event_is_normalized() -> None:
    """A provider error event becomes a non-fatal ErrorEvent."""
    gw = _gw()
    await gw._handle_event(_ev("error", error=SimpleNamespace(message="boom")))
    [e] = await _drain(gw)
    assert e.type == EventType.ERROR and "boom" in e.message


async def test_unknown_event_type_is_ignored() -> None:
    """An unrecognized event type produces no gateway events."""
    gw = _gw()
    await gw._handle_event(_ev("some.unhandled.event", foo=1))
    assert await _drain(gw) == []


def test_tool_definitions_render_realtime_function_shape() -> None:
    """Tools are exported in the Realtime ``session.tools`` function shape."""
    gw = _gw(
        [ToolSpec(name="add_note", description="d", parameters={"type": "object"})]
    )
    [defn] = gw._tool_definitions()
    assert defn == {
        "type": "function",
        "name": "add_note",
        "description": "d",
        "parameters": {"type": "object"},
    }


class _FakeConn:
    """Records the outbound Realtime calls made by the send_* helpers."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        outer = self

        class _Buf:
            async def append(self, **kw):
                outer.calls.append(("input_audio_buffer.append", kw))

        class _Item:
            async def create(self, **kw):
                outer.calls.append(("item.create", kw))

        class _Conv:
            item = _Item()

        class _Resp:
            async def create(self, **kw):
                outer.calls.append(("response.create", kw))

            async def cancel(self, **kw):
                outer.calls.append(("response.cancel", kw))

        self.input_audio_buffer = _Buf()
        self.conversation = _Conv()
        self.response = _Resp()


async def test_send_audio_appends_base64_pcm() -> None:
    """send_audio base64-encodes PCM into an input_audio_buffer.append."""
    gw = _gw()
    gw._conn = _FakeConn()
    await gw.send_audio(b"\x01\x02\x03")
    name, kw = gw._conn.calls[0]
    assert name == "input_audio_buffer.append"
    assert base64.b64decode(kw["audio"]) == b"\x01\x02\x03"


async def test_send_text_creates_item_then_requests_response() -> None:
    """send_text adds a user message item and triggers a response."""
    gw = _gw()
    gw._conn = _FakeConn()
    await gw.send_text("hello")
    names = [c[0] for c in gw._conn.calls]
    assert names == ["item.create", "response.create"]


async def test_send_tool_result_creates_function_output() -> None:
    """A successful tool result is returned as a function_call_output item."""
    gw = _gw()
    gw._conn = _FakeConn()
    await gw.send_tool_result("call_1", "add_note", {"ok": True}, ok=True)
    item = gw._conn.calls[0][1]["item"]
    assert item["type"] == "function_call_output"
    assert item["call_id"] == "call_1"
    assert "result" in item["output"]


async def test_send_helpers_are_noops_without_connection() -> None:
    """Before connect (no conn) the send_* helpers silently do nothing."""
    gw = _gw()
    await gw.send_audio(b"x")
    await gw.send_text("hi")
    await gw.send_tool_result("c", "n", "r")
    await gw.send_video(b"jpeg")  # always a no-op for the audio path


async def test_interrupt_closes_audio_and_cancels_response() -> None:
    """Barge-in clears the buffer, ends open audio, and cancels the response."""
    gw = _gw()
    gw._conn = _FakeConn()
    gw._audio_open = True
    gw._assistant_buf = "partial"
    await gw.interrupt()
    assert gw._assistant_buf == ""
    kinds = [e.type for e in await _drain(gw)]
    assert EventType.AUDIO_END in kinds
    assert ("response.cancel", {}) in gw._conn.calls


async def test_connect_without_api_key_raises() -> None:
    """connect fails fast (RuntimeError) when no API key is configured."""
    gw = _gw()  # conftest forces OPENAI_API_KEY="" → no key
    with pytest.raises(RuntimeError):
        await gw.connect()


async def test_close_is_idempotent() -> None:
    """close tears down once and tolerates being called again."""
    gw = _gw()
    await gw.close()
    assert gw._closed is True
    await gw.close()  # no raise on the second call
