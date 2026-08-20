# Testing FarryOn on a real phone (cloud build + deploy)

This path needs **nothing installed on your machine** — the backend already runs
at <https://farryon.izylrn.com>, the Android APK is built by GitHub Actions, and
you just install the APK and point it at the backend.

> ⚠️ **Before you start — rotate your API keys.** Any key pasted into a chat or
> committed to git must be considered compromised. Create fresh keys and put
> them only in the server's secret env file (never in the repo).
>
> ⚠️ **Use realtime/live models.** FarryOn streams audio over the realtime APIs,
> so you must use a realtime-capable model:
> - OpenAI: `gpt-4o-realtime-preview` (or `gpt-realtime`) — **not** `gpt-4.1`.
> - Gemini: `gemini-2.0-flash-live-001` — **not** `gemini-1.5-flash`.
> Standard chat models will fail to open the realtime stream.

---

## Step 1 — Check the backend is up

The backend is deployed to the Hostinger VPS by the `deploy-hostinger` workflow,
which runs on every push to `main`. There is nothing to set up per test — just
confirm it is answering:

- <https://farryon.izylrn.com/healthz> → `{"status":"ok",...}`
- <https://farryon.izylrn.com/readyz> → `{"status":"ready",...}`

The phone connects to `wss://farryon.izylrn.com:443/ws/live`.

Secrets (`GEMINI_API_KEY`, `OPENAI_API_KEY`, …) live in the env file on the VPS,
not in the repo. See [DEPLOYMENT.md](DEPLOYMENT.md) for the deploy itself.

> Render is no longer used. It ran a **separate database**, so an account made
> against the live backend does not exist there — pointing a phone at the old
> `*.onrender.com` host looks like a broken login, not a wrong address.

---

## Step 2 — Build the Android APK (GitHub Actions)

1. In GitHub → **Actions** tab → **Build Android APK** → **Run workflow**
   (or it runs automatically on a push that touches `mobile/`).
2. Wait for the run to finish (first run installs the Flutter SDK; a few minutes).
3. Open the completed run → **Artifacts** → download **farryon-debug-apk**.

> This is the first real compile of the app. If the build fails on a dependency
> version, that's expected on a brand-new Flutter app — share the log and it's a
> quick fix.

---

## Step 3 — Install and point it at your backend

1. Copy the APK to your Android phone and install it (allow **Install unknown
   apps** for your file manager/browser).
2. Open **FarryOn** → tap the **⚙️ settings** icon and enter:
   - **Host:** `farryon.izylrn.com`
   - **Port:** `443`
   - **Secure (wss):** `ON`
3. Grant **camera** and **microphone** permissions when prompted.
4. Tap connect — the status should go to **listening**. Speak or point the
   camera; you should see streaming transcripts, hear the reply, and watch tool
   activity (e.g. "create note") appear.

---

## iOS

A cloud iOS build needs an Apple Developer account and signing, which Actions
can't do unsigned. For iPhone, the simplest path is opening `mobile/` in Xcode
on a Mac and running on a device. (Android is the quickest way to test today.)

---

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| App stuck "connecting" | Backend asleep (wait for cold start) or wrong host/port. Verify `/healthz` in a browser. |
| Connects then drops immediately | Realtime stream rejected — check the model is a **realtime/live** model and the key is valid (VPS logs). |
| No audio reply | Mic permission denied, or provider key/model wrong. Check the VPS logs for `gateway` errors. |
| Tools never fire | Check the VPS logs; `web_search` falls back to mock unless `WEB_SEARCH_PROVIDER`/key are set. |
| `flutter build apk` fails in CI | First-compile dependency nudge — share the Actions log. |
