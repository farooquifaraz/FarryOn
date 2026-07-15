"""Resolve WHICH mailbox an email tool should use.

The client sends every configured mailbox in ``hello.emails`` (carried on
:attr:`ToolContext.emails`). Each email tool takes an optional ``account``
argument — the label the user named ("Work") — and these helpers turn that into
concrete credentials, defaulting to the primary account. Kept separate so the
read and send tools share one selection policy.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext

_NO_EMAIL = (
    "No email is configured. Ask the user to add their email address and app "
    "password in Settings."
)


def usable_accounts(ctx: ToolContext) -> list[dict[str, Any]]:
    """All mailboxes with both an address and a password, primary first.

    Falls back to the legacy single ``ctx.email`` when ``ctx.emails`` is unset
    (an older client, or the back-compat path).
    """
    raw: list[dict[str, Any]] = list(ctx.emails or [])
    if not raw and ctx.email:
        raw = [ctx.email]
    accts = [
        a
        for a in raw
        if a
        and (a.get("address") or "").strip()
        and (a.get("appPassword") or "").strip()
    ]
    accts.sort(key=lambda a: 0 if a.get("primary") else 1)
    return accts


def account_labels(ctx: ToolContext) -> list[str]:
    """Human labels of every usable mailbox (for prompts / disambiguation)."""
    return [(a.get("label") or a.get("address") or "?") for a in usable_accounts(ctx)]


def resolve_account(
    ctx: ToolContext, account: str | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the mailbox to use.

    Returns ``(account_dict, error_message)`` — exactly one is non-None:
      * no mailbox configured → ``(None, <add-email message>)``
      * ``account`` given and matched (by label, else address substring) →
        ``(dict, None)``
      * ``account`` given but not matched → ``(None, <which-account message>)``
      * ``account`` omitted → the primary → ``(dict, None)``
    """
    accts = usable_accounts(ctx)
    if not accts:
        return None, _NO_EMAIL
    if account and account.strip():
        want = account.strip().lower()
        for a in accts:
            if (a.get("label") or "").strip().lower() == want:
                return a, None
        for a in accts:
            if want in (a.get("address") or "").lower():
                return a, None
        labels = ", ".join(account_labels(ctx)) or "none"
        return None, (
            f"No mailbox called '{account}'. Available accounts: {labels}. "
            "Ask the user which one they mean."
        )
    return accts[0], None  # primary sorts first
