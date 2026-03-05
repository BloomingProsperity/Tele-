from __future__ import annotations

from payment_bridge.sub2api_client import Sub2APIClient


def test_build_recharge_code_sanitizes_order_id() -> None:
    code = Sub2APIClient.build_recharge_code(" cm-123_ABC.# ", "s2p_")
    assert code == "s2p_cm-123_ABC"

