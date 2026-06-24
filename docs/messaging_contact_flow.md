# Messaging (WhatsApp / Telegram) — Contact-Confirm Flow Design

Status: **IMPLEMENTED (2026-06-24).** Privacy-max round-trip + resolve-first +
masked confirm, on all three providers (Gemini / OpenAI / Grok). A server-side
recall safety-net covers weaker models that forget to thread the contact_id.
Verified end-to-end on all providers; 96 backend + 53 mobile tests green.

---

## 0. FINALIZED DESIGN (your decisions) — this supersedes §3–§5 below

You chose: **(1) privacy-max round-trip** · **(2) dedicated `resolve_contact`
step** · **(3) masked number** · **(4) country code = whatever the phone's
SIM/locale says, fallback `971`**.

So contacts **never leave the phone**, and the real number is **never sent to
the server** — not even to build the wa.me link. The backend only ever sees a
*masked* number for read-back.

### 0.1 New protocol messages

```
server → client : resolve_contact_request
    { type, requestId, name, channel:"whatsapp"|"telegram" }

client → server : resolve_contact_result
    { type, requestId, status, candidates:[ {contactId, displayName,
      maskedNumber, hasTelegram} ] }
    status ∈ found | not_found | ambiguous | no_number | permission_denied

server → client : open_messaging   (after you say "yes")
    { type, action:"open_messaging", channel, contactId, message }
```

The phone keeps a **session-local map `contactId → real number`** (a random id
minted per session). The server stores only `contactId` + `maskedNumber`, never
the real digits.

### 0.2 End-to-end flow (WhatsApp by name)

```
You:   "WhatsApp कमलेश ko bolo kal milte hain"
Model: → resolve_contact(name="कमलेश", channel="whatsapp")   [read-only, no confirm]
Backend orchestrator:
       → emits resolve_contact_request{requestId, "कमलेश"}
       → AWAITS resolve_contact_result (timeout ~8s)
Phone: requests Contacts permission if needed → matches locally
       → replies candidates with masked numbers + opaque contactIds
Backend: returns the (masked) result to the model
   ├─ found(1)  → Model: "कमलेश — +971 50 *** **67 — 'kal milte hain' भेज दूँ?"
   │              You: "हाँ"
   │              Model → send_whatsapp(contact_id=…, message=…)
   │              Backend → action:"open_messaging" (contactId + message)
   │              Phone  → looks up real number locally → opens wa.me → "Send दबा दो"
   ├─ ambiguous → Model: "दो कमलेश मिले — Home या Office?" (lists displayNames)
   ├─ not_found → Model: "कमलेश contacts में नहीं मिला, नंबर बता दो" (BEFORE any 'sent')
   ├─ no_number → Model: "कमलेश के पास नंबर नहीं है"
   └─ permission_denied → Model: "Contacts की permission दे दो तो नाम से भेज सकूँ"
```

Number given directly (no name) → no round-trip; confirm the typed number → send.
Telegram: same round-trip; bot path = "भेज दिया" (truly sent), deep-link path =
"चैट खोल दी, paste कर दो" (NOT sent).

### 0.3 Await mechanism (backend)

- `orchestrator` holds `pending_resolves: dict[requestId, asyncio.Future]`.
- `resolve_contact` tool: mint `requestId`, send request via the session's
  `notify_client`, `await asyncio.wait_for(future, 8s)`.
- `session` `resolve_contact_result` handler: `future.set_result(payload)`.
- Timeout / socket drop → tool returns `index_unavailable` → "एक सेकंड…" (retry),
  never a false "sent".

### 0.4 Confirm-before-send guarantee

`resolve_contact` is **read-only → no confirmation**. `send_whatsapp` /
`send_telegram` stay under the confirm-before-acting rule, and now the model
confirms on the **validated masked number**, so "sent/भेज दिया" can only be said
after a real resolution + your "yes". On any `ok:false` the model states the
problem and does **not** claim it sent.

### 0.45 USER FLOW with validation (step-by-step)

Each step has a **Validation gate**: what is checked, what happens on PASS, and
what Farry says/does on FAIL. Farry never advances a step until the current
gate passes. "Sent / भेज दिया" is reachable ONLY from Step 7-PASS.

```
STEP 1 — Intent
  User: "WhatsApp/Telegram <kisi> ko bolo <message>"
  Gate 1 — Channel + message present?
    • message empty/unclear  → FAIL → "क्या message भेजना है?" (re-ask, stay Step 1)
    • channel unclear (WA/TG?) → FAIL → "WhatsApp पर या Telegram पर?"
    • PASS → go Step 2

STEP 2 — Recipient type
  Gate 2 — Did the user give a NUMBER or a NAME?
    • NUMBER given     → skip to Step 6 (validate number) — no contact lookup
    • NAME given       → go Step 3
    • neither          → FAIL → "किसे भेजूँ — नाम या नंबर?"

STEP 3 — Resolve (read-only round-trip, NO confirm)
  Farry → resolve_contact(name, channel)  → phone resolves locally
  Gate 3 — Contacts permission on the phone?
    • denied/blocked   → FAIL → "Contacts की permission दे दो तो नाम से भेज सकूँ।
                         या सीधे number बता दो।" (offer number path → Step 6)
    • request times out (8s) / socket drop → FAIL → "एक सेकंड, contacts load नहीं
                         हुए — फिर बोलो।" (retry, no false 'sent')
    • PASS → go Step 4

STEP 4 — Match outcome
  Gate 4 — How many contacts matched <name>?
    • 0 matches        → FAIL → "<name> contacts में नहीं मिला। नंबर बता दो तो भेज
                         दूँ (चाहो तो save भी कर लूँ)।" (→ Step 6 if user gives one)
    • match has NO number (WA) / NO @handle+chat_id (TG)
                       → FAIL → "<name> के पास number नहीं है।" (→ ask for one)
    • 2+ DISTINCT numbers → FAIL(ambiguous) → "<name> के दो मिले — <A> या <B>?"
                         (user picks → re-resolve that one → Step 5)
    • exactly 1 (or duplicates of same number) → PASS → go Step 5

STEP 5 — Confirm recipient + message  (CONFIRM-BEFORE-SEND gate)
  Farry: "<name> — <masked number, e.g. +971 50 *** **67> — पर
          '<message>' भेज दूँ?"
  Gate 5 — Explicit yes?
    • "नहीं" / change   → adjust (new name/number/message) → back to relevant step
    • silence/unclear   → re-ask once → if still unclear, stop (no send)
    • "हाँ" → go Step 7  (Telegram-deeplink → Step 6.5 first)

STEP 6 — Number validation (only when a raw number was given)
  Gate 6 — normalize_phone(number): digits, country code (SIM/locale, fallback 971)
    • < 8 or > 15 digits → FAIL → "वो number ठीक नहीं लग रहा, दोबारा बता दो।"
    • PASS → confirm masked number (Step 5) → on yes → Step 7

STEP 6.5 — Telegram capability check (Telegram only)
  Gate 6.5 — Can we actually SEND, or only OPEN?
    • bot token + recipient chat_id present → "SEND" mode  → Step 7 (real send)
    • only @username                        → "OPEN" mode  → Step 7 but Farry
        will say "चैट खोल दी, message paste कर दो" (NOT 'sent')
    • neither                               → FAIL → "Telegram @username बता दो।"

STEP 7 — Execute
  WhatsApp:  Farry → send_whatsapp(contact_id|number, message)
             Backend → action:"open_messaging" → phone opens wa.me (text ready)
             Gate 7 — did WhatsApp open?
               • launch fails (not installed) → FAIL → "WhatsApp नहीं खुला —
                 installed है?" (no success claim)
               • opened → Farry: "WhatsApp खोल दिया, बस Send दबा दो।" ← honest
  Telegram SEND mode: Bot API call
               • API ok    → "Telegram पर भेज दिया।"  ← truly sent
               • blocked   → "उन्होंने bot block किया है।"
               • API error/timeout → "Telegram पर अभी नहीं भेज पाया।"
  Telegram OPEN mode: opens t.me/<username>
               • opened    → "चैट खोल दी, message paste कर दो।" ← NOT 'sent'

SIDE FLOW — Save contact (optional, when name unknown)
  After a not_found, if user says "save कर लो / yaad rakho":
    Gate S — name + (number or @username) present?
      • missing → "किसका नंबर/हैंडल save करूँ?"
      • PASS → confirm "<name> = <masked> save कर दूँ?" → yes → save_contact
```

Validation summary (the gates that BLOCK a send): non-empty message (G1),
known recipient type (G2), contacts permission (G3), a single valid match (G4),
valid number format (G6), Telegram sendability (G6.5), and — above all — an
explicit **yes** on the masked read-back (G5). Only after G7 succeeds does Farry
state it as done.

### 0.5 Files (round-trip version)

- Backend: `tools/contacts.py` (+`ResolveContactTool`), `tools/whatsapp.py` &
  `telegram.py` (accept `contact_id`, return `open_messaging`),
  `agent/orchestrator.py` (pending-future map + `notify_client`),
  `ws/session.py` (`resolve_contact_result` handler), `prompts/system.py`
  (routing: resolve→confirm→send; honesty wording).
- Mobile: `live_controller.dart` (handle `resolve_contact_request` → read
  contacts, mask, reply; handle `open_messaging` → local number → wa.me/t.me;
  keep session `contactId→number` map; drop old fire-and-forget
  `_applyResolveContact`), `live_client.dart` (send `resolve_contact_result`).
- Tests: backend resolve/await/timeout + all statuses + cross-provider; mobile
  analyze + suite green.

---
Scope: `send_whatsapp`, `send_telegram`, `save_contact`, contact resolution, and
the confirm-before-send guarantee. Applies to **all three providers** (Gemini /
OpenAI / Grok) because the logic lives in the shared tools + prompt.

---

## 1. The bug we are fixing

What happens today (from your screenshot — "WhatsApp कमलेश"):

1. You say "WhatsApp कमलेश ko bhejo …".
2. Farry asks "भेज दूँ?" and you say yes.
3. The model calls `send_whatsapp(contact_name="कमलेश")`.
4. The backend does **not** know your phone's contacts, so it returns
   `ok: true, action: "resolve_contact"` — an *optimistic* success.
5. The model treats `ok:true` as "done" and **says "मैसेज भेज दिया गया"**.
6. ONLY THEN the phone looks "कमलेश" up in device contacts, fails, and shows the
   snackbar **"Couldn't find कमलेश with a number in your contacts."**

**Result: Farry claims success, then contradicts itself.** The send is announced
before the recipient is even validated. The order is backwards.

### Root cause
There is **no contact validation before the model confirms/sends**. Contacts
live on the phone; the backend tool returns `ok:true` without ever checking that
"कमलेश" resolves to a real number. Resolution happens on the device *after* the
tool has already returned and the model has already spoken.

---

## 2. Goals

1. **Validate the recipient BEFORE Farry says anything about sending.**
2. **Confirm the exact resolved recipient** ("कमलेश — +971 50 *** **67 — ये भेज
   दूँ?") so you approve the real number, not a guess.
3. Never announce "sent / भेज दिया" unless the recipient is resolved and you said
   yes.
4. Clear, specific spoken handling for every failure: not found, multiple
   matches, no number, permission denied, invalid number, app not installed.
5. Same behaviour on Gemini, OpenAI, and Grok.
6. No regression to existing tools.

---

## 3. Chosen approach — device syncs a lightweight contact index

The cleanest way to make resolution **synchronous inside the tool** (so the model
gets a real found / not-found / multiple result *before* it speaks) is to give
the backend a session-scoped copy of the user's contact index — the same pattern
already used for `location_update` (device pushes data → cached on the
orchestrator → a tool reads it).

```
Phone (on permission grant / session start)
   → reads device contacts (name, phone numbers, telegram handle if any)
   → sends a compact `contacts_sync` control message
Backend session
   → caches the list on the orchestrator (NOT persisted to DB)
send_whatsapp / send_telegram
   → resolve the name against: (a) app-saved contacts table, then
     (b) this cached device index
   → returns a VALIDATED result the model can confirm on
```

Why this one (vs. a device round-trip): it needs **no new await/correlation
machinery**, reuses the proven `location_update` cache pattern, and lets the tool
return a fully-decided result in a single step. Trade-off = the contact index is
held in server memory for the session (see §8 Privacy; a round-trip alternative
is documented there if you prefer contacts never leave the phone).

---

## 4. Target flow — WhatsApp (by name)

```
You:    "WhatsApp कमलेश ko bolo kal milte hain"
Model:  → resolve_contact(name="कमलेश")          [READ-ONLY, no confirm needed]
Tool:   looks up "कमलेश" in saved + device index
        ├─ EXACTLY ONE match w/ number
        │     → returns {found, name:"कमलेश", phone:"+9715xxxxxx67"}
        │  Model: "कमलेश को, नंबर +971 50 *** **67 पर,
        │          'kal milte hain' भेज दूँ?"        ← CONFIRM with real number
        │  You:   "हाँ"
        │  Model: → send_whatsapp(contact_name="कमलेश", message=…)
        │  Tool:  re-resolves (now known) → {ok, action:open_url, wa.me link}
        │  Phone: opens WhatsApp with text ready
        │  Model: "WhatsApp खोल दिया, बस Send दबा दो."   ← honest, never "sent"
        │
        ├─ MORE THAN ONE match (e.g. "कमलेश Home" / "कमलेश Office")
        │     → returns {ambiguous, options:[…]}
        │  Model: "दो कमलेश मिले — Home या Office?"      ← ask, do NOT send
        │
        └─ NO match
              → returns {not_found, name:"कमलेश"}
           Model: "कमलेश आपके contacts में नहीं मिला। नंबर बता दो तो भेज दूँ
                   (और चाहो तो save कर लूँ)."             ← BEFORE any "sent"
```

Key change: a dedicated **`resolve_contact` read-only step** runs first and is
allowed without confirmation (it only *reads*). The model confirms on its result,
and `send_whatsapp` is only ever called once the recipient is known.

### Alternative (no extra model step)
If we prefer fewer round-trips, `send_whatsapp` itself resolves first and, when
the name is unknown/ambiguous, returns `ok:false` with a spoken reason **instead
of** the optimistic `resolve_contact` action. The model then never says "sent"
because the tool reported `ok:false`. (This is simpler but the confirm happens on
the model's pre-call guess rather than on the validated number.) — *Your call in
§10.*

---

## 5. Target flow — Telegram (by name)

Telegram has two sub-cases (already in code), both must validate first:

```
You:   "Telegram राहुल ko bhejo …"
Model: → resolve_contact(name="राहुल", channel="telegram")
Tool:  ├─ saved contact has telegram_chat_id (bot-connected)
       │     → {found, via:"bot"}  → confirm → send_telegram → delivered
       ├─ saved/device has @username only
       │     → {found, via:"deeplink", username}
       │       Model confirms → opens t.me/<username>
       │       (Telegram CANNOT pre-fill text — Farry says: "चैट खोल दी,
       │        message paste कर दो" — never "sent")
       └─ nothing
             → {not_found} → "राहुल का Telegram नहीं मिला, @username बता दो."
```

Honesty rule: bot path = "भेज दिया" (truly sent). Deep-link path = "चैट खोल दी"
(NOT sent — user pastes). These must be worded differently.

---

## 6. Validation rules

**Name matching** (in order, first hit wins):
1. App saved-contacts table (`find_contact`, exact then `ilike`).
2. Device index: exact display-name (case-insensitive), then "starts-with", then
   "contains". Works for Latin and non-Latin (Devanagari/Arabic) names.
3. Collapse duplicates that point to the same number → treat as one match.
4. If 2+ *distinct* numbers remain → `ambiguous` (ask which).

**Phone normalisation** (`normalize_phone`, shared by backend + mobile):
- Strip everything non-digit.
- Keep a leading country code if present; else prepend the default
  (`default_country_code`, currently `971`) and drop a national leading `0`.
- Reject if < 8 or > 15 digits → `invalid_number`.

**Message**: must be non-empty after trim; otherwise ask what to say.

**Confirmation text**: always read back **name + masked number** (`+971 50 ***
**67`) so a wrong match is caught by ear without exposing the full number on
screen.

---

## 7. Exception-handling matrix

| Case | Detected where | Tool result | What Farry says | Sends? |
|---|---|---|---|---|
| Name not in any contacts | tool (after sync) | `not_found` | "X contacts में नहीं मिला, नंबर बता दो" | No |
| 2+ different numbers | tool | `ambiguous` + options | "X के दो नंबर हैं — कौन सा?" | No |
| Contact has no number | tool | `no_number` | "X के पास नंबर नहीं है" | No |
| Invalid/short number | tool | `invalid_number` | "वो नंबर ठीक नहीं लग रहा" | No |
| Contacts permission denied | phone | `permission_denied` | "Contacts की permission दे दो तो नाम से भेज सकूँ" | No |
| Contacts not synced yet | tool | `index_unavailable` | "एक सेकंड, contacts load हो रहे…" (retry) | No |
| Number given directly (no name) | tool | `found` | confirm number → send | Yes |
| WhatsApp not installed | phone (launch fails) | n/a | "WhatsApp नहीं खुला — installed है?" | No |
| Telegram deep-link (no bot) | tool | `found via:deeplink` | "चैट खोल दी, paste कर दो" (NOT sent) | Opens |
| Telegram bot blocked | tool | bot 403 | "उन्होंने bot block किया है" | No |
| Telegram send API error | tool | `send_failed` | "Telegram पर नहीं भेज पाया" | No |
| Network down (Telegram bot) | tool | timeout | "अभी connect नहीं हो पा रहा" | No |

Every `ok:false` path → the model must **state the problem and NOT say sent**.

---

## 8. Privacy

- The device index is **session-scoped, in server memory only, never written to
  the DB** (same handling as the email app-password and GPS location today).
- Only `{displayName, phones[], telegramUsername?}` are sent — no emails,
  photos, or other fields.
- It is dropped when the socket closes.
- **Privacy-max alternative (if you prefer contacts NEVER leave the phone):** a
  device round-trip — the tool emits a `resolve_contact_request`, the phone
  resolves locally and replies with `resolve_contact_result`, the orchestrator
  awaits that (with a request-id + timeout) and feeds it to the model. Costs one
  extra await mechanism + a little latency, but no contact ever reaches the
  server. Pick this in §10 if privacy outweighs simplicity.

---

## 9. Files that will change (for reference — not yet touched)

Backend
- `app/tools/contacts.py` — new `resolve_contact` read-only tool (+ keep
  `save_contact`).
- `app/tools/whatsapp.py` / `telegram.py` — resolve against saved + device index;
  return validated `found/not_found/ambiguous/no_number/invalid_number`.
- `app/agent/orchestrator.py` — hold `contact_index` (like `location`).
- `app/ws/session.py` — handle a new `contacts_sync` control message.
- `app/prompts/system.py` — routing: resolve first; confirm with the real number;
  never say "sent" on `ok:false` or on a deep-link/open path.

Mobile
- `lib/state/live_controller.dart` — on contacts-permission grant, read contacts
  and send `contacts_sync`; keep `_applyOpenUrl`; the device no longer needs to
  resolve names itself (backend does), so `_applyResolveContact` is simplified/
  removed.
- `lib/data/live_client.dart` — send the `contacts_sync` message.

Tests
- Backend: resolve/not_found/ambiguous/no_number/invalid_number, WhatsApp +
  Telegram, confirm-ordering; cross-provider routing probe.
- Mobile: analyze + existing suite stay green.

## 10. Decisions I need from you

1. **Approach:** (A) device syncs contact index to backend *(recommended,
   simpler)*, or (B) privacy-max device round-trip (contacts never leave phone)?
2. **Resolve step:** dedicated `resolve_contact` read-only step *(recommended,
   confirms on the real number)*, or have `send_whatsapp` resolve in one shot and
   return `ok:false` when unknown *(fewer round-trips)*?
3. **Default country code:** keep `971` (UAE)? (Used when a saved number has no
   country code.)
4. **Number in confirmation:** masked `+971 50 *** **67` *(recommended)* or full?

Once you pick, I implement + test (local + all-providers) and only then push.
