"""Pydantic schemas for /api/v1/me/2fa/* and /api/v1/auth/2fa/verify-login."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EnrollResponse(BaseModel):
    secret: str  # base32, for manual entry if the user can't scan
    otpauth_uri: str
    qr_code_png_base64: str


class ConfirmEnrollRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class ConfirmEnrollResponse(BaseModel):
    enabled: bool
    recovery_codes: list[str]  # shown exactly once


class DisableRequest(BaseModel):
    password: str


class VerifyLoginRequest(BaseModel):
    pending_token: str
    # 6-digit TOTP code, or a "xxxxxxxx-xxxxxxxx" recovery code (17 chars).
    code: str = Field(min_length=6, max_length=20)
