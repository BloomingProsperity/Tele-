import pytest

from tele_ai.state import StateStore


@pytest.mark.asyncio
async def test_global_pause_toggle(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init(global_pause_default=False)

    assert await store.is_global_paused() is False
    await store.set_global_pause(True)
    assert await store.is_global_paused() is True
    await store.set_global_pause(False)
    assert await store.is_global_paused() is False
    await store.close()


@pytest.mark.asyncio
async def test_target_language_majority_and_recency(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init()

    await store.record_chat_language(100, "en")
    await store.record_chat_language(100, "es")
    await store.record_chat_language(100, "en")
    target = await store.get_target_language(100, default_lang="de", history_limit=8)
    assert target == "en"

    await store.record_chat_language(200, "fr")
    await store.record_chat_language(200, "de")
    target_tie = await store.get_target_language(200, default_lang="en", history_limit=8)
    assert target_tie == "de"

    await store.close()


@pytest.mark.asyncio
async def test_provider_stats(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init()

    await store.record_provider_result("nvidia", True)
    await store.record_provider_result("nvidia", False)
    await store.record_provider_result("kimi", True)
    stats = await store.get_provider_stats()

    assert stats["nvidia"]["success_count"] == 1
    assert stats["nvidia"]["failure_count"] == 1
    assert stats["kimi"]["success_count"] == 1
    assert stats["kimi"]["failure_count"] == 0

    await store.close()


@pytest.mark.asyncio
async def test_cleanup_lang_history_retention(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init()

    conn = store._conn
    assert conn is not None
    await conn.execute(
        """
        INSERT INTO lang_history(chat_id, lang, created_at)
        VALUES(1, 'en', datetime('now', '-30 hours'))
        """
    )
    await conn.execute(
        """
        INSERT INTO lang_history(chat_id, lang, created_at)
        VALUES(1, 'fr', datetime('now', '-1 hours'))
        """
    )
    await conn.commit()

    removed = await store.cleanup_lang_history(retention_hours=24)
    assert removed == 1

    target = await store.get_target_language(chat_id=1, default_lang="de", history_limit=8)
    assert target == "fr"
    await store.close()


@pytest.mark.asyncio
async def test_target_language_tie_prefers_most_recent(tmp_path) -> None:
    """When two languages have equal count, return whichever appeared more recently."""
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init()

    # en=2, fr=2, de=1. Most recent among tied: fr (inserted 4th vs en 3rd).
    await store.record_chat_language(300, "en")
    await store.record_chat_language(300, "fr")
    await store.record_chat_language(300, "en")
    await store.record_chat_language(300, "fr")
    await store.record_chat_language(300, "de")

    target = await store.get_target_language(300, default_lang="ja", history_limit=8)
    assert target == "fr"

    await store.close()


@pytest.mark.asyncio
async def test_target_language_filters_zh(tmp_path) -> None:
    """Ensure zh records are excluded from voting."""
    db_path = tmp_path / "state.db"
    store = StateStore(db_path)
    await store.init()

    await store.record_chat_language(400, "zh")
    await store.record_chat_language(400, "zh")
    await store.record_chat_language(400, "en")

    target = await store.get_target_language(400, default_lang="de", history_limit=8)
    assert target == "en"

    await store.close()
