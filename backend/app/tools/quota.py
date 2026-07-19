"""Per-user daily quota checks (P0-3, cost protection).

A single ``check_quota`` entry-point the metered tools call before doing paid
work. It is a NO-OP unless ``quota_enforcement_enabled`` is set, so it adds zero
overhead and cannot interrupt anyone until quotas are deliberately turned on
(they only make sense with real per-user auth). When enabled it compares the
plan's daily cap against a DB-backed counter and, if there's room, records the
use; otherwise it returns a friendly ``quota_exceeded`` result the model speaks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.db import repo
from app.logging_conf import get_logger
from app.tools.base import ToolContext

logger = get_logger(__name__)


def _user_key(ctx: ToolContext) -> str:
    """Stable per-user key: the user id, else the session id, else anonymous."""
    return user_key_for(ctx.user_id, ctx.session_id)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def plan_cap(metric: str, plan: str | None = None) -> int:
    """A plan's daily cap for ``metric``: ``-1`` unlimited, ``0`` off.

    Public because the live session meters voice itself — it can't route through
    :func:`check_quota`, which is built around a per-call :class:`ToolContext`
    that a raw audio frame doesn't have. The session resolves the user's plan
    once (async, off :func:`billing.service.active_plan_name`) and passes it in;
    ``plan=None`` falls back to the global default for callers that have no user.
    """
    settings = get_settings()
    return settings.plan_limits.get(
        plan or settings.default_plan, {}
    ).get(metric, -1)


async def _plan_for(ctx: ToolContext) -> str:
    """The caps-bearing plan name for the user behind ``ctx``.

    Resolved from their active subscription, cached on the context so a session
    that fires several metered tools doesn't re-query the plan each time. Any
    lookup failure falls back to the default rather than blocking the tool — a
    quota check that can't read the plan must not become a hard denial.
    """
    if ctx.resolved_plan is not None:
        return ctx.resolved_plan
    settings = get_settings()
    plan = settings.default_plan
    if ctx.session is not None and ctx.user_id is not None:
        try:
            from app.modules.billing import service as billing

            plan = await billing.active_plan_name(ctx.session, ctx.user_id)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("quota.plan_lookup_failed", error=str(e))
    ctx.resolved_plan = plan
    return plan


def user_key_for(user_id: int | None, session_id: str | None) -> str:
    """The ``daily_usage`` key for a user, matching what the tools use.

    Shared so the session and the tools meter the *same* person. Two spellings of
    this would quietly bill one user twice under different keys.
    """
    if user_id is not None:
        return f"u{user_id}"
    return session_id or "anonymous"


async def check_quota(
    ctx: ToolContext, metric: str, *, cost: int = 1
) -> dict[str, Any] | None:
    """Enforce (and record) one unit of a metered resource.

    Returns ``None`` when the action is allowed (and records the usage); returns
    a ``{ok: False, status: "quota_exceeded", message}`` dict when the plan's
    daily cap is reached. A no-op returning ``None`` when enforcement is off.
    """
    settings = get_settings()
    if not settings.quota_enforcement_enabled:
        return None
    if ctx.session is None:
        # No DB to meter against — we can't record a use, so we can't fairly
        # enforce a cap either. Allow it, same fail-open rule the voice meter
        # uses on a DB error: metering protects the bill, and blocking a tool
        # over missing plumbing costs more than the one call it would save. In
        # production a metered tool always has a session; this is the edge.
        return None
    plan = await _plan_for(ctx)
    cap = settings.plan_limits.get(plan, {}).get(metric, -1)
    if cap < 0:  # unlimited on this plan — still record for visibility
        if ctx.session is not None:
            await repo.bump_daily_usage(
                ctx.session, user_key=_user_key(ctx), day=_today(),
                **{metric: cost},
            )
        return None
    label = metric.replace("_", " ")
    if cap == 0:
        return {
            "ok": False, "status": "quota_exceeded",
            "message": f"{label} isn't available on your current plan.",
        }
    key, day = _user_key(ctx), _today()
    row = await repo.get_daily_usage(ctx.session, user_key=key, day=day)
    used = getattr(row, metric, 0) if row else 0
    if used + cost > cap:
        logger.info("quota.exceeded", user_key=key, metric=metric, used=used, cap=cap)
        return {
            "ok": False, "status": "quota_exceeded",
            "message": (
                f"You've reached today's {label} limit on the {plan} plan. "
                "Upgrade to Pro for much more."
            ),
        }
    await repo.bump_daily_usage(
        ctx.session, user_key=key, day=day, **{metric: cost}
    )
    return None
