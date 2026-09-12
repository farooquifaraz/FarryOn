"""``inbox_summary`` and ``mark_email_read`` tools.

Both are built on the IMAP plumbing in :mod:`app.tools.email_read`; they
exist because two things kept going wrong in live use:

* **Counts.** ``read_emails`` returns the newest ten, and the assistant said
  "you got ten emails" whatever the real number was. ``inbox_summary``
  reports the true totals (how many matched, how many are unread, how many
  the inbox holds) and only then the messages, ranked by importance.
* **Triage.** "Anything important?" needs evidence, not a guess. Every
  message carries the importance level and the reasons the scorer found
  (see :mod:`app.tools.email_triage`); the model turns that into a
  three-line spoken summary.

``mark_email_read`` is the one write the reader gets: flip ``\\Seen`` on one
message (by uid or the newest match) or on everything matching a filter. It
is reversible, so it is the one email action that needs no confirmation.
"""

from __future__ import annotations

import asyncio
import imaplib
from collections import Counter
from typing import Any

from app.logging_conf import get_logger
from app.tools import email_read
from app.tools.base import Tool, ToolContext
from app.tools.email_accounts import resolve_account
from app.tools.email_triage import rank

logger = get_logger(__name__)

#: The summary scans more mail than the list view, but reads less of each:
#: headers + the first few KB is enough for a snippet and the triage signals.
_SUMMARY_LIMIT = 30
_SUMMARY_FETCH_BYTES = 12_288
_SUMMARY_SNIPPET = 110
_SUMMARY_SUBJECT = 90
_TOP_LISTED = 6
_OTHERS_LISTED = 10

_RANGES = ["today", "yesterday", "week", "month"]
_CATEGORIES = [
    "promotions", "social", "updates", "forums", "primary",
    "important", "unread", "starred",
]


def _compact(item: dict[str, Any], *, with_snippet: bool) -> dict[str, Any]:
    """The few fields the model needs to talk about a message.

    The ones worth replying to (``with_snippet``) carry the address, date and
    a snippet; the rest are a line each — the whole result is re-billed every
    turn, and the model can ``read_email(uid)`` anything it wants more of.
    """
    out: dict[str, Any] = {
        "uid": item.get("uid"),
        "from": item.get("from_name") or item.get("from_email"),
        "subject": (item.get("subject") or "")[:_SUMMARY_SUBJECT],
    }
    if item.get("unread"):
        out["unread"] = True
    if with_snippet:
        out["from_email"] = item.get("from_email")
        if item.get("date"):
            out["date"] = item["date"]
        if item.get("snippet"):
            out["snippet"] = item["snippet"][:_SUMMARY_SNIPPET]
    if item.get("importance_reasons"):
        out["why"] = item["importance_reasons"]
    return out


def _count_word(n: int, has_more: bool) -> str:
    return f"more than {n}" if has_more else str(n)


class InboxSummaryTool(Tool):
    """Totals + triage for a time window, ranked most-important first."""

    name = "inbox_summary"
    description = (
        "Overview of the user's inbox for a time window: the TRUE number of "
        "emails received (not just the ones fetched), how many are unread, "
        "which are critical or important and why, who wrote most, and how "
        "many are newsletters. Use for 'summarise my inbox', 'anything "
        "important / urgent?', 'how many emails did I get', 'what did I "
        "miss'. Read-only. Speak it as a short spoken summary, not a list."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "range": {
                "type": "string",
                "enum": _RANGES,
                "description": "Time window (default today; if today is empty "
                "the tool widens to this week and says so).",
            },
            "category": {
                "type": "string",
                "enum": _CATEGORIES,
                "description": "Optional: only this kind of email.",
            },
            "query": {
                "type": "string",
                "description": "Optional sender or keyword filter.",
            },
            "account": {
                "type": "string",
                "description": "Which mailbox, as the user said it: 'primary', "
                "'secondary', a label or an address. Omit on the first email "
                "request of a session and the tool tells you what to ask.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account, ask = resolve_account(ctx, (kwargs.get("account") or "").strip() or None)
        if ask:
            return ask
        host, address, password, label = email_read._imap_creds(account)
        range_ = kwargs.get("range") or "today"
        category = kwargs.get("category") or None
        query = (kwargs.get("query") or "").strip() or None

        async def fetch(rng: str) -> email_read.MailPage:
            return await email_read._fetch_with_retry(
                host, address, password, _SUMMARY_LIMIT, query, category, rng,
                False, fetch_bytes=_SUMMARY_FETCH_BYTES, with_unread=True,
            )

        widened: str | None = None
        try:
            page = await fetch(range_)
            if not page and range_ == "today" and not query and not category:
                page = await fetch("week")
                widened = "week"
        except imaplib.IMAP4.error as exc:
            logger.warning("inbox_summary.auth_failed", error=str(exc))
            return email_read._auth_message(label)
        except UnicodeEncodeError:
            return {
                "ok": False,
                "message": "I can only search mail by English-letter keywords.",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox_summary.failed", error=str(exc))
            return {
                "ok": False,
                "message": (
                    f"Couldn't reach {label} right now. Tell the user checking "
                    "email failed — do NOT say the inbox is empty."
                ),
            }
        email_read.remember_threads(ctx, page, address)

        total = getattr(page, "total", len(page))
        fetched = len(page)
        has_more = total > fetched
        ranked = rank(list(page))
        critical = [_compact(e, with_snippet=True) for e in ranked
                    if e.get("importance") == "critical"][:_TOP_LISTED]
        important = [_compact(e, with_snippet=True) for e in ranked
                     if e.get("importance") == "high"][:_TOP_LISTED]
        rest = [e for e in ranked if e.get("importance") not in ("critical", "high")]
        others = [_compact(e, with_snippet=False) for e in rest[:_OTHERS_LISTED]]
        senders = Counter(
            (e.get("from_name") or e.get("from_email") or "?") for e in page
        )
        top_senders = [
            {"name": name, "count": n} for name, n in senders.most_common(3) if n >= 2
        ]
        newsletters = sum(1 for e in page if e.get("_bulk"))
        unread_total = getattr(page, "unread_total", None)
        if unread_total is None:
            unread_total = sum(1 for e in page if e.get("unread"))

        effective_range = widened or range_
        count_phrase = _count_word(fetched if has_more else total, has_more)
        parts = [
            f"Say the count as '{count_phrase} emails' for {effective_range}"
            + (f" (the total is {total})" if has_more else "")
            + f", {unread_total} unread."
        ]
        if widened:
            parts.append("Nothing arrived today, so this covers the week — say so.")
        if critical or important:
            parts.append(
                "Then the critical and important ones: sender, the gist, and "
                "why they matter (use `why`). Then anything that needs a reply."
            )
        else:
            parts.append("Nothing looks urgent — say that plainly.")
        parts.append(
            "At most three short spoken points, no numbering, no reading every "
            "email. Offer to read any one in full (use its uid with read_email)."
        )

        # The instruction and the counts come BEFORE the lists: a result the
        # orchestrator has to clip for the model loses its tail, and the tail
        # must be the twelfth "other" mail, never the guidance or the totals.
        result: dict[str, Any] = {
            "ok": True,
            "account": label,
            "range": effective_range,
            "total": total,
            "has_more": has_more,
            "fetched": fetched,
            "unread": unread_total,
            "_instruction": " ".join(parts),
        }
        if widened:
            result["widened_from"] = "today"
        inbox_total = getattr(page, "inbox_total", None)
        inbox_unread = getattr(page, "inbox_unread", None)
        if inbox_total is not None:
            result["inbox_total"] = inbox_total
        if inbox_unread is not None:
            result["inbox_unread"] = inbox_unread
        result.update({
            "newsletters": newsletters,
            "top_senders": top_senders,
            "others_not_listed": max(0, len(rest) - len(others)),
            "critical": critical,
            "important": important,
            "others": others,
        })
        return result


class MarkEmailReadTool(Tool):
    """Mark one email (or every match) as read / unread."""

    name = "mark_email_read"
    description = (
        "Mark an email as read — or unread with unread=true. Pick it by uid "
        "(from read_emails / read_email / inbox_summary) or by a sender / "
        "subject keyword (newest match). With all=true, every email matching "
        "the filters is marked (e.g. all promotions this week). Reversible, so "
        "no confirmation is needed — but only do it when the user asks."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "uid": {"type": "string", "description": "The email's uid."},
            "query": {
                "type": "string",
                "description": "Sender or subject keyword, if no uid.",
            },
            "category": {
                "type": "string",
                "enum": _CATEGORIES,
                "description": "With all=true: which kind of emails.",
            },
            "range": {
                "type": "string",
                "enum": _RANGES,
                "description": "Search window (default week).",
            },
            "unread": {
                "type": "boolean",
                "description": "true = mark as UNREAD instead of read.",
            },
            "all": {
                "type": "boolean",
                "description": "true = every email matching query / category / "
                "range, not just the newest one.",
            },
            "account": {
                "type": "string",
                "description": "Which mailbox, as the user said it. Omit on the "
                "first email request of a session and the tool tells you what "
                "to ask.",
            },
        },
    }

    async def run(self, ctx: ToolContext, **kwargs: Any) -> dict[str, Any]:
        account, ask = resolve_account(ctx, (kwargs.get("account") or "").strip() or None)
        if ask:
            return ask
        host, address, password, label = email_read._imap_creds(account)
        uid = str(kwargs.get("uid") or "").strip() or None
        query = (kwargs.get("query") or "").strip() or None
        category = kwargs.get("category") or None
        range_ = kwargs.get("range") or "week"
        seen = not bool(kwargs.get("unread"))
        all_matching = bool(kwargs.get("all"))
        if not uid and not query and not (all_matching and (category or range_)):
            return {
                "ok": False,
                "message": "Which email? Give me the sender or subject.",
            }
        try:
            done = await asyncio.to_thread(
                email_read.set_seen, host, address, password, uid=uid,
                query=query, category=category, range_=range_, seen=seen,
                all_matching=all_matching,
            )
        except imaplib.IMAP4.error as exc:
            logger.warning("mark_email_read.failed", error=str(exc))
            return email_read._auth_message(label)
        except UnicodeEncodeError:
            return {
                "ok": False,
                "message": "I can only search mail by English-letter keywords.",
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("mark_email_read.failed", error=str(exc))
            return {"ok": False, "message": "Couldn't update that email right now."}
        state = "read" if seen else "unread"
        if not done.get("count"):
            return {"ok": False, "message": "No matching email found."}
        result: dict[str, Any] = {
            "ok": True, "account": label, "marked": state,
            "count": done["count"], "uids": done.get("uids", []),
        }
        if done.get("subject") is not None:
            result["subject"] = done["subject"]
        if done.get("from"):
            result["from"] = done["from"]
        logger.info("mark_email_read.done", account=label, count=done["count"], state=state)
        return result
