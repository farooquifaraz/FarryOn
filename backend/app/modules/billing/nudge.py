"""The upgrade nudge: who has used up their talk time, and the email that
asks them to pick a plan.

Two free users had used 31 and 35 of their 30 lifetime minutes and nobody —
not the admin, not the users — had been told (Faraz, 2026-09-29). Here:

* :func:`talk_usage` — one user's talk budget as the meters see it (lifetime
  for a trial, this month for a paid plan) and a state the admin can filter
  on: ``ok`` / ``near`` (80 %+) / ``out`` / ``unlimited``.
* :func:`build_upgrade_email` — the email, in the app's own look, with the
  person's numbers and one button to the plans on the website. The plans
  themselves are NOT in the email (Faraz, 2026-09-30): the cards collided in
  Gmail, and the website already shows each region its own prices.
* :func:`send_upgrade_nudge` — the rules: only to a verified address, never
  after an opt-out, at most one every ``upgrade_nudge_gap_days``. Sent by
  itself the first time a person hits their limit (``upgrade_nudge_auto``),
  by hand from the admin, and again by :func:`sweep_out_of_quota` for as
  long as they stay out of talk time — once a gap, so weekly by default
  (``upgrade_nudge_sweep``; Faraz, 2026-09-30).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import repo
from app.db.models import User
from app.logging_conf import get_logger
from app.web.pricing import _COPY as PLAN_COPY, _money, _rupees

logger = get_logger(__name__)

#: From this share of the cap on, a user is "near the limit".
NEAR_SHARE = 0.8


@dataclass(frozen=True, slots=True)
class TalkUsage:
    """One person's talk budget, as the meters count it."""

    plan: str
    used_s: int
    cap_s: int  # -1 = unlimited
    window: str  # "lifetime" for a trial, else "YYYY-MM"
    image_scans: int = 0
    web_searches: int = 0

    @property
    def state(self) -> str:
        if self.cap_s < 0:
            return "unlimited"
        if self.used_s >= self.cap_s:
            return "out"
        if self.cap_s and self.used_s / self.cap_s >= NEAR_SHARE:
            return "near"
        return "ok"

    @property
    def lifetime(self) -> bool:
        return self.window == "lifetime"


async def talk_usage(db: AsyncSession, user_id: int, plan: str | None = None) -> TalkUsage:
    """The talk budget for ``user_id`` over its plan's window — the same
    numbers :meth:`Session._talk_used_seconds` refuses on."""
    from app.modules.billing.service import active_plan_name

    settings = get_settings()
    plan = plan or await active_plan_name(db, user_id)
    key = f"u{user_id}"
    cap = int(settings.plan_limits.get(plan, {}).get("voice_seconds", -1))
    if settings.usage_window(plan) == "lifetime":
        window = "lifetime"
        used = await repo.lifetime_voice_seconds(db, user_key=key)
        scans = await repo.usage_all_time(db, user_key=key, metric="image_scans")
        searches = await repo.usage_all_time(db, user_key=key, metric="web_searches")
    else:
        window = datetime.now(timezone.utc).strftime("%Y-%m")
        used = await repo.usage_this_month(
            db, user_key=key, month=window, metric="voice_seconds"
        )
        scans = await repo.usage_this_month(
            db, user_key=key, month=window, metric="image_scans"
        )
        searches = await repo.usage_this_month(
            db, user_key=key, month=window, metric="web_searches"
        )
    return TalkUsage(
        plan=plan,
        used_s=int(used),
        cap_s=cap,
        window=window,
        image_scans=int(scans),
        web_searches=int(searches),
    )


# ---- Plans to offer -----------------------------------------------------------


def region_for_user(user: User) -> str | None:
    """The price list a person sees: their signup country when it is one we
    sell in regionally (IN, AE), else the global list."""
    country = (user.country or "").upper()
    return country if country in ("IN", "AE") else None


def plans_to_offer(settings: Settings, region: str | None) -> list[str]:
    """The monthly paid plans sold in ``region``, cheapest first."""
    names = [
        n
        for n in settings.plans_for_region(region)
        if settings.plan_price_cents(n) > 0
        and settings.plan_interval(n) == "month"
        and not settings.is_trial_plan(n)
    ]
    return sorted(names, key=settings.plan_price_cents)


def recommended_plan(settings: Settings, plans: list[str]) -> str | None:
    """The one the email's button buys: the plan the website marks popular
    (Plus), else the middle of the list."""
    for n in plans:
        base = n.split("_")[0]
        if PLAN_COPY.get(base, {}).get("popular"):
            return n
        if settings.plan_title(n).lower().startswith("plus"):
            return n
    return plans[len(plans) // 2] if plans else None


def price_label(settings: Settings, plan: str) -> str:
    """``$5`` / ``AED 15`` / ``₹299`` — the plan's own currency, whole where
    the currency is."""
    amount = settings.plan_price_cents(plan) / 100
    currency = settings.plan_currency(plan)
    if currency == "INR":
        return f"₹{_rupees(amount)}"
    if currency == "AED":
        return f"AED {amount:g}"
    return f"${_money(amount)}"


# ---- The email ----------------------------------------------------------------


def opt_out_token(settings: Settings, user_id: int) -> str:
    """A link the email carries that switches the nudge off for this person
    — signed, so nobody can opt someone else out by guessing an id."""
    mac = hmac.new(
        settings.jwt_secret.encode("utf-8"),
        f"nudge-opt-out:{user_id}".encode("utf-8"),
        hashlib.sha256,
    )
    return mac.hexdigest()[:32]


def opt_out_token_ok(settings: Settings, user_id: int, token: str) -> bool:
    return hmac.compare_digest(opt_out_token(settings, user_id), (token or "")[:32])


def _t(text: str) -> str:
    """Escape a text node: <, > and & only, so an apostrophe stays an
    apostrophe (attribute values keep the full escape)."""
    return escape(text, quote=False)


def _first_name(user: User) -> str:
    name = (user.display_name or "").strip()
    if not name and user.email:
        name = user.email.split("@")[0]
    return name.split()[0] if name else "there"


def _minutes(seconds: int) -> int:
    return max(0, round(seconds / 60))


def build_upgrade_email(
    *,
    user: User,
    usage: TalkUsage,
    pricing_url: str,
    app_url: str,
    opt_out_url: str,
) -> tuple[str, str, str]:
    """``(subject, text, html)``. The HTML is self-contained inline styles
    (Gmail strips everything else) in the app's Midnight Aurora colours."""
    name = _first_name(user)
    used_min = _minutes(usage.used_s)
    cap_min = _minutes(usage.cap_s) if usage.cap_s > 0 else 0

    if usage.lifetime:
        subject = f"You've used your {cap_min} free minutes — keep talking with Farry"
        headline = f"{name}, you've used all {cap_min} free minutes"
    else:
        subject = f"You've used this month's {cap_min} minutes — keep talking with Farry"
        headline = f"{name}, this month's {cap_min} minutes are used up"

    button_label = "See the plans"

    # -- text --------------------------------------------------------------
    lines = [
        headline,
        "",
        (
            "Farry has been listening, looking and remembering for you. Pick a "
            "plan and carry on right where you left off — your notes, reminders "
            "and conversations are all still there."
        ),
        "",
        (
            f"So far: {used_min} min talking with Farry · {usage.image_scans} "
            f"things identified · {usage.web_searches} web searches"
        ),
        "",
        f"{button_label}: {pricing_url}",
        f"Open the app: {app_url}",
        "",
        (
            "You're receiving this because you signed up for FarryOn and used "
            "your free talk time. Payments are handled securely by Stripe."
        ),
        f"Don't send me offers like this: {opt_out_url}",
    ]
    text = "\n".join(lines)

    # -- html --------------------------------------------------------------
    html = f"""\
<div style="font-family:Helvetica,Arial,sans-serif;max-width:600px;margin:0 auto;background:#0B0E14;color:#E8EAED;border-radius:20px;overflow:hidden">
  <div style="padding:36px 36px 30px">
    <div style="font-size:22px;font-weight:700;color:#5DCAA5">FarryOn</div>
    <div style="font-size:30px;font-weight:700;line-height:1.2;margin-top:14px">{_t(headline)}</div>
    <div style="font-size:15px;line-height:1.6;color:#B7BCC4;margin-top:14px">Farry has been listening, looking and remembering for you. Pick a plan and carry on right where you left off — your notes, reminders and conversations are all still there.</div>
  </div>
  <div style="padding:0 36px 28px">
    <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:separate;border-spacing:10px 0;margin-left:-10px">
      <tr>
        <td style="padding:14px;border-radius:14px;background:rgba(255,255,255,0.06);width:33%"><div style="font-size:22px;font-weight:700;color:#5DCAA5">{used_min} min</div><div style="font-size:12px;color:#8A9099">talking with Farry</div></td>
        <td style="padding:14px;border-radius:14px;background:rgba(255,255,255,0.06);width:33%"><div style="font-size:22px;font-weight:700;color:#5DCAA5">{usage.image_scans}</div><div style="font-size:12px;color:#8A9099">things identified</div></td>
        <td style="padding:14px;border-radius:14px;background:rgba(255,255,255,0.06);width:33%"><div style="font-size:22px;font-weight:700;color:#5DCAA5">{usage.web_searches}</div><div style="font-size:12px;color:#8A9099">web searches</div></td>
      </tr>
    </table>
  </div>
  <div style="padding:26px 36px;background:#10141B;text-align:center">
    <div style="font-size:18px;font-weight:700">Keep talking with Farry</div>
    <div style="font-size:14px;line-height:1.6;color:#B7BCC4;margin-top:8px">Monthly plans with more talk time, photo scans and web searches. Yearly plans save two months.</div>
    <a href="{escape(pricing_url)}" style="display:block;margin:18px auto 0;max-width:280px;padding:14px 0;border-radius:25px;background:#1D9E75;color:#04342C;text-align:center;font-size:16px;font-weight:700;text-decoration:none">{_t(button_label)}</a>
  </div>
  <div style="padding:26px 36px">
    <div style="font-size:16px;font-weight:700">What you keep with a plan</div>
    <div style="font-size:14px;line-height:1.8;color:#B7BCC4;margin-top:10px">
      • Talk hands-free — through the phone or your smart glasses<br>
      • “What is this?” — Farry looks through the camera and tells you<br>
      • Notes, reminders and your email, read and sent by voice<br>
      • Live translation, heard or typed
    </div>
    <a href="{escape(app_url)}" style="display:inline-block;margin-top:12px;color:#5DCAA5;font-size:14px;font-weight:600">Open the FarryOn app →</a>
  </div>
  <div style="padding:20px 36px 28px;font-size:11.5px;line-height:1.6;color:#6E7580;border-top:1px solid rgba(255,255,255,0.06)">
    You’re receiving this because you signed up for FarryOn with {_t(user.email or '')} and used your free talk time. Payments are handled securely by Stripe. <a href="{escape(opt_out_url)}" style="color:#8A9099">Don’t send me offers like this</a>.
  </div>
</div>"""
    return subject, text, html


# ---- Sending, with the rules -------------------------------------------------


def nudge_blocker(
    settings: Settings, user: User, now: datetime, *, ignore_gap: bool = False
) -> str | None:
    """Why this person must NOT be emailed right now, or None.

    ``ignore_gap`` is the admin's "Send anyway": a human decided this one is
    worth a second email inside the week. The other two blockers are the
    person's own (no address of theirs verified, or they asked us to stop)
    and nobody overrides those.
    """
    if not user.email or user.email_verified_at is None:
        return "no_verified_email"
    if user.upgrade_nudge_opt_out:
        return "opted_out"
    if ignore_gap:
        return None
    gap = timedelta(days=int(settings.upgrade_nudge_gap_days))
    last = user.upgrade_nudged_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < gap:
            return "recent"
    return None


async def send_upgrade_nudge(
    db: AsyncSession,
    *,
    user: User,
    source: str,
    now: datetime | None = None,
    ignore_gap: bool = False,
) -> str:
    """Send the upgrade email to ``user`` if the rules allow; returns
    ``"sent"`` or ``"skipped:<reason>"``. Marks ``upgrade_nudged_at`` on a
    send (flushed, not committed — the caller owns the transaction).

    ``source`` is ``"auto"`` (the cap was just hit) or ``"admin"``.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    blocked = nudge_blocker(settings, user, now, ignore_gap=ignore_gap)
    if blocked:
        return f"skipped:{blocked}"

    usage = await talk_usage(db, user.id)
    base = settings.sso_redirect_base_url.rstrip("/")
    opt_out_url = (
        f"{base}/api/v1/billing/nudge/opt-out?u={user.id}"
        f"&t={opt_out_token(settings, user.id)}"
    )
    subject, text, html = build_upgrade_email(
        user=user,
        usage=usage,
        pricing_url=f"{base}/#pricing",
        app_url=f"{base}/#download",
        opt_out_url=opt_out_url,
    )
    from app.modules.auth.notifications import send_upgrade_email

    send_upgrade_email(to_email=user.email, subject=subject, text=text, html=html)
    user.upgrade_nudged_at = now
    await db.flush()
    logger.info(
        "nudge.sent",
        user_id=user.id,
        source=source,
        state=usage.state,
        plan=usage.plan,
    )
    return "sent"


async def auto_nudge_on_cap(user_id: int) -> None:
    """The cap was just hit in a live session: tell the person once, by
    itself. Runs detached from the session (its own DB session), so nothing
    here can slow or break the refusal the user is already seeing."""
    settings = get_settings()
    if not settings.upgrade_nudge_auto:
        return
    from app.db.base import get_sessionmaker

    try:
        async with get_sessionmaker()() as db:
            user = await db.get(User, user_id)
            if user is None or user.deleted_at is not None:
                return
            if user.upgrade_nudged_at is not None:
                return  # told once by itself; the admin decides after that
            result = await send_upgrade_nudge(db, user=user, source="auto")
            await db.commit()
            logger.info("nudge.auto", user_id=user_id, result=result)
    except Exception as exc:  # noqa: BLE001 - never reaches the session
        logger.warning("nudge.auto_failed", user_id=user_id, error=repr(exc))


# ---- The weekly sweep ---------------------------------------------------------


async def sweep_out_of_quota(now: datetime | None = None) -> dict[str, int]:
    """Email everyone still out of talk time whose gap has passed.

    Runs on its own DB session. Returns ``{"checked", "sent", "skipped"}``.

    Two uvicorn workers run this loop, and a sweep that simply read the
    users and sent would have two sweeps read the same user before either
    had stamped them. So the stamp comes FIRST, as a conditional update —
    ``upgrade_nudged_at`` moves to now only where it is still older than the
    gap — and only the worker whose update changed a row goes on to send.
    A send that then fails gives the stamp back, so the person is not
    silently skipped until next week.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    gap = timedelta(days=int(settings.upgrade_nudge_gap_days))
    from app.db.base import get_sessionmaker
    from app.modules.users import service as users

    checked = sent = skipped = 0
    async with get_sessionmaker()() as db:
        page = 1
        while True:
            items, _total = await users.list_users(
                db, search=None, status_filter="active", role_filter=None,
                kind="app", page=page, page_size=users.PAGE_SIZE_MAX,
            )
            for user, _roles in items:
                checked += 1
                if nudge_blocker(settings, user, now) is not None:
                    continue
                usage = await talk_usage(db, user.id)
                if usage.state != "out":
                    continue
                claimed = await db.execute(
                    update(User)
                    .where(
                        User.id == user.id,
                        or_(
                            User.upgrade_nudged_at.is_(None),
                            User.upgrade_nudged_at < now - gap,
                        ),
                    )
                    .values(upgrade_nudged_at=now)
                    # Decided by the database, not by the ORM re-evaluating
                    # the WHERE in Python (SQLite hands back naive datetimes).
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
                if claimed.rowcount != 1:
                    skipped += 1  # the other worker got there first
                    continue
                try:
                    # The stamp is ours; the send must not refuse it as recent.
                    user.upgrade_nudged_at = None
                    result = await send_upgrade_nudge(db, user=user, source="weekly", now=now)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 - give the claim back
                    await db.rollback()
                    user.upgrade_nudged_at = None
                    await db.commit()
                    logger.warning("nudge.sweep_send_failed", user_id=user.id, error=repr(exc))
                    skipped += 1
                    continue
                if result == "sent":
                    sent += 1
                else:
                    skipped += 1
            if len(items) < users.PAGE_SIZE_MAX:
                break
            page += 1
    logger.info("nudge.sweep", checked=checked, sent=sent, skipped=skipped)
    return {"checked": checked, "sent": sent, "skipped": skipped}


async def nudge_sweeper() -> None:
    """The loop behind the weekly email: a sweep every
    ``upgrade_nudge_sweep_hours``, the first one a few minutes after start
    so a deploy is not what decides when people are emailed. Never raises:
    a failed sweep is logged and the next one still happens."""
    settings = get_settings()
    await asyncio.sleep(120 + random.uniform(0, 60))
    while True:
        try:
            await sweep_out_of_quota()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("nudge.sweep_failed", error=repr(exc))
        await asyncio.sleep(float(settings.upgrade_nudge_sweep_hours) * 3600)


# ---- What the admin sees ------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def latest_app_build() -> int | None:
    """The build the website serves right now (build-info.json beside the
    APKs), or None when there is none to compare against."""
    from app.web.router import _build_number

    return _build_number()


async def user_insight(
    db: AsyncSession, user: User, *, latest_build: int | None, now: datetime | None = None
) -> dict:
    """The three things the admin asked to see per user (2026-09-29): their
    talk budget and where they stand against it, which app build they are on
    and how far behind the website's, and whether — and when — they were
    nudged to upgrade."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    usage = await talk_usage(db, user.id)
    behind = None
    if latest_build and user.last_app_build:
        behind = max(0, int(latest_build) - int(user.last_app_build))
    gap = timedelta(days=int(settings.upgrade_nudge_gap_days))
    last = user.upgrade_nudged_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    next_at = (last + gap) if last is not None else None
    can_send = (
        bool(user.email)
        and user.email_verified_at is not None
        and not user.upgrade_nudge_opt_out
        and (next_at is None or next_at <= now)
    )
    return {
        "quota": {
            "plan": usage.plan,
            "used_s": usage.used_s,
            "cap_s": usage.cap_s,
            "window": usage.window,
            "state": usage.state,
        },
        "app": {
            "build": user.last_app_build,
            "platform": user.last_app_platform,
            "seen_at": _iso(user.last_seen_at),
            "latest_build": latest_build,
            "behind": behind,
            "outdated": bool(behind),
        },
        "nudge": {
            "last_at": _iso(user.upgrade_nudged_at),
            "opt_out": bool(user.upgrade_nudge_opt_out),
            "can_send": can_send,
            "next_at": _iso(next_at) if next_at and next_at > now else None,
        },
    }
