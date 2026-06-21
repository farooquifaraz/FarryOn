"""Factory that builds an :class:`~app.ai.base.AIGateway` from settings.

Keeps provider selection in one place and ensures provider SDK imports stay
lazy: the Gemini/OpenAI adapter modules import their SDKs only inside
``connect``, so this factory can construct any gateway without the SDK present.
"""

from __future__ import annotations

from app.ai.base import AIGateway, ToolSpec
from app.config import Settings, get_settings
from app.prompts.system import build_system_prompt


def _to_tool_specs(schemas: list[dict]) -> list[ToolSpec]:
    """Convert exported tool schema dicts into :class:`ToolSpec` objects."""
    return [
        ToolSpec(
            name=s["name"],
            description=s["description"],
            parameters=s["parameters"],
        )
        for s in schemas
    ]


def build_gateway(
    tool_schemas: list[dict],
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    system_prompt: str | None = None,
) -> AIGateway:
    """Construct a gateway for the given (or configured) provider.

    Args:
        tool_schemas: Tool schemas (from ``ToolEngine.export_schemas``) to
            expose to the model for function calling.
        settings: Settings to use (defaults to the cached global settings).
        provider: Provider to build (``gemini`` | ``openai`` | ``grok`` |
            ``mock``). When ``None`` falls back to ``settings.ai_provider`` —
            this is how a client picks its provider per-session.
        system_prompt: System instruction for the assistant.

    Returns:
        An unconnected :class:`AIGateway`. Call ``await gateway.connect()``.

    Raises:
        ValueError: If the provider is not a recognized value.
    """
    settings = settings or get_settings()
    tools = _to_tool_specs(tool_schemas)
    provider = (provider or settings.ai_provider).lower()
    if system_prompt is None:
        system_prompt = build_system_prompt()

    if provider == "mock":
        from app.ai.mock import MockGateway

        return MockGateway(system_prompt=system_prompt, tools=tools)

    if provider == "gemini":
        from app.ai.gemini import GeminiGateway

        return GeminiGateway(system_prompt=system_prompt, tools=tools)

    if provider == "openai":
        from app.ai.openai_realtime import OpenAIRealtimeGateway

        return OpenAIRealtimeGateway(system_prompt=system_prompt, tools=tools)

    if provider == "grok":
        from app.ai.grok import GrokRealtimeGateway

        return GrokRealtimeGateway(system_prompt=system_prompt, tools=tools)

    raise ValueError(f"unknown AI provider: {provider!r}")
