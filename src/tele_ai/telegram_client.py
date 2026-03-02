from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from telethon import TelegramClient, events
from telethon.events.newmessage import NewMessage
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from tele_ai.config import Settings

EventHandler = Callable[[NewMessage.Event], Awaitable[None]]


class TelegramGateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = TelegramClient(
            session=settings.tg_session_name,
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash,
        )
        self._logger = logging.getLogger(__name__)
        self._self_id: int | None = None
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._last_send_by_chat: dict[int, float] = {}

    async def start(self) -> None:
        phone = self._settings.tg_phone
        await self._client.start(phone=phone if phone else lambda: input("Please enter your phone: "))
        me = await self._client.get_me()
        if me is None:
            raise RuntimeError("Failed to load Telegram account profile.")
        self._self_id = me.id
        self._logger.info("Telegram authenticated as user_id=%s", self._self_id)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def run_until_disconnected(self) -> None:
        await self._client.run_until_disconnected()

    @property
    def self_id(self) -> int:
        if self._self_id is None:
            raise RuntimeError("TelegramGateway.start() must be called first.")
        return self._self_id

    def add_incoming_handler(self, handler: EventHandler) -> None:
        @self._client.on(events.NewMessage(incoming=True))
        async def _wrapped(event: NewMessage.Event) -> None:
            await handler(event)

    def add_outgoing_handler(self, handler: EventHandler) -> None:
        @self._client.on(events.NewMessage(outgoing=True))
        async def _wrapped(event: NewMessage.Event) -> None:
            await handler(event)

    async def send_message(self, chat_id: int, text: str, reply_to: int | None = None) -> int:
        """Send a message and return its message ID."""
        lock = self._send_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_send_by_chat.get(chat_id, 0.0)
            gap = self._settings.send_interval_seconds - (now - last)
            if gap > 0:
                await asyncio.sleep(gap)
            msg = await self._send_entity_message_with_retry(
                entity=chat_id, text=text, reply_to=reply_to
            )
            self._last_send_by_chat[chat_id] = time.monotonic()
            return msg.id

    async def send_saved_message(self, text: str) -> int:
        """Send a message to 'Saved Messages' and return its message ID."""
        key = -1
        lock = self._send_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_send_by_chat.get(key, 0.0)
            gap = self._settings.send_interval_seconds - (now - last)
            if gap > 0:
                await asyncio.sleep(gap)
            msg = await self._send_entity_message_with_retry(entity="me", text=text, reply_to=None)
            self._last_send_by_chat[key] = time.monotonic()
            return msg.id

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Edit an existing message in place."""
        await self._edit_message_with_retry(chat_id=chat_id, message_id=message_id, text=text)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((TimeoutError, OSError)),
    )
    async def _send_entity_message_with_retry(
        self, entity, text: str, reply_to: int | None
    ):
        return await self._client.send_message(entity=entity, message=text, reply_to=reply_to)

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        retry=retry_if_exception_type((TimeoutError, OSError)),
    )
    async def _edit_message_with_retry(
        self, chat_id: int, message_id: int, text: str
    ):
        return await self._client.edit_message(entity=chat_id, message=message_id, text=text)
