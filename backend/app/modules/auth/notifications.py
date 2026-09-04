"""Transactional email sending for the auth flows.

Real sending happens over plain SMTP (implicit TLS) using the mailbox
configured in ``AUTH_SMTP_*`` — the operator already owns domain mailboxes
(Hostinger), so no third-party mail-provider account is needed. When
``AUTH_SMTP_HOST`` is empty (dev, tests, CI) the functions fall back to the
original behaviour and only LOG the link, so the auth flows never block on
mail infrastructure.

Design constraints that shaped this module:

* Callers sit inside async request handlers, and ``smtplib`` blocks for as
  long as the SMTP conversation takes — so every send runs on a daemon
  thread and NEVER raises into the caller. A user must still be able to
  register when the mail host is down; the failure is logged instead.
* The links point at backend-rendered pages (``/verify-email`` and
  ``/reset-password`` in app/web/router.py) built from
  ``sso_redirect_base_url`` — the public site origin in production.
"""

from __future__ import annotations

import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr

from app.config import get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

_SEND_TIMEOUT_S = 20


def _base_url() -> str:
    return get_settings().sso_redirect_base_url.rstrip("/")


def _send(*, to_email: str, subject: str, text: str, html: str, kind: str) -> None:
    """Queue one mail on a daemon thread; log-only when SMTP is unconfigured."""
    s = get_settings()
    if not s.auth_smtp_host:
        # Dev/test fallback — the link stays discoverable in the log.
        logger.info("auth.email.log_only", kind=kind, to=to_email)
        return

    msg = EmailMessage()
    msg["From"] = formataddr(
        (s.auth_email_from_name, s.auth_email_from or s.auth_smtp_user)
    )
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    def _worker() -> None:
        try:
            with smtplib.SMTP_SSL(
                s.auth_smtp_host, s.auth_smtp_port, timeout=_SEND_TIMEOUT_S
            ) as smtp:
                smtp.login(s.auth_smtp_user, s.auth_smtp_password)
                smtp.send_message(msg)
            logger.info("auth.email.sent", kind=kind, to=to_email)
        except Exception as exc:  # noqa: BLE001 - never break an auth flow
            logger.error(
                "auth.email.send_failed", kind=kind, to=to_email, error=repr(exc)
            )

    threading.Thread(target=_worker, name=f"authmail-{kind}", daemon=True).start()


def _button_html(*, title: str, body: str, link: str, button: str) -> str:
    """One shared, self-contained template — renders fine in every client."""
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;
            padding:28px;background:#0e242b;border-radius:12px;color:#e8f4f2">
  <h2 style="margin:0 0 12px;color:#7fe3c8">{title}</h2>
  <p style="line-height:1.6;color:#cfe6e0">{body}</p>
  <p style="text-align:center;margin:26px 0">
    <a href="{link}" style="background:#18b98a;color:#04211a;text-decoration:none;
       padding:12px 26px;border-radius:8px;font-weight:bold">{button}</a>
  </p>
  <p style="font-size:12px;color:#7fa39b;line-height:1.5">If the button does not
  work, copy this link into your browser:<br>{link}</p>
  <p style="font-size:12px;color:#7fa39b">If you did not request this, you can
  safely ignore this email.</p>
</div>"""


def send_verification_email(*, to_email: str, token: str) -> None:
    link = f"{_base_url()}/verify-email?token={token}"
    _send(
        to_email=to_email,
        kind="verification",
        subject="Verify your FarryOn email",
        text=(
            "Welcome to FarryOn!\n\n"
            f"Confirm your email address by opening this link:\n{link}\n\n"
            "If you did not create a FarryOn account, ignore this email."
        ),
        html=_button_html(
            title="Welcome to FarryOn",
            body="Tap the button below to confirm your email address and "
            "activate your account.",
            link=link,
            button="Verify my email",
        ),
    )


def send_password_reset_email(*, to_email: str, token: str) -> None:
    link = f"{_base_url()}/reset-password?token={token}"
    _send(
        to_email=to_email,
        kind="password_reset",
        subject="Reset your FarryOn password",
        text=(
            "Someone asked to reset the password for this FarryOn account.\n\n"
            f"Set a new password here:\n{link}\n\n"
            "If this wasn't you, ignore this email — nothing changes."
        ),
        html=_button_html(
            title="Reset your password",
            body="Tap the button below to choose a new password for your "
            "FarryOn account.",
            link=link,
            button="Set a new password",
        ),
    )


def send_invite_email(*, to_email: str, token: str) -> None:
    """An admin-invited user's "set your password" link.

    Reuses the same opaque-token mechanics as a password reset (see
    app/modules/users/service.py::invite_user) — the token is a
    PasswordResetToken row; only the email copy differs.
    """
    link = f"{_base_url()}/reset-password?token={token}&invite=1"
    _send(
        to_email=to_email,
        kind="invite",
        subject="You've been invited to FarryOn",
        text=(
            "You've been invited to FarryOn.\n\n"
            f"Choose your password to activate the account:\n{link}"
        ),
        html=_button_html(
            title="You're invited to FarryOn",
            body="An administrator created a FarryOn account for this email. "
            "Choose your password to activate it.",
            link=link,
            button="Activate my account",
        ),
    )
