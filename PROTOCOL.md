# FarryOn — Realtime Wire Protocol (`/ws/live`)

> **This file is the shared contract.** The Flutter app, the Python backend, and
> the docs MUST all conform to it. Do not change a message shape without updating
> this file. Version it via `protocolVersion`.

**Current version:** `1`

---

## 1. Transport

- Single **WebSocket** connection at `wss://<host>/ws/live`.
- The connection is **bi-directional and full-duplex**: audio, video, text, and
  control messages all flow over the same socket.
- Two kinds of frames are used on the same socket:
  - **Text frames** → UTF-8 **JSON** control / event messages.
  - **Binary frames** → raw media (audio PCM, video JPEG) with a tiny header.

Authentication: a short-lived token is passed as a query param
`?token=<jwt>` (or `Authorization: Bearer` header where the client supports it).

---

## 2. Binary frame format (media)

Every **binary** frame uses this fixed little-endian header so both Dart and
Python can parse it trivially. WebSocket frames are already length-delimited, so
no length field is needed.

```
 offset  size  field
 ------  ----  -----------------------------------------------------------
   0      1    tag   (uint8)   — stream type, see table below
   1      8    ts    (uint64)  — capture/emit time, ms since epoch (LE)
   9      ..   payload         — raw bytes (PCM or JPEG)
```

| tag    | name          | direction        | payload format                    |
| ------ | ------------- | ---------------- | --------------------------------- |
| `0x01` | INPUT_AUDIO   | client → server  | PCM signed 16-bit LE, 16 kHz mono |
| `0x02` | INPUT_VIDEO   | client → server  | JPEG (single frame, ~1 fps)       |
| `0x03` | OUTPUT_AUDIO  | server → client  | PCM signed 16-bit LE, 24 kHz mono |

Audio chunking guidance: send INPUT_AUDIO in ~20–100 ms chunks (e.g. 320–1600
samples). OUTPUT_AUDIO is streamed back in similar small chunks for low latency.

---

## 3. Text messages — Client → Server

All JSON messages have a `type` field.

```jsonc
// Sent once, immediately after the socket opens.
{ "type": "hello",
  "protocolVersion": 1,
  "client": { "platform": "android|ios", "appVersion": "1.0.0" },
  "device": {                      // which capture device is feeding media
    "kind": "phone|glasses|external",
    "id": "string",
    "capabilities": ["audio_in", "video_in", "audio_out"]
  },
  "session": { "resumeId": "optional-previous-session-id" },
  "provider": "gemini|openai|grok|mock",  // optional; AI backend for this
                                          // session. Omit to use the server
                                          // default. Switching providers in the
                                          // app reconnects with a new hello.
  "clientTime": "2026-06-21T22:30:00+05:30", // optional; device local time w/
                                          // offset, so the model resolves
                                          // relative reminder times correctly.
  "webSearch": {                          // optional; per-session search keys.
    "provider": "tavily", "apiKey": "…",
    "fallbackProvider": "serper", "fallbackApiKey": "…" },
  "email": {                              // optional; enables read_emails.
    "address": "you@gmail.com", "appPassword": "…" }  // never persisted
}

// Declares the media formats the client will send/expects.
{ "type": "config",
  "audioIn":  { "encoding": "pcm16", "sampleRate": 16000, "channels": 1 },
  "videoIn":  { "format": "jpeg", "fps": 1, "maxWidth": 1024 },
  "audioOut": { "encoding": "pcm16", "sampleRate": 24000, "channels": 1 } }

{ "type": "audio_start" }            // user begins speaking / mic opened
{ "type": "audio_stop" }             // mic closed
{ "type": "text", "text": "..." }    // typed user input (no mic)
{ "type": "interrupt" }              // barge-in: stop current TTS playback
{ "type": "tool_permission", "id": "call-id", "granted": true }  // optional gate
{ "type": "ping", "t": 1718764800000 }
```

---

## 4. Text messages — Server → Client

```jsonc
{ "type": "ready", "sessionId": "uuid", "protocolVersion": 1,
  "model": "gemini-live|gpt-realtime" }

// Streaming transcripts (both user ASR and assistant text).
{ "type": "transcript", "role": "user|assistant",
  "text": "partial or full text", "final": false }

// Assistant is about to / done sending OUTPUT_AUDIO binary frames.
{ "type": "audio_start" }
{ "type": "audio_end" }

// Agent tool lifecycle (for UI display + optional permission gating).
{ "type": "tool_call",   "id": "call-id", "name": "create_note",
  "args": { "text": "..." }, "needsPermission": false }
{ "type": "tool_result", "id": "call-id", "name": "create_note",
  "ok": true, "result": { "id": 12 } }

{ "type": "state", "value": "idle|listening|thinking|speaking" }
{ "type": "error", "code": "string", "message": "human readable",
  "fatal": false }
{ "type": "pong", "t": 1718764800000 }
```

---

## 5. Tool schemas (function calling)

These are the canonical tool definitions exposed to the model. Backend MUST
register exactly these names/params; Flutter renders them by `name`.

```jsonc
[
  { "name": "create_note",
    "description": "Save a short note for the user.",
    "parameters": { "type": "object",
      "properties": { "text": { "type": "string" } },
      "required": ["text"] } },

  { "name": "web_search",
    "description": "Search the web and return top results.",
    "parameters": { "type": "object",
      "properties": { "query": { "type": "string" } },
      "required": ["query"] } },

  { "name": "create_task",
    "description": "Create a to-do task with an optional due date.",
    "parameters": { "type": "object",
      "properties": {
        "title": { "type": "string" },
        "due_date": { "type": "string", "description": "ISO-8601 date/time" } },
      "required": ["title"] } },

  { "name": "send_message",
    "description": "Send a text message to a known contact.",
    "parameters": { "type": "object",
      "properties": {
        "contact": { "type": "string" },
        "text": { "type": "string" } },
      "required": ["contact", "text"] } },

  { "name": "set_camera_zoom",
    "description": "Zoom the device camera (client-executed) to see distant or small objects.",
    "parameters": { "type": "object",
      "properties": {
        "level": { "type": "number", "description": "Magnification 1.0–8.0" } },
      "required": ["level"] } }
]
```

Tool-call loop: model emits a tool call → backend executes the tool → backend
feeds the `tool_result` back to the model → model produces the final spoken/text
answer. The UI is notified via `tool_call` / `tool_result` events for display.

---

## 6. Session lifecycle

```
open socket
  → client: hello, config
  → server: ready
  ⇄ media streaming (binary) + events (json) + tool calls
  → client: interrupt / audio_stop / text as needed
close / drop
  → client reconnects, sends hello with session.resumeId
```

---

## 7. Reconnection strategy

- Client uses **exponential backoff with jitter**: 0.5s, 1s, 2s, 4s, 8s (max),
  reset on a successful `ready`.
- Heartbeat: client sends `ping` every 15s; if no `pong` within 10s, drop and
  reconnect.
- On reconnect the client sends `hello` with `session.resumeId` so the backend
  can re-attach context (best-effort; AI realtime session may be fresh).
- Media already captured during a drop is discarded (not buffered) to avoid
  stale context; only the latest video frame matters.

---

## 8. Audio / video summary

| stream       | format        | rate     | channels | notes                  |
| ------------ | ------------- | -------- | -------- | ---------------------- |
| mic in       | PCM16 LE      | 16000 Hz | 1        | 20–100 ms chunks       |
| TTS out      | PCM16 LE      | 24000 Hz | 1        | streamed, low-latency  |
| camera in    | JPEG          | ~1 fps   | —        | downscale ≤ 1024 px    |
