"""Transactional email sending for the auth flows.

No email provider is wired into FarryOn yet (the existing `send_email`/
`read_emails` tools act on the END USER's own Gmail via OAuth — not suited
for system transactional mail). Until a provider (Postmark/Resend/SES/etc.)
is chosen, links are logged at INFO level so they're usable in dev/staging
without blocking the auth flow on an unrelated integration decision.

Swap point: replace the body of :func:`send_verification_email` and
:func:`send_password_reset_email` with a real provider call. Callers
(app/modules/auth/service.py) don't need to change.
"""

from __future__ import annotations

from app.logging_conf import get_logger

logger = get_logger(__name__)


def send_verification_email(*, to_email: str, token: str) -> None:
    logger.info("auth.email.verification_link", to=to_email, token=token)


def send_password_reset_email(*, to_email: str, token: str) -> None:
    logger.info("auth.email.password_reset_link", to=to_email, token=token)


def send_invite_email(*, to_email: str, token: str) -> None:
    """An admin-invited user's "set your password" link.

    Reuses the same opaque-token mechanics as a password reset (see
    app/modules/users/service.py::invite_user) — the token is a
    PasswordResetToken row; only the email copy differs.
    """
    logger.info("auth.email.invite_link", to=to_email, token=token)
