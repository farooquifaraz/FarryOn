"""P0-2 tests: context-window compression config + tool-result truncation.

These pin the two token-cost levers that don't require a live provider:
the Gemini config carries a sliding-window compression block, and oversized
tool results are capped before being fed back to the model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.orchestrator import Orchestrator
from app.ai.gemini import GeminiGateway
from app.config import get_settings

pytestmark = pytest.mark.asyncio


def _orch() -> Orchestrator:
    return Orchestrator(
        engine=None,  # type: ignore[arg-type]
        gateway=None,  # type: ignore[arg-type]
        sessionmaker=None,  # type: ignore[arg-type]
        notify_client=lambda m: None,  # type: ignore[arg-type]
    )


async def test_gemini_config_has_context_compression() -> None:
    """The Live config must carry a sliding-window compression block so the
    session history isn't re-billed in full every turn."""
    gw = GeminiGateway(system_prompt="sys", tools=[])
    cfg = gw._build_config()
    comp = getattr(cfg, "context_window_compression", None)
    assert comp is not None
    sw = getattr(comp, "sliding_window", None)
    assert sw is not None
    assert getattr(sw, "target_tokens", None) == get_settings().context_target_tokens


async def test_small_tool_result_passes_through_unchanged() -> None:
    orch = _orch()
    payload = {"ok": True, "to": "Ahsan", "message": "hi"}
    assert orch._truncate_for_model(payload) == payload


async def test_large_tool_result_is_truncated() -> None:
    orch = _orch()
    limit = get_settings().tool_result_max_chars
    big = {"ok": True, "text": "x" * (limit + 500)}
    out = orch._truncate_for_model(big)
    assert isinstance(out, str)          # collapsed to a capped string
    assert len(out) <= limit + 60        # limit + the "[truncated …]" note
    assert "truncated" in out


async def test_usage_metadata_is_recorded_and_accumulates() -> None:
    """P1-7: per-turn token counts from usage_metadata accumulate on the
    session so the cost log/metric is accurate."""
    gw = GeminiGateway(system_prompt="sys", tools=[])
    msg = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            total_token_count=100, prompt_token_count=70, response_token_count=30
        ),
        server_content=None,
        tool_call=None,
    )
    await gw._handle_message(msg)
    assert gw._tokens_total == 100
    await gw._handle_message(msg)   # a second turn adds on
    assert gw._tokens_total == 200
