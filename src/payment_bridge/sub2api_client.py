from __future__ import annotations

import re

import httpx


class Sub2APIRequestError(Exception):
    """Raised when Sub2API request fails."""


class Sub2APIClient:
    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def build_recharge_code(order_id: str, prefix: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "", order_id.strip())
        if not sanitized:
            raise ValueError("order_id became empty after sanitization.")
        return f"{prefix}{sanitized}"

    async def create_and_redeem(
        self,
        *,
        order_id: str,
        user_id: int,
        amount: float,
        notes: str,
        code_prefix: str,
        idempotency_key: str,
    ) -> dict[str, object]:
        code = self.build_recharge_code(order_id, code_prefix)
        url = f"{self._base_url}/api/v1/admin/redeem-codes/create-and-redeem"
        payload = {
            "code": code,
            "type": "balance",
            "value": round(float(amount), 2),
            "user_id": user_id,
            "notes": notes,
        }
        headers = {
            "x-api-key": self._api_key,
            "Idempotency-Key": idempotency_key,
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise Sub2APIRequestError(f"Network error while calling Sub2API: {exc}") from exc

        if response.status_code in {200, 201}:
            try:
                body = response.json()
            except ValueError:
                body = {"raw": response.text}
            return {"code": code, "status_code": response.status_code, "body": body}

        raise Sub2APIRequestError(
            f"Sub2API error {response.status_code}: {response.text[:1000]}"
        )

