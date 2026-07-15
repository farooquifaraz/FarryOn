"""Cross-cutting HTTP middleware: request-id correlation and security headers.

Both are pure ASGI-level concerns wired once in ``app/main.py`` — no route
needs to know about them.
"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# Swagger/ReDoc load their JS/CSS from a CDN; a strict CSP would blank them.
# Everything else this service serves is JSON, where 'none' is correct.
_CSP_EXEMPT_PATHS = ("/docs", "/redoc", "/openapi.json")

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Ignored by browsers over plain http (local dev); enforced once served
    # over https (Render terminates TLS in front of the app).
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind a per-request ``request_id`` into structlog's contextvars.

    Every log line emitted while handling the request carries the id
    (``logging_conf`` already has ``merge_contextvars`` first in its
    processor chain), and the response echoes it as ``X-Request-ID`` so a
    client-reported failure can be grepped straight to its server logs.
    An inbound ``X-Request-ID`` (e.g. from a proxy) is honored; otherwise
    one is generated.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if not request.url.path.startswith(_CSP_EXEMPT_PATHS):
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        return response
