"""Grok (xAI) realtime adapter.

xAI's Voice Agent API is **OpenAI Realtime-compatible**: the same WebSocket
protocol, only a different endpoint (``wss://api.x.ai/v1/realtime``) and API
key. So this adapter is a thin subclass of :class:`OpenAIRealtimeGateway` that
swaps in the xAI key, model, and websocket base URL — everything else (audio
in/out, tool calls, transcripts, barge-in) is inherited unchanged.
"""

from __future__ import annotations

from app.ai.base import ToolSpec
from app.ai.openai_realtime import OpenAIRealtimeGateway
from app.config import get_settings

#: xAI's OpenAI-compatible realtime websocket base (SDK appends ``/realtime``).
_XAI_WS_BASE = "wss://api.x.ai/v1"


class GrokRealtimeGateway(OpenAIRealtimeGateway):
    """Adapter over xAI Grok realtime sessions (OpenAI-compatible)."""

    provider = "grok"

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        super().__init__(
            system_prompt=system_prompt,
            tools=tools,
            model=model or settings.grok_realtime_model,
            api_key=settings.grok_api_key,
            websocket_base_url=_XAI_WS_BASE,
        )
