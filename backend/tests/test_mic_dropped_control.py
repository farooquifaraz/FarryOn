"""The phone's report of audio it threw away while muted.

Purely a measurement: it must be logged with its numbers, must not count as
the user being present (it arrives on its own, whether anyone is there or not),
and must never disturb the session.
"""

from __future__ import annotations

import pytest

from app.agent.tool_engine import ToolEngine
from app.config import get_settings
from app.ws import session as session_mod
from app.ws.session import Session

pytestmark = pytest.mark.asyncio


def _session() -> Session:
    s = Session(
        object(),
        gateway_factory=lambda _p, _s: object(),
        engine=ToolEngine.from_tools([]),
        settings=get_settings(),
    )
    s._gateway = object()
    return s


async def test_the_numbers_are_logged(monkeypatch) -> None:
    seen: list[dict] = []
    real = session_mod.logger.info

    def spy(event, **kw):
        if event == "mic.dropped":
            seen.append(kw)
        return real(event, **kw)

    monkeypatch.setattr(session_mod.logger, "info", spy)
    s = _session()
    await s._dispatch_control(
        {"type": "mic_dropped", "speechMs": 640, "tailMs": 420, "windowMs": 5100, "chunks": 32}
    )
    assert seen == [
        {"session_id": s.session_id, "speech_ms": 640, "tail_ms": 420, "window_ms": 5100, "chunks": 32}
    ]


async def test_it_is_not_a_sign_of_life() -> None:
    """Idle detection must not be reset by a report that arrives on its own."""
    s = _session()
    s._last_activity = 0.0
    await s._dispatch_control({"type": "mic_dropped", "speechMs": 1, "tailMs": 0, "windowMs": 1, "chunks": 1})
    assert s._last_activity == 0.0


async def test_a_malformed_report_is_harmless() -> None:
    s = _session()
    await s._dispatch_control({"type": "mic_dropped"})  # no fields at all
    await s._dispatch_control({"type": "mic_dropped", "speechMs": None, "chunks": "x"[:0]})
