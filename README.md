# FarryOn — Realtime Voice + Vision + Agentic AI

FarryOn is a production-grade reference system for **real-time multimodal
assistants**: stream your **camera** and **microphone** to an AI that **sees,
listens, talks back, and takes actions** (notes, tasks, web search, messages) —
on the phone today, on **smart glasses** tomorrow.

It re-implements and improves on the VisionClaw architecture with a clean,
modular, testable codebase.

```
┌──────────────┐   WebSocket (audio+video+events)   ┌──────────────┐
│  Flutter app │  ───────────────────────────────►  │   FastAPI    │
│ camera + mic │  ◄───────────────────────────────  │   backend    │
└──────────────┘        TTS audio + events           └──────┬───────┘
                                                            │
                                              ┌─────────────┴─────────────┐
                                              │  AI Gateway (Gemini Live  │
                                              │   / OpenAI Realtime)      │
                                              └─────────────┬─────────────┘
                                                            │ tool calls
                                              ┌─────────────┴─────────────┐
                                              │  Agent + Tools (notes,    │
                                              │  tasks, search, messages) │
                                              └───────────────────────────┘
```

## Repository layout

| Path          | What                                                            |
| ------------- | -------------------------------------------------------------- |
| `PROTOCOL.md` | **Shared wire contract** for `/ws/live` (read this first).      |
| `backend/`    | Python · FastAPI · WebSockets · AI gateway · agent · tools · DB |
| `mobile/`     | Flutter app (Android + iOS) · camera · mic · playback · WS      |
| `docs/`       | Architecture, data-flow, prompts, deployment plan, diagrams     |
| `docker-compose.yml` | Local stack (backend + Postgres + Prometheus + Grafana) |

## Quick start

```bash
# Backend
cd backend && cp .env.example .env   # add your GEMINI_API_KEY / OPENAI_API_KEY
pip install -r requirements.txt
uvicorn app.main:app --reload        # ws://localhost:8000/ws/live

# Mobile
cd mobile && flutter pub get && flutter run
```

See `docs/ARCHITECTURE.md` for the full design and `docs/DEPLOYMENT.md` to ship.

## Features

- 🎥 **Realtime vision** — ~1 fps JPEG frames → scene understanding, OCR, reasoning
- 🎙️ **Realtime voice** — PCM16 mic in (16 kHz), streamed TTS out (24 kHz)
- 🤖 **Agentic actions** — model-driven tool calling with a clean tool engine
- 🕶️ **Universal device adapter** — phone today, smart glasses tomorrow
- 🔌 **Pluggable AI** — Gemini Live or OpenAI Realtime behind one gateway
- ♻️ **Resilient** — heartbeats, exponential-backoff reconnect, barge-in

## License

MIT (see `LICENSE`).
