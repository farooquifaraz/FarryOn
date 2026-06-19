"""Typed events emitted by an :class:`~app.ai.base.AIGateway`.

The gateway abstraction is provider-agnostic: every adapter (Gemini, OpenAI,
mock) normalizes its provider-specific stream into this small, stable set of
events. The :class:`~app.ws.session.Session` consumes them and translates each
into the corresponding ``PROTOCOL.md`` server message.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventType(str, Enum):
    """Discriminator for :class:`GatewayEvent` subclasses."""

    TRANSCRIPT = "transcript"
    AUDIO_START = "audio_start"
    AUDIO_CHUNK = "audio_chunk"
    AUDIO_END = "audio_end"
    TOOL_CALL = "tool_call"
    TURN_COMPLETE = "turn_complete"
    ERROR = "error"


@dataclass(slots=True)
class GatewayEvent:
    """Base class for all gateway events."""

    type: EventType


@dataclass(slots=True)
class TranscriptEvent(GatewayEvent):
    """A streaming transcript fragment for the user or assistant.

    Attributes:
        role: ``"user"`` (ASR) or ``"assistant"`` (model text).
        text: Partial or full text.
        final: ``True`` when this fragment finalizes the current utterance.
    """

    role: str
    text: str
    final: bool = False
    type: EventType = field(default=EventType.TRANSCRIPT, init=False)


@dataclass(slots=True)
class AudioStartEvent(GatewayEvent):
    """Marks the beginning of a streamed assistant audio response."""

    type: EventType = field(default=EventType.AUDIO_START, init=False)


@dataclass(slots=True)
class AudioChunkEvent(GatewayEvent):
    """A chunk of assistant TTS audio.

    Attributes:
        pcm: Raw PCM signed 16-bit LE samples at 24 kHz mono (per protocol).
    """

    pcm: bytes
    type: EventType = field(default=EventType.AUDIO_CHUNK, init=False)


@dataclass(slots=True)
class AudioEndEvent(GatewayEvent):
    """Marks the end of a streamed assistant audio response."""

    type: EventType = field(default=EventType.AUDIO_END, init=False)


@dataclass(slots=True)
class ToolCallEvent(GatewayEvent):
    """The model is requesting a tool invocation.

    Attributes:
        id: Provider call id (echoed back with the result).
        name: Tool name; MUST be one of the registered tool names.
        args: Decoded argument object.
    """

    id: str
    name: str
    args: dict[str, Any]
    type: EventType = field(default=EventType.TOOL_CALL, init=False)


@dataclass(slots=True)
class TurnCompleteEvent(GatewayEvent):
    """The model has finished its current turn (response complete)."""

    type: EventType = field(default=EventType.TURN_COMPLETE, init=False)


@dataclass(slots=True)
class ErrorEvent(GatewayEvent):
    """A non-fatal-by-default error surfaced from the provider stream.

    Attributes:
        code: Short machine code.
        message: Human-readable description.
        fatal: Whether the session should be torn down.
    """

    code: str
    message: str
    fatal: bool = False
    type: EventType = field(default=EventType.ERROR, init=False)
