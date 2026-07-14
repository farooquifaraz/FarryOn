# FarryOn — Device Test Plan (Cost Optimization + Bug Fixes)

_Covers: P0-1 frame gate, Vision fallback, reminder + camera-churn fix, P0-2
compression + session caps + tool-result cap, P1-7 token logging, P0-3 quotas
(enforcement off), B1–B5, the camera-restart-on-reconnect fix, and the Telegram
contact_id / cross-channel work._

_Not included: P1-5 (system-prompt trim) — deliberately not re-applied after the
prompt grew with other features; it's the smallest saving and the riskiest edit._

**Pre-check**
- Backend running and reachable at `ws://192.168.1.107:8000/ws/live`.
- App installed (latest APK), opened, **Connect** pressed → status shows connected.
- On first launch, **Allow** all permissions (notification / alarm / battery /
  camera / mic / contacts).
- Mark each scenario **✅ / ❌**. For any ❌, note what happened + share a fresh
  `farryon_log`.

---

## Core scenarios (must test: 1, 3, 4, 6, 11)

### 1. Reminder — relative time  ⏱️  _(reminder fix)_
- **Say:** "2 minute baad paani peene ka reminder lagao"
- **Steps:** AI reads it back → say "yes".
- **Expected:** "Reminder set." → a **notification fires ~2 min later**, even with
  the screen locked.
- **Pass if:** the notification actually appears. _(Before the fix it silently
  never fired.)_

### 2. Reminder — absolute time
- **Say:** "kal subah 8 baje meeting ka reminder"
- **Expected:** AI confirms "tomorrow 8 AM meeting" → "yes" → "set". (Fires
  tomorrow; here just confirm it schedules without error.)

### 3. Camera stability  📷  _(camera-churn + restart-on-reconnect fix)_
- **Steps:** Camera on, use normally for ~40s; background the app (home button),
  wait 5s, reopen it; ask a vision question.
- **Expected:** Camera comes back on its own after reopening; **no** repeated
  on/off cycling; **no** false "turn the camera on" message.
- **Pass if:** after returning to the app, the camera preview is live and vision
  works without a manual toggle.

### 4. Vision — identify a product  🛍️  _(Vision-403 fallback)_
- **Steps:** Point the camera at any object (mouse, bottle, ball). **Say:** "ye
  kya hai" / "scan this".
- **Expected:** A name + description + shopping links. **No** "Something went
  wrong" error card.
- **Pass if:** you get an identification (Cloud Vision billing no longer required).

### 5. Vision — read / answer
- **Steps:** Point at text / a clock / a number. **Say:** "isme kya likha hai" /
  "kitne baje hain".
- **Expected:** It reads/answers correctly.

### 6. Telegram — pick from list & send  💬  _(contact_id + prompt rule 10)_
- **Say:** "Kamlesh ko telegram pe 'test message' bhejo"
- **Steps:** AI lists matches → say one exactly (e.g. "Kamlesh India") → "yes".
- **Expected:** It sends **directly** → "Sent on Telegram". It must **NOT** ask
  for a @username or re-resolve.
- **Pass if:** delivered without asking for a username. ❌ if it says "I can't see
  the username".

### 7. Same person → WhatsApp  _(cross-channel reuse)_
- **After #6, say:** "yahi message WhatsApp pe bhi bhejo"
- **Expected:** WhatsApp opens pre-filled (you tap Send) — **without** asking for
  the number again.

### 8. WhatsApp — new contact
- **Say:** "<name> ko WhatsApp karo 'hello'" → resolve → confirm → "yes".
- **Expected:** WhatsApp opens with the text ready (you tap Send — normal; WA
  can't auto-send).

### 9. Web search  🔎
- **Say:** "aaj Dubai ka mausam batao" (or any current question).
- **Expected:** "one sec" → a correct, current answer. (Quotas are OFF — this must
  never be blocked.)

### 10. Notes / tasks
- **Say:** "note kar lo: doodh lana hai" → confirm → "yes"; then "meri notes
  padho".
- **Expected:** Saved, then read back correctly.

### 10b. Read a LONG email in full  📧  _(tool-result cap regression)_
- **Say:** "aaj ke emails padho" → then pick a long one: "us <sender> wali email
  poori padho / uska summary do".
- **Expected:** it reads/summarises the **whole** email, not just the opening.
- **Why:** the model's copy of a tool result is now size-capped. The cap sits
  above read_email's own 4000-char body limit, so nothing should be clipped —
  this scenario proves it.

### 10c. Glasses one-shot photo  🕶️  _(frame gate vs capture_photo)_
- _Only if glasses are connected._ **Say:** "ye kya hai" while wearing them.
- **Expected:** it describes the **fresh** photo (not a previous scene, not
  "couldn't get a fresh look").
- **Why:** camera frames to the model are now throttled; a frame a capture is
  waiting for must never be dropped.

### 11. Long conversation  🕒  _(compression + session cap)_
- **Steps:** Keep talking / asking for ~4–5 minutes continuously.
- **Expected:** No mid-session disconnect (cap is 30 min); replies stay normal.
- **Pass if:** the session survives and stays coherent.

---

## What I verify from the backend log (not visible in the app)

Share a fresh `farryon_log` after testing; the backend log confirms:

| Signal (backend log) | Confirms | Healthy value |
|---|---|---|
| `vision.frame_forwarded sent=… received=…` | P0-1 frame gate | ~1 sent per ~6 received |
| `vision.frame_summary saved_pct=…` | frame savings | ~80% saved |
| `gemini.usage turn_total=… session_total=…` | P1-7 token logging | present each turn |
| `Notifications: reminder N scheduled` | reminder fix | appears on every reminder |
| `session.expired reason=…` | P0-2 caps | only after 30 min / 5 min idle |
| `gemini.session_ended` | B3 | logged (not silent) if a turn ends the session |

---

## Known / intentional behaviour (NOT bugs)
- **WhatsApp / SMS** open pre-filled and you tap Send — they cannot auto-send
  (platform rule). Only **Telegram** delivers automatically from your account.
- **Quotas** never block right now — enforcement is intentionally OFF until
  per-user auth exists.
- A brand-new frame right after the camera turns on can take ~1s to arrive; ask
  again a second later if the very first scan says "no frame".

---

## Result line
Reply with a compact pass/fail, e.g. `1✅ 2✅ 3✅ 4✅ 5✅ 6✅ 7✅ 8✅ 9✅ 10✅ 11✅`,
and attach a `farryon_log`. All green → changes get pushed (Render auto-deploys).
