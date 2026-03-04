from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Iterable
from typing import Any

import discord
from cachetools import TTLCache

from discord_ai.config import Settings, load_settings
from tele_ai.constants import LANG_AUTO, LANG_ZH
from tele_ai.formatter import format_system, has_translation_prefix
from tele_ai.lang_detect import LanguageDetector
from tele_ai.state import StateStore
from tele_ai.translator.interface import Direction, TranslationRequest
from tele_ai.translator.kimi_provider import KimiProvider
from tele_ai.translator.nvidia_provider import NvidiaProvider
from tele_ai.translator.router import TranslationFailedError, TranslatorRouter
from tele_ai.utils import CommandRateLimiter, split_text_by_limit


LOGGER = logging.getLogger(__name__)
CJK_RE = re.compile(r"[\u3400-\u9FFF]")
LANG_CODE_PATTERN = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{2,8})?$", re.IGNORECASE)


def looks_like_chinese_text(text: str, min_ratio: float = 0.2) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    cjk_count = len(CJK_RE.findall(candidate))
    return (cjk_count / max(len(candidate), 1)) >= min_ratio


class DiscordAutoTranslator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._state = StateStore(settings.state_db_path)
        self._detector = LanguageDetector()
        self._processed = TTLCache(
            maxsize=settings.processed_cache_maxsize,
            ttl=settings.processed_cache_ttl_seconds,
        )
        self._failure_notice = TTLCache(
            maxsize=settings.failure_notice_maxsize,
            ttl=settings.failure_notice_ttl_seconds,
        )
        self._cleanup_task: asyncio.Task[None] | None = None
        self._rate_limiter = CommandRateLimiter(settings.command_cooldown_seconds)
        self._send_locks: dict[int, asyncio.Lock] = {}
        self._last_send_by_channel: dict[int, float] = {}

        primary = NvidiaProvider(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_model,
            name="nvidia_primary",
        )
        if settings.kimi_api_key and settings.kimi_base_url and settings.kimi_model:
            fallback = KimiProvider(
                api_key=settings.kimi_api_key,
                base_url=settings.kimi_base_url,
                model=settings.kimi_model,
                name="kimi_fallback",
            )
        else:
            fallback = NvidiaProvider(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_base_url,
                model=settings.nvidia_fallback_model,
                name="nvidia_fallback",
            )
        self._router = TranslatorRouter(
            primary=primary,
            fallback=fallback,
            on_provider_result=self._state.record_provider_result,
        )

        intents = discord.Intents.default()
        intents.guilds = True
        intents.messages = True
        intents.message_content = True
        intents.dm_messages = True
        self._client = discord.Client(intents=intents)
        self._register_events()

    def _register_events(self) -> None:
        @self._client.event
        async def on_ready() -> None:
            user_id = self._client.user.id if self._client.user else "unknown"
            LOGGER.info(
                "Discord bot authenticated as user_id=%s, guilds=%s",
                user_id,
                len(self._client.guilds),
            )

        @self._client.event
        async def on_message(message: discord.Message) -> None:
            await self._handle_message(message)

    async def run(self) -> None:
        await self._state.init(global_pause_default=self._settings.global_pause)
        self._cleanup_task = asyncio.create_task(self._state_cleanup_loop())
        try:
            await self._client.start(self._settings.discord_bot_token)
        finally:
            await self.close()

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None

        await self._router.close()
        await self._state.close()
        if not self._client.is_closed():
            await self._client.close()

    async def _handle_message(self, message: discord.Message) -> None:
        try:
            if self._client.user is not None and message.author.id == self._client.user.id:
                return
            if message.author.bot:
                return
            if message.type not in {discord.MessageType.default, discord.MessageType.reply}:
                return
            if not self._is_scope_enabled(message):
                return

            text = (message.content or "").strip()
            if not text:
                return

            cache_key = (message.channel.id, message.id)
            if cache_key in self._processed:
                return
            self._processed[cache_key] = True

            if text.startswith(self._settings.discord_command_prefix):
                await self._handle_command(message, text)
                return

            if await self._state.is_global_paused():
                return
            if has_translation_prefix(text, self._settings.translation_prefix):
                return

            detection = self._detector.detect(text)
            is_chinese_outgoing = (
                detection is not None
                and detection.lang_code == LANG_ZH
                and detection.confidence >= self._settings.lang_confidence_threshold
            )
            if not is_chinese_outgoing and looks_like_chinese_text(text):
                is_chinese_outgoing = True

            if is_chinese_outgoing:
                target_lang = await self._state.get_target_language(
                    chat_id=message.channel.id,
                    default_lang=self._settings.default_target_lang,
                    history_limit=self._settings.lang_history_limit,
                )
                if target_lang == LANG_ZH:
                    return
                translated_text = await self._translate_message(
                    text=text,
                    source_lang=LANG_ZH,
                    target_lang=target_lang,
                    chat_id=message.channel.id,
                    direction="outgoing",
                )
                await self._send_translation_reply(message, translated_text)
                return

            source_lang: str | None = None
            if (
                detection is not None
                and detection.lang_code != LANG_ZH
                and detection.confidence >= self._settings.lang_confidence_threshold
            ):
                source_lang = detection.lang_code
                await self._state.record_chat_language(message.channel.id, detection.lang_code)

            translated_text = await self._translate_message(
                text=text,
                source_lang=source_lang,
                target_lang=LANG_ZH,
                chat_id=message.channel.id,
                direction="incoming",
            )
            await self._send_translation_reply(message, translated_text)
        except TranslationFailedError:
            await self._notify_translation_failure(message.channel.id, message)
        except (discord.DiscordException, OSError, RuntimeError, ValueError, TimeoutError) as exc:
            LOGGER.exception("Unexpected Discord message handler error: %s", exc)

    async def _handle_command(self, message: discord.Message, text: str) -> None:
        command_line = text[len(self._settings.discord_command_prefix) :].strip()
        if not command_line:
            return

        if self._settings.discord_owner_id and message.author.id != self._settings.discord_owner_id:
            return

        parts = command_line.split(maxsplit=1)
        command = parts[0].lower()
        payload = parts[1].strip() if len(parts) > 1 else ""

        if not self._rate_limiter.check(message.channel.id, command):
            return

        if command == "ai_pause":
            await self._state.set_global_pause(True)
            await self._send_text(
                message.channel.id,
                format_system(self._settings.translation_prefix, "Auto-translation paused."),
                reply_to=message,
            )
            return

        if command == "ai_resume":
            await self._state.set_global_pause(False)
            await self._send_text(
                message.channel.id,
                format_system(self._settings.translation_prefix, "Auto-translation resumed."),
                reply_to=message,
            )
            return

        if command == "ai_status":
            paused = await self._state.is_global_paused()
            stats = await self._state.get_provider_stats()
            provider_lines = []
            for provider in sorted(stats.keys()):
                row = stats[provider]
                provider_lines.append(
                    f"{provider} success/failure: {row['success_count']}/{row['failure_count']}"
                )
            if not provider_lines:
                provider_lines.append("No provider stats yet.")

            status_text = (
                f"{self._settings.translation_prefix}[SYS]\n"
                f"status: {'paused' if paused else 'running'}\n"
                f"scope: guilds={'on' if self._settings.discord_enable_guilds else 'off'}, "
                f"dms={'on' if self._settings.discord_enable_dms else 'off'}\n"
                + "\n".join(provider_lines)
            )
            await self._send_text(message.channel.id, status_text, reply_to=message)
            return

        if command == "tr":
            await self._handle_manual_translate(message, payload)
            return

    async def _handle_manual_translate(self, message: discord.Message, payload: str) -> None:
        source_text = ""
        target_override: str | None = None

        if payload:
            split = payload.split(maxsplit=1)
            maybe_lang = split[0].strip().lower()
            if LANG_CODE_PATTERN.fullmatch(maybe_lang):
                target_override = maybe_lang
                if len(split) > 1:
                    source_text = split[1].strip()
            else:
                source_text = payload

        if not source_text:
            source_text = await self._get_referenced_text(message)

        if not source_text:
            await self._send_text(
                message.channel.id,
                format_system(
                    self._settings.translation_prefix,
                    "Usage: reply to a message and send !tr, or use !tr <target_lang> <text>.",
                ),
                reply_to=message,
            )
            return

        detection = self._detector.detect(source_text)
        source_lang = detection.lang_code if detection else LANG_AUTO
        if detection and detection.lang_code != LANG_ZH:
            await self._state.record_chat_language(message.channel.id, detection.lang_code)

        target_lang, direction = await self._resolve_manual_target(
            channel_id=message.channel.id,
            source_lang=source_lang,
            target_override=target_override,
        )
        translated = await self._translate_message(
            text=source_text,
            source_lang=source_lang if source_lang != LANG_AUTO else None,
            target_lang=target_lang,
            chat_id=message.channel.id,
            direction=direction,
        )
        await self._send_text(message.channel.id, translated, reply_to=message)

    async def _resolve_manual_target(
        self, channel_id: int, source_lang: str, target_override: str | None
    ) -> tuple[str, Direction]:
        if target_override:
            direction: Direction = "incoming" if target_override == LANG_ZH else "outgoing"
            return target_override, direction
        if source_lang != LANG_ZH:
            return LANG_ZH, "incoming"
        target_lang = await self._state.get_target_language(
            chat_id=channel_id,
            default_lang=self._settings.default_target_lang,
            history_limit=self._settings.lang_history_limit,
        )
        if target_lang == LANG_ZH:
            target_lang = self._settings.default_target_lang
        return target_lang, "outgoing"

    async def _translate_message(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        chat_id: int,
        direction: Direction,
    ) -> str:
        chunks = split_text_by_limit(text, self._settings.max_text_chars)
        if not chunks:
            return ""

        translated_parts: list[str] = []
        for index, chunk in enumerate(chunks):
            request = TranslationRequest(
                text=chunk,
                source_lang=source_lang,
                target_lang=target_lang,
                chat_id=chat_id,
                direction=direction,
                context_hint=f"chunk={index + 1}/{len(chunks)}",
            )
            result = await self._router.translate_with_fallback(request)
            translated_parts.append(result.translated_text.strip())
        return "\n".join(part for part in translated_parts if part)

    async def _send_translation_reply(self, message: discord.Message, text: str) -> None:
        await self._send_text(message.channel.id, text, reply_to=message)

    async def _send_text(
        self,
        channel_id: int,
        text: str,
        reply_to: discord.Message | None = None,
    ) -> None:
        channel = self._client.get_channel(channel_id)
        if channel is None:
            fetched = await self._client.fetch_channel(channel_id)
            channel = fetched

        if not isinstance(channel, discord.abc.Messageable):
            return

        chunks = self._split_for_discord(text)
        for idx, chunk in enumerate(chunks):
            lock = self._send_locks.setdefault(channel_id, asyncio.Lock())
            async with lock:
                now = time.monotonic()
                last = self._last_send_by_channel.get(channel_id, 0.0)
                gap = self._settings.send_interval_seconds - (now - last)
                if gap > 0:
                    await asyncio.sleep(gap)

                if reply_to is not None and idx == 0:
                    await reply_to.reply(chunk, mention_author=False)
                else:
                    await channel.send(chunk)
                self._last_send_by_channel[channel_id] = time.monotonic()

    def _split_for_discord(self, text: str) -> list[str]:
        chunks = split_text_by_limit(text, 1900)
        if chunks:
            return chunks
        return [text[:1900]] if text else [""]

    async def _notify_translation_failure(
        self,
        channel_id: int,
        message: discord.Message | None = None,
    ) -> None:
        if channel_id in self._failure_notice:
            return
        self._failure_notice[channel_id] = True
        notice = format_system(self._settings.translation_prefix, "Translation failed temporarily. Try again later.")
        await self._send_text(channel_id, notice, reply_to=message)

    async def _get_referenced_text(self, message: discord.Message) -> str:
        if message.reference is None:
            return ""
        ref = message.reference.resolved
        if isinstance(ref, discord.Message):
            return (ref.content or "").strip()
        if message.reference.message_id is None:
            return ""
        with contextlib.suppress(discord.DiscordException):
            fetched = await message.channel.fetch_message(message.reference.message_id)
            return (fetched.content or "").strip()
        return ""

    def _is_scope_enabled(self, message: discord.Message) -> bool:
        if message.guild is None:
            return self._settings.discord_enable_dms
        return self._settings.discord_enable_guilds

    async def _state_cleanup_loop(self) -> None:
        interval = self._settings.state_cleanup_interval_minutes * 60
        while True:
            try:
                removed = await self._state.cleanup_lang_history(
                    retention_hours=self._settings.lang_history_retention_hours
                )
                if removed > 0:
                    LOGGER.info("State cleanup removed %s lang history rows.", removed)
            except asyncio.CancelledError:
                raise
            except (OSError, RuntimeError) as exc:
                LOGGER.warning("State cleanup failed: %s", exc)
            await asyncio.sleep(interval)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> Any:
    settings = load_settings()
    configure_logging(settings.log_level)
    service = DiscordAutoTranslator(settings)
    try:
        return asyncio.run(service.run())
    except KeyboardInterrupt:
        LOGGER.info("Received KeyboardInterrupt, shutdown complete.")
        return None


if __name__ == "__main__":
    main()

