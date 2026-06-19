"""Structured logging configuration using :mod:`structlog`.

Emits JSON log lines so the service plays nicely with log aggregators. Call
:func:`configure_logging` exactly once at process start (the FastAPI lifespan
handler does this).
"""

from __future__ import annotations

import logging
import sys

import structlog

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib ``logging`` and ``structlog`` to emit JSON.

    Idempotent: safe to call more than once (subsequent calls are no-ops).

    Args:
        level: Root log level name, e.g. ``"INFO"`` or ``"DEBUG"``.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger.

    Args:
        name: Optional logger name, conventionally the module ``__name__``.
    """
    return structlog.get_logger(name)
