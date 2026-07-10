"""Tool abstraction.

A :class:`Tool` couples a JSON-Schema (exposed to the model exactly as defined
in ``PROTOCOL.md``) with an async implementation. Tools receive a
:class:`ToolContext` carrying request-scoped dependencies (DB session, owning
session id, user id) so implementations stay decoupled from transport and config.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable
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
        web_search: Optional per-session web-search config supplied by the
            client (``{provider, apiKey, fallbackProvider, fallbackApiKey}``).
            When present it overrides the server's env settings for this session.
        email: Optional per-session email (IMAP) config supplied by the client
            (``{address, appPassword, host?}``). Used by the ``read_emails``
            tool to read the user's recent mail. Never persisted server-side.
        location: Optional last-known device location supplied by the client
            (``{lat, lng, address?}``). Updated via ``location_update`` and read
            by the ``get_location`` tool to answer "where am I?".
        last_frame: Most recent camera frame (raw JPEG bytes) streamed by the
            device over INPUT_VIDEO. Cached by the session and read by the
            ``identify_image`` tool to answer "what landmark/object is this?".
        last_frame_at: Monotonic timestamp (``time.monotonic()``) when
            ``last_frame`` arrived, so the tool can reject a stale frame from
            before the camera was lowered/turned off.
    """

    session: AsyncSession
    session_id: str | None = None
    user_id: int | None = None
    web_search: dict[str, Any] | None = None
    email: dict[str, Any] | None = None
    location: dict[str, Any] | None = None
    last_frame: bytes | None = None
    last_frame_at: float | None = None
    #: Round-trip to the device to resolve a contact NAME against the phone's
    #: own contacts (privacy-preserving: the real number never reaches the
    #: server — only a masked number + an opaque per-session contact id). Set by
    #: the orchestrator; ``None`` outside a live session (e.g. in tests).
    #: Signature: ``await resolve_contact(name, channel) -> dict``.
    resolve_contact: Callable[[str, str], Awaitable[dict[str, Any]]] | None = None
    #: Recall the contact id from a recent device resolution of a name, so a
    #: send_* tool still works if the model passed only the name (not the id).
    #: Signature: ``recall_resolved(name) -> contact_id | None``.
    recall_resolved: Callable[[str], str | None] | None = None
    #: Recall the real phone number from a recent device resolution — used by
    #: send_telegram (user-account / MTProto) which must dial a number
    #: server-side. Signature: ``recall_phone(name) -> phone | None``.
    recall_phone: Callable[[str], str | None] | None = None
    #: Cache a phone resolved server-side (e.g. a Telegram-contact search) so a
    #: later send_telegram by name can use it. Signature:
    #: ``note_phone(name, phone) -> None``.
    note_phone: Callable[[str, str], None] | None = None
    #: Await the NEXT camera frame (INPUT_VIDEO). Used by ``capture_photo`` so a
    #: voice-triggered glasses photo is in context before the model answers.
    #: Signature: ``await wait_for_frame(timeout) -> bool`` (True if a frame
    #: landed). Set by the orchestrator; ``None`` outside a live session.
    wait_for_frame: Callable[..., Awaitable[bool]] | None = None


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
