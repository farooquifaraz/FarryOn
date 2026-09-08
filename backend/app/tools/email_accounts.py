"""Resolve WHICH mailbox an email tool should use — and refuse to guess.

The client sends every configured mailbox in ``hello.emails`` (carried on
:attr:`ToolContext.emails`). Each email tool takes an optional ``account``
argument, and these helpers turn that into concrete credentials. Kept separate
so the read and send tools share one selection policy.

The policy (live test, 2026-09-08 — the assistant was reading a mailbox the
user had never been asked about):

* **no mailbox** → the user is told none is registered; nothing is read.
* **one mailbox, nothing chosen yet** → the user is asked to confirm THAT
  address before it is touched;
* **two or more, nothing chosen yet** → the user is told both (Primary /
  Secondary) and asked which;
* **a choice named** (``primary``, ``secondary``, a label, an address) → that
  mailbox, and it is remembered for the rest of the session so the next email
  question does not ask again;
* **a choice that names nothing** → asked again, with both spelled out.

An account is therefore never assumed: the tool itself enforces it, so a
model that forgets to ask still cannot read the wrong inbox.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext

#: Spec wording — what the user hears when nothing is registered.
NO_ACCOUNT_MESSAGE = (
    "No email account is registered in the app. Please register an account first."
)

_PRIMARY_WORDS = ("primary", "main", "first", "default")
_SECONDARY_WORDS = ("secondary", "second", "other")


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


def account_role(index: int, accts: list[dict[str, Any]]) -> str:
    """The word the user knows an account by: ``Primary``, ``Secondary``, or —
    past two — its own label."""
    if index == 0:
        return "Primary"
    if index == 1:
        return "Secondary"
    return (accts[index].get("label") or f"Account {index + 1}")


def _address(a: dict[str, Any]) -> str:
    return (a.get("address") or "").strip()


def _label(a: dict[str, Any]) -> str:
    return (a.get("label") or "").strip()


def describe_accounts(accts: list[dict[str, Any]]) -> str:
    """``Primary: 'a@x' and Secondary: 'b@y'`` — the list the user is read."""
    parts = [
        f"{account_role(i, accts)}: '{_address(a)}'" for i, a in enumerate(accts)
    ]
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


def _public(accts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The accounts as the model may see them: role, label, address. Never a
    password."""
    return [
        {"role": account_role(i, accts), "label": _label(a), "address": _address(a)}
        for i, a in enumerate(accts)
    ]


def _match(want: str, accts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Which account ``want`` names, or None if it names nothing clearly."""
    want = want.strip().lower()
    if not want:
        return None
    # Exact label or exact address first — the unambiguous answers.
    for a in accts:
        if _label(a).lower() == want or _address(a).lower() == want:
            return a
    # The words the user is taught: "primary" / "secondary" (in a sentence too:
    # "use my primary account").
    tokens = set(want.replace("'", " ").replace('"', " ").split())
    if tokens & set(_PRIMARY_WORDS):
        return accts[0]
    if tokens & set(_SECONDARY_WORDS) and len(accts) > 1:
        return accts[1]
    # An address or label spoken loosely ("the gmail one", "abc at test").
    for a in accts:
        addr = _address(a).lower()
        if addr and (addr in want or want in addr):
            return a
    for a in accts:
        lab = _label(a).lower()
        if lab and (lab in want or want in lab):
            return a
    return None


def memory_set(memory: dict[str, Any] | None, address: str) -> None:
    if memory is not None:
        memory["address"] = address


def _ask_which(accts: list[dict[str, Any]], *, unclear: str | None) -> dict[str, Any]:
    """The 'which account?' result — first time, or after an unclear answer."""
    listed = describe_accounts(accts)
    if unclear is None:
        if len(accts) == 2:
            say = (
                "Both email accounts are registered. Your registered accounts "
                f"are: {listed}. Please let me know which account I can help "
                "you with."
            )
        else:
            say = (
                f"{len(accts)} email accounts are registered: {listed}. "
                "Please let me know which account I can help you with."
            )
    else:
        if len(accts) == 2:
            say = (
                "Please specify whether you want me to use your "
                f"{account_role(0, accts)} account ({_address(accts[0])}) or "
                f"{account_role(1, accts)} account ({_address(accts[1])})."
            )
        else:
            say = f"Please specify which account to use: {listed}."
    return {
        "ok": False,
        "status": "needs_selection",
        "message": say,
        "accounts": _public(accts),
        "instructions": (
            "Say exactly this to the user and wait for their answer. Do not "
            "read or send anything yet. When they name one — 'primary', "
            "'secondary', a label, or an address — call this tool again with "
            "`account` set to what they said and the user's ORIGINAL request "
            "unchanged. If their answer does not clearly name one, call again "
            "with what they said and you will be given the question to ask."
        ),
    }


def resolve_account(
    ctx: ToolContext, account: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pick the mailbox to use, or say why one cannot be picked yet.

    Returns ``(account_dict, result)`` — exactly one is non-None. When
    ``result`` is set it is a complete tool result (``ok: False`` plus a
    ``status``) for the tool to return as-is:

      * ``no_account``        — nothing registered; the user is told.
      * ``needs_confirmation`` — one mailbox, not yet confirmed this session.
      * ``needs_selection``   — several mailboxes, none chosen (or the choice
        named nothing).

    A mailbox the user has confirmed or chosen is remembered on
    :attr:`ToolContext.email_selection` for the rest of the session, so later
    email requests use it without asking again.
    """
    accts = usable_accounts(ctx)
    if not accts:
        return None, {
            "ok": False,
            "status": "no_account",
            "message": NO_ACCOUNT_MESSAGE,
            "instructions": (
                "Say exactly this to the user. Do not try any other mailbox "
                "or tool for this request."
            ),
        }

    memory = ctx.email_selection if ctx.email_selection is not None else None

    def asked(result: dict[str, Any]) -> tuple[None, dict[str, Any]]:
        if memory is not None:
            memory["asked"] = True
        return None, result

    if account and account.strip():
        # A named account counts only once the user has been ASKED this
        # session. Without that, the model fills the address in from memory
        # — device-seen 2026-09-08: a fresh session, "check my email", and
        # read_email went straight to the mailbox with the address the user
        # had confirmed an hour earlier in another session. The spec wants
        # the question every time; the answer to it is the one named account
        # that is honoured.
        if memory is not None and not memory.get("asked") and not memory.get("address"):
            account = None
        else:
            chosen = _match(account, accts)
            if chosen is None:
                return asked(_ask_which(accts, unclear=account))
            memory_set(memory, _address(chosen))
            return chosen, None

    if memory is None:
        memory = {}

    # Nothing named this call: use what the user already settled on…
    remembered = (memory.get("address") or "").lower()
    if remembered:
        for a in accts:
            if _address(a).lower() == remembered:
                return a, None
        memory.pop("address", None)  # the mailbox is gone; ask afresh

    # …otherwise never assume.
    if len(accts) == 1:
        only = _address(accts[0])
        return asked({
            "ok": False,
            "status": "needs_confirmation",
            "message": (
                f"Only one email account is registered: '{only}'. Should I "
                "continue with this account?"
            ),
            "accounts": _public(accts),
            "instructions": (
                "Ask the user exactly this and wait. If they say yes, call this "
                f"tool again with account='{only}' and the user's ORIGINAL "
                "request unchanged. If they say no, do not access the mailbox; "
                "ask what they would like to do instead."
            ),
        })
    return asked(_ask_which(accts, unclear=None))
