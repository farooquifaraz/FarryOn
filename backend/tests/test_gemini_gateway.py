"""Unit tests for the Gemini gateway's stream handling — no network/SDK.

These lock in the fixes for the field report:
  * ``session.receive()`` ends after each turn, so the receive loop must
    re-enter it for EVERY turn (otherwise the 2nd reply never comes);
  * the model's private reasoning (``model_turn.parts[].text`` / ``thought``)
    must NEVER leak into the transcript — the spoken transcript comes only from
    ``output_transcription``;
  * assistant transcripts are emitted cumulatively and finalized on turn end.

We drive :meth:`GeminiGateway._receive_loop` with a fake session that mimics the
google-genai per-turn ``receive()`` contract.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.ai.events import EventType
from app.ai.gemini import GeminiGateway


def _part(*, audio: bytes | None = None, text: str | None = None,
          thought: bool = False) -> SimpleNamespace:
    inline = SimpleNamespace(data=audio) if audio is not None else None
    return SimpleNamespace(inline_data=inline, text=text, thought=thought)


def _content_msg(*, parts=None, out_tx=None, in_tx=None,
                 turn_complete=False, interrupted=False) -> SimpleNamespace:
    model_turn = SimpleNamespace(parts=parts or []) if parts else None
    server_content = SimpleNamespace(
        model_turn=model_turn,
        output_transcription=(
            SimpleNamespace(text=out_tx) if out_tx is not None else None
        ),
        input_transcription=(
            SimpleNamespace(text=in_tx) if in_tx is not None else None
        ),
        turn_complete=turn_complete,
        interrupted=interrupted,
    )
    return SimpleNamespace(server_content=server_content, tool_call=None)


class _FakeSession:
    """Yields one batch of messages per ``receive()`` call, then empties.

    Mirrors google-genai: each ``receive()`` represents a single model turn.
    """

    def __init__(self, turns: list[list[SimpleNamespace]]) -> None:
        self._turns = list(turns)

    def receive(self):
        batch = self._turns.pop(0) if self._turns else []

        async def _gen():
            for message in batch:
                yield message

        return _gen()


async def _drain(gateway: GeminiGateway) -> list:
    recv = asyncio.create_task(gateway._receive_loop())
    events = [event async for event in gateway.events()]
    await recv
    return events


def _gateway() -> GeminiGateway:
    return GeminiGateway(system_prompt="sys", tools=[])


async def test_multi_turn_receive_loop_handles_second_turn() -> None:
    """The loop must process a SECOND turn after the first completes."""
    gw = _gateway()
    gw._session = _FakeSession(
        [
            # Turn 1: a thought part (must be hidden) + audio + spoken words.
            [
                _content_msg(
                    parts=[
                        _part(text="INTERNAL REASONING", thought=True),
                        _part(audio=b"\x01\x02"),
                    ],
                    out_tx="Hello",
                ),
                _content_msg(out_tx=" there.", turn_complete=True),
            ],
            # Turn 2: the message that previously got no reply.
            [
                _content_msg(out_tx="Hi again.", turn_complete=True),
            ],
        ]
    )

    events = await _drain(gw)
    kinds = [e.type for e in events]

    # Two turns ⇒ two TurnComplete events (the regression: only one before).
    assert kinds.count(EventType.TURN_COMPLETE) == 2
    # Audio was forwarded.
    assert EventType.AUDIO_CHUNK in kinds

    transcripts = [e for e in events if e.type == EventType.TRANSCRIPT]
    texts = [t.text for t in transcripts]
    # Reasoning never leaks.
    assert all("INTERNAL REASONING" not in t for t in texts)
    # Cumulative + finalized assistant lines for each turn.
    finals = [t.text for t in transcripts if t.role == "assistant" and t.final]
    assert "Hello there." in finals
    assert "Hi again." in finals


async def test_thought_parts_never_become_transcripts() -> None:
    """A turn that is ONLY reasoning yields no assistant transcript text."""
    gw = _gateway()
    gw._session = _FakeSession(
        [
            [
                _content_msg(
                    parts=[_part(text="thinking hard...", thought=True)],
                    turn_complete=True,
                ),
            ],
        ]
    )
    events = await _drain(gw)
    transcripts = [e for e in events if e.type == EventType.TRANSCRIPT]
    assert transcripts == []  # nothing spoken ⇒ nothing shown
    assert any(e.type == EventType.TURN_COMPLETE for e in events)


async def test_input_transcription_is_attributed_to_user() -> None:
    """User ASR (input_transcription) surfaces with role=user, finalized."""
    gw = _gateway()
    gw._session = _FakeSession(
        [
            [
                _content_msg(in_tx="what is this"),
                _content_msg(out_tx="It's a cup.", turn_complete=True),
            ],
        ]
    )
    events = await _drain(gw)
    user_finals = [
        e.text
        for e in events
        if e.type == EventType.TRANSCRIPT and e.role == "user" and e.final
    ]
    assert "what is this" in user_finals
