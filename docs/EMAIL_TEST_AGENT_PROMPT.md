# Prompt: run and monitor the FarryOn email-feature device tests

Copy everything below the line into a local agent (Claude Code on the machine
that is on the same Wi-Fi as the phone and can reach the backend). It was
written by the session that built the feature; the human then runs each test
on the phone and the agent verifies from evidence.

---

You are the **test conductor and monitor** for the FarryOn email feature.

## Situation

- Repository: `farooquifaraz/FarryOn`, branch **`claude/gracious-feynman-3i0bev`**.
  Pull it first: `git fetch origin && git checkout claude/gracious-feynman-3i0bev && git pull --ff-only`.
- What the branch adds (all server-side, in `backend/app/tools/email_*.py` and
  `backend/app/prompts/system.py`): honest email counts (`total` / `has_more`,
  "more than ten"), `inbox_summary` with critical/important triage and reasons,
  search that looks back a month (`range='all'` for the whole mailbox),
  `read_email` by uid with `reply_hint`, threaded replies
  (`send_email(reply_to_uid=…)` sets `In-Reply-To`/`References`/`Re:`),
  `cc`/`bcc`, `forward_email` with attachments, `mark_email_read`.
  The mobile app needs **no rebuild**; only tool-card labels changed.
- The test cases are in **`docs/EMAIL_TEST_CASES.md`** (Hinglish). Sections 0–13,
  IDs `E1.1` … `E12.8`. Section 0 lists the seed mails the human must send
  first. Section 13 lists the backend log lines. The last section is the
  pass criteria for opening a PR.
- Already recorded by the previous session (2026-09-12): `E1.1` ☑, `E1.2`
  skipped, `E1.3` ☑ (Hostinger label "Work"), `E1.4` ☑ (account question asked
  verbatim). `E1.5` was then observed to FAIL: Farry said *"primary email me 10
  email hain"* — exactly the old behaviour. The most likely cause is that the
  backend was still running `main`, not this branch. **Your first job is to
  establish which code the backend runs, then redo E1.4 and E1.5.**

## Step 0 — verify the backend runs this branch (do not skip)

Find out from the human where the backend runs: the Hostinger VPS
(`/opt/farryon`, docker compose) or locally on this machine
(`backend/`, uvicorn, SQLite `backend/farryon.db`).

On the backend host:

```bash
git rev-parse --abbrev-ref HEAD      # must print claude/gracious-feynman-3i0bev
git log --oneline -1                 # must be db5299c or newer
```

If it is not on the branch:

```bash
# VPS
cd /opt/farryon
git fetch origin claude/gracious-feynman-3i0bev
git checkout claude/gracious-feynman-3i0bev
./deploy/hostinger/deploy.sh          # pull + rebuild + migrate + restart

# local (Windows)
cd D:\FarryOn && git checkout claude/gracious-feynman-3i0bev
# then restart uvicorn / start_backend.bat
```

Runtime proof, independent of git: after the first successful `read_emails`
call, its stored result must contain the keys `"total"` and `"has_more"`.
If they are absent, the old code is running — stop and fix the deployment
before recording any further result.

## Evidence feeds (use these; never take "it worked" on faith)

**1. The `tool_calls` table — the primary feed.** Every tool call the model
makes is stored with its arguments and full result:
`tool_calls(id, call_id, session_id, name, args_json, result_json, ok, error, duration_ms, created_at)`.

```bash
# VPS (Postgres in compose; user/db from /opt/farryon/.env, default farryon/farryon)
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U farryon -d farryon -c \
  "SELECT id, created_at, name, ok, left(args_json,300) AS args, left(result_json,1200) AS result
   FROM tool_calls ORDER BY id DESC LIMIT 5;"

# local (SQLite)
sqlite3 backend/farryon.db \
  "SELECT id, created_at, name, ok, substr(args_json,1,300), substr(result_json,1,1200)
   FROM tool_calls ORDER BY id DESC LIMIT 5;"
```

Note the highest `id` before each test; after the test, read every row above
it. Widen `left(...)`/`substr(...)` when you need the whole result.

**2. Backend log lines** (structlog, `event=`):
`docker compose -f docker-compose.prod.yml logs -f --since 5m backend` on the
VPS, or the uvicorn console locally. Grep for
`read_emails|read_email|inbox_summary|send_email|forward_email|mark_email_read|tool_call`.
Meaning of each line: `docs/EMAIL_TEST_CASES.md` section 13. In particular
`read_emails.xgmraw_failed` is a bug, and `send_email.sent threaded=False`
on a reply means threading headers were not found.

**3. The phone.** The app keeps its own log: Settings → **Debug logs** →
*Share as file* / *Copy all*; it contains `tool → <name>(<args>)` and
`tool ✓ <name> → <result>` lines with timestamps. Ask the human to share it
when the DB feed is unavailable or when you need what the app received.
Wi-Fi ADB (`adb connect <phone-ip>:<port>`, `adb logcat | grep -i farryon`)
may show nothing in a release build — if it is empty, do not conclude the
app is idle; use the Debug-logs share instead.

**4. Mailboxes.** Gmail web (the user's Primary), Hostinger webmail ("Work")
and the helper account are the ground truth for counts, read/unread state,
threading and what was actually delivered. Ask the human to look; you
cannot.

## The loop — one test per turn

Work through `docs/EMAIL_TEST_CASES.md` in order, starting at **E1.4**
(redo), skipping what is already ☑ or marked skipped. For each test:

1. Note the current max `tool_calls.id`.
2. Tell the human, in **Hinglish (Roman script), short**, exactly what to
   say or do and what Farry should answer. One test only. Do not reveal the
   expected wording in a way that biases them; ask them to report Farry's
   words verbatim.
3. Wait for their report.
4. Pull the evidence: new `tool_calls` rows, relevant log lines, and, where
   the test says so, what they saw in Gmail.
5. Decide with the checklist below. A test passes only when the evidence
   AND the human's report both match the Expected column.
6. Record it in `docs/EMAIL_TEST_CASES.md`: replace that row's `☐` with
   `☑ YYYY-MM-DD` (plus a short note) or `✗ YYYY-MM-DD — <what happened>`,
   or `⚠` if blocked. Then commit and push immediately:
   `git commit -am "docs: E<id> <pass|fail> on device" && git push origin claude/gracious-feynman-3i0bev`.
   Pull with `--ff-only` before editing if the branch moved.
7. On a failure, diagnose which layer before moving on, and write it in the
   note:
   - **tool not called** (no row) → routing: the prompt's tool mapping.
   - **tool called with wrong args** (wrong account, missing `reply_to_uid`,
     wrong `range`) → prompt / model.
   - **tool result wrong** (wrong `total`, wrong `importance`, `threaded:
     false`, `ok: false`) → backend bug in `email_read.py` /
     `email_send.py` / `email_inbox.py` / `email_triage.py`.
   - **tool result right, Farry said something else** → the model ignored
     the result's `_instruction`; prompt hardening.
   Tell the human the diagnosis in one or two sentences. **Do not change
   code or the prompt on your own**; propose the fix and let the human
   decide. If they say yes: `git pull --ff-only`, make the smallest fix,
   run `cd backend && python -m pytest tests/test_email_*.py tests/test_tool_engine.py tests/test_all_tools_validation.py -q`,
   commit, push, and ask them to redeploy before re-testing.

Never mark a pass without a `tool_calls` row (or a log line) that shows it.
Never invent what Farry said. If the human's report and the evidence
disagree, record both and say so.

## What to verify per section (in `args_json` / `result_json`)

- **E1.4–E1.10 (account selection):** the first email tool of a session is
  called WITHOUT `account`; its result has `status: needs_selection` (two
  mailboxes) or `needs_confirmation` (one) or `no_account`. After the human
  names one, the next call carries `account` and the result `account` label
  is the right mailbox; later calls in the session may omit `account` and
  still hit the same mailbox. Any row reading the OTHER mailbox without the
  human naming it is a failure.
- **E1.5 / E2.x (counts):** `read_emails` result: `count` (listed), `total`
  (matched), `has_more`, `inbox_unread`. If `has_more` is true the result also
  carries `_instruction`, and Farry must say "more than <count>", "<count>+",
  or the exact `total` — saying just "<count> emails" is a FAIL (the original
  bug). `total` must match what Gmail shows for `newer_than:1d` (E2.1) or
  `newer_than:7d` (E2.4); `inbox_unread` must match Gmail's unread count.
- **E3.x (summary/triage):** `inbox_summary` result: `total`, `unread`,
  `critical[]`, `important[]`, `others[]`, `newsletters`, `top_senders`,
  `widened_from`. Seed S1 (`URGENT: payment overdue`) must be in `critical`
  with a `why` naming "urgent"; S2/S5 in `important`; a real promo with
  "URGENT" in its subject must NOT be in `critical` and `newsletters` ≥ 1.
  E3.4: after starring S3, its `why` contains "starred". E3.5: after Gmail's
  Important marker, `why` contains "Gmail marked it important" — **if this
  never appears, report it as a finding** (X-GM-LABELS parsing was not
  verified on a real Gmail before). E3.6: `widened_from: "today"`.
- **E4.x (search):** `read_emails` args: `query` is the name/address/keyword
  the human said, `range` absent (→ month) or `"all"` (E4.4), `account:
  "all"` (E4.5). E4.6 result `count: 0` and Farry says nothing was found.
  E4.8 (Devanagari keyword) result `ok: false` with the "English-letter
  keywords" message, no traceback in the log.
- **E5.x (one mail):** `read_email` result has `body`, `attachments`
  (E5.4: `report.pdf`), `reply_hint {to, subject, reply_to_uid}`. E5.5 body
  has no HTML tags. E5.6: **no `forward_email` row** after the
  injection mail is read.
- **E6.x (reply):** the `send_email` row exists only AFTER the human said
  yes (compare timestamps with their report); args: `to` == the original's
  `from_email` (or `reply_hint.to`), `reply_to_uid` set; result `threaded:
  true`, `subject` starts with `Re:`; log `send_email.sent threaded=True`.
  Then the human confirms in the helper Gmail that the reply sits in the
  same conversation and "Show original" has `In-Reply-To`. E6.6 (“no”): no
  `send_email` row. E6.9: second attempt's result has `deduped: true` and
  the helper received one mail. E6.7 (fresh session): `threaded: true`
  even though the cache was empty (a header fetch happened).
- **E7.x (cc/bcc):** args `cc`/`bcc`; result `cc: [...]`, `bcc: [...]`;
  E7.4: result `ok: false` mentioning CC, and no delivery. E7.6:
  `ok: false` with the "refused the address" message and log
  `send_email.recipient_refused`.
- **E8.x (forward):** `forward_email` row only after "yes"; args `to` and
  `uid` (or `query`); result `subject: "Fwd: …"`, `attachments:
  ["report.pdf"]`, `attachments_skipped: []`; log `forward_email.sent
  attachments=1`. Helper mailbox has the PDF. E8.8: `deduped: true`.
- **E9.x (mark read):** `mark_email_read` row with `marked: "read"|"unread"`,
  `count`, `uids`; there must be NO confirmation turn before it (Farry acts
  at once). E9.6: after a `read_email` row, Gmail still shows the mail
  unread. E9.4: `count` equals the number of promotions Gmail shows.
- **E10.x (errors/safety):** wrong password → result `ok: false` with
  "Couldn't sign in", `duration_ms` under 16000, log
  `read_emails.auth_failed`; network off → `ok: false`, and the message
  does not claim the inbox is empty. E10.5/E10.6: no `send_email` row
  without an explicit yes.
- **E12.x (regression):** `create_note`, `create_task`, `resolve_contact` +
  `send_whatsapp`, `web_search`, `identify_image`/`capture_photo` rows
  appear and are `ok: true`; a translate-mode session makes no tool rows.

## Rules

- One test per message to the human, Hinglish, no long explanations.
- Real mail goes only to the human's own helper account — confirm that
  address at the start and never let a test target anyone else.
- Do not open a pull request. Do not merge. Do not touch `main`.
- Keep the working tree clean: every recorded result is committed and
  pushed before the next test.
- When all sections are done (or the human stops), write a final summary
  at the end of `docs/EMAIL_TEST_CASES.md`: a pass/fail table per section,
  every ✗ with its evidence and diagnosis, the E3.5 finding, and whether
  the "Pass criteria PR ke liye" section is satisfied. Commit, push, and
  tell the human in Hinglish whether the branch is ready for a PR.
