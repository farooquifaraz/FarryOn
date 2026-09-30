"""The upgrade nudge: who has used up their talk time, and the email that
asks them to pick a plan.

Two free users had used 31 and 35 of their 30 lifetime minutes and nobody —
not the admin, not the users — had been told (Faraz, 2026-09-29). Here:

* :func:`talk_usage` — one user's talk budget as the meters see it (lifetime
  for a trial, this month for a paid plan) and a state the admin can filter
  on: ``ok`` / ``near`` (80 %+) / ``out`` / ``unlimited``.
* :func:`build_upgrade_email` — the email, in the app's own look, with the
  person's numbers, the plans sold in their region (allowances and prices
  straight from the catalog, so it can never disagree with the website) and
  one checkout button.
* :func:`send_upgrade_nudge` — the rules: only to a verified address, never
  after an opt-out, at most one every ``upgrade_nudge_gap_days``. Sent by
  itself the first time a person hits their limit (``upgrade_nudge_auto``),
  and by hand from the admin after that.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape

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


def _plan_row(settings: Settings, plan: str) -> dict:
    p = settings.plan_catalog.get(plan, {})
    base = plan.split("_")[0]
    copy = PLAN_COPY.get(base, {})
    return {
        "name": plan,
        "title": settings.plan_title(plan),
        "price": price_label(settings, plan),
        "per": "for 30 days" if settings.plan_is_one_time(plan) else "/month",
        "desc": str(copy.get("desc", "")),
        "minutes": int(p.get("talk_minutes", 0)),
        "scans": int(p.get("image_scans", 0)),
        "searches": int(p.get("web_searches", 0)),
        "extra": [str(x) for x in copy.get("extra", [])],
        "popular": bool(copy.get("popular")),
    }


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
    settings: Settings,
    user: User,
    usage: TalkUsage,
    plans: list[str],
    recommended: str | None,
    checkout_url: str | None,
    pricing_url: str,
    app_url: str,
    opt_out_url: str,
) -> tuple[str, str, str]:
    """``(subject, text, html)``. The HTML is self-contained inline styles
    (Gmail strips everything else) in the app's Midnight Aurora colours."""
    name = _first_name(user)
    used_min = _minutes(usage.used_s)
    cap_min = _minutes(usage.cap_s) if usage.cap_s > 0 else 0
    rows = [_plan_row(settings, p) for p in plans]

    if usage.lifetime:
        subject = f"You've used your {cap_min} free minutes — keep talking with Farry"
        headline = f"{name}, you've used all {cap_min} free minutes"
    else:
        subject = f"You've used this month's {cap_min} minutes — keep talking with Farry"
        headline = f"{name}, this month's {cap_min} minutes are used up"

    button_url = checkout_url or pricing_url
    rec_title = next((r["title"] for r in rows if r["name"] == recommended), None)
    button_label = f"Upgrade to {rec_title}" if rec_title else "See the plans"

    # -- text --------------------------------------------------------------
    lines = [
        headline,
        "",
        "Farry has been listening, looking and remembering for you. Pick a plan "
        "and carry on right where you left off — your notes, reminders and "
        "conversations are all still there.",
        "",
        f"So far: {used_min} min talking with Farry · {usage.image_scans} things "
        f"identified · {usage.web_searches} web searches",
        "",
    ]
    for r in rows:
        star = " (most popular)" if r["name"] == recommended else ""
        lines.append(
            f"{r['title']}{star} — {r['price']} {r['per']}: {r['minutes']} min talk, "
            f"{r['scans']} photo scans, {r['searches']} web searches. "
            + " · ".join(x.replace("&amp;", "&") for x in r["extra"])
        )
    lines += ["", f"{button_label}: {button_url}", f"All plans: {pricing_url}",
              f"Open the app: {app_url}", "",
              "You're receiving this because you signed up for FarryOn and used "
              "your free talk time. Payments are handled securely by Stripe.",
              f"Don't send me offers like this: {opt_out_url}"]
    text = "\n".join(lines)

    # -- html --------------------------------------------------------------
    def card(r: dict) -> str:
        rec = r["name"] == recommended
        border = "2px solid #1D9E75" if rec else "1px solid rgba(255,255,255,0.10)"
        bg = "rgba(29,158,117,0.14)" if rec else "rgba(255,255,255,0.05)"
        badge = (
            '<span style="display:inline-block;margin-left:8px;padding:3px 8px;'
            'border-radius:8px;background:#1D9E75;color:#04342C;font-size:11px;'
            'font-weight:700;vertical-align:middle">MOST POPULAR</span>'
            if rec
            else ""
        )
        extras = " · ".join(r["extra"])
        button = (
            f'<a href="{escape(button_url)}" style="display:block;margin-top:12px;'
            'padding:14px 0;border-radius:25px;background:#1D9E75;color:#04342C;'
            'text-align:center;font-size:16px;font-weight:700;text-decoration:none">'
            f"{_t(button_label)}</a>"
            if rec
            else ""
        )
        return f"""
      <div style="padding:18px;border-radius:16px;background:{bg};border:{border};margin-bottom:12px">
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <span style="font-size:17px;font-weight:700;color:#E8EAED">{_t(r['title'])}{badge}</span>
          <span style="font-size:22px;font-weight:700;color:#E8EAED">{_t(r['price'])}<span style="font-size:13px;color:#8A9099;font-weight:500"> {_t(r['per'])}</span></span>
        </div>
        <div style="font-size:13px;color:#8A9099;margin-top:4px">{_t(r['desc'])}</div>
        <div style="font-size:13.5px;line-height:1.7;color:#D2D6DC;margin-top:6px">{r['minutes']} min talk · {r['scans']} photo scans · {r['searches']} web searches<br>{extras}</div>
        {button}
      </div>"""

    cards = "".join(card(r) for r in rows)
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
  <div style="padding:26px 36px;background:#10141B">
    <div style="font-size:18px;font-weight:700;margin-bottom:14px">Choose how much Farry you need</div>
    {cards}
    <div style="font-size:13px;color:#8A9099;text-align:center">Another plan? <a href="{escape(pricing_url)}" style="color:#5DCAA5">See them all</a> · Yearly plans save two months.</div>
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


async def send_upgrade_nudge(
    db: AsyncSession,
    *,
    user: User,
    source: str,
    now: datetime | None = None,
) -> str:
    """Send the upgrade email to ``user`` if the rules allow; returns
    ``"sent"`` or ``"skipped:<reason>"``. Marks ``upgrade_nudged_at`` on a
    send (flushed, not committed — the caller owns the transaction).

    ``source`` is ``"auto"`` (the cap was just hit) or ``"admin"``.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    if not user.email or user.email_verified_at is None:
        return "skipped:no_verified_email"
    if user.upgrade_nudge_opt_out:
        return "skipped:opted_out"
    gap = timedelta(days=int(settings.upgrade_nudge_gap_days))
    last = user.upgrade_nudged_at
    if last is not None:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if now - last < gap:
            return "skipped:recent"

    usage = await talk_usage(db, user.id)
    region = region_for_user(user)
    plans = plans_to_offer(settings, region)
    rec = recommended_plan(settings, plans)

    checkout_url: str | None = None
    if rec and settings.stripe_secret_key:
        try:
            from app.modules.billing.service import create_payment_link

            checkout_url = (await create_payment_link(db, user=user, plan_name=rec))["url"]
        except Exception as exc:  # noqa: BLE001 - the pricing page still works
            logger.warning("nudge.checkout_link_failed", user_id=user.id, error=repr(exc))

    base = settings.sso_redirect_base_url.rstrip("/")
    opt_out_url = (
        f"{base}/api/v1/billing/nudge/opt-out?u={user.id}"
        f"&t={opt_out_token(settings, user.id)}"
    )
    subject, text, html = build_upgrade_email(
        settings=settings,
        user=user,
        usage=usage,
        plans=plans,
        recommended=rec,
        checkout_url=checkout_url,
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
        recommended=rec,
        checkout=bool(checkout_url),
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
