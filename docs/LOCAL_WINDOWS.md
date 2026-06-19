# Complete local setup on Windows (`D:\FarryOn`)

Run the **whole project** — the FastAPI **backend** *and* the Flutter **app** —
on your own PC. Your machine has open internet, so the backend talks to
Gemini/OpenAI directly: no CI, no tunnel.

The project has two halves:
- **backend/** — the realtime "brain" (WebSocket `/ws/live`, AI providers, tools)
- **mobile/** — the Flutter app (the "face"); ships Dart code only, the native
  Android project is generated locally with `flutter create`.

> Both providers are already verified working:
> Gemini → `gemini-2.5-flash-native-audio-latest`, OpenAI → `gpt-realtime`.

---

## Part A — Install the tools (one time)

| Tool | Why | Get it |
| --- | --- | --- |
| **Git** | fetch the code | <https://git-scm.com/download/win> |
| **Python 3.11** | run the backend | <https://www.python.org/downloads/> — tick **Add to PATH** |
| **Flutter SDK** | build/run the app | <https://docs.flutter.dev/get-started/install/windows> |
| **Android Studio** | Android SDK + emulator + JDK | <https://developer.android.com/studio> |

After installing Flutter + Android Studio, open a **new** terminal and run:
```powershell
flutter doctor                  # shows what's still missing
flutter doctor --android-licenses   # type 'y' to accept all
```
Get `flutter doctor` to a state where **Flutter** and **Android toolchain** show
green check-marks. (You can ignore the Visual Studio / Chrome lines — those are
for Windows/web targets we don't need.)

> **Don't want to install Flutter/Android Studio?** You can skip Part D2 and use
> the **prebuilt APK** instead (Part D1) — only the backend needs setting up then.

---

## Part B — Get the code into `D:\FarryOn`
```powershell
git clone https://github.com/farooquifaraz/FarryOn.git D:\FarryOn
cd D:\FarryOn
git checkout claude/gallant-franklin-j6qzbx
```
`dir` should now show `backend\`, `mobile\`, `docs\`, `README.md`, …

---

## Part C — Backend

```powershell
cd D:\FarryOn\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
notepad .env
```
In `.env`, set the provider and key (models are already correct defaults):
```ini
AI_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-key
# or:
# AI_PROVIDER=openai
# OPENAI_API_KEY=your-openai-key
```
Run it (listens on all interfaces so the phone can reach it):
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Verify: open <http://localhost:8000/healthz> → `{"status":"ok",...}`.

**Quick provider test (no app, instant)** — in a *second* terminal:
```powershell
cd D:\FarryOn\backend
.\.venv\Scripts\Activate.ps1
python scripts\e2e_smoke.py        # expect: PASS
```

> Keep this backend terminal **running** while you use the app. Every request +
> any provider error prints here live.

---

## Part D — The app

### D1 — Easiest: prebuilt APK over WiFi (no Flutter install)
1. Download the APK artifact from GitHub → **Actions → Build Android APK →**
   latest run → **Artifacts → farryon-release-apk** → install on the phone.
2. Phone + PC on the **same WiFi**.
3. Find your PC IP: `ipconfig` → IPv4 (e.g. `192.168.1.50`). Allow port 8000
   through the Windows Firewall prompt (**Private** network).
4. App → ⚙️ → **Host** = `192.168.1.50`, **Port** = `8000`, **Secure (wss)** =
   **OFF** → connect.

### D2 — Full: build & run the app from source
```powershell
cd D:\FarryOn\mobile
flutter create --platforms=android .     # generates the android\ project
flutter pub get
```
Then add the runtime permissions — open
`mobile\android\app\src\main\AndroidManifest.xml` and paste these three lines
just inside the `<manifest ...>` tag (above `<application>`):
```xml
<uses-permission android:name="android.permission.INTERNET"/>
<uses-permission android:name="android.permission.CAMERA"/>
<uses-permission android:name="android.permission.RECORD_AUDIO"/>
```
Pick a device and run:
```powershell
flutter devices        # list emulators / connected phones
flutter run            # builds & launches; press 'r' to hot-reload
```
- **Android emulator** (start one from Android Studio → Device Manager): the host
  PC is `10.0.2.2`. App ⚙️ → Host `10.0.2.2`, Port `8000`, Secure **OFF**.
- **Physical phone over USB** (enable Developer options → USB debugging): run
  `adb reverse tcp:8000 tcp:8000`, then App ⚙️ → Host `127.0.0.1`, Port `8000`,
  Secure **OFF**.
- **Physical phone over WiFi**: App ⚙️ → Host = PC IPv4, Port `8000`, Secure **OFF**.

---

## Part E — Use it
Backend terminal running + app connected → grant camera/mic → tap-to-talk or
type. You'll see transcripts, hear replies, and watch tool calls
(e.g. `create_note`) — all served by your local backend.

---

## Troubleshooting
| Symptom | Fix |
| --- | --- |
| `python`/`git`/`flutter` not recognized | Tool not on PATH — reopen the terminal after install; for Python re-run installer with **Add to PATH**. |
| `.\.venv\Scripts\Activate.ps1` blocked | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` then retry; or call `.\.venv\Scripts\python.exe -m uvicorn ...` directly. |
| `flutter doctor` Android licenses | `flutter doctor --android-licenses` → accept all. |
| App can't reach backend | Same WiFi? Firewall allowed (Private, port 8000)? Correct host (10.0.2.2 emulator / 127.0.0.1 with `adb reverse` / PC-IP on WiFi), Secure **OFF**, port **8000**. |
| Connects but no reply | Look at the backend terminal — provider/key errors print there. |
| Verify the key alone | `python scripts\e2e_smoke.py`. |
