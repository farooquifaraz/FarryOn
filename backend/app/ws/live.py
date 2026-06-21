"""The ``/ws/live`` WebSocket endpoint.

Accepts the connection (optionally verifying the ``?token=`` JWT), builds the
configured AI gateway and tool engine, and hands control to a :class:`Session`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi import APIRouter, WebSocket, status

from app.agent.tool_engine import ToolEngine
from app.ai.factory import build_gateway
from app.config import get_settings
from app.logging_conf import get_logger
from app.observability import metrics
from app.tools import build_default_tools
from app.ws.session import Session

logger = get_logger(__name__)
router = APIRouter()


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment, restoring missing padding."""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def verify_jwt(token: str, secret: str) -> bool:
    """Verify a minimal HS256 JWT signature (no external dependency).

    This is intentionally small: it validates the HMAC-SHA256 signature over
    ``header.payload`` using ``secret``. It does not check ``exp``/claims —
    swap in ``pyjwt`` for production claim validation. Returns ``False`` on any
    malformed input rather than raising.
    """
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError:
        return False
    try:
        header = json.loads(_b64url_decode(header_b64))
    except (ValueError, json.JSONDecodeError):
        return False
    if header.get("alg") != "HS256":
        return False
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    try:
        provided = _b64url_decode(signature_b64)
    except ValueError:
        return False
    return hmac.compare_digest(expected, provided)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """Entry point for the realtime multimodal session.

    Auth is enforced only when a non-default ``JWT_SECRET`` is configured (see
    :attr:`Settings.auth_enabled`); local/dev runs skip it for convenience.
    """
    settings = get_settings()

    if settings.auth_enabled:
        token = websocket.query_params.get("token")
        if not token or not verify_jwt(token, settings.jwt_secret):
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

    def gateway_factory(provider: str | None):
        """Build a gateway for the provider the client requested in hello."""
        return build_gateway(schemas, settings, provider=provider)

    session = Session(
        websocket,
        gateway_factory=gateway_factory,
        engine=engine,
        settings=settings,
    )
    try:
        await session.run()
    finally:
        metrics.WS_ACTIVE.dec()
        logger.info("ws.disconnected", session_id=session.session_id)
