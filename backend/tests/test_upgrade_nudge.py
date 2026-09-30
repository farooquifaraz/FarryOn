"""The upgrade nudge (app/modules/billing/nudge.py) and what the admin sees.

Two free users were at 31 and 35 of their 30 lifetime minutes and nobody had
been told (2026-09-29). Now: the admin's user list says who is out of talk
time, near it, or on an old app build; an email with their own numbers and
the plans sold in their region goes out once by itself when the cap is hit,
and by hand after that — never to an unverified address, never after an
opt-out, never more than once a week.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.core.security import hash_password
from app.db import base as db_base
from app.db import repo
from app.db.models import User, UserRole
from app.db.seed import seed_roles_and_permissions
from app.main import create_app
from app.modules.auth import notifications
from app.modules.billing import nudge

PASSWORD = "correct-horse-1"
_TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _client() -> TestClient:
    return TestClient(create_app())


async def _seed_admin(email: str) -> None:
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as db:
        roles = await seed_roles_and_permissions(db)
        user = User(
            external_id=f"user:{email}", email=email,
            password_hash=hash_password(PASSWORD), status="active",
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=roles["super_admin"].id))
        await db.commit()


async def _seed_app_user(
    email: str, *, country: str | None = None, verified: bool = True,
    talk_s: int = 0, build: int | None = None, name: str | None = None,
) -> int:
    """An app user (no admin role) with ``talk_s`` of lifetime talk on the
    clock — the free plan's budget is 1800 s."""
    sessionmaker = db_base.get_sessionmaker()
    async with sessionmaker() as db:
        user = User(
            external_id=f"user:{email}", email=email, display_name=name,
            password_hash=hash_password(PASSWORD), status="active",
            email_verified_at=datetime.now(timezone.utc) if verified else None,
            country=country, last_app_build=build,
        )
        db.add(user)
        await db.flush()
        if talk_s:
            await repo.bump_daily_usage(
                db, user_key=f"u{user.id}", day=_TODAY,
                voice_seconds=talk_s, image_scans=6, web_searches=7,
            )
        await db.commit()
        return user.id


def _login(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


@pytest.fixture
def outbox(monkeypatch) -> list[dict]:
    """Every upgrade email 'sent', captured instead of mailed."""
    box: list[dict] = []

    def capture(*, to_email, subject, text, html):
        box.append({"to": to_email, "subject": subject, "text": text, "html": html})

    monkeypatch.setattr(notifications, "send_upgrade_email", capture)
    return box


# ---- The numbers ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_talk_usage_names_the_state_the_admin_filters_on() -> None:
    out = await _seed_app_user("out@example.com", talk_s=1906)
    near = await _seed_app_user("near@example.com", talk_s=1500)
    fine = await _seed_app_user("fine@example.com", talk_s=100)
    async with db_base.get_sessionmaker()() as db:
        a = await nudge.talk_usage(db, out)
        b = await nudge.talk_usage(db, near)
        c = await nudge.talk_usage(db, fine)
    assert (a.state, a.used_s, a.cap_s, a.window) == ("out", 1906, 1800, "lifetime")
    assert b.state == "near" and c.state == "ok"
    assert a.image_scans == 6 and a.web_searches == 7


# ---- The email -----------------------------------------------------------------


def test_the_email_carries_their_numbers_and_one_button_to_the_plans() -> None:
    user = User(id=42, external_id="x", email="sana.k@example.com",
                display_name="Sana Khan", country="IN")
    usage = nudge.TalkUsage(plan="free", used_s=2100, cap_s=1800,
                            window="lifetime", image_scans=6, web_searches=7)
    subject, text, html = nudge.build_upgrade_email(
        user=user, usage=usage,
        pricing_url="https://farryon.test/#pricing",
        app_url="https://farryon.test/#download",
        opt_out_url="https://farryon.test/api/v1/billing/nudge/opt-out?u=42&t=abc",
    )
    assert subject == "You've used your 30 free minutes — keep talking with Farry"
    assert "Sana, you've used all 30 free minutes" in html
    assert "35 min" in html and ">6<" in html and ">7<" in html
    # No plan cards (Faraz, 2026-09-30: they collided in Gmail, and the site
    # already shows each region its own prices) — one button, to the site.
    assert "₹" not in html and "$" not in html and "MOST POPULAR" not in html
    assert 'href="https://farryon.test/#pricing"' in html and "See the plans" in html
    assert "opt-out?u=42&amp;t=abc" in html
    # the plain-text twin says the same things
    assert "35 min talking with Farry" in text
    assert "See the plans: https://farryon.test/#pricing" in text
    assert "Don't send me offers like this: https://farryon.test" in text


def test_the_regions_plans_and_prices_are_what_the_site_sells() -> None:
    s = get_settings()
    assert nudge.plans_to_offer(s, "IN") == ["sathi_in", "plus_in", "pro_in"]
    assert nudge.recommended_plan(s, nudge.plans_to_offer(s, "IN")) == "plus_in"
    assert nudge.plans_to_offer(s, None) == ["lite", "plus", "pro"]
    assert nudge.price_label(s, "lite") == "$5" and nudge.price_label(s, "pro") == "$15"
    assert nudge.price_label(s, "sathi_in") == "₹299"


def test_a_monthly_cap_reads_as_this_month() -> None:
    user = User(id=7, external_id="x", email="omar@example.com", country="US")
    usage = nudge.TalkUsage(plan="lite", used_s=7200, cap_s=7200, window="2026-09")
    subject, _, html = nudge.build_upgrade_email(
        user=user, usage=usage,
        pricing_url="https://x/#pricing", app_url="https://x/#download",
        opt_out_url="https://x/o",
    )
    assert subject.startswith("You've used this month's 120 minutes")
    assert "omar, this month's 120 minutes are used up" in html
    assert 'href="https://x/#pricing"' in html


# ---- The rules -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_nudge_goes_once_and_then_waits_a_week(outbox) -> None:
    uid = await _seed_app_user("once@example.com", talk_s=1906, name="Sana K")
    async with db_base.get_sessionmaker()() as db:
        user = await db.get(User, uid)
        assert await nudge.send_upgrade_nudge(db, user=user, source="admin") == "sent"
        first = user.upgrade_nudged_at
        assert first is not None
        assert await nudge.send_upgrade_nudge(db, user=user, source="admin") == "skipped:recent"
        # a week later it may go again
        later = first + timedelta(days=7, seconds=1)
        assert await nudge.send_upgrade_nudge(db, user=user, source="admin", now=later) == "sent"
    assert [m["to"] for m in outbox] == ["once@example.com"] * 2
    assert "Sana, you've used all 30 free minutes" in outbox[0]["html"]


@pytest.mark.asyncio
async def test_no_verified_address_and_an_opt_out_are_both_respected(outbox) -> None:
    unverified = await _seed_app_user("unv@example.com", verified=False, talk_s=1906)
    opted = await _seed_app_user("opt@example.com", talk_s=1906)
    async with db_base.get_sessionmaker()() as db:
        u = await db.get(User, unverified)
        assert await nudge.send_upgrade_nudge(db, user=u, source="admin") == "skipped:no_verified_email"
        o = await db.get(User, opted)
        o.upgrade_nudge_opt_out = True
        assert await nudge.send_upgrade_nudge(db, user=o, source="admin") == "skipped:opted_out"
    assert outbox == []


@pytest.mark.asyncio
async def test_the_weekly_sweep_emails_whoever_is_still_out_and_due(outbox) -> None:
    # Faraz, 2026-09-30: the email should go again, by itself, every week.
    due = await _seed_app_user("due@example.com", talk_s=1906)
    never = await _seed_app_user("never@example.com", talk_s=1906)
    recent = await _seed_app_user("recent@example.com", talk_s=1906)
    opted = await _seed_app_user("opted@example.com", talk_s=1906)
    fine = await _seed_app_user("fine@example.com", talk_s=100)
    unverified = await _seed_app_user("unv@example.com", talk_s=1906, verified=False)
    now = datetime.now(timezone.utc)
    async with db_base.get_sessionmaker()() as db:
        (await db.get(User, due)).upgrade_nudged_at = now - timedelta(days=8)
        (await db.get(User, recent)).upgrade_nudged_at = now - timedelta(days=2)
        (await db.get(User, opted)).upgrade_nudge_opt_out = True
        await db.commit()

    result = await nudge.sweep_out_of_quota(now=now)

    assert sorted(m["to"] for m in outbox) == ["due@example.com", "never@example.com"]
    assert result == {"checked": 6, "sent": 2, "skipped": 0}
    async with db_base.get_sessionmaker()() as db:
        stamped = (await db.get(User, due)).upgrade_nudged_at
        assert stamped is not None and stamped.replace(tzinfo=timezone.utc) >= now - timedelta(seconds=1)
        assert (await db.get(User, fine)).upgrade_nudged_at is None
        assert (await db.get(User, unverified)).upgrade_nudged_at is None
    # a second sweep the same day finds nobody due
    assert (await nudge.sweep_out_of_quota(now=now))["sent"] == 0
    assert len(outbox) == 2


@pytest.mark.asyncio
async def test_two_workers_sweeping_at_once_send_one_email_not_two(outbox) -> None:
    # Production runs two uvicorn workers, each with its own loop; the stamp
    # is claimed with a conditional update before anything is sent.
    await _seed_app_user("twice@example.com", talk_s=1906)
    now = datetime.now(timezone.utc)
    a, b = await asyncio.gather(nudge.sweep_out_of_quota(now=now), nudge.sweep_out_of_quota(now=now))
    assert [m["to"] for m in outbox] == ["twice@example.com"]
    assert sorted([a["sent"], b["sent"]]) == [0, 1]


@pytest.mark.asyncio
async def test_a_send_that_fails_gives_the_claim_back(outbox, monkeypatch) -> None:
    uid = await _seed_app_user("retry@example.com", talk_s=1906)

    def boom(**_kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notifications, "send_upgrade_email", boom)
    result = await nudge.sweep_out_of_quota()
    assert result["sent"] == 0 and result["skipped"] == 1
    async with db_base.get_sessionmaker()() as db:
        assert (await db.get(User, uid)).upgrade_nudged_at is None, "still due next sweep"


@pytest.mark.asyncio
async def test_the_cap_being_hit_sends_it_by_itself_exactly_once(outbox) -> None:
    uid = await _seed_app_user("auto@example.com", talk_s=1906)
    await nudge.auto_nudge_on_cap(uid)
    await nudge.auto_nudge_on_cap(uid)  # the next refusal, seconds later
    assert len(outbox) == 1
    async with db_base.get_sessionmaker()() as db:
        assert (await db.get(User, uid)).upgrade_nudged_at is not None
    # switched off by the operator: nothing goes
    s = get_settings()
    uid2 = await _seed_app_user("auto2@example.com", talk_s=1906)
    object.__setattr__(s, "upgrade_nudge_auto", False)
    try:
        await nudge.auto_nudge_on_cap(uid2)
    finally:
        object.__setattr__(s, "upgrade_nudge_auto", True)
    assert len(outbox) == 1


@pytest.mark.asyncio
async def test_even_with_stripe_no_checkout_is_made_the_button_is_the_site(outbox, monkeypatch) -> None:
    # The email names no plan, so it must not open a checkout for one either:
    # a Stripe session per nudge would be a charge waiting to be misread.
    from app.modules.billing import service as billing

    uid = await _seed_app_user("stripe@example.com", talk_s=1906, country="AE")
    s = get_settings()
    monkeypatch.setattr(s, "stripe_secret_key", "sk_test_x")
    asked: list[str] = []

    async def fake_link(db, *, user, plan_name):
        asked.append(plan_name)
        return {"url": "https://checkout.stripe.test/cs_ae"}

    monkeypatch.setattr(billing, "create_payment_link", fake_link)
    async with db_base.get_sessionmaker()() as db:
        user = await db.get(User, uid)
        assert await nudge.send_upgrade_nudge(db, user=user, source="auto") == "sent"
    assert asked == []
    assert "checkout.stripe" not in outbox[0]["html"]
    assert "AED 25" not in outbox[0]["html"]
    base = s.sso_redirect_base_url.rstrip("/")
    assert f'href="{base}/#pricing"' in outbox[0]["html"]


# ---- The session stamps the build --------------------------------------------


def test_the_hello_stamps_the_users_build_and_platform() -> None:
    from app.ws.session import Session

    s = Session.__new__(Session)
    user = User(id=5, external_id="user:x", email="x@example.com")
    s._stamp_client(user, {"appVersion": "1.0.0+4557", "abi": "arm64", "platform": "android"})
    assert user.last_app_build == 2557 and user.last_app_platform == "android"
    assert user.last_seen_at is not None
    # an anonymous row is never stamped
    anon = User(id=1, external_id=repo.ANON_EXTERNAL_ID)
    s._stamp_client(anon, {"appVersion": "1.0.0+4557", "platform": "android"})
    assert anon.last_app_build is None


# ---- What the admin sees ---------------------------------------------------------


def test_the_user_list_says_who_is_out_near_or_outdated(monkeypatch) -> None:
    client = _client()
    asyncio.run(_seed_admin("admin@example.com"))
    out = asyncio.run(_seed_app_user("out@example.com", talk_s=1906, build=2546))
    asyncio.run(_seed_app_user("near@example.com", talk_s=1500, build=2560))
    fine = asyncio.run(_seed_app_user("fine@example.com", talk_s=60, build=2560))
    monkeypatch.setattr(nudge, "latest_app_build", lambda: 2560)
    h = _login(client, "admin@example.com")

    r = client.get("/api/v1/users?kind=app&quota=out", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    assert [x["id"] for x in rows] == [out] and r.json()["meta"]["total"] == 1
    row = rows[0]
    assert row["quota"] == {"plan": "free", "used_s": 1906, "cap_s": 1800,
                            "window": "lifetime", "state": "out"}
    assert row["app"]["build"] == 2546 and row["app"]["behind"] == 14 and row["app"]["outdated"]
    assert row["nudge"] == {"last_at": None, "opt_out": False, "can_send": True, "next_at": None}

    r = client.get("/api/v1/users?kind=app&quota=near", headers=h)
    assert [x["email"] for x in r.json()["data"]] == ["near@example.com"]
    r = client.get("/api/v1/users?kind=app&app=outdated", headers=h)
    assert [x["id"] for x in r.json()["data"]] == [out]
    # the unfiltered list still carries the fields for every row
    r = client.get("/api/v1/users?kind=app", headers=h)
    by_id = {x["id"]: x for x in r.json()["data"]}
    assert by_id[fine]["quota"]["state"] == "ok" and by_id[fine]["app"]["behind"] == 0

    r = client.get("/api/v1/users/summary", headers=h)
    assert r.json()["data"] == {
        "app_users": 3, "paying": 0, "out_of_quota": 1, "near_limit": 1,
        "outdated_app": 1, "latest_build": 2560,
    }


def test_the_admin_sends_the_email_by_hand_and_in_bulk(outbox, monkeypatch) -> None:
    client = _client()
    asyncio.run(_seed_admin("admin2@example.com"))
    a = asyncio.run(_seed_app_user("a@example.com", talk_s=1906))
    b = asyncio.run(_seed_app_user("b@example.com", talk_s=1906))
    asyncio.run(_seed_app_user("c@example.com", talk_s=300))  # not out: never targeted
    monkeypatch.setattr(nudge, "latest_app_build", lambda: None)
    h = _login(client, "admin2@example.com")

    r = client.post(f"/api/v1/users/{a}/upgrade-email", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["result"] == "sent" and r.json()["data"]["nudged_at"]
    r = client.post(f"/api/v1/users/{a}/upgrade-email", headers=h)
    assert r.json()["data"] == {"result": "skipped", "reason": "recent",
                                "nudged_at": r.json()["data"]["nudged_at"]}

    r = client.post("/api/v1/users/upgrade-email", headers=h,
                    json={"all_out_of_quota": True})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"sent": [b], "skipped": [{"id": a, "reason": "recent"}]}
    assert [m["to"] for m in outbox] == ["a@example.com", "b@example.com"]

    # the list now shows when, and that it must wait
    r = client.get("/api/v1/users?kind=app&quota=out", headers=h)
    n = {x["id"]: x["nudge"] for x in r.json()["data"]}
    assert n[a]["last_at"] and n[a]["can_send"] is False and n[a]["next_at"]

    # and it is an audited, permissioned action
    r = client.get("/api/v1/audit-logs?page_size=5", headers=h)
    assert any(x["action"] == "billing.upgrade_email" for x in r.json()["data"])
    plain = _login(client, "c@example.com")
    assert client.post(f"/api/v1/users/{a}/upgrade-email", headers=plain).status_code == 403


def test_the_opt_out_link_works_without_signing_in(outbox) -> None:
    client = _client()
    uid = asyncio.run(_seed_app_user("bye@example.com", talk_s=1906))
    s = get_settings()
    good = nudge.opt_out_token(s, uid)
    assert client.get(f"/api/v1/billing/nudge/opt-out?u={uid}&t=wrong").status_code == 404
    r = client.get(f"/api/v1/billing/nudge/opt-out?u={uid}&t={good}")
    assert r.status_code == 200 and "no more upgrade offers" in r.text
    async def _try() -> str:
        async with db_base.get_sessionmaker()() as db:
            return await nudge.send_upgrade_nudge(db, user=await db.get(User, uid), source="auto")
    assert asyncio.run(_try()) == "skipped:opted_out"
    assert outbox == []
