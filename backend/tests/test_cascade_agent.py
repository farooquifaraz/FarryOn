"""The ``cascade`` provider: hear once, think with the same tools, hand the
words to the phone. No network: the STT and LLM backends are fakes with the
exact interface the real ones expose, plus one HTTP-level test through
httpx's MockTransport for the OpenAI-compatible wire shape."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.ai.base import ToolSpec
from app.ai.cascade_agent import (
    CascadeAgentGateway,
    LLMReply,
    OpenAICompatLLM,
    OpenAICompatSTT,
    pcm16_to_wav,
)
from app.ai.events import ToolCallEvent, TranscriptEvent, TurnCompleteEvent

pytestmark = pytest.mark.asyncio


class _STT:
    label = "fake-stt"

    def __init__(self, text: str) -> None:
        self.text = text
        self.audio: list[bytes] = []

    async def transcribe(self, pcm: bytes) -> str:
        self.audio.append(pcm)
        return self.text

    async def close(self) -> None:
        return None


class _LLM:
    """Replies in order; records every history it was shown."""

    label = "fake-llm"

    def __init__(self, replies: list[LLMReply]) -> None:
        self.replies = list(replies)
        self.seen: list[list[dict[str, Any]]] = []

    async def complete(self, system, messages, tools) -> LLMReply:
        self.seen.append(list(messages))
        assert system.startswith("PROMPT")
        assert [t.name for t in tools] == ["create_note"]
        return self.replies.pop(0)

    async def close(self) -> None:
        return None


def _gateway(stt: Any, llm: Any) -> CascadeAgentGateway:
    tools = [ToolSpec("create_note", "Save a note.", {"type": "object", "properties": {"text": {"type": "string"}}})]
    return CascadeAgentGateway(system_prompt="PROMPT", tools=tools, stt=stt, llm=llm)


async def _drain(gw: CascadeAgentGateway, n: int, timeout: float = 5.0) -> list[Any]:
    out: list[Any] = []
    it = gw.events()
    for _ in range(n):
        out.append(await asyncio.wait_for(it.__anext__(), timeout))
    return out


def _second(ms: int) -> bytes:
    return bytes(16_000 * 2 * ms // 1000)


async def test_an_utterance_becomes_a_user_transcript_and_a_spoken_reply() -> None:
    stt = _STT("what time is it")
    llm = _LLM([LLMReply("It's ten past nine.", [], {"prompt_tokens": 50, "completion_tokens": 7})])
    gw = _gateway(stt, llm)
    await gw.connect()
    assert gw.model_label.startswith("cascade:")

    await gw.send_activity_start()
    await gw.send_audio(_second(600))
    await gw.send_activity_end()

    events = await _drain(gw, 4)
    assert [type(e).__name__ for e in events] == [
        "TranscriptEvent", "TranscriptEvent", "TranscriptEvent", "TurnCompleteEvent",
    ]
    hearing, heard, reply, _ = events
    assert (hearing.role, hearing.final, hearing.text) == ("user", False, "what time is it")
    assert (heard.role, heard.final) == ("user", True)
    assert (reply.role, reply.final, reply.text) == ("assistant", True, "It's ten past nine.")
    assert len(stt.audio) == 1 and len(stt.audio[0]) == 16_000 * 2 * 600 // 1000
    await gw.close()


async def test_a_tool_call_goes_out_and_its_result_comes_back_into_the_next_round() -> None:
    stt = _STT("note that the milk is finished")
    llm = _LLM([
        LLMReply("", [("call-1", "create_note", {"text": "milk is finished"})]),
        LLMReply("Saved that note.", []),
    ])
    gw = _gateway(stt, llm)
    await gw.send_activity_start()
    await gw.send_audio(_second(500))
    await gw.send_activity_end()

    events = await _drain(gw, 3)
    call = events[2]
    assert isinstance(call, ToolCallEvent)
    assert (call.id, call.name, call.args) == ("call-1", "create_note", {"text": "milk is finished"})

    await gw.send_tool_result("call-1", "create_note", {"id": 7, "text": "milk is finished"}, ok=True)
    events = await _drain(gw, 2)
    assert isinstance(events[0], TranscriptEvent) and events[0].text == "Saved that note."
    assert isinstance(events[1], TurnCompleteEvent)

    # The second round saw the assistant's tool call AND the tool's answer.
    second = llm.seen[1]
    roles = [m["role"] for m in second]
    assert roles == ["user", "assistant", "tool"]
    assert second[1]["tool_calls"][0]["function"]["name"] == "create_note"
    assert json.loads(second[2]["content"])["id"] == 7
    await gw.close()


async def test_short_blips_and_silence_make_no_turn() -> None:
    stt = _STT("")
    llm = _LLM([])
    gw = _gateway(stt, llm)
    await gw.send_activity_start()
    await gw.send_audio(_second(100))  # under the 300 ms floor
    await gw.send_activity_end()
    await asyncio.sleep(0.05)
    assert stt.audio == [], "too short to be words: not even transcribed"

    await gw.send_activity_start()
    await gw.send_audio(_second(800))
    await gw.send_activity_end()
    await asyncio.sleep(0.05)
    assert len(stt.audio) == 1
    assert gw._queue.empty(), "no speech in it: no transcript, no reply"
    await gw.close()


async def test_typed_text_is_a_turn_too_and_history_is_kept() -> None:
    llm = _LLM([LLMReply("Hi!", []), LLMReply("Still here.", [])])
    gw = _gateway(_STT("x"), llm)
    await gw.send_text("hello")
    await _drain(gw, 2)
    await gw.send_text("you there?")
    await _drain(gw, 2)
    assert [m["role"] for m in llm.seen[1]] == ["user", "assistant", "user"]
    await gw.close()


async def test_interrupt_cancels_the_turn_in_flight() -> None:
    class _SlowLLM(_LLM):
        async def complete(self, system, messages, tools):
            await asyncio.sleep(10)
            return LLMReply("late", [])

    gw = _gateway(_STT("x"), _SlowLLM([]))
    await gw.send_text("go")
    await asyncio.sleep(0.05)
    assert gw._turn_task is not None and not gw._turn_task.done()
    await gw.interrupt()
    await asyncio.sleep(0.05)
    assert gw._turn_task is None
    await gw.close()


async def test_a_second_utterance_waits_for_the_reply_instead_of_killing_it() -> None:
    """Device 2026-09-15 00:07: "hello?" repeated every 3 s cancelled the
    turn in flight each time — no reply ever. Now the running turn finishes
    and only the NEWEST held utterance runs after it."""
    gate = asyncio.Event()

    class _SlowLLM(_LLM):
        async def complete(self, system, messages, tools):
            self.seen.append(list(messages))
            if len(self.seen) == 1:
                await gate.wait()
            return LLMReply(f"reply {len(self.seen)}", [])

    llm = _SlowLLM([])
    gw = _gateway(_STT("x"), llm)
    await gw.send_text("hello")
    await asyncio.sleep(0.02)
    await gw.send_text("hello?")   # held
    await gw.send_text("hello??")  # replaces the held one
    await asyncio.sleep(0.02)
    assert gw._turn_task is not None and not gw._turn_task.done()
    gate.set()

    events = await _drain(gw, 4)
    texts = [e.text for e in events if isinstance(e, TranscriptEvent)]
    assert texts == ["reply 1", "reply 2"]
    assert llm.seen[1][-1]["content"] == "hello??", "only the newest held utterance ran"
    await asyncio.sleep(0.02)
    assert gw._turn_task is None
    await gw.close()


async def test_a_slow_or_broken_model_hands_the_turn_to_the_standin(monkeypatch) -> None:
    """Device 2026-09-15 00:11: the free OpenRouter endpoint sat silent for
    60 s and the user got nothing. The turn now moves to Gemini Flash-Lite
    after the budget, and the reply still arrives."""
    import app.ai.cascade_agent as mod

    monkeypatch.setattr(mod, "_LLM_TURN_TIMEOUT_S", 0.05)

    class _Hanging(_LLM):
        async def complete(self, system, messages, tools):
            await asyncio.sleep(10)
            return LLMReply("never", [])

    class _Standin(_LLM):
        label = "standin"

    standin = _Standin([LLMReply("from the stand-in", [])])
    gw = _gateway(_STT("x"), _Hanging([]))
    gw._fallback_llm = standin  # what _standin_llm() would build on the server key

    await gw.send_text("hello")
    events = await _drain(gw, 2)
    assert isinstance(events[0], TranscriptEvent) and events[0].text == "from the stand-in"
    assert isinstance(events[1], TurnCompleteEvent)
    assert standin.seen[0][-1]["content"] == "hello"
    await gw.close()


async def test_openai_compatible_wire_shape() -> None:
    """One STT and one chat call through MockTransport: paths, auth, bodies."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello there"})
        body = json.loads(request.content)
        assert body["messages"][0] == {"role": "system", "content": "SYS"}
        assert body["tools"][0]["function"]["name"] == "create_note"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None, "tool_calls": [
                {"id": "c9", "type": "function", "function": {"name": "create_note", "arguments": "{\"text\": \"hi\"}"}}
            ]}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        })

    transport = httpx.MockTransport(handler)
    stt = OpenAICompatSTT(base_url="https://stt.example/v1", api_key="k1", model="whisper-x")
    llm = OpenAICompatLLM(base_url="https://llm.example/v1", api_key="k2", model="m")
    stt._client = httpx.AsyncClient(transport=transport)
    llm._client = httpx.AsyncClient(transport=transport)

    assert await stt.transcribe(_second(400)) == "hello there"
    reply = await llm.complete("SYS", [{"role": "user", "content": "hi"}],
                               [ToolSpec("create_note", "d", {"type": "object"})])
    assert reply.tool_calls == [("c9", "create_note", {"text": "hi"})]
    assert reply.usage == {"prompt_tokens": 12, "completion_tokens": 3}

    assert seen[0].headers["authorization"] == "Bearer k1"
    assert b"whisper-x" in seen[0].content and b"RIFF" in seen[0].content
    assert seen[1].headers["authorization"] == "Bearer k2"
    await stt.close()
    await llm.close()


async def test_wav_header_is_16k_mono_pcm16() -> None:
    wav = pcm16_to_wav(b"\x00\x01" * 8)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert wav[22:24] == b"\x01\x00"  # channels
    assert int.from_bytes(wav[24:28], "little") == 16_000
    assert int.from_bytes(wav[40:44], "little") == 16


async def test_the_users_own_keys_beat_the_servers(monkeypatch) -> None:
    """Dev Mode: keys from hello.devKeys pick the OpenAI-compatible backends
    even when the server has none; a blank one leaves the server's choice."""
    from app.ai.factory import build_gateway
    from app.config import Settings

    s = Settings(gemini_api_key="server-gemini", cascade_stt_api_key="", cascade_llm_api_key="")
    schemas = [{"name": "create_note", "description": "d", "parameters": {"type": "object"}}]

    gw = build_gateway(schemas, s, provider="cascade", system_prompt="P")
    assert gw.model_label == "cascade:gemini-3.5-flash-lite+gemini-3.5-flash-lite"

    gw = build_gateway(
        schemas, s, provider="cascade", system_prompt="P",
        provider_options={"stt_api_key": "gsk_user", "llm_api_key": ""},
    )
    assert isinstance(gw._stt, OpenAICompatSTT) and gw._stt.api_key == "gsk_user"
    assert gw.model_label.startswith("cascade:whisper-large-v3-turbo+gemini-3.5-flash-lite")

    gw = build_gateway(
        schemas, s, provider="cascade", system_prompt="P",
        provider_options={"stt_api_key": "gsk_user", "llm_api_key": "sk-or-user"},
    )
    assert isinstance(gw._llm, OpenAICompatLLM) and gw._llm.api_key == "sk-or-user"
    assert gw.model_label == "cascade:whisper-large-v3-turbo+nvidia/nemotron-3-ultra-550b-a55b:free"
    await gw.close()

