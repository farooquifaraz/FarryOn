"""Pydantic schemas for /api/v1/users (admin-side user management)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

UserStatus = Literal["active", "invited", "suspended", "deactivated"]
BulkAction = Literal["suspend", "activate", "delete"]


class UserListItem(BaseModel):
    id: int
    email: str | None
    display_name: str | None
    status: str
    email_verified: bool
    roles: list[str]
    created_at: datetime


class UserDetail(UserListItem):
    timezone: str | None
    locale: str | None
    avatar_url: str | None
    updated_at: datetime


class InviteUserRequest(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=255)
    role_ids: list[int] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    status: UserStatus | None = None
    timezone: str | None = None
    locale: str | None = None


class BulkActionRequest(BaseModel):
    user_ids: list[int] = Field(min_length=1, max_length=500)
    action: BulkAction


class BulkActionResultItem(BaseModel):
    user_id: int
    ok: bool
    error: str | None = None
