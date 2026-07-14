"""Uniform response envelope for the admin/user module's ``/api/v1/*`` routes.

Existing endpoints (``/notes``, ``/tasks``, ``/detect``, ...) are untouched —
they keep returning their current (non-enveloped) shapes so no existing
client breaks. This envelope is opt-in: only routers under ``app/modules/``
use it.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """A stable, machine-readable API error.

    Raised by module services/routers; converted to the uniform error
    envelope by :func:`app_error_handler`. Never raised by pre-existing
    (non-module) code, so registering the handler has zero effect on any
    endpoint outside ``app/modules/``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        fields: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.fields = fields or {}


def ok(data: Any = None, *, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a successful envelope."""
    envelope: dict[str, Any] = {"success": True, "data": data, "error": None}
    if meta is not None:
        envelope["meta"] = meta
    return envelope


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """FastAPI exception handler for :class:`AppError`."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "fields": exc.fields,
            },
        },
    )
