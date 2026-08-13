"""Prometheus metrics for FarryOn.

Exposed at ``/metrics`` (see :mod:`app.main`). Metric objects are module-level
singletons so they register exactly once with the default registry.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# -- WebSocket / session ------------------------------------------------------
WS_CONNECTIONS = Counter(
    "farryon_ws_connections_total",
    "Total /ws/live connections accepted.",
)
WS_ACTIVE = Gauge(
    "farryon_ws_active_connections",
    "Currently active /ws/live connections.",
)
WS_DISCONNECTS = Counter(
    "farryon_ws_disconnects_total",
    "Total /ws/live disconnects, labelled by reason.",
    ["reason"],
)

# -- Media throughput ---------------------------------------------------------
FRAMES_IN = Counter(
    "farryon_frames_in_total",
    "Inbound binary media frames, labelled by stream kind.",
    ["kind"],  # audio | video | unknown
)
FRAMES_SENT_TO_MODEL = Counter(
    "farryon_frames_sent_to_model_total",
    "Video frames actually forwarded to the AI model (after gating). Compare "
    "against FRAMES_IN{kind=video} to confirm the cost-saving frame gate works.",
)
TOKENS_USED = Counter(
    "farryon_tokens_used_total",
    "Model tokens billed this process, read from the provider's usage_metadata. "
    "Labelled by kind so input/output cost can be tracked over time.",
    ["kind"],  # total | input | output
)
AUDIO_BYTES_IN = Counter(
    "farryon_audio_bytes_in_total",
    "Inbound audio payload bytes (PCM16).",
)
AUDIO_BYTES_OUT = Counter(
    "farryon_audio_bytes_out_total",
    "Outbound audio payload bytes (PCM16).",
)

# -- Tools --------------------------------------------------------------------
TOOL_CALLS = Counter(
    "farryon_tool_calls_total",
    "Tool calls requested by the model, labelled by tool name.",
    ["name"],
)
TOOL_LATENCY = Histogram(
    "farryon_tool_latency_seconds",
    "Tool execution latency in seconds, labelled by tool name.",
    ["name"],
)

# -- AI provider --------------------------------------------------------------
AI_LATENCY = Histogram(
    "farryon_ai_first_event_seconds",
    "Latency from connect to first gateway event, labelled by provider.",
    ["provider"],
)
AI_ERRORS = Counter(
    "farryon_ai_errors_total",
    "Errors surfaced from the AI gateway, labelled by provider.",
    ["provider"],
)

# -- External billed vision APIs (cost tracking) -----------------------------
# One increment == one billed unit. Watch these on /metrics to see exactly how
# many Google Vision / Gemini calls the app makes, without the Cloud Console.
VISION_API_CALLS = Counter(
    "farryon_vision_api_calls_total",
    "Google Cloud Vision images:annotate calls (1 billed unit each), "
    "labelled by feature (LANDMARK_DETECTION | WEB_DETECTION) and outcome "
    "(ok | error).",
    ["feature", "outcome"],
)
GEMINI_API_CALLS = Counter(
    "farryon_gemini_api_calls_total",
    "Google Gemini generateContent calls, labelled by purpose "
    "(identify | answer | explain) and outcome (ok | error).",
    ["purpose", "outcome"],
)

# -- Live translation ---------------------------------------------------------
# Kept separate from the assistant's counters on purpose: a translate session
# has a different cost shape (no vision, no tools, continuous audio both ways),
# and folding it into the same series would make both harder to read.
#
# Nothing here ever carries transcript CONTENT — language codes and durations
# only. A translator is pointed at other people's conversations, and the person
# being translated never agreed to anything.
TRANSLATE_SESSIONS = Counter(
    "farryon_translate_sessions_total",
    "Translate sessions started, labelled by target language.",
    ["target"],
)
TRANSLATE_ACTIVE = Gauge(
    "farryon_translate_active_sessions",
    "Currently active translate sessions.",
)
TRANSLATE_AUDIO_SECONDS = Counter(
    "farryon_translate_audio_seconds_total",
    "Input audio seconds metered on translate sessions. This is the billable "
    "quantity; compare against the quota caps.",
)
TRANSLATE_FIRST_AUDIO = Histogram(
    "farryon_translate_first_audio_seconds",
    "Seconds from the end of a heard utterance to the first byte of its "
    "translated audio — the delay a listener actually feels.",
)
TRANSLATE_ERRORS = Counter(
    "farryon_translate_errors_total",
    "Translate failures, labelled by stage (connect | stream).",
    ["stage"],
)
TRANSLATE_UPSTREAM_REOPENS = Counter(
    "farryon_translate_upstream_reopens_total",
    "Upstream sockets replaced mid-session. The Live API caps how long one "
    "may live, so a long conversation costs several — a count that stays at "
    "zero while sessions run long means the rollover has stopped working.",
)
