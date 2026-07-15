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
    if ctx.user_id is not None:
        return f"u{ctx.user_id}"
    return ctx.session_id or "anonymous"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    plan = settings.default_plan
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
