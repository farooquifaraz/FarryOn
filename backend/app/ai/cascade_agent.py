"""The ``cascade`` provider: hear, think and speak in three cheap steps.

Why it exists. The assistant runs on a native-audio Live model that
re-bills its whole context every turn, with no caching, at audio prices.
Every hour of testing is paid at that rate. This gateway keeps the
assistant's contract — the same system prompt, the same 33 tools, the same
events the session and the app already understand — and pays for almost
none of it:

* **hear** — the app's own energy gate brackets each utterance with
  ``speech_start`` / ``speech_end`` (the session turns those into
  :meth:`send_activity_start` / :meth:`send_activity_end`); the audio in
  between goes, once, to a speech-to-text endpoint. Default: Groq's Whisper
  (free tier, OpenAI-compatible). Without a Groq key: Gemini Flash-Lite
  reading the audio (about $0.0006 a minute).
* **think** — an OpenAI-compatible chat completion with function calling
  over the same tool schemas. Default: OpenRouter's free Nemotron. Without
  a key: Gemini Flash-Lite text (about $0.001 a turn).
* **speak** — nothing here. The final assistant text goes to the app as a
  transcript; the phone speaks it with its own voice (see
  ``LiveController._speakOnDevice``). The ``ready`` message's model label
  starts with ``cascade:`` and that is how the app knows to.

What is different, on purpose: replies start a second or two later than
Live (three network legs instead of one), the voice is the phone's, and
there is no barge-in inside a sentence. For testing that is the trade.

Everything a tool does is unchanged: tool calls are dispatched by the same
orchestrator, their results come back through :meth:`send_tool_result`,
and the loop continues until the model answers in words.
"""

from __future__ import annotations

import asyncio
import io
import json
import struct
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.ai.base import AIGateway, ToolSpec
from app.ai.events import (
    GatewayEvent,
    ToolCallEvent,
    TranscriptEvent,
    TurnCompleteEvent,
)
from app.config import get_settings
from app.logging_conf import get_logger

logger = get_logger(__name__)

_SAMPLE_RATE = 16_000
_MIN_UTTERANCE_MS = 300
_MAX_TOOL_ROUNDS = 6
_TOOL_RESULT_TIMEOUT_S = 60.0
_HTTP_TIMEOUT_S = 60.0

#: How long one chat completion may take before the turn moves to the
#: stand-in model. The free OpenRouter endpoint answered in 1.5-2.5 s all
#: evening and then sat silent for 60 s+ (device 2026-09-15 00:11) — a free
#: tier has no promise to keep. Twenty seconds covers a slow tool round on a
#: 550B model; past that the user has stopped waiting.
_LLM_TURN_TIMEOUT_S = 20.0


def pcm16_to_wav(pcm: bytes, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV header (what STT endpoints accept)."""
    buf = io.BytesIO()
    data_len = len(pcm)
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + data_len))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", data_len))
    buf.write(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------- hearing


class OpenAICompatSTT:
    """``POST {base}/audio/transcriptions`` — Groq, OpenAI, any compatible host."""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.label = f"{model}"
        self._client: Any = None

    def _http(self) -> Any:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        return self._client

    async def transcribe(self, pcm: bytes) -> str:
        r = await self._http().post(
            f"{self.base_url}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("utterance.wav", pcm16_to_wav(pcm), "audio/wav")},
            data={"model": self.model, "response_format": "json"},
        )
        r.raise_for_status()
        return str(r.json().get("text") or "").strip()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class GeminiSTT:
    """Gemini reading the audio through ``generate_content`` (no Live session)."""

    _PROMPT = (
        "Transcribe this audio exactly, in the language and script it was "
        "spoken in. Output only the transcript. If there is no speech, "
        "output nothing."
    )

    def __init__(self, *, model: str, api_key: str) -> None:
        self.model = model
        self.label = model
        self._api_key = api_key
        self._client: Any = None

    def _genai(self) -> Any:
        from google import genai  # type: ignore[import-not-found]

        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def transcribe(self, pcm: bytes) -> str:
        from google.genai import types  # type: ignore[import-not-found]

        resp = await self._genai().aio.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(data=pcm16_to_wav(pcm), mime_type="audio/wav"),
                self._PROMPT,
            ],
        )
        return str(getattr(resp, "text", "") or "").strip()

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------- thinking


class LLMReply:
    """One model reply: words and/or tool calls, plus what it cost."""

    __slots__ = ("text", "tool_calls", "usage", "raw")

    def __init__(
        self,
        text: str,
        tool_calls: list[tuple[str, str, dict[str, Any]]],
        usage: dict[str, int] | None = None,
        raw: Any = None,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls
        self.usage = usage or {}
        #: The provider's own reply object, replayed verbatim on the next
        #: round when the provider needs it (Gemini 3.x function calls carry
        #: a thought_signature that a rebuilt part would lack — the API then
        #: refuses the round with 400).
        self.raw = raw


class OpenAICompatLLM:
    """``POST {base}/chat/completions`` with ``tools`` — OpenRouter, Groq, NVIDIA…"""

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.label = model
        self._client: Any = None

    def _http(self) -> Any:
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        return self._client

    @staticmethod
    def _tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> LLMReply:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": 0.4,
        }
        if tools:
            body["tools"] = self._tools(tools)
            body["tool_choice"] = "auto"
        r = await self._http().post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://farryon.izylrn.com",
                "X-Title": "FarryOn",
            },
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        calls: list[tuple[str, str, dict[str, Any]]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
            except json.JSONDecodeError:
                args = {}
            calls.append((tc.get("id") or uuid.uuid4().hex, fn.get("name") or "", args))
        text = msg.get("content")
        if isinstance(text, list):  # some hosts return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        usage = data.get("usage") or {}
        return LLMReply(
            (text or "").strip(),
            calls,
            {
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
            },
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class GeminiLLM:
    """Gemini text with function calling, fed the same OpenAI-shaped history."""

    def __init__(self, *, model: str, api_key: str) -> None:
        self.model = model
        self.label = model
        self._api_key = api_key
        self._client: Any = None

    def _genai(self) -> Any:
        from google import genai  # type: ignore[import-not-found]

        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    @staticmethod
    def _contents(types: Any, messages: list[dict[str, Any]]) -> list[Any]:
        out: list[Any] = []
        for m in messages:
            role = m.get("role")
            if role == "user":
                out.append(types.Content(role="user", parts=[types.Part(text=m.get("content") or "")]))
            elif role == "assistant":
                if m.get("raw") is not None:
                    out.append(m["raw"])
                    continue
                parts = []
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(name=fn.get("name"), args=args)
                        )
                    )
                if parts:
                    out.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                try:
                    payload = json.loads(m.get("content") or "{}")
                except json.JSONDecodeError:
                    payload = {"result": m.get("content")}
                if not isinstance(payload, dict):
                    payload = {"result": payload}
                out.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=m.get("name") or "tool", response=payload
                            )
                        ],
                    )
                )
        return out

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec]
    ) -> LLMReply:
        from google.genai import types  # type: ignore[import-not-found]

        decls = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters=t.parameters
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            temperature=0.4,
        )
        resp = await self._genai().aio.models.generate_content(
            model=self.model, contents=self._contents(types, messages), config=config
        )
        calls: list[tuple[str, str, dict[str, Any]]] = []
        for fc in getattr(resp, "function_calls", None) or []:
            calls.append(
                (
                    getattr(fc, "id", None) or uuid.uuid4().hex,
                    getattr(fc, "name", "") or "",
                    dict(getattr(fc, "args", None) or {}),
                )
            )
        text = ""
        if not calls:
            text = str(getattr(resp, "text", "") or "").strip()
        usage = getattr(resp, "usage_metadata", None)
        raw = None
        try:
            raw = resp.candidates[0].content
        except (AttributeError, IndexError, TypeError):
            raw = None
        return LLMReply(
            text,
            calls,
            {
                "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
                "completion_tokens": int(
                    getattr(usage, "candidates_token_count", 0) or 0
                ),
            },
            raw=raw,
        )

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------- gateway


class CascadeAgentGateway(AIGateway):
    """See the module docstring."""

    provider = "cascade"

    #: The session must drive activity with the app's speech markers: this
    #: gateway has no detector of its own — an utterance IS the audio between
    #: speech_start and speech_end.
    requires_manual_vad = True

    def __init__(
        self,
        *,
        system_prompt: str,
        tools: list[ToolSpec],
        settings: Any | None = None,
        stt: Any | None = None,
        llm: Any | None = None,
        stt_api_key: str | None = None,
        llm_api_key: str | None = None,
    ) -> None:
        """``stt_api_key`` / ``llm_api_key`` are the user's own keys for this
        session (Settings → Dev Mode → "Your API keys", carried in
        ``hello.devKeys``); a non-empty one beats the server's. The keys stay
        in this object — never logged, never stored."""
        s = settings or get_settings()
        self._stt = stt or self._pick_stt(s, stt_api_key)
        self._llm = llm or self._pick_llm(s, llm_api_key)
        # The stand-in when the chosen model is slow or down: Gemini
        # Flash-Lite on the server's own key (cents), built on first use.
        # None when the chosen model already IS that (nothing to fall to).
        self._settings = s
        self._fallback_llm: Any | None = None
        super().__init__(
            system_prompt=system_prompt,
            tools=tools,
            model=f"cascade:{self._stt.label}+{self._llm.label}",
        )
        self.manual_vad = True
        self._history_turns = int(getattr(s, "cascade_history_turns", 12) or 12)
        self._queue: asyncio.Queue[GatewayEvent | None] = asyncio.Queue()
        self._buf = bytearray()
        self._listening = False
        self._turn_task: asyncio.Task[None] | None = None
        #: An utterance that arrived mid-turn, run when the turn ends.
        self._held_turn: Any | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._closed = False

    # -- backends ------------------------------------------------------------

    @staticmethod
    def _pick_stt(s: Any, override: str | None = None) -> Any:
        key = (override or getattr(s, "cascade_stt_api_key", "") or "").strip()
        if key:
            return OpenAICompatSTT(
                base_url=s.cascade_stt_base_url, api_key=key, model=s.cascade_stt_model
            )
        return GeminiSTT(model=s.cascade_gemini_stt_model, api_key=s.gemini_api_key)

    @staticmethod
    def _pick_llm(s: Any, override: str | None = None) -> Any:
        key = (override or getattr(s, "cascade_llm_api_key", "") or "").strip()
        if key:
            return OpenAICompatLLM(
                base_url=s.cascade_llm_base_url, api_key=key, model=s.cascade_llm_model
            )
        return GeminiLLM(model=s.cascade_gemini_llm_model, api_key=s.gemini_api_key)

    # -- AIGateway -----------------------------------------------------------

    async def connect(self) -> None:
        logger.info("cascade.connected", stt=self._stt.label, llm=self._llm.label)

    async def send_audio(self, pcm: bytes, ts_ms: int | None = None) -> None:
        if self._listening:
            self._buf.extend(pcm)

    async def send_video(self, jpeg: bytes, ts_ms: int | None = None) -> None:
        return None  # the vision TOOLS see the frame; the text model need not

    async def send_activity_start(self) -> None:
        self._listening = True
        self._buf.clear()

    async def send_activity_end(self) -> None:
        if not self._listening:
            return
        self._listening = False
        audio = bytes(self._buf)
        self._buf.clear()
        ms = len(audio) * 1000 // (_SAMPLE_RATE * 2)
        if ms < _MIN_UTTERANCE_MS:
            logger.info("cascade.utterance_too_short", ms=ms)
            return
        self._start_turn(self._turn_from_audio(audio, ms))

    async def send_text(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self._start_turn(self._turn_from_text(text))

    async def send_tool_result(
        self, call_id: str, name: str, result: Any, ok: bool = True
    ) -> None:
        fut = self._pending.pop(call_id, None)
        if fut is None or fut.done():
            return
        fut.set_result(result if ok else {"ok": False, "error": result})

    async def events(self) -> AsyncIterator[GatewayEvent]:
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    async def interrupt(self) -> None:
        self._cancel_turn()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_turn()
        for closer in (self._stt.close, self._llm.close):
            try:
                await closer()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        if self._fallback_llm is not None:
            try:
                await self._fallback_llm.close()
            except Exception:  # noqa: BLE001 - shutdown must not raise
                pass
        await self._queue.put(None)

    # -- turns ---------------------------------------------------------------

    def _start_turn(self, coro: Any) -> None:
        """Run a turn, or hold it until the one in flight is done.

        A new utterance used to CANCEL the running turn — right for a Live
        model that is already speaking, wrong here: the reply is still being
        thought about, nothing is playing, and the user who says "hello?"
        again because nothing came back was killing the very answer they
        were waiting for (device 2026-09-15 00:07: six utterances, six
        cancelled turns, no reply at all). Only the newest held utterance is
        kept — "hello? hello? hello?" earns one answer, not three. A real
        barge-in still arrives through :meth:`interrupt`.
        """
        task = self._turn_task
        if task is not None and not task.done():
            held = self._held_turn
            if held is not None:
                held.close()
            self._held_turn = coro
            logger.info("cascade.turn_held")
            return
        self._held_turn = None
        self._turn_task = asyncio.create_task(coro, name="cascade_turn")
        self._turn_task.add_done_callback(self._turn_done)

    def _turn_done(self, task: asyncio.Task[None]) -> None:
        if self._turn_task is not task:
            return  # cancelled and replaced; the newer task owns the slot
        self._turn_task = None
        held = self._held_turn
        if held is not None and not self._closed:
            self._held_turn = None
            self._start_turn(held)

    def _cancel_turn(self) -> None:
        task = self._turn_task
        if task is not None and not task.done():
            task.cancel()
        self._turn_task = None
        held = self._held_turn
        if held is not None:
            held.close()
        self._held_turn = None
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _turn_from_audio(self, audio: bytes, ms: int) -> None:
        t0 = time.monotonic()
        try:
            text = await self._stt.transcribe(audio)
        except Exception as exc:  # noqa: BLE001 - one bad STT call is one lost turn
            logger.warning("cascade.stt_failed", error=repr(exc)[:200])
            return
        stt_ms = int((time.monotonic() - t0) * 1000)
        if not text:
            logger.info("cascade.no_speech", audio_ms=ms, stt_ms=stt_ms)
            return
        # Non-final first: that is the session's "turn.hearing" signal.
        await self._queue.put(TranscriptEvent(role="user", text=text, final=False))
        await self._queue.put(TranscriptEvent(role="user", text=text, final=True))
        await self._think(text, audio_ms=ms, stt_ms=stt_ms)

    async def _turn_from_text(self, text: str) -> None:
        await self._think(text, audio_ms=0, stt_ms=0)

    def _window(self) -> list[dict[str, Any]]:
        """The last N user turns of history (with their tool traffic)."""
        starts = [i for i, m in enumerate(self._history) if m.get("role") == "user"]
        if len(starts) <= self._history_turns:
            return list(self._history)
        return list(self._history[starts[-self._history_turns] :])

    async def _complete_with_fallback(self) -> LLMReply:
        """One chat round on the chosen model; on a timeout or error, the
        same round on the stand-in so the turn still ends in words."""
        try:
            return await asyncio.wait_for(
                self._llm.complete(self.system_prompt, self._window(), self.tools),
                _LLM_TURN_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure of the chosen model
            fallback = self._standin_llm()
            if fallback is None:
                raise
            logger.warning(
                "cascade.llm_fallback",
                from_model=self._llm.label,
                to_model=fallback.label,
                error=repr(exc)[:200],
            )
            return await asyncio.wait_for(
                fallback.complete(self.system_prompt, self._window(), self.tools),
                _LLM_TURN_TIMEOUT_S,
            )

    def _standin_llm(self) -> Any | None:
        if isinstance(self._llm, GeminiLLM):
            return None
        if self._fallback_llm is None:
            s = self._settings
            key = getattr(s, "gemini_api_key", "") or ""
            if not key:
                return None
            self._fallback_llm = GeminiLLM(model=s.cascade_gemini_llm_model, api_key=key)
        return self._fallback_llm

    async def _think(self, user_text: str, *, audio_ms: int, stt_ms: int) -> None:
        self._history.append({"role": "user", "content": user_text})
        t0 = time.monotonic()
        rounds = 0
        tokens_in = tokens_out = 0
        final_text = ""
        try:
            for _ in range(_MAX_TOOL_ROUNDS):
                rounds += 1
                reply = await self._complete_with_fallback()
                tokens_in += reply.usage.get("prompt_tokens", 0)
                tokens_out += reply.usage.get("completion_tokens", 0)
                if not reply.tool_calls:
                    final_text = reply.text
                    if final_text:
                        self._history.append({"role": "assistant", "content": final_text})
                    break
                self._history.append(
                    {
                        "role": "assistant",
                        "content": reply.text or None,
                        "raw": reply.raw,
                        "tool_calls": [
                            {
                                "id": cid,
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(args)},
                            }
                            for cid, name, args in reply.tool_calls
                        ],
                    }
                )
                loop = asyncio.get_running_loop()
                futures = []
                for cid, name, args in reply.tool_calls:
                    fut: asyncio.Future[Any] = loop.create_future()
                    self._pending[cid] = fut
                    futures.append((cid, name, fut))
                    await self._queue.put(ToolCallEvent(id=cid, name=name, args=args))
                for cid, name, fut in futures:
                    try:
                        result = await asyncio.wait_for(fut, _TOOL_RESULT_TIMEOUT_S)
                    except asyncio.TimeoutError:
                        result = {"ok": False, "error": "tool timed out"}
                    self._history.append(
                        {
                            "role": "tool",
                            "tool_call_id": cid,
                            "name": name,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the session must survive a bad turn
            logger.warning("cascade.llm_failed", error=repr(exc)[:300])
            final_text = "Sorry, I could not answer that just now."
        if final_text:
            await self._queue.put(
                TranscriptEvent(role="assistant", text=final_text, final=True)
            )
        logger.info(
            "cascade.turn",
            audio_ms=audio_ms,
            stt_ms=stt_ms,
            llm_ms=int((time.monotonic() - t0) * 1000),
            rounds=rounds,
            prompt_tokens=tokens_in,
            completion_tokens=tokens_out,
        )
        await self._queue.put(TurnCompleteEvent())
