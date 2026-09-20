from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


class CartItem(BaseModel):
    """One line of the cart: a model slug, how many, and a colour where the
    model offers one. Validated against the catalog in the service."""

    slug: str = Field(min_length=2, max_length=16)
    qty: int = Field(ge=1, le=5)
    colour: str | None = Field(default=None, max_length=24)


class Customer(BaseModel):
    """Who is buying and where the parcel goes — asked for in the cart,
    before the card. The country is validated against the shippable list in
    the service; the rest is length-checked here and trimmed."""

    email: EmailStr
    name: str = Field(min_length=2, max_length=100)
    phone: str = Field(min_length=6, max_length=30)
    line1: str = Field(min_length=3, max_length=120)
    line2: str | None = Field(default=None, max_length=120)
    po_box: str | None = Field(default=None, max_length=20)
    city: str = Field(min_length=2, max_length=60)
    state: str | None = Field(default=None, max_length=60)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str = Field(min_length=2, max_length=2)

    @field_validator("name", "phone", "line1", "line2", "po_box", "city", "state", "postal_code", mode="before")
    @classmethod
    def _trim(cls, v):
        if isinstance(v, str):
            v = " ".join(v.split())
            return v or None
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit())
        if len(digits) < 6:
            raise ValueError("phone number needs at least 6 digits")
        return v

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return v.upper()


class ShopCheckoutRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=10)
    customer: Customer


class OrderStatusRequest(BaseModel):
    status: Literal["paid", "shipped", "delivered", "cancelled"]
    note: str | None = Field(default=None, max_length=500)


class StockRequest(BaseModel):
    in_stock: bool
