from __future__ import annotations

import aiosqlite


class PaymentStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS payment_orders (
                order_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                code TEXT NOT NULL,
                payment_status TEXT NOT NULL DEFAULT 'pending',
                recharge_status TEXT NOT NULL DEFAULT 'pending',
                notes TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_payment_orders_recharge_status
            ON payment_orders (recharge_status);
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("PaymentStore.init() must be called before use.")
        return self._conn

    async def upsert_payment_success(
        self,
        *,
        order_id: str,
        user_id: int,
        amount: float,
        code: str,
        notes: str,
    ) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO payment_orders (order_id, user_id, amount, code, payment_status, recharge_status, notes)
            VALUES (?, ?, ?, ?, 'success', 'pending', ?)
            ON CONFLICT(order_id) DO UPDATE SET
                user_id=excluded.user_id,
                amount=excluded.amount,
                code=excluded.code,
                payment_status='success',
                recharge_status=CASE
                    WHEN payment_orders.recharge_status='success' THEN 'success'
                    ELSE 'pending'
                END,
                notes=excluded.notes,
                updated_at=CURRENT_TIMESTAMP
            """,
            (order_id, user_id, amount, code, notes),
        )
        await conn.commit()

    async def mark_recharge_success(self, order_id: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE payment_orders
            SET recharge_status='success',
                last_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE order_id=?
            """,
            (order_id,),
        )
        await conn.commit()

    async def mark_recharge_failed(self, order_id: str, error: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            UPDATE payment_orders
            SET recharge_status='failed',
                last_error=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE order_id=?
            """,
            (error[:2000], order_id),
        )
        await conn.commit()

    async def get_order(self, order_id: str) -> dict[str, object] | None:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT
                order_id,
                user_id,
                amount,
                code,
                payment_status,
                recharge_status,
                notes,
                last_error,
                created_at,
                updated_at
            FROM payment_orders
            WHERE order_id=?
            """,
            (order_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_failed_orders(self, limit: int = 50) -> list[dict[str, object]]:
        conn = self._require_conn()
        cursor = await conn.execute(
            """
            SELECT
                order_id,
                user_id,
                amount,
                code,
                payment_status,
                recharge_status,
                notes,
                last_error,
                created_at,
                updated_at
            FROM payment_orders
            WHERE payment_status='success'
              AND recharge_status='failed'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

