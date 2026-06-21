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

        # 5. Feed the result back to the model to continue the turn.
        try:
            payload: Any = result.result if result.ok else (result.error or "error")
            await self._gateway.send_tool_result(
                event.id, event.name, payload, ok=result.ok
            )
        except Exception as exc:  # noqa: BLE001 - provider feedback best-effort
            logger.error("tool_call.feedback_failed", error=str(exc))

        return result

    async def _safe_notify(self, message: dict[str, Any]) -> None:
        """Send a client UI message, swallowing transport errors."""
        try:
            await self._notify(message)
        except Exception as exc:  # noqa: BLE001 - client may have gone away
            logger.warning("tool_call.notify_failed", error=str(exc))
