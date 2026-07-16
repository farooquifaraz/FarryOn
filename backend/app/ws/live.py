"""The ``/ws/live`` WebSocket endpoint.

Resolves who is calling from the ``?token=`` JWT, builds the configured AI
gateway and tool engine, and hands control to a :class:`Session`.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, status

from app.agent.tool_engine import ToolEngine
from app.ai.factory import build_gateway
from app.config import Settings, get_settings
from app.core.security import decode_token
from app.logging_conf import get_logger
from app.observability import metrics
from app.tools import build_default_tools
from app.ws.session import Session

logger = get_logger(__name__)
router = APIRouter()


def _resolve_claims(websocket: WebSocket, settings: Settings) -> dict | None:
    """The signed-in user behind this connection, or ``None`` if there isn't one.

    A WebSocket handshake can't carry an ``Authorization`` header, so the access
    token rides in the query string instead — but it is the same token, verified
    the same way as :func:`app.core.deps.get_current_user`. That equivalence is
    the point: this was a hand-rolled signature check that read no claims and
    ignored ``exp``, so an expired token — or a refresh token used as an access
    token — sailed through.

    Returns ``None`` for absent/invalid/expired and lets the caller decide
    whether that is fatal; see :func:`ws_live`.
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    claims = decode_token(settings=settings, token=token)
    if claims is None or claims.get("type") != "access":
        return None
    try:
        int(claims["sub"])  # a token whose subject isn't an id names nobody
    except (KeyError, TypeError, ValueError):
        return None
    return claims


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Entry point for the realtime multimodal session.

    Auth is *enforced* only when a non-default ``JWT_SECRET`` is configured (see
    :attr:`Settings.auth_enabled`), so a local run still connects without one.
    The token is *read* either way: a signed-in dev session must own its notes
    and tasks exactly as a production one does, or scoping is a thing that only
    happens in prod — which is where it would first be tested, and first break.
    """
    settings = get_settings()

    claims = _resolve_claims(websocket, settings)
    if settings.auth_enabled and claims is None:
        logger.warning("ws.auth_rejected")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    metrics.WS_CONNECTIONS.inc()
    metrics.WS_ACTIVE.inc()
    logger.info("ws.connected", client=str(websocket.client))

    engine = ToolEngine.from_tools(
        build_default_tools(), timeout_seconds=settings.tool_timeout_seconds
    )
    schemas = engine.export_schemas()

    def gateway_factory(provider: str | None, system_prompt: str | None):
        """Build a gateway for the provider/prompt resolved from hello."""
        return build_gateway(
            schemas, settings, provider=provider, system_prompt=system_prompt
        )

    session = Session(
        websocket,
        gateway_factory=gateway_factory,
        engine=engine,
        settings=settings,
        claims=claims,
    )
    try:
        await session.run()
    finally:
        metrics.WS_ACTIVE.dec()
        logger.info("ws.disconnected", session_id=session.session_id)
