"""The :class:`AIGateway` abstraction.

An ``AIGateway`` adapts a realtime multimodal model provider (Gemini Live,
OpenAI Realtime, or the deterministic mock) to a single, transport-agnostic
interface. Adding a new provider means implementing exactly this one class.

Lifecycle / contract
---------------------
1. Construct the gateway (cheap; no network).
2. ``await gateway.connect()`` — open the upstream realtime session and start
   any background receive loop. Idempotent-safe to await once.
3. Feed input with ``send_audio`` / ``send_video`` / ``send_text``. These are
   fire-and-forget from the caller's perspective; the gateway buffers/sends to
   the provider.
4. Concurrently iterate ``async for event in gateway.events():`` to receive
   :class:`~app.ai.events.GatewayEvent` objects (transcripts, audio chunks,
   tool-call requests, turn-complete, errors). The generator runs until the
   session closes.
5. When the model requests a tool, the gateway emits a
   :class:`~app.ai.events.ToolCallEvent`. After the host executes the tool it
   calls ``send_tool_result`` so the model can continue the turn.
6. ``await gateway.interrupt()`` — barge-in: stop in-flight TTS/generation.
7. ``await gateway.close()`` — tear down upstream resources. Idempotent.

Implementations MUST:
- Be safe to import even when the provider SDK or API key is absent (guard
  imports lazily; raise a clear error only from :meth:`connect`).
- Never block the event loop on network I/O (use async clients / threads).
- Normalize provider audio to PCM16 mono. Output audio is expected at 24 kHz
  to match ``PROTOCOL.md``; adapters resample if their provider differs.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Any

from app.ai.events import GatewayEvent


class ToolSpec:
    """A provider-neutral tool definition handed to a gateway at connect time.

    Attributes:
        name: Canonical tool name (matches ``PROTOCOL.md``).
        description: Human/model-facing description.
        parameters: JSON-Schema object describing the arguments.
    """

    __slots__ = ("name", "description", "parameters")

    def __init__(
        self, name: str, description: str, parameters: dict[str, Any]
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical ``{name, description, parameters}`` dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class AIGateway(abc.ABC):
    """Abstract realtime multimodal AI session.

    See the module docstring for the full lifecycle contract.
    """

    #: Human-readable provider name, surfaced in the ``ready`` message ``model``
    #: field hints and logs. Subclasses should set this.
    provider: str = "abstract"

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        model: str | None = None,
    ) -> None:
        """Initialize the gateway (no network).

        Args:
            system_prompt: System instruction for the assistant.
            tools: Tool specifications exposed to the model for function calling.
            model: Provider model identifier (provider default if ``None``).
        """
        self.system_prompt = system_prompt
        self.tools = tools
        self.model = model

    @property
    def model_label(self) -> str:
        """Short model label for the ``ready`` server message."""
        return self.model or self.provider

    @abc.abstractmethod
    async def connect(self) -> None:
        """Open the upstream realtime session. Raise on misconfiguration."""

    @abc.abstractmethod
    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        """Send a chunk of input audio (PCM16 LE, 16 kHz mono)."""

    @abc.abstractmethod
    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        """Send a single input video frame (JPEG)."""

    @abc.abstractmethod
    async def send_text(self, text: str) -> None:
        """Send typed user input as a turn."""

    @abc.abstractmethod
    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        """Return a tool's output to the model to continue the turn."""

    @abc.abstractmethod
    def events(self) -> AsyncIterator[GatewayEvent]:
        """Yield :class:`GatewayEvent` objects until the session closes."""

    async def send_activity_start(self) -> None:
        """Signal that the user began speaking (manual activity detection).

        Gateways using server-side automatic VAD ignore this; manual-VAD
        adapters (e.g. Gemini push-to-talk) open a turn window here. No-op by
        default so existing adapters need no changes.
        """

    async def send_activity_end(self) -> None:
        """Signal that the user stopped speaking (manual activity detection).

        Closes the turn window so the model replies. No-op by default.
        """

    def set_camera_kind(self, kind: str | None) -> None:
        """Tell the gateway which camera is active (``"phone"``, ``"glasses"``,
        a combo like ``"phone+glasses"``, or ``None``).

        Adapters that batch camera frames (e.g. OpenAI Realtime, which attaches
        the latest frame on a turn) use this to size their frame-freshness
        window: photo-trigger smart glasses deliver a still several seconds
        after it is taken, so a phone-sized freshness window would drop it.
        No-op by default; streaming adapters (Gemini) ignore it. Safe to call
        before :meth:`connect` and again mid-session when the camera changes.
        """

    @abc.abstractmethod
    async def interrupt(self) -> None:
        """Barge-in: cancel in-flight generation / TTS playback."""

    @abc.abstractmethod
    async def close(self) -> None:
        """Tear down upstream resources. Must be idempotent."""

    async def __aenter__(self) -> "AIGateway":
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
