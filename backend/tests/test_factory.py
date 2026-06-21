"""Tests for the gateway factory's provider selection.

The factory must build any provider's adapter *without* importing the provider
SDK (the SDK imports are deferred to ``connect``). These tests lock that in and
cover every branch — including the per-session ``provider`` override and the
error for an unknown provider — none of which were previously exercised.
"""

from __future__ import annotations

import pytest

from app.ai.factory import build_gateway
from app.ai.gemini import GeminiGateway
from app.ai.grok import GrokRealtimeGateway
from app.ai.mock import MockGateway
from app.ai.openai_realtime import OpenAIRealtimeGateway

# A single tool schema in the shape ``ToolEngine.export_schemas`` produces.
_SCHEMAS = [
    {"name": "add_note", "description": "Add a note.", "parameters": {"type": "object"}}
]


def test_default_provider_is_mock_in_tests() -> None:
    """With AI_PROVIDER=mock (conftest), the default build is the mock gateway."""
    gw = build_gateway(_SCHEMAS)
    assert isinstance(gw, MockGateway)
    # The tool schema is converted into a ToolSpec exposed to the model.
    assert [t.name for t in gw.tools] == ["add_note"]


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("mock", MockGateway),
        ("gemini", GeminiGateway),
        ("openai", OpenAIRealtimeGateway),
        ("grok", GrokRealtimeGateway),
        ("OpenAI", OpenAIRealtimeGateway),  # case-insensitive
    ],
)
def test_provider_override_selects_adapter(provider, expected) -> None:
    """A per-session ``provider`` override picks the matching adapter class."""
    gw = build_gateway(_SCHEMAS, provider=provider)
    assert isinstance(gw, expected)


def test_grok_inherits_openai_adapter_with_xai_endpoint() -> None:
    """Grok is an OpenAI-compatible subclass pointed at the xAI endpoint."""
    gw = build_gateway(_SCHEMAS, provider="grok")
    assert isinstance(gw, OpenAIRealtimeGateway)  # inheritance
    assert gw.provider == "grok"
    assert gw._ws_base_url == "wss://api.x.ai/v1"


def test_unknown_provider_raises() -> None:
    """An unrecognized provider name is a clear ValueError, not a silent default."""
    with pytest.raises(ValueError, match="unknown AI provider"):
        build_gateway(_SCHEMAS, provider="does-not-exist")


def test_custom_system_prompt_is_passed_through() -> None:
    """An explicit system prompt overrides the default build."""
    gw = build_gateway(_SCHEMAS, provider="mock", system_prompt="be terse")
    assert gw.system_prompt == "be terse"
