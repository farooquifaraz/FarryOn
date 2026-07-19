# Notes & reminders: local-first, server-synced

**Status: phases 1-4 built and device-verified (2, 3, 4 on the Vivo). Phase 5 is mostly a test.**
Written 2026-07-16 after he asked for notes/reminders to be "managed locally,
synced to the server, and downloaded again on a new phone".

---

## What he asked for, and what it means

> *"speed aur offline ki chinta h… sab locally manage ho, sirf AI call ke alawa.
> Aur server bhi sync rahe. App uninstall ya dusre mobile pe install → download
> from server, but managed from locally."*

Read plainly, that is **local-first with sync**, and it's the right shape:

| | Today | After |
|---|---|---|
| Open Notes | REST call, spinner, **empty when offline** | Instant, from the phone's own DB |
| Add / delete | REST call, wait | Instant; the server finds out after |
| Offline | Nothing works | Everything works; changes queue |
| New phone | — | Sign in → everything downloads |
| Farry reads your notes | Works (server-side tool) | Still works — unchanged |
| Admin moderation | Works | Still works — unchanged |

**One thing cannot move, and it's worth being blunt about:** the AI itself. Every
voice frame and camera frame goes to Gemini/OpenAI because the model runs in
Google's data centre, not on a phone. That isn't a design choice we get to make.
This document is about notes and reminders — which genuinely can be local — and
nothing else.

---

## The two things that make this harder than it looks

### 1. The database can't sync yet

`notes` and `tasks` have `created_at` and nothing else:

```
notes:  id, user_id, session_id, text,               created_at
tasks:  id, user_id, session_id, title, due_date, done, created_at
```

Two columns are missing, and without them sync is not "harder", it's **wrong**:

- **No `updated_at`** → we can't ask "what changed since I last synced?", so every
  sync drags the whole table down. Fine at 10 notes, absurd at 10,000.
- **No `deleted_at`** → deletes are `DELETE FROM`. A row that vanishes is
  indistinguishable from a row that was never sent. **Delete a note on phone A and
  phone B keeps it forever** — the server has no way to say "this used to exist
  and is now gone."

So step one is a migration: add `updated_at`, add `deleted_at`, switch
`repo.delete_note`/`delete_task` to a soft delete, and filter the reads.

### 2. There are two writers, not one

The phone is not the only thing that creates notes. **Farry does**, server-side,
mid-conversation:

```
you: "yaad rakho — dentist Tuesday"
     → Gemini calls create_note
     → backend writes the row      ← the phone wasn't involved
```

So a design where "the phone owns the data and pushes it up" is already false.
Both ends write. That is the crux, and it's what makes naive local-first lose
data.

**The good news: the channel already exists.** `live_controller._applyReminder`
listens for `tool_result` over the WebSocket and schedules the Android alarm off
it. The same message can write into the local DB. Farry's writes arrive in
roughly a second, without polling.

---

## The design

### Where things live

```
┌─ phone ──────────────────────────┐        ┌─ server ─────────────┐
│                                  │        │                      │
│  SQLite  ← the screen reads      │        │  Postgres/SQLite     │
│    ↑  ↓                          │        │    ↑                 │
│  outbox (queued local changes)   │ ─push→ │  REST               │
│                                  │ ←pull─ │                      │
│                                  │ ←─ WS ─│  Farry's tool writes │
└──────────────────────────────────┘        └──────────────────────┘
```

- **The screen only ever reads local SQLite.** No network on the read path — that
  is where "instant" and "works offline" come from.
- **Local writes go to SQLite first, then into an outbox.** The row is on screen
  before the server hears about it.
- **The server stays the source of truth for durability** — a new phone, Farry's
  own reads, and the admin panel all depend on it.

### Sync, concretely

```
pull:  GET /notes?since=<updated_at>   → upsert into local, apply tombstones
push:  drain the outbox (create/update/delete), oldest first
when:  app start · app resumes · after a WS tool_result · after a local edit
       · retry with backoff while the outbox is non-empty
```

### Ids: the part that bites

> **Update 2026-07-19.** This section turned out not to apply to phase 3. The app
> has no way to create a note offline — adding is voice-only and the model is
> server-side — so the outbox only ever carries operations on rows that already
> have a server id. `client_id` shipped in migration 0006 anyway: it costs
> nothing and a future "type a note" button would need it immediately.

The server allocates ids (`notes.id` is autoincrement). A phone that is offline
can't ask for one — but it must show the note *now*.

So: **every row gets a client-generated UUID at birth**, on whichever side
creates it. The server's integer id stays the primary key, and the UUID becomes
the sync identity. Without it, an offline-created note that's pushed twice
(app killed mid-push) lands as two notes, and there's no way to tell.

That means `notes`/`tasks` also gain a `client_id` (UUID, unique).

### Conflicts

Notes and tasks are small and mostly append-only, so **last-write-wins on
`updated_at`** is honest and enough. The cases:

| Case | Resolution |
|---|---|
| Edited in two places | Later `updated_at` wins. The loser is gone — acceptable for a note; say so in the UI if we ever add editing. |
| Deleted here, edited there | **Delete wins.** Undeleting something someone deleted is worse than losing an edit. |
| Same row pushed twice | `client_id` makes it idempotent. |
| Clock skew | The **server's** `updated_at` is authoritative on write; the phone never sets it. A phone with a wrong clock can't win a conflict it shouldn't. |

### What each end still owns

| | Owner | Why |
|---|---|---|
| Reads for the screen | Phone | Speed, offline |
| Durability, new-device restore | Server | A phone is not a backup |
| Farry's reads/writes | Server | The tools run there; the model can't see the phone |
| Admin moderation | Server | It reads the server DB |
| Quota / billing | Server | A phone can't be trusted to report its own usage |

---

## Cost, honestly

Faraz's stated worry is speed and offline, so this is worth writing down: **the
REST calls this removes are nearly free.** They hit our own backend; they cost
some CPU and no cash. The money goes to Gemini/OpenAI on the voice path, and this
design does not change that by a single frame.

So the case for doing it is **UX** — an instant screen and an app that works on a
plane — not the bill. Worth doing for that reason, not oversold as a saving.

---

## Phases

Each is shippable on its own, and each is testable before the next.

| # | What | Why this order |
|---|---|---|
| **1** | ~~Migration + soft delete~~ — **DONE 2026-07-16** | `0006_notes_tasks_sync`. Verified against seeded 0005-era rows (backfill `updated_at = created_at`, nothing lost) and a downgrade/re-upgrade round trip. `GET ?since=` is *not* built — phase 3 needs it, nothing does yet. |
| **2** | ~~Local mirror; screens read from it~~ — **DONE 2026-07-16** | Shipped as JSON in SharedPreferences, not SQLite: the API caps these at 200 rows and `chat_history.dart` already stores this way. Swap the storage behind `DataCache`'s six functions when phase 3 wants real rows. Verified on the Vivo: killed the backend, force-stopped the app, reopened — both notes came back off disk. |
| **3** | ~~Outbox: local writes queue and push~~ — **DONE, device-verified 2026-07-19** | Much smaller than planned: the app can't *create* (voice-only, and Farry is server-side), so every queueable op acts on an existing server row and is idempotent. No `client_id` needed — the column still earns its place for a future editor. `core/outbox.dart` + `outbox_sync.dart`. Verified on the Vivo with the backend stopped: a deleted note stayed deleted (it used to spring back), the op sat in `outbox.v1.u15`, survived a force-stop, and reached the server on the next launch — `notes.deleted_at` set, `tasks.done=1`, queue empty. |
| **4** | ~~WS `tool_result` → local cache~~ — **DONE, device-verified 2026-07-19** | `core/cache_patch.dart`. Covers create/delete note+task and complete_task; `update_task` is skipped because its result is partial. Verified on the Vivo: asked Farry to save a note, **killed the backend**, opened Notes — the new note was top of the list. It could only have come from the tool_result. |
| **5** | New-device restore = full pull | Falls out of phase 1 + 2. Mostly a test. |

**Phase 1 is the one to do first even if we stop there** — the schema gap is a
real bug today, not just a sync blocker: `DELETE FROM` means the admin panel's
moderation can never show what a user deleted, and nothing can be audited.

---

## What this costs us

Being straight about the downsides:

- **A second database to keep honest.** Two stores that disagree is a class of
  bug we don't have today.
- **The phone's DB is unencrypted by default.** Notes may hold personal things.
  SQLCipher, or accept it and say so.
- **More code on the riskiest path** — the one holding the user's own data.
- **Testing gets harder**: offline, conflicts, and interleaved Farry writes are
  states the current tests don't have to imagine.

**The alternative worth considering:** phase 2 alone (a read cache, no outbox) is
maybe a fifth of the work and buys most of the felt speed — instant screens and
offline reads. Offline *writes* are the expensive half. If the pain is "Notes are
slow and empty on a plane", phase 2 fixes it. If it's "I want to add a note on a
plane", we need 3.

---

## Open questions for Faraz

1. **Phase 2 only, or the whole thing?** Read cache is cheap and fixes the felt
   problem. Offline writes cost 3× more.
2. **Encrypt the phone DB?** Notes can be personal.
3. **How long do tombstones live?** Deleted rows can't be purged until every
   device has seen them. 90 days is the usual answer.
