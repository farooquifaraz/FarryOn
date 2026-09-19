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
        lambda: SimpleNamespace(quota_enforcement_enabled=False,
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
        ),
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
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
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
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
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
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
        ),
    )
    ctx = ToolContext(session=None, session_id="no-db")
    for _ in range(5):
        assert await quota.check_quota(ctx, "web_searches") is None


async def test_a_plan_config_does_not_know_is_not_unlimited(monkeypatch) -> None:
    # The hole this closes: `premium` was retired from plan_limits but stayed in
    # the plans table, so anyone still subscribed to it resolved to a cap of -1
    # — unlimited voice, unlimited scans, unbounded bill. An unknown plan must
    # fall back to the default tier's caps, never to "no limit".
    settings = SimpleNamespace(
        quota_enforcement_enabled=True,
        default_plan="free",
        plan_limits={"free": {"voice_seconds": 180, "image_scans": 2}},
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
        )
    monkeypatch.setattr(quota, "get_settings", lambda: settings)

    assert quota.plan_cap("voice_seconds", "premium") == 180
    assert quota.plan_cap("image_scans", "some-operator-plan") == 2
    # A known plan is untouched.
    assert quota.plan_cap("voice_seconds", "free") == 180


async def test_an_unknown_plan_still_blocks_at_the_default_cap(
    db_session, monkeypatch
) -> None:
    settings = SimpleNamespace(
        quota_enforcement_enabled=True,
        default_plan="free",
        plan_limits={"free": {"image_scans": 1}},
            # Paid plans spend their caps over a month (Settings.usage_window).
            usage_window=lambda plan: "month",
        )
    monkeypatch.setattr(quota, "get_settings", lambda: settings)

    ctx = ToolContext(session=db_session, session_id="s-unknown")
    ctx.resolved_plan = "premium"  # retired plan, no caps in config

    assert await quota.check_quota(ctx, "image_scans") is None  # 1/1
    blocked = await quota.check_quota(ctx, "image_scans")
    assert blocked is not None, "an unknown plan must not mean unlimited"
    assert blocked["status"] == "quota_exceeded"


async def test_a_signed_out_caller_is_one_pool_not_one_pool_per_session(db_session, monkeypatch) -> None:
    """Keyed by session id, every reconnect was a fresh quota: a trial's
    one-time budget reset as often as the app reopened. A caller with no
    user now meters under the single anonymous key whatever the session,
    and a signed-in user still meters under their own."""
    monkeypatch.setattr(
        quota, "get_settings",
        lambda: SimpleNamespace(
            quota_enforcement_enabled=True,
            default_plan="free",
            plan_limits={"free": {"image_scans": 2}},
            usage_window=lambda plan: "month",
        ),
    )
    assert quota.user_key_for(None, "session-a") == quota.user_key_for(None, "session-b") == quota.ANONYMOUS_KEY
    assert quota.user_key_for(7, "session-a") == "u7"
    first = ToolContext(session=db_session, session_id="session-a")
    second = ToolContext(session=db_session, session_id="session-b")  # "a reconnect"
    assert await quota.check_quota(first, "image_scans") is None
    assert await quota.check_quota(second, "image_scans") is None
    blocked = await quota.check_quota(second, "image_scans")
    assert blocked is not None and blocked["status"] == "quota_exceeded"
    # A signed-in user is untouched by the anonymous pool.
    user = ToolContext(session=db_session, session_id="session-c", user_id=7)
    assert await quota.check_quota(user, "image_scans") is None
