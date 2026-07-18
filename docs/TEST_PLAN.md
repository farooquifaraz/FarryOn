# FarryOn — Test Plan

What still needs proving before real users, and what is already proven.

> One-page summary: **[STATUS.md](STATUS.md)**. This file is the detail.

**The automated suites are not repeated here.** 266 backend tests (`pytest`) and
132 mobile tests (`flutter test`) run in seconds and cover the logic. This
document is for what they *can't* reach: a real device, real hardware, real
providers, real money, and the admin panel — which has **zero** automated tests.

Legend: ☐ not done · ☑ done+verified (date) · ⚠ blocked · ✗ dropped

---

## Part 1 — What's pending (work, not tests)

### Blockers before a single real user

| # | Thing | Why it blocks | Where |
|---|---|---|---|
| 1 | **Postgres — wherever you host** | Not a Render problem, a *SQLite* problem: the file is wiped on every container rebuild, so accounts vanish. The admin module's schema also uses partial unique indexes and tz-aware timestamps that only Postgres enforces — CI already runs those 75 tests against Postgres 16 and they pass. | `render.yaml` is now moot; the setting that matters is `DATABASE_URL` |
| 2 | **Email provider** | Verification + reset links are only *logged*, never sent. Nobody can verify an email or recover a password. | `backend/app/modules/auth/notifications.py` — the three `send_*` functions are the swap point |
| 3 | **Payment provider** | The webhook is gated by a shared secret, not a provider HMAC. Anyone who learns the secret can forge "payment succeeded". | `backend/app/modules/billing/router.py` |
| 4 | **Release keystore** | Release builds are signed with the **debug** key. Play Store will reject it, and a real keystore's different SHA-1 needs its own Android OAuth client or Google sign-in breaks. | `mobile/android/app/build.gradle.kts:39` |

### Your action (I can't do these)

| # | Thing | Why |
|---|---|---|
| 5 | **Reset the Google client secret** | `GOCSPX-…` was pasted into chat. I never stored it, but treat it as public. |
| 6 | ~~Remove `CURL_CA_BUNDLE`~~ — **fixed in code 2026-07-16** | It points at a file PostgreSQL never shipped, and it broke Google sign-in on every phone: the backend couldn't fetch Google's certs, so the app said "Couldn't reach Google". `app/core/tls.py` now drops it at startup. Still worth removing from the Windows env — it breaks `pip` too, which we can't fix from here. |
| 7 | ~~Decide about the push~~ — **done 2026-07-16** | 112 commits merged to `main` via PR #1; `main` is the default branch. |

### Feature backlog

| # | Thing | State |
|---|---|---|
| 8 | Glasses Sprint 2 (vendor `.aar` drop-in) | Waiting on your "stub test pass" go |
| 9 | Cost optimization P0-2, P0-3, P1-5, P1-7, B1–B5 | P0-1 + Vision-403 done |
| 10 | Roadmap #4 — Location | Tool exists; needs a real GPS device test |
| 11 | Official Google "G" asset | Currently hand-drawn. Google's branding rules require theirs before release. |

---

## Part 2 — Test cases

### A. Auth on the device

The auth code is the newest and the most load-bearing: everything else hangs off
who you are.

| # | Case | Expected | State |
|---|---|---|---|
| A1 | Google sign-in from the login screen | Lands on the live screen, **login form gone** | ☑ 2026-07-15 |
| A2 | Google sign-in from the **signup** screen | Same — two routes above home must both pop | ☐ |
| A3 | Password sign-in | Lands on live screen, form gone | ☑ 2026-07-16 (Vivo) |
| A4 | Wrong password | "Incorrect email or password", stays put, button re-enables | ☑ 2026-07-16 (Vivo) |
| A5 | Sign out | Back to splash; Settings closes too | ☑ 2026-07-15 |
| A6 | Kill the app and reopen | Restore splash → straight to live screen, no login | ☐ |
| A7 | **Airplane mode, then open the app** | Restore falls back to the cached token; live screen shows its offline state. Must **not** sign you out. | ☑ **2026-07-16 — pinned by tests** (`mobile/test/auth_restore_test.dart`, 6 cases, mutation-checked). Driven through AuthNotifier, not the radio: airplane mode also kills the phone's wireless ADB, so the screen goes with it. Covers offline, a *hung* backend (the 4s timeout — the nastier case), 401, rotation, and no-session. The restore path had **no test at all** before this. |
| A8 | Sign in on the phone, then check the admin panel | The user is listed, with the right provider | ☑ 2026-07-16 (phone-made accounts all appear, none with a role) |
| A9 | Google sign-in, cancel the account picker | No error banner — cancelling is not a failure | ☑ 2026-07-16 (Vivo) |
| A10 | 2FA account: sign in | Code prompt, then live screen | ☐ |
| A11 | Backend down, tap Sign In | Honest "can't reach" message, not a hang | ☐ |
| A13 | **Notes/Reminders with the backend dead** | Cached rows still show; no false "check the backend" | ☑ **2026-07-16 (Vivo)** — opened Notes online (2 notes), killed the backend, reopened: same 2 notes. Then force-stopped the app and reopened cold with the backend still dead: same 2 notes, read from disk. |
| A12 | Avatar tap | Opens Settings, showing your name + email | ☑ 2026-07-15 |

**A7 was the one to test first, and it had no test at all.** The restore path's
never-sign-out-unless-401 rule is now pinned; the remaining ☐s here need either a
2FA account (A10), a signup-screen Google run (A2), or a person watching the
screen (A6, A11).

### B. User scoping — two real accounts

Proven at the API level (`test_data_scoping.py`, 12 tests) and on the live
server, and now on a real phone (B4).

| # | Case | Expected | State |
|---|---|---|---|
| B1 | Two accounts, API level | Each sees only their own | ☑ 2026-07-15 |
| B2 | B deletes A's note by id | 404, row survives | ☑ 2026-07-15 |
| B3 | WS session owner = token holder | Session row carries the real user | ☑ 2026-07-15 (device) |
| B4 | **Sign in as A on the phone, save a note by voice, sign out, sign in as B** | B's Notes screen is **empty** | ☑ 2026-07-16 (Vivo) — "1 NOTE", only their own; the other user's note seeded at the same moment did not appear |
| B5 | Same, then sign back in as A | A's note is back | ☐ |
| B6 | A and B on **two different phones**, at once | Neither sees the other's notes; neither session steals the other's rows | ☐ |
| B7 | Ask Farry "read my notes" as B | Farry reads only B's | ☐ |

**B4–B7 are the real proof.** Everything so far tested the plumbing; these test
the promise.

### C. Admin panel — **no automated tests at all**

The biggest untested surface in the project. Every case below is manual.

| # | Case | Expected | State |
|---|---|---|---|
| C1 | Admin login | Dashboard loads | ☑ 2026-07-16 |
| C2 | **Non-admin logs into the admin panel** | Refused — this is the whole point of RBAC | ☑ 2026-07-16 — **found a bug**: the panel let them in (data was safe; backend refused all 11 admin routes). Gated on `dashboard.read`. |
| C3 | User list: search, paginate | Works past page 1 | ☑ 2026-07-16 (14 accounts listed) |
| C4 | Change a user's role | Takes effect; audit log records it | ☑ 2026-07-16 — set `manager`; not cosmetic: they then get 200 on `/users` (has `users.read`) and 403 on DELETE (no `users.delete`). Audit: `user.set_roles` by actor 16. |
| C5 | Suspend a user → that user uses the app | Kicked out (403 `USER_SUSPENDED`) | ☑ 2026-07-16 — **found two bugs**: the app said "check the backend" over a healthy backend, and the WS let a suspended token keep a live session open. Both fixed. |
| C6 | Impersonate a user | Works; audit log shows the `act` claim | ☑ 2026-07-16 — token carries `sub:15` + `act:{impersonator_id:16}`, audit row records both. The admin gets the *target's* lesser power, not their own (DELETE → 403). Impersonation lives in memory only, so a reload drops it. |
| C7 | Revenue screen with zero payments | Shows $0, not a crash or a blank | ☑ 2026-07-16 — $0.00 / $0.00 / 0 with "No payments yet." and "No active subscriptions." `Math.max(1, ...)` already guards the empty-spread `-Infinity`. Real numbers also reconcile to the DB: $28.99 = 1900 + 999 cents. |
| C8 | Subscriptions list | Free vs paid vs expired are distinguishable | ☑ 2026-07-16 — pro/premium, status + lifetime paid per row, filters for active/trialing/past due/canceled. |
| C9 | Token expires while the panel is open | Auto-refresh, no surprise logout | ☑ 2026-07-16 — corrupted the access token; the page loaded anyway and the refresh token rotated. Single-flight, so a burst of 401s triggers one refresh. |
| C10 | Audit log | Every admin action is there | ☑ 2026-07-16 (logins recorded) |

**C2 and C5 were run first and both earned it** — see the findings above. The
remaining gap in this section is that all ten are still *manual*: nothing here
runs in CI, so the next regression is found by a person or not at all.

### D. The live session (Farry herself)

| # | Case | Expected | State |
|---|---|---|---|
| D1 | Voice turn end-to-end | Farry hears, answers, TTS plays | ☑ **2026-07-16 — Faraz spoke into the mic.** Transcripts prove the whole chain: `user: "Can you please check what is this?"` → `assistant: "I see a prayer rug on the floor…"` → `user: "Yes."` → `assistant: "Yes, you can pray there."` — and `user: "हेलो"`, so it heard both English and Hindi, with the camera feeding it at the same time. Earlier text-driven run had already shown `listening → thinking → speaking` + 3.5s of streamed TTS. |
| D2 | Camera on → "what am I looking at" | Correct answer from a fresh frame | ☑ **2026-07-16 — pointed at a real laptop.** Farry: "a dark-coloured **Lenovo** laptop… a **USB-C adapter** connected to its **left side**… the screen displays a **chat application** with text in **English and Hindi/Devanagari**". Brand, port, port *side*, and the script on the screen — all correct. Region-correct buy links (Amazon UAE/Saudi). Frame gate visible in the same log: 15 of 54 frames forwarded. **This test also caught the API-key leak** — see the commit. |
| D3 | Barge-in — talk over her | She stops immediately | ☑ 2026-07-16 — sent the same `interrupt` message the app sends, 1.5s into her answer (177 audio frames in). **Last audio frame landed 0.01s *before* the interrupt** — nothing after it — and state went `speaking → listening` in the same instant. |
| D4 | Screen off, keep talking | Mic stays alive | ☑ 2026-07-16 (Faraz) |
| D5 | "Note yaad rakho X" → Notes screen | X is there, owned by you | ☑ 2026-07-16 — Farry ran `create_note`; the row came out owned by the speaker, showed on their `/notes`, and stayed invisible to another account. This is also **B7**: scoping holds through the agent's own tools, not just REST. |
| D6 | "Reminder lagao" → fires | Notification fires (release build — R8 has bitten twice here) | ☑ **2026-07-16 — fires.** Release build, Vivo, driven from the app. Farry ran `create_task`; Android registered `RTC_WAKEUP origWhen=18:09:00.000 tag=…ScheduledNotificationReceiver`, listed under `Alarm clock:` and `Next wake from idle` (so `setAlarmClock()` — Doze can't defer it). Slept the screen, waited: the notification arrived, `icon=RESOURCE id=0x7f07007c` (= `ic_notification`, so the shrinker didn't eat it). **Caveat: it silently does nothing until POST_NOTIFICATIONS is granted** — see D10. |
| D7 | Wifi drop mid-session | Reconnects; camera comes back | ☑ 2026-07-16 — killed the **backend** for 15s rather than the phone's wifi (dropping wifi also drops the phone's wireless ADB, so the screen goes with it): the app reconnected on its own and came back **LIVE with the camera still running**. Separately, a real 10s wifi drop *also* reconnected — and survived the phone's IP changing (.127 → .118), which is the harder case. An earlier "Camera off" after that drop turned out to be one of my own stray taps, not a regression. |
| D8 | Provider fallback (bad OpenAI key) | Falls back to Gemini + a non-fatal notice | ☑ 2026-07-16 — ran the backend with a deliberately broken `OPENAI_API_KEY`: `gateway.fallback` → Gemini, session survived, client told which model it got. |
| D10 | **Reminder with notifications denied** | Should say so, not fail silently | ☐ — **found 2026-07-16**: the first D6 attempt scheduled nothing at all. `dumpsys alarm` had zero farryon entries and no error surfaced anywhere; Farry cheerfully confirmed "reminder set". Granting POST_NOTIFICATIONS fixed it. A reminder that quietly never fires is the same class of bug as the two R8 ones. |
| D9 | Long session | Watchdog ends it rather than billing forever | ☑ 2026-07-16 — with `IDLE_DISCONNECT_SECONDS=8`, a silent session closed itself at 12.8s (`session.expired reason=idle`). Real caps are 5 min idle / 30 min hard. |

**D6 deserves paranoia**: reminders have silently broken twice in release builds
(R8 stripping Gson signatures, then the resource shrinker eating the icon). It
works in debug and fails in release, so **only a release build proves it** — and
only a session driven from the app, since `_applyReminder` hangs off the WS
`tool_result` the phone receives. Note the old "fixed with minify-off" is no
longer how it works: `isMinifyEnabled = true` is back, held safe by
`proguard-rules.pro`. Re-verify on a device after touching those settings.

### E. Glasses

| # | Case | Expected | State |
|---|---|---|---|
| E1 | Sprint 1-V stub suite | 6/6 | ☑ 2026-07-05 |
| E2 | Connect, battery, wear-to-talk | Per LAB_NOTES | ☑ |
| E3 | Photo → phone gallery | Lands in `DCIM/FarryOn` | ☑ |
| E4 | Delete from glasses storage | ~30s, no ack | ☑ 2026-07-12 |
| E5 | **Glasses + the new auth** | Glasses session belongs to the signed-in user, not anonymous | ☐ |
| E6 | Sprint 2 `.aar` drop-in | Blocked on your go | ⚠ |

**E5 is new and untested** — the scoping change touched every live session,
glasses included.

### F. Email

| # | Case | Expected | State |
|---|---|---|---|
| F1 | Test-connection in Settings | Green for good creds, honest error for bad | ☑ 2026-07-13 |
| F2 | "Read my email" | Reads from the primary mailbox | ☐ |
| F3 | "Read email from my work account" | Reads the **named** mailbox | ☐ |
| F4 | Send from a named mailbox | Sends from the right address | ☐ |
| F5 | Wrong app password | Graceful message, no hang | ☐ |

### G. Cost + quota

| # | Case | Expected | State |
|---|---|---|---|
| G1 | Frame gate | Frames sent < frames captured (`vision.frame_forwarded` logs both) | ☑ |
| G2 | Daily quota exhausted | Refused cleanly, told why | ☑ 2026-07-16 — metering verified live (`QUOTA_ENFORCEMENT_ENABLED=true` → three searches recorded as `web_searches=3` against `u15`). The refusal itself is covered by `test_quota_allows_up_to_cap_then_blocks` (→ `quota_exceeded`); driving a real cap to its limit would have burned ~11 live Tavily calls to re-prove tested logic. |
| G3 | Token cost logged per turn | It's in the log | ☑ |
| G4 | Vision 403 fallback | Degrades instead of dying | ☑ |
| G5 | `voice_seconds` is capped and counted | The cap should mean something | ☑ **fixed 2026-07-16.** `Session._meter_voice` counts mic bytes against the plan's daily cap, batched to the DB every 15s of speech plus a flush on close; over the cap the session is told and closed. `text_turns` / `frames_sent` are still dead columns — nothing writes them, and nothing reads them either. |

### H. Production deploy — **parked 2026-07-16**

Faraz has dropped Render and will pick a host later, so there is no production to
test against. These stay written down because they're the right list for whatever
host comes next — and because none of them can be faked locally: the whole point
of H1 is that a *real* redeploy doesn't wipe real accounts.

Do not run them until there's a host with a real `DATABASE_URL`. On local SQLite
they'd pass and prove nothing.

| # | Case | Expected | State |
|---|---|---|---|
| H1 | Deploy, then **redeploy** | Accounts **survive** — the whole point | ⚠ |
| H2 | Sign up on prod, verify email | Link **arrives** (needs #2) | ⚠ |
| H3 | Password reset on prod | Link arrives, works | ⚠ |
| H4 | ~~Cold Render dyno~~ | — | ✗ **dropped 2026-07-16**: Faraz isn't deploying to Render. Whatever host replaces it, the equivalent test is "first request after the instance has been idle" — keep it in mind, but there's nothing to test until a host is picked. |
| H5 | Google sign-in against prod | Needs the prod SHA-1's own OAuth client | ⚠ |
| H6 | Two users on prod | Scoping holds with Postgres, not just SQLite | ⚠ |

### I. Release build

| # | Case | Expected | State |
|---|---|---|---|
| I1 | `flutter build apk --release` | Builds (R8 has broken this before) | ☑ 2026-07-15 |
| I2 | Reminders in release | Fires (see D6) | ☑ 2026-07-16 — same run as D6: release build, alarm registered as `setAlarmClock`, fired with the screen asleep. |
| I3 | Notification icon in release | Not blank (shrinker ate it once) | ☑ 2026-07-16 — the fired notification carried `icon=RESOURCE id=0x7f07007c`, and `aapt2 dump resources` finds `drawable/ic_notification` in the release APK. keep.xml is doing its job. |
| I4 | Signed with a **real** keystore | Blocked on Part 1 #4 | ⚠ |
| I5 | Google sign-in with the release SHA-1 | Needs its own Android OAuth client | ⚠ |

---

## Suggested order

1. **B4–B7** — user scoping on real devices. It's this week's change and the one
   with a real blast radius: getting it wrong shows someone another person's notes.
2. **C2, C5** — RBAC. A broken admin gate is worse than a missing feature.
3. **A7** — offline restore, the auth path most likely to annoy a real user.
4. **D6 / I2** — reminders in a release build. Twice bitten.
5. **E5** — glasses under the new auth.
6. Then Part 1 #1 → #2 → the whole of **H**.

## Running the automated suites

```bash
cd backend && .venv/Scripts/python.exe -m pytest -q      # 246 tests, ~8 min
cd mobile  && flutter test                                # 120 tests, ~30 s
cd mobile  && flutter analyze                             # 2 known pre-existing infos
```
