from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import aiosqlite

from tele_ai.constants import LANG_ZH


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self, global_pause_default: bool = False) -> None:
        self._conn = await aiosqlite.connect(self._db_path.as_posix())
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lang_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                lang TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_lang_history_chat_id_id
            ON lang_history(chat_id, id DESC);

            CREATE TABLE IF NOT EXISTS provider_stats (
                provider TEXT PRIMARY KEY,
                success_count INTEGER NOT NULL DEFAULT 0,
                failure_count INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO app_settings(key, value)
            VALUES('global_pause', ?)
            """,
            ("1" if global_pause_default else "0",),
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def is_global_paused(self) -> bool:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(
                "SELECT value FROM app_settings WHERE key = 'global_pause' LIMIT 1"
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return row["value"] == "1"

    async def set_global_pause(self, paused: bool) -> None:
        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                """
                INSERT INTO app_settings(key, value)
                VALUES('global_pause', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("1" if paused else "0",),
            )
            await conn.commit()

    async def record_chat_language(self, chat_id: int, lang_code: str) -> None:
        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                "INSERT INTO lang_history(chat_id, lang) VALUES(?, ?)",
                (chat_id, lang_code),
            )
            await conn.commit()

    async def get_target_language(
        self, chat_id: int, default_lang: str, history_limit: int
    ) -> str:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(
                """
                SELECT lang
                FROM lang_history
                WHERE chat_id = ? AND lang != ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (chat_id, LANG_ZH, history_limit),
            )
            rows = await cursor.fetchall()

        langs = [row["lang"] for row in rows if row["lang"]]
        if not langs:
            return default_lang

        # Find the most frequent language(s). On a tie, prefer whichever
        # appeared most recently (i.e. earliest in the DESC-ordered list).
        counts = Counter(langs)
        max_count = max(counts.values())
        candidates = {lang for lang, count in counts.items() if count == max_count}

        for lang in langs:
            if lang in candidates:
                return lang
        return default_lang

    async def record_provider_result(self, provider: str, success: bool) -> None:
        conn = self._require_conn()
        success_delta = 1 if success else 0
        failure_delta = 0 if success else 1
        async with self._lock:
            await conn.execute(
                """
                INSERT INTO provider_stats(provider, success_count, failure_count)
                VALUES(?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    success_count = success_count + excluded.success_count,
                    failure_count = failure_count + excluded.failure_count
                """,
                (provider, success_delta, failure_delta),
            )
            await conn.commit()

    async def get_provider_stats(self) -> dict[str, dict[str, int]]:
        conn = self._require_conn()
        async with self._lock:
            cursor = await conn.execute(
                "SELECT provider, success_count, failure_count FROM provider_stats ORDER BY provider ASC"
            )
            rows = await cursor.fetchall()
        return {
            row["provider"]: {
                "success_count": row["success_count"],
                "failure_count": row["failure_count"],
            }
            for row in rows
        }

    async def cleanup_lang_history(self, retention_hours: int) -> int:
        conn = self._require_conn()
        async with self._lock:
            await conn.execute(
                """
                DELETE FROM lang_history
                WHERE created_at < datetime('now', ?)
                """,
                (f"-{retention_hours} hours",),
            )
            cursor = await conn.execute("SELECT changes() AS affected")
            row = await cursor.fetchone()
            await conn.commit()
        if row is None:
            return 0
        return int(row["affected"])

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("StateStore.init() must be called before use.")
        return self._conn
