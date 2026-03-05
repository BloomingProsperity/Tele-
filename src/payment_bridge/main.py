from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from .config import Settings, load_settings
from .models import PaymentWebhookPayload, RechargeResponse
from .store import PaymentStore
from .sub2api_client import Sub2APIClient, Sub2APIRequestError


LOGGER = logging.getLogger("payment_bridge")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _require_secret(configured_secret: str | None, incoming_secret: str | None, label: str) -> None:
    if not configured_secret:
        return
    if incoming_secret != configured_secret:
        raise HTTPException(status_code=401, detail=f"Invalid {label}.")


def create_app(settings: Settings) -> FastAPI:
    store = PaymentStore(str(settings.payment_state_db_path))
    sub2api_client = Sub2APIClient(
        base_url=settings.sub2api_base_url,
        api_key=settings.sub2api_admin_api_key,
        timeout_seconds=settings.sub2api_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.init()
        LOGGER.info("Payment store initialized at %s", settings.payment_state_db_path)
        try:
            yield
        finally:
            await sub2api_client.close()
            await store.close()

    app = FastAPI(title="Sub2API Payment Bridge", version="0.1.0", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/payment/success", response_model=RechargeResponse)
    async def payment_success_webhook(
        payload: PaymentWebhookPayload,
        x_webhook_secret: str | None = Header(default=None),
    ) -> RechargeResponse:
        _require_secret(settings.payment_webhook_secret, x_webhook_secret, "webhook secret")

        if payload.status != "success":
            return RechargeResponse(
                ok=False,
                order_id=payload.order_id,
                code="",
                recharge_status="ignored",
                detail=f"status={payload.status}",
            )

        code = sub2api_client.build_recharge_code(payload.order_id, settings.recharge_code_prefix)
        notes = payload.notes or f"external payment order: {payload.order_id}"
        await store.upsert_payment_success(
            order_id=payload.order_id,
            user_id=payload.user_id,
            amount=payload.amount,
            code=code,
            notes=notes,
        )

        idem_key = f"pay-{payload.order_id}-success"
        try:
            await sub2api_client.create_and_redeem(
                order_id=payload.order_id,
                user_id=payload.user_id,
                amount=payload.amount,
                notes=notes,
                code_prefix=settings.recharge_code_prefix,
                idempotency_key=idem_key,
            )
        except Sub2APIRequestError as exc:
            LOGGER.error("Recharge failed for order %s: %s", payload.order_id, exc)
            await store.mark_recharge_failed(payload.order_id, str(exc))
            return RechargeResponse(
                ok=False,
                order_id=payload.order_id,
                code=code,
                recharge_status="failed",
                detail=str(exc),
            )

        await store.mark_recharge_success(payload.order_id)
        return RechargeResponse(
            ok=True,
            order_id=payload.order_id,
            code=code,
            recharge_status="success",
        )

    @app.get("/admin/orders/{order_id}")
    async def get_order(
        order_id: str,
        x_admin_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_secret(settings.payment_admin_secret, x_admin_secret, "admin secret")
        order = await store.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        return order

    @app.get("/admin/orders/failed")
    async def list_failed_orders(
        limit: int = 50,
        x_admin_secret: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_secret(settings.payment_admin_secret, x_admin_secret, "admin secret")
        safe_limit = min(max(limit, 1), 200)
        rows = await store.list_failed_orders(safe_limit)
        return {"items": rows, "count": len(rows)}

    @app.post("/admin/orders/{order_id}/retry", response_model=RechargeResponse)
    async def retry_order_recharge(
        order_id: str,
        x_admin_secret: str | None = Header(default=None),
    ) -> RechargeResponse:
        _require_secret(settings.payment_admin_secret, x_admin_secret, "admin secret")
        order = await store.get_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order["payment_status"] != "success":
            raise HTTPException(status_code=409, detail="Payment is not in success state.")
        if order["recharge_status"] == "success":
            return RechargeResponse(
                ok=True,
                order_id=order_id,
                code=str(order["code"]),
                recharge_status="success",
                detail="Already recharged.",
            )

        retry_key = f"retry-{order_id}-{int(time.time())}"
        try:
            await sub2api_client.create_and_redeem(
                order_id=order_id,
                user_id=int(order["user_id"]),
                amount=float(order["amount"]),
                notes=str(order.get("notes") or f"retry order: {order_id}"),
                code_prefix=settings.recharge_code_prefix,
                idempotency_key=retry_key,
            )
        except Sub2APIRequestError as exc:
            LOGGER.error("Retry failed for order %s: %s", order_id, exc)
            await store.mark_recharge_failed(order_id, str(exc))
            return RechargeResponse(
                ok=False,
                order_id=order_id,
                code=str(order["code"]),
                recharge_status="failed",
                detail=str(exc),
            )

        await store.mark_recharge_success(order_id)
        return RechargeResponse(
            ok=True,
            order_id=order_id,
            code=str(order["code"]),
            recharge_status="success",
        )

    return app


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.payment_listen_host,
        port=settings.payment_listen_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

