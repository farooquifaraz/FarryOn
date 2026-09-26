# WhatsApp inbox — read incoming messages, reply hands-free

**Status:** design, ready to implement · **Scope:** Android only · **Default:** OFF

This is the implementation spec for the feature. It is written for whoever
builds it — every change is named by file, every new wire message has its JSON,
and every existing behaviour that must stay identical is listed with the test
that proves it. Read `PROTOCOL.md` and `backend/app/tools/contacts.py` first:
this feature is the same shape as contact resolution, and copies it on purpose.

---

## 1. What it does

```
Ahmed sends a WhatsApp → phone notification
      │
      ▼  (phone → backend: sender only, NO text)
Farry: "Ahmed ka message aaya hai. Padhun?"
      │
   👤 "Haan padho"
      │
      ▼  (backend → phone: read_messages → phone returns the text)
Farry: "Ahmed kehta hai — 10 min late hu."
      │
   👤 "Bol do koi baat nahi"
      │
      ▼  (backend → phone: reply_message → phone replies through the
          notification's own Reply action; WhatsApp never opens)
Farry: "Bhej diya."
```

Three rules the design is built around:

1. **Message text stays on the phone** until the user asks to hear it. The
   backend learns *that* a message arrived and from whom; it gets the body only
   inside a `read_messages` round-trip the user triggered.
2. **No WhatsApp API, no linked device, no scraping.** The phone reads its own
   notifications (Android `NotificationListenerService`, the same mechanism
   car apps and watches use) and replies through the notification's
   `RemoteInput` action. Nothing touches WhatsApp's servers or terms.
3. **Off by default, additive everywhere.** A backend with the flag off and an
   app without the toggle behave exactly as today. Section 8 lists the tests
   that pin this.

### Non-goals (say no if asked)

- iOS. Apple does not let third-party apps read notifications. Not possible.
- Message history, media contents, voice notes. Only what a notification carries
  (text; media shows as "📷 Photo").
- Any other messaging app in this pass. The wire format carries an `app` field
  so Telegram/SMS can follow without a protocol change.
- The business inbox (messages sent *to* FarryOn's own number). Different
  problem — Cloud API webhooks — not this.

---

## 2. The pattern being copied: a device round-trip

The backend already asks the phone for things it must not hold itself. Contact
resolution (`resolve_contact` tool) works like this today:

```
tool.run()  ──►  ctx.resolve_contact(name)          [ToolContext callable]
                    │  orchestrator.request_contact_resolution()
                    │    creates Future keyed by requestId
                    │    _safe_notify({"type":"resolve_contact_request", …})
                    │    await asyncio.wait_for(future, 8.0)
                    ▼
            phone: _handleResolveContactRequest()   [live_controller.dart]
                    │  looks up locally, masks numbers
                    │  _client.send(ResolveContactResultMessage(…))
                    ▼
     session.py: elif mtype == "resolve_contact_result":
                    orchestrator.resolve_pending(requestId, message)   → Future done
```

Files that implement it (read them; the new code sits next to each):

| Layer | File | What |
| --- | --- | --- |
| Tool | `backend/app/tools/contacts.py` `ResolveContactTool` | calls `ctx.resolve_contact`, maps timeouts to a status |
| Context | `backend/app/tools/base.py` `ToolContext.resolve_contact` | optional callable, `None` outside a live session |
| Orchestrator | `backend/app/agent/orchestrator.py` `request_contact_resolution`, `resolve_pending`, `_pending_resolves` | Future map + timeout |
| Session | `backend/app/ws/session.py` ~L1223 `elif mtype == "resolve_contact_result"` | routes the reply |
| Wire types (app) | `mobile/lib/protocol/protocol.dart` `MsgType`, `mobile/lib/protocol/messages.dart` | typed messages, `UnknownServerMessage` fallback |
| Handler (app) | `mobile/lib/state/live_controller.dart` ~L1789 `case ResolveContactRequestMessage()` → `_handleResolveContactRequest` | does the work, replies |
| Native template | `mobile/android/.../CallChannel.kt` `register(messenger, app)` | MethodChannel shape |
| Tests | `backend/tests/test_orchestrator.py` (round-trip with a fake notifier), `backend/tests/test_all_tools_validation.py` (every tool dispatches with no bridge) | copy these |

This feature adds **two more round-trips of the same shape** and **one
phone → backend notification**. It does not modify `request_contact_resolution`
or `resolve_pending`; it adds a small generic version beside them.

---

## 3. Wire protocol (add to `PROTOCOL.md`)

### 3.1 Client → Server

```jsonc
// The phone saw a new message notification. Sent only while a live session is
// connected AND the user has turned the inbox on. Carries NO text on purpose.
{ "type": "message_arrived",
  "app": "whatsapp",                 // "whatsapp" | "whatsapp_business"
  "id": "n:0|com.whatsapp|…|1727340000",  // opaque, stable per notification line
  "sender": "Ahmed",                 // display name from the notification
  "isGroup": false,
  "group": null,                     // group title when isGroup
  "ts": 1727340000123 }

// Reply to read_messages_request (see 3.2).
{ "type": "read_messages_result",
  "requestId": "…",
  "status": "ok",                    // ok | disabled | permission_denied | empty | error
  "messages": [
    { "id": "…", "app": "whatsapp", "sender": "Ahmed", "isGroup": false,
      "group": null, "text": "10 min late hu", "ts": 1727340000123,
      "unread": true, "canReply": true }
  ] }

// Reply to reply_message_request.
{ "type": "reply_message_result",
  "requestId": "…",
  "status": "sent",                  // sent | expired | no_reply_action | disabled | error
  "id": "…" }
```

### 3.2 Server → Client

```jsonc
{ "type": "read_messages_request",
  "requestId": "…",
  "limit": 5,                        // 1–10
  "since": "new",                    // "new" (unread since last read) | "all" (buffer)
  "sender": null }                   // optional case-insensitive sender filter

{ "type": "reply_message_request",
  "requestId": "…",
  "id": "…",                         // a message id from a read_messages_result
  "text": "Koi baat nahi, main wait kar raha hu" }
```

Unknown `type` values are already ignored on both sides (`session.py`
`control.unknown_type` warning; `messages.dart` `UnknownServerMessage`), so an
old app against a new backend, or the reverse, degrades to "feature absent".

While there, document the existing `resolve_contact_request` /
`resolve_contact_result` pair in the same section — it is implemented but not
in `PROTOCOL.md` today.

---

## 4. Backend changes (Python)

### 4.1 `backend/app/config.py` — one flag

```python
# -- WhatsApp inbox (read incoming messages, reply from the notification) ----
# OFF by default. When off the two inbox tools are not offered to the model,
# message_arrived notes are ignored, and nothing about a session changes.
# Android only: the phone reads its own notifications; the server never holds
# message text beyond the one tool call that reads it aloud.
messages_inbox_enabled: bool = Field(default=False)
```

### 4.2 `backend/app/tools/base.py` — two optional callables on `ToolContext`

Add beside `resolve_contact` (same style, default `None`):

```python
#: Round-trip to the phone for the buffered message notifications. Text
#: comes back only through this call. Signature:
#: ``await read_messages({"limit", "since", "sender"}) -> dict``.
read_messages: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
#: Round-trip to the phone to reply through the notification's own Reply
#: action. Signature: ``await reply_message({"id", "text"}) -> dict``.
reply_message: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None
```

Defaults of `None` mean every existing tool, test and harness constructs
`ToolContext` exactly as before.

### 4.3 `backend/app/tools/messages_inbox.py` — new file, two tools

```python
class ReadMessagesTool(Tool):
    name = "read_messages"
    description = (
        "Read the user's recent WhatsApp messages from their phone — call it when "
        "they ask to hear their messages, or say yes after you offered to read a "
        "new one. Read-only. Returns status ok with messages, or unavailable / "
        "disabled / permission_denied / empty. NEVER invent a message; if the "
        "status is not ok, say what it is."
    )
    parameters = {
        "type": "object",
        "properties": {
            "limit":  {"type": "integer", "minimum": 1, "maximum": 10},
            "since":  {"type": "string", "enum": ["new", "all"]},
            "sender": {"type": "string", "description": "Only from this person"},
        },
    }
    async def run(self, ctx, **kw):
        if ctx.read_messages is None:
            return {"ok": True, "status": "unavailable",
                    "message": "Messages are not available on this device."}
        limit = max(1, min(10, int(kw.get("limit") or 5)))
        since = kw.get("since") if kw.get("since") in ("new", "all") else "new"
        sender = (kw.get("sender") or "").strip() or None
        res = await ctx.read_messages({"limit": limit, "since": since, "sender": sender})
        # res is the device payload or {"status": "unavailable"} on timeout
        return {"ok": True, **res}

class ReplyMessageTool(Tool):
    name = "reply_message"
    description = (
        "Reply to a WhatsApp message the user just heard, from their own phone, "
        "without opening WhatsApp. Needs the message id from read_messages and "
        "the exact text. Only after the user confirmed the text. Returns sent, "
        "or expired / no_reply_action / disabled / unavailable — on anything but "
        "sent, offer send_whatsapp instead."
    )
    parameters = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
        "required": ["id", "text"],
    }
    async def run(self, ctx, **kw):
        mid = (kw.get("id") or "").strip(); text = (kw.get("text") or "").strip()
        if not mid or not text:
            return {"ok": False, "status": "invalid", "message": "Need id and text."}
        if len(text) > 1000:
            return {"ok": False, "status": "invalid", "message": "Too long."}
        gate = rate_gate(ctx, "reply_message")          # same guard send_whatsapp uses
        if gate: return gate
        if ctx.reply_message is None:
            return {"ok": True, "status": "unavailable"}
        res = await ctx.reply_message({"id": mid, "text": text})
        sent = res.get("status") == "sent"
        # `message` + `channel` + `sent` are what _log_send_if_messaging reads.
        return {"ok": True, "channel": "whatsapp", "message": text, "sent": sent, **res}
```

Reuse `rate_gate` from `app/tools/safety.py` exactly as `send_whatsapp` does
(read that file for the call shape). Keep both tools in this one module.

### 4.4 `backend/app/tools/__init__.py` — register

Import both, add to `__all__`, append to the list in `build_default_tools()`.
They are registered unconditionally so `test_all_tools_validation.py` covers
them; the flag is applied where the engine is built (4.7).

### 4.5 `backend/app/agent/orchestrator.py` — a generic round-trip + two hooks

Add, **without touching** `request_contact_resolution` / `resolve_pending`:

```python
# in __init__, beside _pending_resolves:
self._pending_device: dict[str, asyncio.Future[dict[str, Any]]] = {}

async def request_device(self, kind: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    """Ask the phone for `kind` and await `{kind}_result`. Same shape as
    request_contact_resolution; generic so the next device feature adds no
    orchestrator code."""
    request_id = uuid.uuid4().hex
    future = asyncio.get_running_loop().create_future()
    self._pending_device[request_id] = future
    try:
        await self._safe_notify({"type": f"{kind}_request", "requestId": request_id, **payload})
        return await asyncio.wait_for(future, timeout=timeout)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return {"status": "unavailable"}
    finally:
        self._pending_device.pop(request_id, None)

def device_reply(self, request_id: str, payload: dict[str, Any]) -> None:
    future = self._pending_device.get(request_id)
    if future is not None and not future.done():
        future.set_result(payload)
```

Wire into the `ToolContext(...)` construction (~L372):

```python
read_messages=lambda p: self.request_device("read_messages", p, 6.0),
reply_message=lambda p: self.request_device("reply_message", p, 10.0),
```

Two set updates:

- `_SEND_TOOLS`: add `"reply_message"` so `_log_send_if_messaging` records
  `whatsapp:delivered` and "what did I send" keeps working.
- New `_PRIVATE_RESULT_TOOLS = {"read_messages"}`: before `repo.record_tool_call(...)`
  (~L395), if `event.name` is in it, pass `result={"status": …, "count": len(messages),
  "redacted": True}` instead of the real result. **Message bodies must not land in
  the tool audit table.** (`read_emails` has the same exposure today — out of
  scope here, note it as a follow-up.)

### 4.6 `backend/app/ws/session.py` — three new `elif` branches

Add next to `elif mtype == "resolve_contact_result":` (~L1223):

```python
elif mtype in ("read_messages_result", "reply_message_result"):
    req_id = message.get("requestId")
    if isinstance(req_id, str) and self._orchestrator is not None:
        self._orchestrator.device_reply(req_id, message)

elif mtype == "message_arrived":
    # Sender only — never text. A silent note puts the fact in the model's
    # context so it can OFFER to read; the read itself is a tool call the
    # user has to say yes to. Copies the call_state pattern above.
    if get_settings().messages_inbox_enabled and self._message_note_allowed():
        sender = str(message.get("sender") or "someone")[:60]
        group = str(message.get("group") or "")[:60]
        where = f" in the group {group}" if message.get("isGroup") and group else ""
        note_fn = getattr(self._gateway, "send_silent_note", None)
        if callable(note_fn):
            await note_fn(
                f"(New WhatsApp message from {sender}{where}. Offer to read it "
                f"in one short sentence; do not read or guess its contents until "
                f"the user says yes.)"
            )
```

`_message_note_allowed()`: at most one note per 20 s per session, and
coalesce — if several arrive in the window, the next note says "New WhatsApp
messages from Ahmed and 2 others". Keep the counter on the session object.

`send_silent_note` exists on the Gemini gateway only; the `getattr` guard makes
this a no-op on OpenAI, which is the intended behaviour, not a bug.

### 4.7 `backend/app/ws/live.py` — apply the flag

Where the engine is built (~L92, `build_default_tools()`):

```python
tools = build_default_tools()
if not settings.messages_inbox_enabled:
    tools = [t for t in tools if t.name not in ("read_messages", "reply_message")]
```

With the flag off the model never sees the tools — the exact tool list of
today (pinned by a test, section 8).

### 4.8 `backend/app/prompts/system.py` — guidance, flag-gated

In `build_system_prompt(...)` append this paragraph **only when
`messages_inbox_enabled`** (do not edit `SYSTEM_PROMPT` itself):

> **Messages.** If a note says a WhatsApp message arrived, offer to read it in
> one short sentence and stop. Read only after the user says yes, with
> `read_messages`. Read each message as "<sender> says: <text>". To reply, say
> the exact text back, wait for an explicit yes, then `reply_message(id, text)`.
> If it returns anything but `sent`, offer `send_whatsapp`. Never invent a
> message and never say one was sent unless the tool said `sent`.

Also add `reply_message` to the existing confirm-before-send rule (the line
that lists `send_whatsapp / send_telegram / send_message`).

### 4.9 `backend/app/web/router.py` — privacy page

Under **"Email, WhatsApp and Telegram"** add one bullet:

> If you turn on *Read my WhatsApp messages*, FarryOn reads the notifications
> WhatsApp shows on your phone. Who a message is from reaches our server while
> you are in a session; the text of a message is sent only when you ask to
> hear it, and only to the AI provider that answers you. It is not stored.
> Turn it off any time in Settings or in Android's notification access.

---

## 5. Mobile changes (Flutter + Kotlin)

### 5.1 `mobile/android/app/src/main/AndroidManifest.xml`

Inside `<application>`, next to the existing `<service>` entries:

```xml
<!-- WhatsApp inbox: the system binds this only after the user grants
     "Notification access" in Android settings. Inert until then. -->
<service
    android:name=".messages.MessageNotificationListener"
    android:exported="true"
    android:permission="android.permission.BIND_NOTIFICATION_LISTENER_SERVICE">
    <intent-filter>
        <action android:name="android.service.notification.NotificationListenerService" />
    </intent-filter>
</service>
```

`exported="true"` is required (the OS binds it); the `permission` attribute is
what keeps anyone but the OS from doing so. No `<uses-permission>` is needed
— this access is granted by the user in Settings, not at install.

### 5.2 `mobile/android/.../messages/MessageNotificationListener.kt` — new

```kotlin
class MessageNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in PACKAGES) return                     // com.whatsapp, com.whatsapp.w4b
        val n = sbn.notification
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return  // "3 new messages"
        val ex = n.extras
        val lines = MessagingStyle-lines(ex)                          // EXTRA_MESSAGES → [{text, sender, time}]
                    ?: single-line(ex)                                // EXTRA_TITLE + EXTRA_BIG_TEXT/EXTRA_TEXT
        if (lines.isEmpty() || looksLikeStatus(lines)) return         // "Checking for new messages…"
        val isGroup = ex.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION)
        val group   = ex.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()
        val reply   = n.actions?.firstOrNull { it.remoteInputs?.isNotEmpty() == true }
        MessageInbox.add(sbn.key, lines, isGroup, group, reply)       // dedupe inside
    }
    companion object { val PACKAGES = setOf("com.whatsapp", "com.whatsapp.w4b") }
}
```

`MessageInbox` (Kotlin `object`, same file or `MessageInbox.kt`):

- ring buffer, 30 entries, 24 h TTL, dedupe key = `sbn.key + line.time + hash(text)`;
- keeps the `Notification.Action` per entry for reply; `expired` when the
  notification is gone and the PendingIntent is cancelled;
- `unread` flag, cleared by `markRead(ids)`;
- `clear()` when the user turns the feature off;
- emits **metadata only** (`{id, app, sender, isGroup, group, ts}`) to a
  listener the channel registers — the text is returned only by `list()`.

Reply (in `MessageInbox.reply(id, text)`):

```kotlin
val action = entry.replyAction ?: return "no_reply_action"
val ri = action.remoteInputs.first()
val intent = Intent()
RemoteInput.addResultsToIntent(action.remoteInputs, intent, Bundle().apply { putCharSequence(ri.resultKey, text) })
try { action.actionIntent.send(ctx, 0, intent); "sent" }
catch (e: PendingIntent.CanceledException) { "expired" }
```

This is exactly how Android Auto and Wear OS reply; WhatsApp then updates its
own notification. Do not cancel the notification yourself.

### 5.3 `mobile/android/.../messages/MessagesChannel.kt` — new, shaped like `CallChannel`

`MethodChannel("com.farryon/messages")`:

| method | returns |
| --- | --- |
| `isEnabled` | `NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(packageName)` |
| `openSettings` | starts `Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS` |
| `list({limit, since, sender})` | `[{id, app, sender, isGroup, group, text, ts, unread, canReply}]` — the only call that returns text |
| `reply({id, text})` | `sent \| expired \| no_reply_action` |
| `markRead({ids})` | — |
| `clear` | — |

`EventChannel("com.farryon/messages/events")`: one event per new entry,
**metadata only**. When no Flutter engine is attached (app killed) the sink is
null and the entry just stays in the buffer for the next `list()`.

Register in `MainActivity.configureFlutterEngine` beside `CallChannel.register(...)`.

### 5.4 `mobile/lib/protocol/protocol.dart` + `messages.dart`

`MsgType` constants: `messageArrived`, `readMessagesRequest`, `readMessagesResult`,
`replyMessageRequest`, `replyMessageResult`.

Classes, copying `ResolveContactRequestMessage` / `ResolveContactResultMessage`
line for line: `ReadMessagesRequestMessage`, `ReplyMessageRequestMessage`
(server → client, add to the `fromJson` switch), `MessageArrivedMessage`,
`ReadMessagesResultMessage`, `ReplyMessageResultMessage` (client → server).

### 5.5 `mobile/lib/features/messages/messages_bridge.dart` — new

Thin Dart wrapper over the two channels: `isEnabled()`, `openSettings()`,
`list(...)`, `reply(...)`, `markRead(...)`, `clear()`, and a
`Stream<MessageMeta> arrivals`. Unit-test it with a mocked `MethodChannel`
the way `glasses_channel.dart` is tested.

### 5.6 `mobile/lib/state/live_controller.dart`

Three additions, no changes to existing branches:

- in the server-message `switch` (~L1789): `case ReadMessagesRequestMessage(): unawaited(_handleReadMessages(msg));`
  and `case ReplyMessageRequestMessage(): unawaited(_handleReplyMessage(msg));`
- `_handleReadMessages`: if the setting is off → `status: disabled`; if
  `!isEnabled()` → `permission_denied`; else `bridge.list(...)`, send
  `ReadMessagesResultMessage`, then `markRead` for what was returned.
- `_handleReplyMessage`: `bridge.reply` → `ReplyMessageResultMessage`.
- subscribe to `bridge.arrivals` **while connected and the setting is on**;
  forward each as `MessageArrivedMessage`. Unsubscribe on disconnect.

### 5.7 `mobile/lib/features/settings/settings_screen.dart`

One `SwitchListTile` — *"Read my WhatsApp messages"* — with a two-line
subtitle saying what it does and that text leaves the phone only when they ask.
Turning it on: `openSettings()`, then re-check `isEnabled()` on resume; the
switch reflects the real permission state, not what was tapped. Turning it
off: `clear()`. Persist the preference the way the other settings are.

---

## 6. Sequence, end to end

```
phone                          backend (session.py / orchestrator)         model
─────                          ───────────────────────────────────         ─────
listener: notification  ─┐
inbox.add (text stays)   │
EventChannel (meta)  ────┼──► message_arrived ──► send_silent_note ──────► "(New WhatsApp from Ahmed…)"
                         │                                                  "Ahmed ka message aaya. Padhun?"
                         │                                        user: "haan"
                         │                        tool_call read_messages ◄─┘
                         │◄── read_messages_request ◄── request_device()
bridge.list() (text) ────┼──► read_messages_result ──► device_reply() ──► tool_result {messages:[…]}
markRead                 │                          audit row: redacted    "Ahmed kehta hai: 10 min late hu"
                         │                                        user: "bol do koi baat nahi"
                         │                                                  "Bhej du — 'Koi baat nahi'?"  user: "haan"
                         │◄── reply_message_request ◄── request_device()
RemoteInput → WhatsApp ──┼──► reply_message_result {sent} ─► device_reply ─► tool_result {sent:true}
                         │                          _log_send_if_messaging   "Bhej diya."
```

---

## 7. Edge cases the implementation must handle

| Case | Behaviour |
| --- | --- |
| Feature off in app, backend on | app answers `disabled`; model says it's off and how to turn it on |
| Notification access revoked in Android | `permission_denied`; switch in Settings shows off on next resume |
| WhatsApp "hide message content" on | lines have no text → skip entry; `read_messages` → `empty` |
| Several messages, one notification | `EXTRA_MESSAGES` yields each line as its own entry |
| Group chat | `isGroup`, `group`, per-line `sender`; note says "in the group X" |
| Same message posted again (WhatsApp re-posts on update) | dedupe key drops it |
| Notification dismissed before read | entry stays in buffer until TTL; reply may return `expired` |
| Reply after WhatsApp updated/cleared the notification | `expired` → model offers `send_whatsapp` (existing deep-link path) |
| Message during assistant speech | note is silent; no barge-in, no announcement mid-sentence |
| Burst of messages | notes rate-limited (1 / 20 s) and coalesced |
| App killed, session not running | buffer fills; nothing is sent; next session's `read_messages since=new` returns them |
| OpenAI provider | notes are a no-op (no `send_silent_note`); tools still work on request |
| Older app / iOS | requests time out → `unavailable`; model says messages aren't available on this device |

---

## 8. Zero-impact guarantees and the tests that pin them

Add `backend/tests/test_messages_inbox.py`:

1. **Flag off → engine unchanged.** Build the engine as `live.py` does with the
   flag off and assert the set of tool names equals today's set (snapshot the
   list from `main` in the test). This is the one that matters most.
2. **`ToolContext()` still constructs with no new args** — every existing tool
   test does this implicitly; add an explicit one.
3. **Tools with no bridge** return `status: unavailable` (this is also what
   `test_all_tools_validation.py` will exercise — add both tools to its table:
   `read_messages` → `unavailable`; `reply_message` with empty text → `invalid`).
4. **Round-trip** via `request_device` + `device_reply` with a fake notifier —
   copy `test_orchestrator.py::test_resolve_pending_unknown_id_is_harmless` and
   the success case above it.
5. **Timeout → unavailable**, future removed from `_pending_device`.
6. **Session dispatch**: `read_messages_result` reaches `device_reply`;
   `message_arrived` with the flag off does nothing; with it on calls
   `send_silent_note` once, and a second within 20 s does not.
7. **Audit redaction**: after a `read_messages` result with two messages the
   recorded row has `redacted: True`, `count: 2`, and none of the text.
8. **Prompt**: `build_system_prompt` contains the messages paragraph only when
   the flag is on.
9. **Rate gate** on `reply_message` behaves like `send_whatsapp`'s.

Mobile: `messages_bridge_test.dart` with a mocked channel; and one
`live_controller` test that a `ReadMessagesRequestMessage` with the setting off
produces `status: disabled` without touching the bridge.

Existing suites must pass untouched: `test_orchestrator.py`,
`test_all_tools_validation.py`, `test_ws_live.py`, `test_site_*.py`.

---

## 9. Build order

1. **Backend, flag off** — 4.1–4.8 + tests. Mergeable on its own: nothing
   user-visible changes.
2. **Android** — 5.1–5.7. Testable on a device before the backend flag is on:
   the Settings switch, the listener, `list()` from a debug button.
3. **Turn the flag on** in the environment, try the flow on a phone, tune the
   note wording and the 20 s window.
4. **Privacy page** (4.9) ships with step 3, not before.

Estimated effort: 3–5 working days for one engineer who has read this repo.
The Kotlin part cannot be compiled in a container without the Android SDK —
build and run it on a machine with Android Studio, and treat the device test in
step 2 as the acceptance test for that half.

---

## 10. Definition of done

- With `MESSAGES_INBOX_ENABLED` unset: every existing test green, tool list
  identical, `PROTOCOL.md` diff is additive only.
- With it set, on an Android phone with notification access granted: the
  four-line conversation in section 1 works end to end without touching the
  phone, the reply appears in WhatsApp as sent from the user, and the backend's
  tool-audit table contains no message text.
- Turning the setting off in the app clears the buffer and stops both the
  notes and the tools within the same session.
