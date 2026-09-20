from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    """One line of the cart: a model slug, how many, and a colour where the
    model offers one. Validated against the catalog in the service."""

    slug: str = Field(min_length=2, max_length=16)
    qty: int = Field(ge=1, le=5)
    colour: str | None = Field(default=None, max_length=24)


class ShopCheckoutRequest(BaseModel):
    items: list[CartItem] = Field(min_length=1, max_length=10)


class OrderStatusRequest(BaseModel):
    status: Literal["paid", "shipped", "delivered", "cancelled"]
    note: str | None = Field(default=None, max_length=500)
