from __future__ import annotations

from pydantic import BaseModel


class ImpersonationTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    impersonating_user_id: int
