"""Pydantic request/response schemas for /api/v1/auth/*."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class VerifyEmailRequest(BaseModel):
    token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=256)


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime, seconds


class TwoFactorRequiredResponse(BaseModel):
    two_factor_required: bool = True
    pending_token: str


class UserPublic(BaseModel):
    id: int
    email: str | None
    display_name: str | None
    status: str
    email_verified: bool
    roles: list[str]
    # Permission codes for the admin frontend's <Can> component — UI hiding
    # only; the backend's require_permission stays the source of truth.
    permissions: list[str] = []
