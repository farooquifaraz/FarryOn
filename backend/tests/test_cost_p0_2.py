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


async def test_cap_does_not_clip_a_tools_own_deliberate_limit() -> None:
    """The cap is a backstop for tools that DON'T size their own payload. It
    must stay above the largest deliberate per-tool limit, or it silently clips
    a tool that already thought about this — read_email caps a full body at
    _BODY_CHARS by design, and halving that breaks "read me the whole email"."""
    from app.tools.email_read import _BODY_CHARS

    limit = get_settings().tool_result_max_chars
    assert limit > _BODY_CHARS, (
        f"tool_result_max_chars ({limit}) must exceed read_email's own body "
        f"cap ({_BODY_CHARS}) plus room for its metadata"
    )

    # A realistic full-body read_email result must pass through untouched.
    orch = _orch()
    email = {
        "ok": True, "from": "a@b.com", "subject": "Statement",
        "body": "x" * _BODY_CHARS,
    }
    assert orch._truncate_for_model(email) == email


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


async def test_usage_is_logged_split_by_modality(monkeypatch) -> None:
    """The line that turns a bill into a measurement.

    One number per turn cannot be priced: text input, re-counted audio history
    and camera frames are billed at different rates. The Live API reports the
    split; this pins that we write it down, attributed to a session and a turn.
    """
    from app.ai import gemini as gemini_mod

    seen: list[dict] = []
    real_info = gemini_mod.logger.info

    def spy(event: str, **kw: object) -> None:
        if event == "gemini.usage":
            seen.append(kw)
        return real_info(event, **kw)

    monkeypatch.setattr(gemini_mod.logger, "info", spy)

    from google.genai import types

    gw = GeminiGateway(system_prompt="sys", tools=[])
    gw.session_id, gw.turn_index = "sess-1", 3
    message = types.LiveServerMessage.model_validate(
        {
            "usageMetadata": {
                "promptTokenCount": 8123,
                "responseTokenCount": 140,
                "totalTokenCount": 8263,
                "thoughtsTokenCount": 11,
                "toolUsePromptTokenCount": 22,
                "promptTokensDetails": [
                    {"modality": "TEXT", "tokenCount": 7650},
                    {"modality": "AUDIO", "tokenCount": 473},
                ],
                "responseTokensDetails": [
                    {"modality": "AUDIO", "tokenCount": 140}
                ],
            }
        }
    )
    await gw._handle_message(message)

    assert len(seen) == 1
    line = seen[0]
    assert line["session_id"] == "sess-1" and line["turn"] == 3
    assert line["seq"] == 1
    assert line["input_text"] == 7650 and line["input_audio"] == 473
    assert line["output_audio"] == 140 and line["output_text"] == 0
    assert line["thoughts"] == 11 and line["tool_use"] == 22
    # The pre-existing keys must survive: anything already reading them stays
    # working.
    assert line["turn_total"] == 8263 and line["input"] == 8123


async def test_usage_without_modality_details_still_logs() -> None:
    """An older SDK (or a test double) has no detail lists.

    A missing breakdown must read as zeroes, never as an exception: this line
    runs inside the receive loop, where a raise would take the turn down.
    """
    from app.ai import gemini as gemini_mod

    seen: list[dict] = []
    real_info = gemini_mod.logger.info
    gemini_mod.logger.info = lambda e, **kw: (  # type: ignore[assignment]
        seen.append(kw) if e == "gemini.usage" else None
    ) or real_info(e, **kw)
    try:
        gw = GeminiGateway(system_prompt="sys", tools=[])
        await gw._handle_message(
            SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    total_token_count=100,
                    prompt_token_count=70,
                    response_token_count=30,
                ),
                server_content=None,
                tool_call=None,
            )
        )
    finally:
        gemini_mod.logger.info = real_info  # type: ignore[assignment]

    assert seen and seen[0]["input_audio"] == 0 and seen[0]["input_text"] == 0
    assert seen[0]["session_id"] is None  # untagged gateway is still loggable


async def test_modality_helper_sums_and_tolerates_junk() -> None:
    from app.ai.gemini import _by_modality

    assert _by_modality(None) == {}
    assert _by_modality([]) == {}
    assert _by_modality([SimpleNamespace(modality="AUDIO", token_count=5)]) == {
        "AUDIO": 5
    }
    # Two entries of one modality add up; a missing count reads as zero.
    assert _by_modality(
        [
            SimpleNamespace(modality="TEXT", token_count=3),
            SimpleNamespace(modality="TEXT", token_count=4),
            SimpleNamespace(modality=None, token_count=None),
        ]
    ) == {"TEXT": 7, "UNKNOWN": 0}
