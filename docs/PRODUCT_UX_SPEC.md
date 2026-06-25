# FarryOn — Product UX Spec & Tool User Journeys

> **Purpose:** Turn FarryOn from a working prototype into a *professional product
> that does not irritate users*. This document defines, for every tool, an
> industry-standard user journey (benchmarked against the apps people already
> use), the permanent fixes for the three known bugs, and a phased
> implementation roadmap.
>
> **Audience:** product + engineering. Pair this with
> [`ARCHITECTURE.md`](./ARCHITECTURE.md) (how it works) and
> [`PROTOCOL.md`](../PROTOCOL.md) (the wire contract).
>
> **Status:** v1 — 2026-06-25. Market-researched (sources cited inline).

---

## 0. North-star UX principles (apply to EVERY tool)

These are distilled from how Siri / Google Assistant / Alexa, Google Lens,
Google Maps Live View, Apple Visual Look Up, Amazon Lens and Pinterest Lens
actually behave. They are non-negotiable rules for FarryOn.

| # | Principle | What it means in FarryOn |
| - | --------- | ------------------------ |
| **P1** | **Confirm before any irreversible action** | Read back the exact note/task/recipient/message and wait for an explicit "yes". Already a system-prompt rule — must also be enforced in code for sends/deletes. |
| **P2** | **One confirmation, voice-only** | Never force a screen tap in a hands-free flow. One read-back + one "yes" — not recipient *and* body *and* app separately (unless ambiguous). Users actively resent extra confirmation friction. ([Google Assistant community](https://support.google.com/assistant/thread/2012336)) |
| **P3** | **Never claim success you didn't achieve** | If WhatsApp only *opened* with text pre-filled, say *"I've opened WhatsApp — just tap send"*, **never "Sent."** Only say "sent" when truly delivered (Telegram bot / API success / SMS confirmed). |
| **P4** | **Confidence-gate vision; never assert a wrong name** | For landmark/product, if not confident, phrase it as a question (*"This looks like X — right?"*) or stay honest (*"I'm not sure, but it resembles…"*). One confident wrong answer permanently breaks trust. ([NN/g](https://www.nngroup.com/articles/ai-hallucinations/), [aiuxdesign.guide](https://www.aiuxdesign.guide/patterns/confidence-visualization)) |
| **P5** | **Always offer a fallback — failure is never a dead end** | Couldn't find the contact → offer to spell/save the number. No landmark match → "search the web / open in Maps". No product → "similar items / web search". |
| **P6** | **Feel fast: instant acknowledgement, then enrich** | The user must hear/see *something* within ~1s ("on it…", scanning animation). Heavy work (vision, search) runs after the acknowledgement. Target a sub-second first signal. ([AR latency](https://arxiv.org/pdf/2409.04018)) |
| **P7** | **Graceful recovery & correction** | Let the user say "change it" / "no, the other one" without restarting the whole flow. Disambiguate with *"Did you mean X or Y?"* instead of erroring. ([Google Cloud voice-agent design](https://docs.cloud.google.com/dialogflow/cx/docs/concept/voice-agent-design)) |
| **P8** | **Uniform, honest tool results** | Every tool returns `{ok: bool, message?, ...}`. The model branches on `ok`; the user hears a friendly line either way. Never surface a raw `KeyError: ...` stack string. |

---

## 1. Permanent fixes for the three known bugs

### 🔴 BUG 1 — "WhatsApp send nahi hota / WhatsApp open nahi hota"

**Root cause (confirmed):** The common flow ("WhatsApp Sara") resolves the
contact on the device and the backend returns a tool result with
`action: "open_messaging"` + `contact_id`
([`whatsapp.py:103`](../backend/app/tools/whatsapp.py)). But the Flutter app's
`_applyToolResult` only routes `action == "open_url"` (via `_applyOpenUrl`,
[`live_controller.dart:418`](../mobile/lib/state/live_controller.dart)). The
`open_messaging` action is silently dropped — WhatsApp never opens. The
`OpenMessagingMessage` handler that *does* exist expects a separate
`{type: "open_messaging"}` server message that **the backend never sends** — it
is dead code.

**Permanent fix (client-side, minimal & matches the existing pattern):**
In `_applyToolResult`, handle the `open_messaging` action the same way
`open_url` is handled, routing to the existing `_handleOpenMessaging` logic.

```dart
// live_controller.dart — inside _applyToolResult, alongside _applyOpenUrl(msg):
void _applyOpenMessaging(ToolResultMessage msg) {
  if (!msg.ok) return;
  final res = msg.result;
  if (res == null || res['action'] != 'open_messaging') return;
  final contactId = res['contact_id'] as String?;
  final channel = (res['channel'] as String?) ?? 'whatsapp';
  final message = (res['message'] as String?) ?? '';
  if (contactId == null || contactId.isEmpty) return;
  unawaited(_handleOpenMessaging(
    OpenMessagingMessage(channel: channel, contactId: contactId, message: message),
  ));
}
```

**Also harden the direct path:** the `wa.me` link requires a **plain
international number, digits only, no `+`, no leading zero**
([appsflyer](https://www.appsflyer.com/blog/deep-linking/whatsapp-deep-link/)).
`normalize_phone` mostly does this but accepts 1–2 digit junk — add a
**min/max digit sanity check** (see §3). The deep link **cannot auto-send** —
the user always taps send — so the assistant must say *"opened, tap send"*, not
*"sent"* (P3).

**Acceptance test:** "WhatsApp Sara" → resolve → confirm → "yes" → **WhatsApp
opens with the message pre-filled** for both the saved-contact and
device-contact paths.

---

### 🟡 BUG 2 — "OpenAI aur Grok sahi jawab nahi dete, Gemini deta hai"

**Root cause (confirmed):** the three providers are not wired equally.

| Capability | Gemini (native) | OpenAI | Grok |
| ---------- | --------------- | ------ | ---- |
| Vision (camera) | continuous video stream | **1 latest frame / turn** (<4s old) | **none** — `_vision_items = (provider == "openai")` is `False` for Grok ([`openai_realtime.py:147`](../backend/app/ai/openai_realtime.py)) |
| Response trigger | automatic | `create_response:false` + manual `response.create()` keyed off `input_audio_buffer.committed` ([`openai_realtime.py:314`](../backend/app/ai/openai_realtime.py)) | plain server VAD auto-response |
| User transcription | provider built-in | whisper-1 (extra latency) | whisper-1 |
| Input audio | native | 16k→24k resample per chunk | resample |

So: **Grok is blind** (can't answer "what am I looking at"); **OpenAI** gets only
one frame and its reply hinges on a single event firing — if
`input_audio_buffer.committed` doesn't arrive (SDK/version drift), the model
**never responds**, which reads as "OpenAI not answering".

**Permanent fix:**
1. **Give Grok the single-frame vision path too.** Drive vision-attach off a
   capability flag rather than a hard `provider == "openai"` check, and verify
   xAI honours `input_image` conversation items / `create_response:false`. If
   xAI does **not** support it, set the flag false **and** make the assistant
   honestly say *"I can't see the camera on this model — switch to Gemini for
   visual questions"* instead of guessing.
2. **Safety net for the manual response trigger (OpenAI):** if no
   `response.*` event is seen within ~1.5s of `input_audio_buffer.committed`,
   call `response.create()` again (idempotent-safe) so a missed event can't hang
   the turn. Also handle `response.created`/`response.done` defensively across
   GA and beta event names.
3. **Surface connect failures clearly.** If `GROK_API_KEY`/model is wrong,
   connect raises and the session emits a fatal error — make the app show
   *"Grok is unavailable (check key/model)"* and **auto-fall back to the default
   provider** rather than appearing to "not answer".

**Product guidance:** market-grade voice+vision today is strongest on **Gemini
Live** (continuous native vision). Recommend it as the default, present
OpenAI/Grok as **voice-first** options, and be explicit in-UI about each one's
vision capability.

---

### 🟡 BUG 3 — "Meri awaaz slow process hoti hai / result late aata hai"

**Root causes (confirmed):**
1. **~1.2 s dead-window after every reply.** The mic stays muted for the full
   TTS playback drain **plus a 1200 ms tail margin**
   (`_ttsTailMarginMs`, [`live_controller.dart:96`](../mobile/lib/state/live_controller.dart)).
   Good for echo, bad for responsiveness.
2. **Synchronous JPEG decode/resize on the UI isolate every ~1s.**
   `_downscaleToJpeg` runs `img.decodeImage` on the main isolate
   ([`phone_capture_source.dart:256`](../mobile/lib/capture/phone_capture_source.dart)),
   blocking the event loop that also forwards mic audio and WS sends.
3. **`ResolutionPreset.high`** capture is heavy for a ~1 fps still.
4. **Provider overhead** (resample + whisper-1) on OpenAI/Grok.

**Permanent fix:**
- Move JPEG decode/downscale to a background isolate (`compute()` /
  `Isolate.run`) so it never blocks audio/WS.
- Drop camera capture to `ResolutionPreset.medium` (still ≥1024px after
  downscale — plenty for vision).
- Reduce `_ttsTailMarginMs` to ~350–500 ms and keep it **byte-accurate** to the
  fed PCM so the mic re-opens right after the last word, not a fixed long pause.
  Rely on `enableVoiceProcessing` (AEC) which is already on.
- Acknowledge instantly (P6): the model already says "on it" for slow tools —
  ensure that fires *before* the tool runs, and add a UI scanning state for
  `identify_image`.

**Acceptance target:** user can speak again within ~0.5 s of the assistant
finishing; no audio stutter while a camera frame is processed.

---

## 2. Per-tool user journeys

Each journey uses a consistent template: **Trigger → Happy path → Confirmation
rule → Edge cases & recovery → Honest wording → Fallback → Result card → Current
gap.** The four flagship journeys (WhatsApp, Telegram, Landmark, Product) are
detailed; the rest are concise.

---

### 2.1 ⭐ WhatsApp — `send_whatsapp` (+ `resolve_contact`)

**Benchmark:** Siri/Google Assistant "send a WhatsApp" flow — *intent → resolve
recipient → dictate → read-back → single confirm → honest delivery state*.
([Google support](https://support.google.com/assistant/answer/9984245),
[tuneskit](https://www.tuneskit.com/whatsapp/can-siri-send-whatsapp-messages.html))

**Trigger:** "WhatsApp Sara", "WA karo Sara ko", "send Sara a WhatsApp saying…"

**Happy path:**
1. User: "WhatsApp Sara that I'll be 10 minutes late."
2. Assistant → `resolve_contact("Sara", "whatsapp")` **immediately** (read-only,
   no confirmation). 
3. Backend checks app-saved contacts first, then asks the **device** to match
   its own contacts — returns a **masked number** (`+971 ••• ••67`) + opaque
   `contact_id`. The real number never reaches the server (privacy-preserving).
4. Assistant reads back: *"I'll WhatsApp Sara at +971 ••• ••67: 'I'll be 10
   minutes late.' Shall I send it?"* (recipient + message in **one** read-back,
   P2).
5. User: "Yes."
6. Assistant → `send_whatsapp(contact_id, message)` → backend returns
   `action: open_messaging` → **app opens WhatsApp with text pre-filled.**
7. Assistant: *"I've opened WhatsApp with your message ready — just tap send."*
   (**not "sent"**, P3.)

**Confirmation rule:** mandatory read-back of **recipient (masked) + message**
before send. Single "yes".

**Edge cases & recovery (P5, P7):**
| Situation | `resolve_contact` status | Assistant behaviour |
| --------- | ------------------------ | ------------------- |
| Two "Sara"s | `ambiguous` (options[]) | *"I see Sara Khan and Sara Ali — which one?"* |
| Found, no number | `no_number` | *"Sara's in your contacts but has no number — what's the number?"* |
| Not in contacts | `not_found` | *"I couldn't find Sara — tell me the number, or say 'save Sara' first."* |
| Contacts permission off | `permission_denied` | *"I need Contacts access to find Sara — or just give me the number."* |
| Index slow | `index_unavailable` | *"One sec…"* then retry once. |
| User gives raw number | (skip resolve) | confirm the number aloud, then send. |

**Honest wording:** deep link = "opened, tap send". Never "sent" (wa.me cannot
auto-send). ([appsflyer](https://www.appsflyer.com/blog/deep-linking/whatsapp-deep-link/))

**Fallback:** no number anywhere → offer `save_contact` or ask for the number.

**Result card (UI):** recipient name + masked number, message preview, a
"Open WhatsApp" affordance, status chip ("Ready to send in WhatsApp").

**Hard constraints to respect:** number must be **plain international digits, no
`+`/0/spaces**; **auto-send needs WhatsApp Business Cloud API + opt-in + 24h
window / approved templates** — out of scope for personal sends, so deep-link is
the right Phase-1 choice.
([Callbell](https://callbellsupport.zendesk.com/hc/en-us/articles/360018385118),
[enchant 24h rule](https://www.enchant.com/whatsapp-business-platform-24-hour-rule))

**Current gap:** ❌ `open_messaging` not opened on device (BUG 1) · ⚠️ weak phone
validation · ⚠️ model may say "sent" — tighten prompt to the exact wording above.

---

### 2.2 ⭐ Telegram — `send_telegram`

**Benchmark + hard limit:** a Telegram **bot cannot message a user who hasn't
pressed START**; deep links only carry a start-param, they don't bypass opt-in;
free message body can't be pre-filled to an arbitrary person.
([core.telegram.org/bots/features](https://core.telegram.org/bots/features),
[core.telegram.org/api/links](https://core.telegram.org/api/links))

**Trigger:** "Telegram Ahmed", "TG bhejo Ahmed ko saying…"

**Two honest paths (auto-selected):**
1. **Connected bot (true send):** recipient previously started the FarryOn bot
   (`/start` → `telegram_chat_id` saved via `/webhook/telegram`). Assistant
   confirms, then sends via Bot API → *"Sent on Telegram."* (truly delivered).
2. **Deep-link fallback:** assistant confirms, opens `t.me/<username>`, and says
   honestly: *"I've opened Ahmed's Telegram chat — Telegram won't let me
   pre-type the message, so please paste/type it."* (P3 — be explicit about the
   limitation.)

**Confirmation rule:** read back recipient + message, single "yes".

**Edge cases:**
| Situation | Behaviour |
| --------- | --------- |
| No saved handle | *"I don't have Ahmed's Telegram — what's their @username?"* then `save_contact`. |
| Bot blocked | API returns blocked → *"Ahmed has blocked the bot — I can't send there."* |
| Has @username only (no chat_id) | deep-link path + honest "type it" wording. |

**Fallback:** suggest WhatsApp/SMS if no Telegram handle exists.

**Result card:** recipient + via-bot/deep-link badge, message preview, delivery
state ("Sent" only on real API success).

**Current gap:** ✅ logic mostly correct (bot vs deep-link, blocked detection) ·
⚠️ **bot-path send is non-idempotent** (retry → double-send) — add idempotency
(see §3) · ⚠️ ensure the "type it" honesty wording is in the prompt.

---

### 2.3 ⭐ Landmark / Place — `identify_image` (kind=landmark/auto)

**Benchmark:** Google Lens Places + Maps Live View + Apple Visual Look Up.
Common pattern: **scan feedback → confidence-gated result → rich place card →
navigate action → silence/fallback when unsure.**
([lens.google](https://lens.google/howlensworks/),
[Maps Live View](https://support.google.com/maps/answer/9332056),
[Apple Visual Look Up](https://support.apple.com/guide/iphone/iph21c29a1cf/ios))

**Trigger:** "What is this place / building / landmark?", "kya hai saamne",
"identify this".

**Happy path:**
1. Assistant → instant ack *"Let me look…"* + a **scanning state** in the UI (P6).
2. `identify_image` runs Google Vision LANDMARK + Gemini vision in parallel
   ([`vision.py`](../backend/app/services/vision.py)).
3. **Confidence tiers (P4):**
   - **Famous landmark (Google match):** confident → *"That's the Burj Khalifa
     in Dubai."* + precise GPS, Maps link, Wikipedia summary.
   - **Place but not famous (Gemini place-kind):** *"This looks like a heritage
     fort — I'm fairly sure but not certain."* + Maps **search** link + Wikipedia
     if any.
   - **Low/zero confidence:** **do not invent a name.** *"I can't identify this
     place confidently — want me to search the web or open it in Maps?"*
4. Speak the name + 1–2 key facts; show the card.

**Place card (research-backed useful fields):** name + category · short
description/history (cited Wikipedia) · distance/direction (if GPS) · **Open in
Maps / Navigate** · photo. ([Samsung Bixby](https://insights.samsung.com/2017/06/09/4-ways-bixby-vision-uses-image-recognition-technology-to-help-you-work-without-boundaries/))

**Edge cases & coaching (P5, P7):**
| Situation | Behaviour |
| --------- | --------- |
| No fresh frame / camera off | *"I can't see a clear view — point the camera at the building and ask again."* (stale-guard already exists, 10s.) |
| Pointed at trees/people/sky | coach: *"Try aiming at the building or a sign."* (Maps Live View does exactly this.) |
| Vision API error/quota | friendly: *"I couldn't scan that just now — try once more?"* (must be wrapped — see §3.) |
| Not confident | offer web search / Maps — never a fabricated name. |

**Latency:** instant scanning feedback; aim for a result in ~1–2s; show the
"scanning" cue so it never feels frozen. ([AR latency norms](https://arxiv.org/pdf/2409.04018))

**Current gap:** ⚠️ `identify_image` doesn't wrap `run_detection` (raw error can
reach the model — see §3) · ⚠️ no explicit confidence phrasing in the prompt ·
⚠️ no UI scanning state/animation · ✅ stale-frame guard present.

---

### 2.4 ⭐ Product / Object — `identify_image` (kind=product/auto)

**Benchmark:** Google Lens Shopping, Amazon Lens/Lens Live, Pinterest Lens.
Pattern: **fast first guess → ranked candidate grid (not one verbal answer) →
exact vs similar tiers → buy links → text refine → fallback.**
([blog.google Lens shopping](https://blog.google/products-and-platforms/products/shopping/visual-search-lens-shopping/),
[Amazon Lens Live](https://www.aboutamazon.com/news/retail/search-image-amazon-lens-live-shopping-rufus),
[Pinterest Lens](https://newsroom-archive.pinterest.com/shop-with-your-camera-pinterest-launches-shop-tab-on-lens-visual-search-results))

**Trigger:** "What product is this?", "where can I buy this?", "scan this".

**Happy path:**
1. Instant ack + scanning state (P6).
2. Gemini vision names the item; Google WEB_DETECTION adds matching pages +
   similar images; marketplace links built from the name.
3. **Confidence tiers (P4 — critical to avoid hallucinated brands):**
   - **Exact/branded match:** name it — *"This is a Sony WH-1000XM5 headphone."*
     + price-context + buy links.
   - **Generic object:** **do not invent a brand.** Use a category descriptor —
     *"This looks like an over-ear headphone — here are similar ones to shop."*
     ([Amazon's own copy: "exact **or similar** items"](https://www.aboutamazon.com/news/retail/how-to-use-amazon-lens))
4. Speak the name/category + one useful fact; show the card.

**Product card (research-backed):** product/category name · short AI explanation
(what it is, key features, who it's for — already produced by `vision.py`) ·
**regional marketplace links** (Amazon.ae/.sa/.in, Noon, Flipkart, eBay…) ·
matching pages · similar images. Keep links **tight and relevant** — users read
the top 3–7; dead/irrelevant links destroy trust.
([search UX ranking](https://wizzy.ai/blog/ecommerce-search-ux-mistakes/))

**Edge cases & fallback (P5):**
| Situation | Behaviour |
| --------- | --------- |
| No match | *"I'm not sure what brand this is — want me to web-search it, or show similar items?"* |
| Multiple plausible items | present as candidates (UI grid), not one confident name. |
| Text refine | allow *"the blue one / the Nike one"* to refine the query. |

**Current gap:** ⚠️ same `run_detection` wrap issue · ⚠️ no explicit "don't invent
a brand" confidence phrasing in the prompt · ⚠️ result card could rank/limit
links better · ✅ regional marketplaces + AI explanation already implemented.

---

### 2.5 Notes — `create_note`, `list_notes`, `delete_note`

**Trigger:** "remember…", "note that…", "read my notes", "delete the note about…".

**Journey:** create → read back *"Save the note 'buy milk'?"* → "yes" → persist →
*"Saved."* List/read needs no confirmation. Delete is a change → confirm the
matched note before deleting.

**Edge cases:** empty note → *"What should the note say?"* · delete with multiple
matches → *"You have two notes mentioning 'milk' — which one?"* (don't delete the
first blindly).

**Current gap:** ⚠️ empty/length not validated · ⚠️ not idempotent (retry →
duplicate) · ⚠️ no `{ok}` envelope · ⚠️ delete uses first fuzzy match.

---

### 2.6 Tasks / Reminders — `create_task`, `update_task`, `complete_task`, `delete_task`, `list_tasks`

**Trigger:** "remind me in 20 minutes…", "add a task…", "what's due", "mark X
done", "move X to 5pm".

**Journey:** create → read back **title + resolved time** *"Reminder 'call mom'
today at 5pm — set it?"* → "yes" → persist + the phone schedules a real
notification. Relative times → `remind_in_seconds`; absolute → ISO `due_date`.

**Edge cases:** ambiguous time → ask · malformed date → re-ask, don't store junk ·
"mark it done" with two matches → disambiguate · update/complete/delete confirm
the matched task aloud.

**Current gap:** ⚠️ `due_date` never ISO-validated (junk persists and ships to the
alarm) · ⚠️ `update_task` `assert` can crash on a race · ⚠️ first-match fuzzy ·
⚠️ no `{ok}` envelope · ⚠️ create not idempotent.

---

### 2.7 Email — `read_emails`, `read_email`, `send_email`

**Trigger:** "any new email", "read the email from Faraz", "reply saying…",
"email Ali that I'll be late".

**Journey:**
- **Read:** summarize briefly out loud (sender + subject + snippet); no
  confirmation. Best-implemented tool today (clean error handling).
- **Send:** draft → read back **recipient address + subject + body** → "yes" →
  send → *"Sent."* Replies must use the original `from_email` exactly — never
  guess an address.

**Edge cases:** unsure of address → ask, don't send · auth failure → *"I couldn't
sign in to your mail — check the app password."* · IMAP slow → "one sec".

**Hard rules:** **email send is non-idempotent — a retry double-sends.** Add an
idempotency key (§3). Validate the address properly (current check is just
`"@" in to`). ([email_send.py])

**Current gap:** ⚠️ weak address validation · ⚠️ **double-send on retry** · ⚠️ IMAP
has no socket timeout · ⚠️ raw `query` into IMAP search.

---

### 2.8 Web search — `web_search`

**Trigger:** "what's the latest…", "who/what is…", "score of…", prices, news.

**Journey:** instant ack *"let me check"* → search (Tavily→Serper fallback→mock)
→ state the most authoritative/recent answer confidently; if sources disagree,
say so. Never invent a fact. (System prompt already encodes this well.)

**Current gap:** ⚠️ empty query not rejected · ⚠️ no `{ok}` envelope · ✅ excellent
multi-provider resilience already.

---

### 2.9 Contacts — `resolve_contact`, `save_contact`

Covered under WhatsApp/Telegram. `save_contact`: confirm *"Save Sara, +971…?"* →
persist. **Validate & normalize the phone on save** (currently stored raw, which
then feeds `normalize_phone` garbage later). Fuzzy resolve + ambiguity prompt.

**Current gap:** ⚠️ no phone format validation/normalization on save · ⚠️
`resolve_contact` doesn't guard the device callback (can crash on None).

---

### 2.10 Location — `get_location`

"Where am I?" → returns cached GPS + reverse-geocoded address. No confirmation.
Clean. **Gap:** ✅ minimal — fine as is.

---

### 2.11 Device controls — `set_camera_zoom`, `set_camera`, `rotate_camera`, `mute_mic`, `end_session`

Client-executed, instant, no confirmation (read-only/UI actions). After zoom,
re-look at the next frame before answering (already in prompt). `end_session`
plays the goodbye then disconnects.

**Current gap:** ⚠️ inconsistent result shape (no `{ok}`) — cosmetic.

---

## 3. Cross-cutting engineering standards (the "missing layer")

These close the gaps that recur across many tools. Implement once, apply
everywhere.

### 3.1 Shared validation helpers (`app/tools/validators.py` — NEW)
| Helper | Rule |
| ------ | ---- |
| `clean_text(s, max_len)` | strip; reject empty; cap length (notes 2k, task title 512, message 4k, email body 25k). |
| `valid_email(s)` | proper regex (not just `"@" in s`). |
| `valid_phone(s, cc)` | normalize to digits+CC; **reject <7 or >15 digits**; strip leading 0; no `+`. |
| `parse_due_date(s)` | parse ISO-8601; reject unparseable; return canonical form (don't store "next tuesday"). |
| `coerce_enum(v, choices, default)` | already done ad-hoc — centralize. |

### 3.2 Uniform result envelope
Every `Tool.run` returns `{ok: bool, message?: str, ...}`. Tools currently
returning raw dicts (`create_note`, `create_task`, `list_*`, `web_search`,
camera/device) must add `ok: true`. The orchestrator already forwards
`result.error` on failure; ensure the model always gets a friendly `message`.

### 3.3 Per-tool exception wrapping
Wrap risky calls in each tool and return `{ok:false, message:<friendly>}`:
- `identify_image` → wrap `run_detection`.
- `resolve_contact` → guard `ctx.resolve_contact(...)` and a `None` return.
- mutating repo calls (`add_note/add_task/save_contact`) → catch DB errors.
- `update_task` → replace `assert` with a `not_found` result.
Keep the engine's 20s timeout as the backstop only.

### 3.4 Idempotency for outward sends (P0 safety)
`send_email` and `send_telegram` (bot path) can **double-send on a retried
turn**. Add a dedup keyed on the model's `call_id` (or a content hash within a
short window): if the same call_id was already executed successfully, return the
prior result instead of sending again.

### 3.5 Ambiguity over first-match
`complete/update/delete_task` and `delete_note` act on the **first** `ilike`
match. Return all matches when >1 and let the assistant ask which — never mutate
the wrong item.

### 3.6 Honesty enforcement in the system prompt
Add explicit wording rules: *"opened, tap send" vs "sent"*; *confidence phrasing
for vision (never assert a wrong name; offer web/Maps fallback)*; *never claim a
send succeeded on an `ok:false` or `open_*` action.*

---

## 4. Implementation roadmap (phased)

### Phase 0 — Stop the bleeding (P0, ~1–2 days)
- [ ] **BUG 1:** handle `open_messaging` on device → WhatsApp/SMS open. *(flagship fix)*
- [ ] **BUG 2:** Grok/OpenAI vision flag + manual-response safety net + connect-error fallback.
- [ ] **BUG 3:** JPEG decode → isolate; camera → medium; tail margin → ~400ms.
- [ ] `identify_image` exception wrap; `update_task` assert → result; `resolve_contact` guard.

### Phase 1 — Trust & honesty (P1, ~3–5 days)
- [ ] `validators.py` + apply to phone/email/date/text across all tools.
- [ ] Idempotency for `send_email` / `send_telegram`.
- [ ] Confidence phrasing + "opened vs sent" honesty in the system prompt.
- [ ] Ambiguity prompts for delete/complete/update.
- [ ] IMAP socket timeout + query escaping.

### Phase 2 — Professional polish (P2, ~3–5 days)
- [ ] Uniform `{ok}` envelope across every tool.
- [ ] UI: scanning state for `identify_image`; tighter, ranked product link list;
      richer place card (navigate action).
- [ ] Optional permission gating (`needsPermission`) for sends/deletes.
- [ ] Telemetry: per-tool success/error/latency dashboards (already have
      Prometheus hooks).

### Phase 3 — True automation (later)
- [ ] WhatsApp Business Cloud API for opted-in recipients (auto-send inside 24h
      window / templates). ([WhatsApp policy](https://business.whatsapp.com/policy))
- [ ] Live View-style AR overlays for landmarks; on-device fast-guess model for
      products (fast-first, cloud-enrich).

---

## 5. Source appendix (market benchmarks)

**Messaging:** [Google Assistant messaging](https://support.google.com/assistant/answer/9984245) ·
[Siri + WhatsApp](https://www.tuneskit.com/whatsapp/can-siri-send-whatsapp-messages.html) ·
[wa.me deep links](https://www.appsflyer.com/blog/deep-linking/whatsapp-deep-link/) ·
[WhatsApp 24h window](https://www.enchant.com/whatsapp-business-platform-24-hour-rule) ·
[Telegram bot rules](https://core.telegram.org/bots/features) ·
[voice-agent design](https://docs.cloud.google.com/dialogflow/cx/docs/concept/voice-agent-design)

**Landmark:** [Google Lens](https://lens.google/howlensworks/) ·
[Maps Live View](https://support.google.com/maps/answer/9332056) ·
[Apple Visual Look Up](https://support.apple.com/guide/iphone/identify-objects-in-your-photos-and-videos-iph21c29a1cf/ios) ·
[Bixby Vision](https://insights.samsung.com/2017/06/09/4-ways-bixby-vision-uses-image-recognition-technology-to-help-you-work-without-boundaries/) ·
[NN/g AI hallucinations](https://www.nngroup.com/articles/ai-hallucinations/)

**Product:** [Google Lens Shopping](https://blog.google/products-and-platforms/products/shopping/visual-search-lens-shopping/) ·
[Amazon Lens](https://www.aboutamazon.com/news/retail/how-to-use-amazon-lens) ·
[Amazon Lens Live](https://www.aboutamazon.com/news/retail/search-image-amazon-lens-live-shopping-rufus) ·
[Pinterest Lens](https://newsroom-archive.pinterest.com/shop-with-your-camera-pinterest-launches-shop-tab-on-lens-visual-search-results) ·
[search UX mistakes](https://wizzy.ai/blog/ecommerce-search-ux-mistakes/)
