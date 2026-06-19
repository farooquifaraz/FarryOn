"""Tool abstraction.

A :class:`Tool` couples a JSON-Schema (exposed to the model exactly as defined
in ``PROTOCOL.md``) with an async implementation. Tools receive a
:class:`ToolContext` carrying request-scoped dependencies (DB session, owning
session id, user id) so implementations stay decoupled from transport and config.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(slots=True)
class ToolContext:
    """Request-scoped context passed to :meth:`Tool.run`.

    Attributes:
        session: Active async DB session (committed by the caller's scope).
        session_id: Owning ``/ws/live`` session id, if any.
        user_id: Owning user id, if resolved.
    """

    session: AsyncSession
    session_id: str | None = None
    user_id: int | None = None


class Tool(abc.ABC):
    """Abstract base for an agent tool.

    Subclasses set :attr:`name`, :attr:`description`, and :attr:`parameters`
    (a JSON-Schema object) and implement :meth:`run`.
    """

    #: Canonical tool name (must match ``PROTOCOL.md``).
    name: str
    #: Model-facing description.
    description: str
    #: JSON-Schema object describing the arguments.
    parameters: dict[str, Any]

    def spec(self) -> dict[str, Any]:
        """Return the ``{name, description, parameters}`` schema dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    @abc.abstractmethod
    async def run(self, ctx: ToolContext, **kwargs: Any) -> Any:
        """Execute the tool.

        Args:
            ctx: Request-scoped context.
            **kwargs: Validated arguments matching :attr:`parameters`.

        Returns:
            A JSON-serializable result returned to the model and the client.
        """
