"""Tool registry, schema export, argument validation, and dispatch.

The :class:`ToolEngine` owns the set of available tools. It:
- exports their schemas (for handing to the AI gateway and for tests),
- validates model-provided arguments against each tool's JSON-Schema,
- dispatches to the tool's async ``run`` with a timeout and error capture,
- returns a uniform :class:`ToolResult`.

The validator covers the JSON-Schema subset used by ``PROTOCOL.md`` (object with
typed string/number/integer/boolean properties and a ``required`` list). This
avoids a hard ``jsonschema`` dependency while still rejecting malformed calls.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.logging_conf import get_logger
from app.tools.base import Tool, ToolContext

logger = get_logger(__name__)

_JSON_TYPE_TO_PY: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


class ToolValidationError(ValueError):
    """Raised when model-provided arguments fail schema validation."""


@dataclass(slots=True)
class ToolResult:
    """Outcome of a tool dispatch.

    Attributes:
        name: Tool name.
        ok: Whether execution succeeded.
        result: JSON-serializable result (on success).
        error: Error message (on failure).
        duration_ms: Wall-clock execution time in milliseconds.
    """

    name: str
    ok: bool
    result: Any = None
    error: str | None = None
    duration_ms: int = 0


@dataclass(slots=True)
class ToolEngine:
    """A registry + dispatcher for :class:`~app.tools.base.Tool` instances."""

    tools: dict[str, Tool] = field(default_factory=dict)
    timeout_seconds: float = 20.0

    @classmethod
    def from_tools(
        cls, tools: list[Tool], *, timeout_seconds: float = 20.0
    ) -> "ToolEngine":
        """Build an engine from a list of tool instances."""
        engine = cls(timeout_seconds=timeout_seconds)
        for tool in tools:
            engine.register(tool)
        return engine

    def register(self, tool: Tool) -> None:
        """Register a tool, rejecting duplicate names."""
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool name: {tool.name!r}")
        self.tools[tool.name] = tool

    def has(self, name: str) -> bool:
        """Return whether a tool named ``name`` is registered."""
        return name in self.tools

    def export_schemas(self) -> list[dict[str, Any]]:
        """Return the canonical tool schema list (matches ``PROTOCOL.md``)."""
        return [tool.spec() for tool in self.tools.values()]

    def validate(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Validate ``args`` against the named tool's schema.

        Returns:
            The (possibly coerced) argument dict limited to known properties.

        Raises:
            ToolValidationError: If the tool is unknown or args are invalid.
        """
        tool = self.tools.get(name)
        if tool is None:
            raise ToolValidationError(f"unknown tool: {name!r}")
        if not isinstance(args, dict):
            raise ToolValidationError("arguments must be an object")

        schema = tool.parameters
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])

        for key in required:
            if key not in args or args[key] is None:
                raise ToolValidationError(
                    f"{name}: missing required argument {key!r}"
                )

        cleaned: dict[str, Any] = {}
        for key, value in args.items():
            if key not in properties:
                # Ignore unexpected keys rather than failing the whole call.
                continue
            expected = properties[key].get("type")
            if expected and value is not None:
                py_type = _JSON_TYPE_TO_PY.get(expected)
                # bool is a subclass of int; guard so "integer" != bool.
                if py_type is not None and not isinstance(value, py_type):
                    raise ToolValidationError(
                        f"{name}: argument {key!r} must be {expected}, "
                        f"got {type(value).__name__}"
                    )
                if expected in ("integer", "number") and isinstance(value, bool):
                    raise ToolValidationError(
                        f"{name}: argument {key!r} must be {expected}, got bool"
                    )
            cleaned[key] = value
        return cleaned

    async def dispatch(
        self, name: str, args: dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        """Validate then execute a tool, capturing errors and timing.

        Never raises for tool-level failures; returns a :class:`ToolResult`
        with ``ok=False`` so the caller can still feed the model a result and
        keep the session alive.
        """
        start = time.monotonic()
        try:
            cleaned = self.validate(name, args)
        except ToolValidationError as exc:
            logger.warning("tool.validation_error", tool=name, error=str(exc))
            return ToolResult(
                name=name,
                ok=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )

        tool = self.tools[name]
        try:
            result = await asyncio.wait_for(
                tool.run(ctx, **cleaned), timeout=self.timeout_seconds
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info("tool.ok", tool=name, duration_ms=duration_ms)
            return ToolResult(
                name=name, ok=True, result=result, duration_ms=duration_ms
            )
        except asyncio.TimeoutError:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("tool.timeout", tool=name, duration_ms=duration_ms)
            return ToolResult(
                name=name,
                ok=False,
                error=f"tool timed out after {self.timeout_seconds}s",
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 - capture any tool failure
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("tool.error", tool=name, error=str(exc))
            return ToolResult(
                name=name,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=duration_ms,
            )
