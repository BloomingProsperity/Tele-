from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class PaymentWebhookPayload(BaseModel):
    order_id: str = Field(min_length=1, max_length=128)
    user_id: int = Field(gt=0)
    amount: float = Field(gt=0)
    status: str = Field(default="success")
    notes: str | None = None

    @field_validator("order_id")
    @classmethod
    def normalize_order_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("order_id must not be empty.")
        return stripped

    @field_validator("status")
    @classmethod
    def normalize_status(cls, value: str) -> str:
        stripped = value.strip().lower()
        if not stripped:
            raise ValueError("status must not be empty.")
        return stripped

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class RechargeResponse(BaseModel):
    ok: bool
    order_id: str
    code: str
    recharge_status: str
    detail: str | None = None

