# FarryOn — Status

**Updated: 2026-07-16.** One page: where the project is, what's next, what's in
the way. Detail lives elsewhere — this is the map, not the territory.

- Test-by-test state → [TEST_PLAN.md](TEST_PLAN.md)
- Architecture → [ADMIN_USER_MODULE_ARCHITECTURE.md](ADMIN_USER_MODULE_ARCHITECTURE.md)

---

## Where we are

**Feature-complete for a local product; not ready for a stranger.** Everything a
user touches works and has been driven on a real phone. What's missing is the
plumbing between "works on Faraz's laptop" and "works for someone in another
city": a database that survives a restart, an email that actually sends, a
payment webhook that can't be forged, and an APK signed with a real key.

```
Backend      266 automated tests      green (75 of them against real Postgres 16)
Mobile       163 automated tests      green
Admin panel   39 automated tests      green (auth gate + API client; pages still eyes-only)
Manual plan   44 / 66 verified
Git          main, everything pushed, CI green
```

---

## The four blockers

Nothing else on this page matters until these do. None is hard; all are unstarted.

| # | Blocker | What actually breaks | Where |
|---|---|---|---|
| 1 | **Postgres** | SQLite's file is wiped on any container rebuild — **every account vanishes on deploy**. Not a Render problem; it follows us to any host. The admin schema also leans on partial unique indexes and tz-aware timestamps that only Postgres enforces. | set `DATABASE_URL` |
| 2 | **Email provider** | Verification and reset links are only *logged*. Nobody can verify an email or recover a password. | `backend/app/modules/auth/notifications.py` — three `send_*` functions, one swap |
| 3 | **Payment webhook** | Gated by a shared secret, not a provider HMAC. Anyone who learns the secret can forge "payment succeeded". | `backend/app/modules/billing/router.py` |
| 4 | **Release keystore** | Release builds are signed with the **debug** key. Play Store rejects it, and a real keystore's different SHA-1 needs its own Android OAuth client or Google sign-in breaks. | `mobile/android/app/build.gradle.kts:39` |

**Hosting is parked** (Faraz's call, 2026-07-16 — no Render, provider TBD). That
parks the six H tests, not blockers #1–#4: Postgres and email are worth wiring
locally now, so the host swap is a config change rather than a discovery.

## Only you can do these

| Thing | Why |
|---|---|
| **Rotate the Google client secret** | `GOCSPX-…` was pasted into chat. Treat it as public. |
| **Remove `CURL_CA_BUNDLE` from the Windows env** | The backend now survives it (`app/core/tls.py`), but it still breaks `pip`. PostgreSQL 18's installer set it to a file it never shipped. |

---

## What's verified

| Area | | Notes |
|---|---|---|
| Admin panel | **10/10** | Every case manual — no CI |
| Cost & quota | **5/5** | Frame gate, voice metering, Vision-403 fallback |
| Live session | **9/10** | Voice, camera, barge-in, reminders, watchdog, fallback |
| Auth | 8/12 | Google + password on device; offline restore pinned by tests |
| Scoping | 4/7 | Two accounts on the API, live server, and a real phone |
| Glasses | 4/6 | Sprint 1–3 hardware-verified |
| Release build | 3/5 | Reminders fire; icon survives R8 |
| Email | 1/5 | Only the connection test |
| Production | 0/6 | Parked with hosting |

---

## What's left, and what each needs

**Needs a person + a phone** — I can't do these alone:

| | | Why me alone isn't enough |
|---|---|---|
| A2 | Google sign-in from the **signup** screen | Needs your Google account tapped |
| A6 | Kill the app, reopen | Someone watching the screen |
| A10 | 2FA sign-in | An account with 2FA set up |
| A11 | Backend down → tap Sign In | Someone watching the screen |
| B5, B6 | Two accounts / two phones at once | A second phone signed in |
| E5 | **Glasses + the new auth** | The glasses. Untested since scoping changed every session. |
| F2–F5 | Read/send named mailbox, bad password | A real mailbox |

**I can do these next, unblocked:**

| | | Why it's worth doing |
|---|---|---|
| **Admin panel tests** | 0 → some | The three bugs I fixed there today could all come back silently |
| D10 | Reminder with notifications denied | Farry says "reminder set" and nothing happens |
| B7 | Ask Farry to *read* notes as B | D5 proved the *write* path scopes; the read tools are a different path |

---

## Today (2026-07-16): 8 real bugs

Every one was found by running the plan, and every one was invisible from the
backend logs alone.

| | Bug | How it looked |
|---|---|---|
| 1 | `CURL_CA_BUNDLE` broke Google sign-in **on every phone** | "Couldn't reach Google" — Google was fine; we never left the laptop. I'd "fixed" this before **in my own shell**, so my backend worked and Faraz's didn't. |
| 2 | Login screen stayed up after a successful sign-in | Faraz reported it twice; I explained it away twice from clean logs. He was right. |
| 3 | Admin panel let a no-role user in | Data was safe (backend refused all 11 routes) but the login screen's promise wasn't kept |
| 4 | App blamed a healthy backend when an account was suspended | "Couldn't load — check the backend." + a Retry that could never work |
| 5 | A suspended account could keep talking over the WebSocket | ~15 min of model budget, after being told to leave |
| 6 | Google API key printed into the log on every Vision/Gemini call | Two keys, four call sites. This repo has committed live creds once already. |
| 7 | Reminders silently did nothing without notification permission | Farry confirms "set", the user misses their appointment |
| 8 | `voice_seconds` cap was decoration | Plans sold 300/900s; nothing counted a second. The most expensive thing we do was the one thing unmetered. |

**The pattern worth remembering:** six of the eight were *silent*. Nothing
errored; the logs looked healthy. They only showed up by driving the real thing
and looking at the screen — which is exactly what the automated suites can't do,
and why the manual plan earns its keep.

---

## How to run things

```bash
# backend (needs BILLING_WEBHOOK_SECRET for the billing routes)
cd backend && BILLING_WEBHOOK_SECRET=local-dev-webhook \
  .venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

cd backend && .venv/Scripts/python.exe -m pytest -q     # 266, ~7 min
cd mobile  && flutter test                               # 132, ~30s
cd mobile  && flutter analyze                            # clean
cd admin   && npm run dev                                # :5173

# release APK onto the paired phone
cd mobile && flutter build apk --release \
  --dart-define=FARRYON_HOST=<this machine's LAN IP> \
  --dart-define=GOOGLE_SERVER_CLIENT_ID=<web client id>
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

**Test accounts** (local DB): `testadmin@example.com` (admin role) and
`devicetest@example.com` — both `correct-horse-1`. The seeded
`admin@farryon.app` super-admin's password came from
`FIRST_SUPER_ADMIN_PASSWORD`, which is no longer in `.env`; nobody knows it.

**Gotcha:** dropping the phone's wifi also drops its wireless ADB, so any test
that kills the network takes the screen with it. Kill the backend instead — same
thing from the app's side.
