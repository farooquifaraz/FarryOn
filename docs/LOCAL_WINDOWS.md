# Running FarryOn locally on Windows (`D:\FarryOn`)

This is the fastest dev loop: your PC has open internet, so the backend talks to
Gemini/OpenAI directly — **no CI, no Cloudflare tunnel**. Your phone can connect
to the backend over your home WiFi (LAN), reusing the APK you already installed.

> Both providers are already verified working in CI:
> - **Gemini** → `gemini-2.5-flash-native-audio-latest`
> - **OpenAI** → `gpt-realtime` (the adapter auto-falls back to it; your account
>   doesn't have `gpt-4o-realtime-preview`)

---

## 0. Prerequisites
- **Git** and **Python 3.11** (`python --version` → 3.11.x)
- Optional: **Docker Desktop** (alternative one-command path)
- Optional: **Flutter SDK** (only if you want to run the app from the PC)

## 1. Get the code into `D:\FarryOn`

**If `D:\FarryOn` is already a clone of this repo:**
```powershell
cd D:\FarryOn
git fetch origin claude/gallant-franklin-j6qzbx
git checkout claude/gallant-franklin-j6qzbx
git pull origin claude/gallant-franklin-j6qzbx
```

**Fresh clone:**
```powershell
git clone https://github.com/farooquifaraz/FarryOn.git D:\FarryOn
cd D:\FarryOn
git checkout claude/gallant-franklin-j6qzbx
```

---

## 2. Run the backend — Path A: Python (recommended for fast iteration)

```powershell
cd D:\FarryOn\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
notepad .env
```

In `.env` set the provider + key (pick one). **Models are already the working
defaults — you only need the key:**
```ini
# Gemini:
AI_PROVIDER=gemini
GEMINI_API_KEY=your-rotated-gemini-key

# …or OpenAI (comment out the Gemini lines, uncomment these):
# AI_PROVIDER=openai
# OPENAI_API_KEY=your-rotated-openai-key
```

Start it (binds on all interfaces so your phone can reach it over WiFi):
```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Check in a browser: <http://localhost:8000/healthz> → `{"status":"ok",...}`

## 2. Run the backend — Path B: Docker Desktop (one command)

```powershell
cd D:\FarryOn
notepad .env        # create it at the repo root for docker compose
```
Put your key(s) in that root `.env`:
```ini
AI_PROVIDER=gemini
GEMINI_API_KEY=your-rotated-gemini-key
```
Then:
```powershell
docker compose up -d --build backend
docker compose logs -f backend
```
(`docker compose up -d` brings the full stack — Postgres + Prometheus + Grafana — if you want it.)

---

## 3. Test it — Option 1: smoke test (no phone, instant)

With the backend running, open a **second** terminal:
```powershell
cd D:\FarryOn\backend
.\.venv\Scripts\Activate.ps1          # Path A only
python scripts\e2e_smoke.py
```
Expect: `READY[...] → RESULT ... audio_frames>0 → PASS`. This drives a full
realtime turn (hello → text → audio + transcript) against your key — the exact
check I run in CI.

## 3. Test it — Option 2: your phone over WiFi (reuse the installed APK)

No rebuild needed — just point the app at your PC.
1. Find your PC's LAN IP:
   ```powershell
   ipconfig    # look for IPv4 Address, e.g. 192.168.1.50
   ```
2. Make sure phone + PC are on the **same WiFi**. If Windows Firewall prompts on
   first run, **Allow** access on **Private** networks (so port 8000 is reachable).
3. In **FarryOn** → ⚙️ settings:
   - **Host:** `192.168.1.50`   *(your PC's IPv4)*
   - **Port:** `8000`
   - **Secure (wss):** **OFF**   *(LAN is plain `ws://`, no TLS)*
4. Grant camera + mic → connect → speak. Backend logs stream live in your
   terminal, so any error is right there instantly.

## 3. Test it — Option 3: run the app from the PC (optional, needs Flutter)
```powershell
cd D:\FarryOn\mobile
flutter pub get
flutter run        # on an emulator or USB-connected device
```
On the Android emulator, reach the host backend at `10.0.2.2:8000` (Secure OFF).

---

## Handy `make` targets (if you have `make`)
```
make backend   # uvicorn with autoreload (uses AI_PROVIDER, default mock)
make test      # offline pytest suite (mock provider)
make up        # docker stack up
```

## Troubleshooting
| Symptom | Fix |
| --- | --- |
| Phone can't connect over LAN | Same WiFi? Firewall allowed for port 8000 (Private)? Use the PC IPv4, Secure **OFF**, port **8000**. |
| `uvicorn` not found | Activate the venv: `.\.venv\Scripts\Activate.ps1` |
| Backend won't start | Re-check `.env` — set `AI_PROVIDER` and the matching key. |
| Connects, no voice reply | Check the backend terminal logs — provider errors print there (e.g. wrong key). |
| Want to confirm the key alone | Run `python scripts\e2e_smoke.py` (Option 1). |
