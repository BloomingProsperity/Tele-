from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache
from telethon.events.newmessage import NewMessage

from tele_ai.bot_mode import run_bot_async, run_bot_mode, stop_bot_async
from tele_ai.config import Settings, load_settings
from tele_ai.formatter import (
    format_incoming,
    format_outgoing,
    format_system,
    has_translation_prefix,
    is_command,
)
from tele_ai.constants import CMD_AI_PAUSE, CMD_AI_RESUME, CMD_AI_STATUS, LANG_ZH
from tele_ai.lang_detect import LanguageDetector
from tele_ai.utils import CommandRateLimiter, split_text_by_limit
from tele_ai.state import StateStore
from tele_ai.telegram_client import TelegramGateway
from tele_ai.translator.interface import Direction, TranslationRequest
from tele_ai.translator.kimi_provider import KimiProvider
from tele_ai.translator.nvidia_provider import NvidiaProvider
from tele_ai.translator.router import TranslationFailedError, TranslatorRouter


LOGGER = logging.getLogger(__name__)
CJK_RE = re.compile(r"[\u3400-\u9FFF]")


@dataclass(slots=True)
class ParsedMessage:
    chat_id: int
    message_id: int
    text: str


def looks_like_chinese_text(text: str, min_ratio: float = 0.2) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    cjk_count = len(CJK_RE.findall(candidate))
    return (cjk_count / max(len(candidate), 1)) >= min_ratio



class TeleAIService:
    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        language_detector: LanguageDetector,
        translator_router: TranslatorRouter,
        gateway: TelegramGateway,
    ) -> None:
        self._settings = settings
        self._state = state
        self._language_detector = language_detector
        self._translator_router = translator_router
        self._gateway = gateway
        self._processed = TTLCache(
            maxsize=settings.processed_cache_maxsize,
            ttl=settings.processed_cache_ttl_seconds,
        )
        self._failure_notice = TTLCache(
            maxsize=settings.failure_notice_maxsize,
            ttl=settings.failure_notice_ttl_seconds,
        )
        self._self_user_id: int | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._rate_limiter = CommandRateLimiter(settings.command_cooldown_seconds)

    async def run(self) -> None:
        await self._state.init(global_pause_default=self._settings.global_pause)
        self._cleanup_task = asyncio.create_task(self._state_cleanup_loop())
        self._gateway.add_incoming_handler(self.handle_incoming_message)
        self._gateway.add_outgoing_handler(self.handle_outgoing_message)
        await self._gateway.start()
        self._self_user_id = self._gateway.self_id
        LOGGER.info("Tele AI service started.")
        await self._gateway.run_until_disconnected()

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        await self._translator_router.close()
        await self._state.close()
        await self._gateway.disconnect()

    async def handle_incoming_message(self, event: NewMessage.Event) -> None:
        try:
            parsed = self._parse_event(event)
            if parsed is None:
                return
            LOGGER.debug("[IN] chat=%s text=%r", event.chat_id, (parsed.text[:60] if parsed else ""))
            if not self._is_chat_scope_enabled(event):
                LOGGER.debug("[IN] Skipped: chat scope disabled")
                return
            if await self._state.is_global_paused():
                LOGGER.debug("[IN] Skipped: global pause")
                return
            if not self._mark_processed(("incoming", parsed.chat_id, parsed.message_id)):
                LOGGER.debug("[IN] Skipped: already processed")
                return
            if has_translation_prefix(parsed.text, self._settings.translation_prefix):
                LOGGER.debug("[IN] Skipped: has translation prefix")
                return
            if is_command(parsed.text):
                LOGGER.debug("[IN] Skipped: is command")
                return
            if self._self_user_id is not None and event.sender_id == self._self_user_id:
                LOGGER.debug("[IN] Skipped: own message")
                return

            detection = self._language_detector.detect(parsed.text)
            if detection is not None:
                LOGGER.debug("[IN] Detected: lang=%s confidence=%.3f", detection.lang_code, detection.confidence)

            # Strong Chinese signal: skip incoming translation.
            if detection is not None and detection.lang_code == LANG_ZH:
                if detection.confidence >= self._settings.lang_confidence_threshold:
                    return
            if looks_like_chinese_text(parsed.text):
                return

            source_lang: str | None = None
            if (
                detection is not None
                and detection.lang_code != LANG_ZH
                and detection.confidence >= self._settings.lang_confidence_threshold
            ):
                source_lang = detection.lang_code
                await self._state.record_chat_language(parsed.chat_id, detection.lang_code)
            else:
                LOGGER.debug(
                    "[IN] Low/unknown confidence fallback: use provider auto source detect for chat_id=%s",
                    parsed.chat_id,
                )

            translated_text = await self._translate_message(
                text=parsed.text,
                source_lang=source_lang,
                target_lang=LANG_ZH,
                chat_id=parsed.chat_id,
                direction="incoming",
            )
            await self._deliver_incoming_translation(parsed=parsed, translated_text=translated_text)
        except TranslationFailedError:
            await self._notify_translation_failure(event)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            LOGGER.exception("Unexpected incoming handler error: %s", exc)

    async def handle_outgoing_message(self, event: NewMessage.Event) -> None:
        try:
            parsed = self._parse_event(event)
            if parsed is None:
                return
            LOGGER.debug("[OUT] chat=%s text=%r", event.chat_id, (parsed.text[:60] if parsed else ""))
            if not self._is_chat_scope_enabled(event):
                LOGGER.debug("[OUT] Skipped: chat scope disabled")
                return
            if not self._mark_processed(("outgoing", parsed.chat_id, parsed.message_id)):
                LOGGER.debug("[OUT] Skipped: already processed")
                return
            if has_translation_prefix(parsed.text, self._settings.translation_prefix):
                LOGGER.debug("[OUT] Skipped: has translation prefix")
                return
            if is_command(parsed.text):
                await self._handle_command(parsed, parsed.text)
                return
            if await self._state.is_global_paused():
                LOGGER.debug("[OUT] Skipped: global pause")
                return

            detection = self._language_detector.detect(parsed.text)
            if detection is not None:
                LOGGER.debug("[OUT] Detected: lang=%s confidence=%.3f", detection.lang_code, detection.confidence)
            else:
                LOGGER.debug("[OUT] Detection is None")

            is_chinese_outgoing = (
                detection is not None
                and detection.lang_code == LANG_ZH
                and detection.confidence >= self._settings.lang_confidence_threshold
            )
            if not is_chinese_outgoing and looks_like_chinese_text(parsed.text):
                LOGGER.debug("[OUT] Chinese heuristic matched with low/unknown confidence.")
                is_chinese_outgoing = True

            if not is_chinese_outgoing:
                # Non-Chinese outgoing: skip
                return

            # Chinese -> target language: edit original message in place
            target_lang = await self._state.get_target_language(
                chat_id=parsed.chat_id,
                default_lang=self._settings.default_target_lang,
                history_limit=self._settings.lang_history_limit,
            )
            if target_lang == LANG_ZH:
                return
            translated_text = await self._translate_message(
                text=parsed.text,
                source_lang=LANG_ZH,
                target_lang=target_lang,
                chat_id=parsed.chat_id,
                direction="outgoing",
            )
            await self._gateway.edit_message(
                chat_id=parsed.chat_id,
                message_id=parsed.message_id,
                text=translated_text,
            )
        except TranslationFailedError:
            await self._notify_translation_failure(event)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            LOGGER.exception("Unexpected outgoing handler error: %s", exc)

    def _parse_event(self, event: NewMessage.Event) -> ParsedMessage | None:
        message = event.message
        if message is None:
            return None
        if message.media is not None:
            return None
        if event.chat_id is None:
            return None
        text = (message.message or "").strip()
        if not text:
            return None
        return ParsedMessage(chat_id=event.chat_id, message_id=message.id, text=text)

    def _is_chat_scope_enabled(self, event: NewMessage.Event) -> bool:
        if event.is_private:
            return self._settings.enable_private
        if event.is_group:
            return self._settings.enable_groups
        return False

    def _mark_processed(self, key: tuple[str, int, int]) -> bool:
        if key in self._processed:
            return False
        self._processed[key] = True
        return True

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
            result = await self._translator_router.translate_with_fallback(request)
            translated_parts.append(result.translated_text.strip())
        return "\n".join(part for part in translated_parts if part)

    async def _notify_translation_failure(self, event: NewMessage.Event) -> None:
        if event.chat_id is None or event.message is None:
            return
        if event.chat_id in self._failure_notice:
            return
        self._failure_notice[event.chat_id] = True
        notice = format_system(self._settings.translation_prefix, "Translation failed temporarily. Try again later.")
        if (not event.out) and self._settings.incoming_translation_output_mode == "saved_messages":
            await self._gateway.send_saved_message(
                f"{self._settings.translation_prefix}[ERR][chat:{event.chat_id}][msg:{event.message.id}]\n{notice}"
            )
            return
        await self._gateway.send_message(chat_id=event.chat_id, text=notice, reply_to=event.message.id)

    async def _handle_command(self, parsed: ParsedMessage, text: str) -> None:
        command = text.split()[0].lower()
        if not self._rate_limiter.check(parsed.chat_id, command):
            LOGGER.debug("Rate-limited command %s in chat %s", command, parsed.chat_id)
            return
        if command == CMD_AI_PAUSE:
            await self._state.set_global_pause(True)
            await self._gateway.send_message(
                chat_id=parsed.chat_id,
                text=format_system(self._settings.translation_prefix, "Auto-translation paused."),
                reply_to=parsed.message_id,
            )
            return

        if command == CMD_AI_RESUME:
            await self._state.set_global_pause(False)
            await self._gateway.send_message(
                chat_id=parsed.chat_id,
                text=format_system(self._settings.translation_prefix, "Auto-translation resumed."),
                reply_to=parsed.message_id,
            )
            return

        if command == CMD_AI_STATUS:
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
                f"scope: private={'on' if self._settings.enable_private else 'off'}, "
                f"group={'on' if self._settings.enable_groups else 'off'}\n"
                + "\n".join(provider_lines)
            )
            await self._gateway.send_message(
                chat_id=parsed.chat_id,
                text=status_text,
                reply_to=parsed.message_id,
            )

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

    async def _deliver_incoming_translation(self, parsed: ParsedMessage, translated_text: str) -> None:
        mode = self._settings.incoming_translation_output_mode
        if mode == "off":
            return
        if mode == "saved_messages":
            payload = (
                f"{self._settings.translation_prefix}[IN][chat:{parsed.chat_id}][msg:{parsed.message_id}]\n"
                f"{translated_text}"
            )
            await self._gateway.send_saved_message(payload)
            return

        sent_id = await self._gateway.send_message(
            chat_id=parsed.chat_id,
            text=translated_text,
            reply_to=parsed.message_id,
        )
        self._mark_processed(("outgoing", parsed.chat_id, sent_id))


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def run_userbot(settings: Settings) -> None:
    state = StateStore(settings.state_db_path)
    detector = LanguageDetector()

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

    router = TranslatorRouter(
        primary=primary,
        fallback=fallback,
        on_provider_result=state.record_provider_result,
    )
    gateway = TelegramGateway(settings)
    service = TeleAIService(
        settings=settings,
        state=state,
        language_detector=detector,
        translator_router=router,
        gateway=gateway,
    )

    try:
        await service.run()
    finally:
        await service.close()


async def run_both(settings: Settings) -> None:
    """Run bot mode and userbot mode concurrently."""
    LOGGER.info("Starting both bot and userbot modes.")
    bot_app, bot_runtime = await run_bot_async(settings)

    state = StateStore(settings.state_db_path)
    detector = LanguageDetector()

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

    router = TranslatorRouter(
        primary=primary,
        fallback=fallback,
        on_provider_result=state.record_provider_result,
    )
    gateway = TelegramGateway(settings)
    service = TeleAIService(
        settings=settings,
        state=state,
        language_detector=detector,
        translator_router=router,
        gateway=gateway,
    )

    try:
        await service.run()
    finally:
        await service.close()
        await stop_bot_async(bot_app, bot_runtime)


def main() -> Any:
    settings = load_settings()
    configure_logging(settings.log_level)
    try:
        if settings.run_mode == "bot":
            run_bot_mode(settings)
            return None
        if settings.run_mode == "both":
            return asyncio.run(run_both(settings))
        return asyncio.run(run_userbot(settings))
    except KeyboardInterrupt:
        LOGGER.info("Received KeyboardInterrupt, shutdown complete.")
        return None


if __name__ == "__main__":
    main()

