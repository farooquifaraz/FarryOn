# Brief: harden the glasses media sync against interruptions

Give everything below the line to a local agent (Claude Code on the machine
that has the vendor `.aar`, the phone on Wi-Fi ADB, and the glasses). It was
written after reading the bridge (`HeyCyanGlassesSdk.kt`), the Dart controller
and the lab notes on 2026-09-13; line numbers refer to `main` at that date and
may drift a little.

---

You are implementing a small, well-scoped hardening of the FarryOn smart-glasses
media sync so that a dropped link can never lose a photo or video, and so that
the user is told the truth when a sync is cut short.

## 0. Ground rules

- Repository `farooquifaraz/FarryOn`. Start from `main`:
  `git checkout main && git pull --ff-only && git checkout -b glasses/sync-hardening`.
  Do **not** touch `backend/` or anything under `docs/EMAIL_*` — an email
  branch (`claude/gracious-feynman-3i0bev`) is in test and must stay separate.
- Work in the order below, one commit per step, message style like the repo's
  (a plain subject, a body that says what broke and why this fixes it).
  Push after every step: `git push -u origin glasses/sync-hardening`.
- Do not open a PR. The human decides after the hardware tests in step 6.
- Every hardware measurement goes into
  `mobile/lib/features/glasses_lab/LAB_NOTES.md` (dated entry, same style as
  the existing ones). Every decision you had to make without evidence goes
  into the commit body as a question for the human.
- Talk to the human in Hinglish (Roman script), short messages, one step at a
  time. Ask before anything that could delete media from the glasses.

## 1. The system you are changing (read these first)

- `mobile/android/app/src/main/kotlin/com/farryon/farryon/glasses/HeyCyanGlassesSdk.kt`
  - `wifiListener.fileWasDownloadSuccessfully` (~line 1280): the SDK says one
    file is fully downloaded into the app's private `DCIM_1` folder. Today it
    starts `exportToGallery` on a **new thread** and then immediately calls
    `applyRetention(entity)`.
  - `exportToGallery` (~1412): copies the file into MediaStore
    (`DCIM/FarryOn`) on a background thread; emits `gallery ← name` on
    success, logs and emits on failure. Returns nothing.
  - `applyRetention` (~3004) → `deleteFromGlasses` (~3033): sends the BLE
    delete (`FileHandle.executeFileDelete(name)`, fire-and-forget, ack via
    `onDeletePlate` / `onDeletePlateError` with **no file name**) and drops the
    local `DCIM_1` copy (`GlassesControl.deleteFile(name)`).
  - Retention policy values (`setRetentionDays`, ~2985): `0` keep everything
    (default), `N>0` delete synced photos older than N days (via
    `sweepRetention` ~3083 on each connect, persisted queue in
    SharedPreferences `glasses_lab/retention_pending`), `-1` purge when the
    glasses report storage full (`fullCleanupActive`), `-2` delete right after
    sync.
  - Sync run lifecycle: `startWifiSync` (~2589) — one run at a time, BLE
    media-count probe first, then `importAlbum()`. `armSyncWatchdog` (~2805):
    60 s without a progress callback → `recoverStalledSync` (~2856): P2P reset
    command `0x02 0x01 0x0f`, wait 6 s, `importAlbum()` again, once. A second
    stall ends the run with "switch the glasses off and on". `armSyncDeadline`
    (~2900): 240 s hard ceiling. `stopWifiSync` (~2941): the SDK has **no
    cancel**; it only clears our state.
  - `emitConnectionState` (~608): on `disconnected` it calls
    `onVideoLinkLost()` and stops the foreground service. **It does nothing
    about a sync that is running** — a BLE drop mid-sync is only noticed by
    the 60 s stall watchdog.
  - Terminal `syncProgress` events carry `pct = 100`; that is what releases
    the Dart side's in-flight guard.
- `mobile/lib/state/live_controller.dart`
  - `syncProgress` handling (~1112): `pct >= 100` clears the guard and shows a
    notification: "Nothing to sync" if the text contains `nothing`/`skipped`,
    otherwise **"Saved to your phone"** — so an interrupted or failed run is
    currently announced as saved.
  - `_runAutoGlassesSync` (~1239), `syncGlassesNow` (~1186), guard timeout
    `autoSyncGuardTimeout = 270 s` (must outlast the native worst case).
  - Auto-sync triggers: 3 s after connect, 8 s after a `photoStored` notify,
    6 s after a recording finishes (`_config.autoMediaSync`, default true).
- `mobile/lib/features/glasses_lab/LAB_NOTES.md`: hardware facts. Relevant:
  glasses mark synced media as done lazily (~1.5 s); a BLE drop mid-recording
  does not lose the recording (verified 2026-08-08); an interrupted session
  can leave the glasses' P2P session wedged (BT disconnect + reconnect clears
  it); a 0-file `importAlbum` still spins up P2P and errors.
- The vendor SDK is `mobile/android/app/libs/LIB_GLASSES_SDK-release_*.aar`
  (git-ignored). Its internals are bytecode; what it does with a half-
  downloaded file (resume vs restart, whether the partial file stays) is
  unknown — do not assume, measure in step 6.
- Tests: Flutter tests live in `mobile/test/` (see `glasses_sync_budget_test.dart`,
  `glasses_lab_controller_test.dart`). There are **no Android unit tests**
  today (`mobile/android/app/src/test` does not exist). Step 2 adds the first.

## 2. Step 1 — close the delete race (do this first, small)

The bug: in `fileWasDownloadSuccessfully`, the gallery copy runs on a thread
while `applyRetention` runs at once. `deleteFromGlasses` removes the local
`DCIM_1` copy synchronously; if that lands before the export thread has opened
the file, the photo is gone from the glasses and never reached the gallery.
Default policy `0` never deletes, so most users are unaffected; `-1`, `-2` and
aged `N` files are exposed.

Change:
- Make the export return a verified result. Run the copy on a worker, compare
  bytes copied with `src.length()`, and treat a mismatch or any exception as
  failure. On success return the MediaStore URI and size.
- Call `applyRetention(entity)` **only inside the success path, after the copy
  is verified**. On failure: no delete, emit a `deviceEvent`
  `"kept on glasses: gallery copy failed for <name>"`, and leave the file for
  the next sync.
- `emitSyncedPhotoPreview` stays independent (it is a nicety).
- Keep `applyRetention`'s ordering of glasses-delete then local-delete.

Verify: `flutter analyze` clean; build the APK; on hardware with retention set
to "right after sync" (`-2`), sync three photos and confirm all three are in
`DCIM/FarryOn`, then the glasses count drops. Record in LAB_NOTES.

## 3. Step 2 — a ledger of proven copies

Add `SyncLedger.kt` next to the bridge: a small persisted map (JSON in
`app.filesDir/glasses_sync_ledger.json`, or SharedPreferences — your call,
say why) of `fileName → {sizeBytes, galleryUri, syncedAtMs, deleteState}` where
`deleteState ∈ {kept, requested, confirmed, failed}`.

Use it in four places:
1. **Write** an entry only from the verified-export success path of step 1.
2. **Duplicate suppression:** in `fileWasDownloadSuccessfully`, if the ledger
   already has this name with the same size, skip the gallery export (the
   glasses re-delivered a file after an interrupted run) but still run
   retention. Emit `deviceEvent "already on phone: <name>"`.
3. **Retention only from the ledger:** `sweepRetention` and the
   `fullCleanupActive` purge may delete only names present in the ledger with
   a gallery URI. Anything else stays on the glasses.
4. **Delete acknowledgement:** the firmware's ack carries no file name, so
   serialise deletes — one in flight at a time, next one sent on
   `onDeletePlate` / `onDeletePlateError` / a 5 s timeout. Mark `confirmed`
   or `failed`; `failed` entries are retried by the next connect's sweep. Keep
   the existing `retention_pending` age queue, or fold it into the ledger —
   either is fine, but only one source of truth at the end.

Add the first Android unit tests: `mobile/android/app/src/test/.../SyncLedgerTest.kt`
(JUnit; add `testImplementation("junit:junit:4.13.2")` to
`mobile/android/app/build.gradle.kts` if absent). Cover: round-trip persist,
duplicate detection by name+size, delete-state transitions, corrupt file on
disk → empty ledger, not a crash. Run with `./gradlew :app:testDebugUnitTest`
from `mobile/android`.

## 4. Step 3 — a dropped link ends the run honestly

In `emitConnectionState`, when the new state is `disconnected` and
`syncActive` is true:
- cancel the stall watchdog and the deadline, set `syncActive = false`,
  `syncRecoveryTried = false`, `fullCleanupActive = false`;
- emit a terminal `syncProgress` (`pct = 100`) with text
  `"interrupted — <done> of <total> saved, the rest stay on the glasses and
  sync on reconnect"` where `done` is the count of verified exports in this
  run and `total` is `syncTotal`;
- set a flag `p2pResetPending = true`. In `startWifiSync`, if the flag is
  set, send the P2P reset (`0x02 0x01 0x0f`), wait `SYNC_RECOVERY_SETTLE_MS`,
  then proceed with the probe + `importAlbum`; clear the flag. This is the
  documented cure for the wedged session and it is what the stall recovery
  already does — you are just doing it up front.

Dart side (`live_controller.dart` `syncProgress` handler): a terminal event
whose text contains `interrupted`, `failed`, `gave up`, `couldn't` or
`switch the glasses` must show a notification like **"Sync interrupted"**
with the text, not "Saved to your phone". Add a Flutter test in `mobile/test/`
for the notification-title choice (extract the choice into a pure function
so it is testable without a bridge).

Do not add a Wi-Fi P2P broadcast receiver unless the hardware test in step 6
shows a P2P-only drop (BLE still up) that nothing catches; if so, register
`WIFI_P2P_CONNECTION_CHANGED_ACTION` and treat "group gone while syncActive"
the same way, and say so in LAB_NOTES.

## 5. Step 4 — verify size if the SDK offers it

Run, from `mobile/android/app/libs`:

```bash
mkdir -p /tmp/sdk && cd /tmp/sdk && unzip -o -q <path>/LIB_GLASSES_SDK-release_*.aar classes.jar \
  && unzip -o -q classes.jar 'com/glasses/wifi/bean/GlassAlbumEntity*' \
  && javap -p com/glasses/wifi/bean/GlassAlbumEntity.class
```

Report the fields to the human. If there is a size (any of `size`,
`fileSize`, `length`), compare it with the downloaded file's length **before**
the gallery export in step 1; a mismatch is a failed export (kept on glasses,
re-downloaded next run). If there is no size, say so and skip this step — do
not invent a checksum, the glasses do not send one.

## 6. Step 5 — hardware tests (the human runs, you verify from logs)

Add these to `docs/TEST_PLAN.md` under **E. Glasses** (IDs continue that
table's numbering) and run each with retention `0` and again with `-2`:

| Case | Do | Pass when |
|---|---|---|
| Wi-Fi drop mid-file | Start a sync of 3+ photos (one large video is better); switch the phone's Wi-Fi off during file 2 | App says "interrupted — 1 of 3 saved…" within ~5 s of the drop (not 60 s); on reconnect the remaining files arrive; every file opens and its size matches the glasses' copy; no duplicates in `DCIM/FarryOn`; glasses count ends at 0 (retention -2) or unchanged (0) |
| BLE drop mid-file | Same, but turn the phone's Bluetooth off | Same |
| App killed mid-file | Same, force-stop the app | On next launch + connect, the remaining files sync; nothing lost, nothing duplicated |
| P2P wedged after the above | Immediately run "Sync now" again | The up-front P2P reset lets the sync run without a glasses power-cycle |
| Storage-full purge | Set retention to "when full", fill the glasses, connect | Files arrive in the gallery first; the glasses delete only after; the ledger shows every deleted name as `confirmed` |

Evidence for each: `adb logcat -s GlassesLab` (the bridge's log tag), the
app's Settings → Debug logs share, the gallery, and the glasses' media count.
The table lives at `### E. Glasses` (E1–E6 exist; continue from E7). Also record what the SDK did with the partial
file (does `DCIM_1` hold a truncated file after the drop; is it re-downloaded
from zero or resumed) — that is the unknown this whole brief hedges on.

## 7. Definition of done

- Steps 1–3 merged into the branch with their tests green:
  `flutter analyze`, `flutter test`, `./gradlew :app:testDebugUnitTest`.
- Step 4 answered (fields reported, verification added or explicitly skipped).
- The five hardware cases pass, results and measurements in LAB_NOTES and
  TEST_PLAN, dated.
- A short section at the end of LAB_NOTES: what the SDK does with partial
  files, and any open question for the vendor.
- Then tell the human, in Hinglish, whether the branch is ready for a PR and
  what you were unsure about.
