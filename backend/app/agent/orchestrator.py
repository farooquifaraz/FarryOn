"""Agent orchestrator: runs the model's tool-call loop.

The orchestrator sits between the AI gateway and the tool engine. When the
gateway emits a :class:`~app.ai.events.ToolCallEvent`, the
:class:`~app.ws.session.Session` calls :meth:`Orchestrator.handle_tool_call`,
which:

1. notifies the client (``tool_call`` UI event) via a callback,
2. dispatches to the :class:`~app.agent.tool_engine.ToolEngine` (validate +
   run, with timeout and error capture) inside a fresh DB session,
3. persists a :class:`~app.db.models.ToolCall` audit row,
4. notifies the client of the outcome (``tool_result`` UI event),
5. feeds the result back to the gateway so the model continues the turn.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.tool_engine import ToolEngine, ToolResult
from app.ai.base import AIGateway
from app.ai.events import ToolCallEvent
from app.db import repo
from app.logging_conf import get_logger
from app.observability import metrics
from app.tools.base import ToolContext

logger = get_logger(__name__)

#: Callback signature for surfacing tool lifecycle to the client UI.
ClientNotify = Callable[[dict[str, Any]], Awaitable[None]]


class Orchestrator:
    """Coordinates tool execution between the gateway and the tool engine."""

    def __init__(
        self,
        *,
        engine: ToolEngine,
        gateway: AIGateway,
        sessionmaker: async_sessionmaker,
        notify_client: ClientNotify,
        session_id: str | None = None,
        user_id: int | None = None,
        web_search: dict[str, Any] | None = None,
        email: dict[str, Any] | None = None,
        location: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            engine: The tool engine to dispatch through.
            gateway: The AI gateway to feed tool results back to.
            sessionmaker: Async DB session factory for per-call sessions.
            notify_client: Async callback emitting ``tool_call``/``tool_result``
                JSON dicts to the client for UI display.
            session_id: Owning live-session id (for audit + tool context).
            user_id: Owning user id (for tool context).
        """
        self._engine = engine
        self._gateway = gateway
        self._sessionmaker = sessionmaker
        self._notify = notify_client
        self._session_id = session_id
        self._user_id = user_id
        self._web_search = web_search
        self._email = email
        #: Mutable — updated in place when the client sends a ``location_update``.
        self.location = location
        #: Mutable — set to the latest INPUT_VIDEO JPEG by the session so the
        #: ``identify_image`` tool can inspect what the camera currently sees,
        #: with the monotonic time it arrived so stale frames can be rejected.
        self.last_frame: bytes | None = None
        self.last_frame_at: float | None = None
        #: In-flight device contact-resolution requests, keyed by requestId.
        #: ``request_contact_resolution`` creates a Future here and the session
        #: resolves it when the matching ``resolve_contact_result`` arrives.
        self._pending_resolves: dict[str, asyncio.Future[dict[str, Any]]] = {}
        #: Futures awaiting the NEXT INPUT_VIDEO frame. The ``capture_photo``
        #: tool (B3 glasses photo-trigger) parks here; the session resolves
        #: them when a fresh frame lands, so the tool returns exactly when the
        #: glasses photo is in the model's context — not before, not after.
        self._frame_waiters: list[asyncio.Future[bool]] = []
        #: Recently device-resolved names -> contact_id, so send_whatsapp /
        #: send_message still work if a weaker model forgets to thread the
        #: contact_id back from resolve_contact (it passes just the name).
        self._resolved_ids: dict[str, str] = {}
        #: Recently device-resolved names -> real phone, for send_telegram's
        #: user-account (MTProto) path which dials the number server-side.
        self._resolved_phones: dict[str, str] = {}

    async def request_contact_resolution(
        self, name: str, channel: str
    ) -> dict[str, Any]:
        """Ask the device to resolve a contact NAME locally and await the reply.

        Privacy-preserving: the phone matches against its own contacts and
        returns only masked numbers + opaque per-session contact ids — the real
        number never reaches the server. Returns the device's payload, or
        ``{"status": "index_unavailable"}`` if it doesn't answer in time.
        """
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_resolves[request_id] = future
        try:
            await self._safe_notify(
                {
                    "type": "resolve_contact_request",
                    "requestId": request_id,
                    "name": name,
                    "channel": channel,
                }
            )
            result = await asyncio.wait_for(future, timeout=8.0)
            # Remember a single unambiguous match so a later send_* call can
            # still find it by name if the model didn't pass the contact_id.
            cands = result.get("candidates") or []
            if result.get("status") == "found" and len(cands) == 1:
                key = name.strip().lower()
                cid = cands[0].get("contactId")
                if cid:
                    self._resolved_ids[key] = cid
                # The device includes the real phone for telegram (the user's
                # own account needs it to dial); cache it for send_telegram.
                ph = cands[0].get("phone")
                if ph:
                    self._resolved_phones[key] = ph
            return result
        except (asyncio.TimeoutError, Exception):  # noqa: BLE001
            return {"status": "index_unavailable"}
        finally:
            self._pending_resolves.pop(request_id, None)

    def resolve_pending(self, request_id: str, payload: dict[str, Any]) -> None:
        """Fulfil the Future for a device ``resolve_contact_result`` reply."""
        future = self._pending_resolves.get(request_id)
        if future is not None and not future.done():
            future.set_result(payload)

    async def wait_for_frame(self, timeout: float = 8.0) -> bool:
        """Block until the next INPUT_VIDEO frame arrives (or ``timeout``).

        Used by the ``capture_photo`` tool so a voice-triggered glasses photo
        is in the model's context before it answers. Returns ``True`` if a
        fresh frame landed, ``False`` on timeout.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._frame_waiters.append(future)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            if future in self._frame_waiters:
                self._frame_waiters.remove(future)

    def notify_new_frame(self) -> None:
        """Wake any tool awaiting a fresh frame (called by the session on an
        incoming INPUT_VIDEO frame)."""
        for future in self._frame_waiters:
            if not future.done():
                future.set_result(True)

    def recall_resolved(self, name: str) -> str | None:
        """Contact id from a recent device resolution of ``name``, if any."""
        return self._resolved_ids.get((name or "").strip().lower())

    def recall_phone(self, name: str) -> str | None:
        """Real phone from a recent device resolution of ``name``, if any."""
        return self._resolved_phones.get((name or "").strip().lower())

    def note_phone(self, name: str, phone: str) -> None:
        """Cache a phone resolved server-side (e.g. a Telegram-contact search)
        so a follow-up send_telegram by name can use it."""
        if name and phone:
            self._resolved_phones[name.strip().lower()] = phone

    async def handle_tool_call(self, event: ToolCallEvent) -> ToolResult:
        """Execute one model-requested tool call end-to-end.

        Returns the :class:`ToolResult`; also feeds it back to the gateway and
        notifies the client. Persistence and gateway feedback failures are
        logged but never propagate (the session must survive a bad tool call).
        """
        metrics.TOOL_CALLS.labels(name=event.name).inc()
        logger.info(
            "tool_call.received",
            tool=event.name,
            call_id=event.id,
            session_id=self._session_id,
        )

        # 1. Notify client that a tool call started.
        needs_permission = False
        await self._safe_notify(
            {
                "type": "tool_call",
                "id": event.id,
                "name": event.name,
                "args": event.args,
                "needsPermission": needs_permission,
            }
        )

        # 2. Dispatch within a dedicated DB session and 3. persist the audit row.
        async with self._sessionmaker() as db:
            ctx = ToolContext(
                session=db,
                session_id=self._session_id,
                user_id=self._user_id,
                web_search=self._web_search,
                email=self._email,
                location=self.location,
                last_frame=self.last_frame,
                last_frame_at=self.last_frame_at,
                resolve_contact=self.request_contact_resolution,
                recall_resolved=self.recall_resolved,
                recall_phone=self.recall_phone,
                note_phone=self.note_phone,
                wait_for_frame=self.wait_for_frame,
            )
            result = await self._engine.dispatch(event.name, event.args, ctx)
            try:
                await repo.record_tool_call(
                    db,
                    call_id=event.id,
                    name=event.name,
                    args=event.args,
                    ok=result.ok,
                    result=result.result,
                    error=result.error,
                    duration_ms=result.duration_ms,
                    session_id=self._session_id,
                )
                await self._log_send_if_messaging(db, event.name, result)
                await db.commit()
            except Exception as exc:  # noqa: BLE001 - audit must not break turn
                await db.rollback()
                logger.error("tool_call.audit_failed", error=str(exc))

        metrics.TOOL_LATENCY.labels(name=event.name).observe(
            result.duration_ms / 1000.0
        )

        # 4. Notify client of the result.
        await self._safe_notify(
            {
                "type": "tool_result",
                "id": event.id,
                "name": event.name,
                "ok": result.ok,
                "result": result.result if result.ok else None,
                "error": result.error,
            }
        )

        # 5. Feed the result back to the model to continue the turn. For
        #    fire-and-forget device tools the model already spoke its one
        #    acknowledgement WHEN it called the tool; if we hand back a plain
        #    result it narrates a SECOND time (heard as a repeated response).
        #    We must still send a result (the Live protocol requires one), so
        #    we embed a "stay silent" instruction for these tools.
        try:
            if result.ok and event.name in self._SILENT_RESULT_TOOLS:
                payload: Any = {
                    "applied": True,
                    "_instruction": "Done. Do NOT speak or respond again about "
                    "this — you already acknowledged it to the user.",
                }
            else:
                payload = result.result if result.ok else (result.error or "error")
            await self._gateway.send_tool_result(
                event.id, event.name, payload, ok=result.ok
            )
        except Exception as exc:  # noqa: BLE001 - provider feedback best-effort
            logger.error("tool_call.feedback_failed", error=str(exc))

        return result

    _SEND_TOOLS = {"send_whatsapp", "send_message", "send_telegram"}

    # Fire-and-forget device tools: the client acts, the model already spoke
    # once. Suppress the post-result re-narration (double voice).
    _SILENT_RESULT_TOOLS = {
        "mute_mic",
        "set_camera",
        "rotate_camera",
        "set_camera_zoom",
        "enable_bluetooth",
        "connect_glasses",
        "disconnect_glasses",
        "end_session",
    }

    async def _log_send_if_messaging(self, db, name: str, result) -> None:
        """Record a successful messaging send to the history/audit log.

        ``status`` encodes channel + outcome (``telegram:delivered`` /
        ``whatsapp:opened`` / ``telegram:copied``) so the user can later ask
        "what did I send" and we have a compliance trail. Best-effort — never
        breaks the turn.
        """
        if name not in self._SEND_TOOLS or not result.ok:
            return
        res = result.result if isinstance(result.result, dict) else {}
        text = (res.get("message") or "").strip()
        if not text:
            return
        channel = (
            res.get("channel")
            or res.get("platform")
            or name.replace("send_", "")
        )
        if res.get("sent") or res.get("delivered"):
            state = "delivered"
        elif res.get("copy_to_clipboard"):
            state = "copied"
        elif res.get("action") in ("open_url", "open_messaging"):
            state = "opened"
        else:
            state = "done"
        recipient = res.get("to") or res.get("name") or "unknown"
        await repo.add_outbound_message(
            db, contact=str(recipient), text=text,
            user_id=self._user_id, session_id=self._session_id,
            status=f"{channel}:{state}",
        )

    async def _safe_notify(self, message: dict[str, Any]) -> None:
        """Send a client UI message, swallowing transport errors."""
        try:
            await self._notify(message)
        except Exception as exc:  # noqa: BLE001 - client may have gone away
            logger.warning("tool_call.notify_failed", error=str(exc))
