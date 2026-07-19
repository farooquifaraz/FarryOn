"""P0-3 tests: per-user daily quota enforcement (flag-gated)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.tools import quota
from app.tools.base import ToolContext

pytestmark = pytest.mark.asyncio


async def test_quota_disabled_is_a_noop(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        quota, "get_settings",
        lambda: SimpleNamespace(quota_enforcement_enabled=False),
    )
    ctx = ToolContext(session=db_session, session_id="s1")
    # Never blocks, never touches the DB.
    assert await quota.check_quota(ctx, "image_scans") is None


async def test_quota_allows_up_to_cap_then_blocks(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        quota, "get_settings",
        lambda: SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": 2}},
        ),
    )
    ctx = ToolContext(session=db_session, session_id="s2")
    assert await quota.check_quota(ctx, "image_scans") is None   # use 1/2
    assert await quota.check_quota(ctx, "image_scans") is None   # use 2/2
    blocked = await quota.check_quota(ctx, "image_scans")        # 3rd → denied
    assert blocked is not None
    assert blocked["ok"] is False
    assert blocked["status"] == "quota_exceeded"


async def test_quota_unlimited_plan_never_blocks(db_session, monkeypatch) -> None:
    monkeypatch.setattr(
        quota, "get_settings",
        lambda: SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="pro",
            plan_limits={"pro": {"image_scans": -1}},
        ),
    )
    ctx = ToolContext(session=db_session, session_id="s3")
    for _ in range(5):
        assert await quota.check_quota(ctx, "image_scans") is None


async def test_no_db_session_allows_rather_than_crashes(monkeypatch) -> None:
    # With enforcement ON (now the default) a metered call whose context has no
    # DB session must be allowed, not crash: we can't record a use, so we can't
    # fairly enforce a cap. Regression for 3 web_search tests that broke the
    # moment the enforcement default flipped to True (2026-07-20).
    monkeypatch.setattr(
        quota, "get_settings",
        lambda: SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"web_searches": 1}},
        ),
    )
    ctx = ToolContext(session=None, session_id="no-db")
    for _ in range(5):
        assert await quota.check_quota(ctx, "web_searches") is None
