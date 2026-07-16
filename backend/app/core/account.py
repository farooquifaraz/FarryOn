"""One rule for whether an access token may still be used, for every door.

The REST side and the WebSocket side both have to answer the same question — *is
this token's account still allowed in?* — and they were answering it differently:
``get_current_user`` checked the soft-delete, the force-logout watermark and the
account status, while the live session checked only the soft-delete. So an admin
could suspend someone and watch them keep talking to Farry on an unexpired token,
spending the operator's model budget, because the socket never asked.

Hence one function, called from both. A rule that exists in two places is a rule
that will be enforced in one.
"""

from __future__ import annotations

from datetime import timezone

from app.db.models import User


def token_rejection(user: User | None, *, issued_at: float | int) -> str | None:
    """Why ``user``'s token must be refused, or ``None`` if it's good.

    ``issued_at`` is the token's ``iat`` claim (epoch seconds). The returned
    string is an error code the caller renders however suits it — REST turns it
    into an envelope, the socket into a close.
    """
    if user is None or user.deleted_at is not None:
        return "UNAUTHENTICATED"

    if user.tokens_revoked_before is not None:
        revoked_before = user.tokens_revoked_before
        if revoked_before.tzinfo is None:
            # SQLite round-trips DateTime(timezone=True) as naive — but the
            # value was always written as UTC, so a naive read must be re-tagged
            # before .timestamp(), which would otherwise assume local time and
            # silently shift by the machine's UTC offset.
            revoked_before = revoked_before.replace(tzinfo=timezone.utc)
        if issued_at < revoked_before.timestamp():
            return "SESSION_REVOKED"

    if user.status in ("suspended", "deactivated"):
        return "USER_SUSPENDED"

    return None
