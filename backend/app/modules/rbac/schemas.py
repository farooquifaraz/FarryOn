"""Pydantic schemas for /api/v1/roles, /api/v1/permissions, /api/v1/users/{id}/roles."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionOut(BaseModel):
    code: str
    description: str | None


class RoleOut(BaseModel):
    id: int
    name: str
    description: str | None
    level: int
    is_system: bool
    permissions: list[str]


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    level: int = Field(default=0, ge=0, le=99)  # 100 (super_admin's level) is reserved
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdateRequest(BaseModel):
    description: str | None = None
    level: int | None = Field(default=None, ge=0, le=99)
    permission_codes: list[str] | None = None


class SetUserRolesRequest(BaseModel):
    role_ids: list[int]
