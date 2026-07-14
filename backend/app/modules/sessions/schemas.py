"""Pydantic schemas for /api/v1/me/sessions and /api/v1/users/{id}/sessions."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionOut(BaseModel):
    family_id: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip: str | None
    is_current: bool
