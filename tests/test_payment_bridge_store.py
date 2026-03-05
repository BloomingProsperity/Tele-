from __future__ import annotations

from pathlib import Path

import pytest

from payment_bridge.store import PaymentStore


@pytest.mark.asyncio
async def test_payment_store_basic_flow(tmp_path: Path) -> None:
    db_path = tmp_path / "payment.db"
    store = PaymentStore(str(db_path))
    await store.init()
    try:
        await store.upsert_payment_success(
            order_id="order_1",
            user_id=123,
            amount=88.8,
            code="s2p_order_1",
            notes="test",
        )

        order = await store.get_order("order_1")
        assert order is not None
        assert order["payment_status"] == "success"
        assert order["recharge_status"] == "pending"

        await store.mark_recharge_failed("order_1", "network timeout")
        order = await store.get_order("order_1")
        assert order is not None
        assert order["recharge_status"] == "failed"
        assert order["last_error"] == "network timeout"

        failed = await store.list_failed_orders(limit=10)
        assert len(failed) == 1
        assert failed[0]["order_id"] == "order_1"

        await store.mark_recharge_success("order_1")
        order = await store.get_order("order_1")
        assert order is not None
        assert order["recharge_status"] == "success"
        assert order["last_error"] is None
    finally:
        await store.close()

