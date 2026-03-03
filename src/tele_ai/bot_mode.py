from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass

from cachetools import TTLCache
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from tele_ai.config import Settings
from tele_ai.constants import LANG_AUTO, LANG_ZH
from tele_ai.formatter import (
    format_incoming,
    format_manual,
    format_outgoing,
    format_system,
    has_translation_prefix,
)
from tele_ai.lang_detect import LanguageDetector
from tele_ai.utils import CommandRateLimiter, split_text_by_limit
from tele_ai.state import StateStore
from tele_ai.translator.interface import Direction, TranslationRequest
from tele_ai.translator.kimi_provider import KimiProvider
from tele_ai.translator.nvidia_provider import NvidiaProvider
from tele_ai.translator.router import TranslationFailedError, TranslatorRouter


LOGGER = logging.getLogger(__name__)
LANG_CODE_PATTERN = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{2,8})?$", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3400-\u9FFF]")


@dataclass(slots=True)
class BotParsedMessage:
    chat_id: int
    message_id: int
    text: str


def looks_like_chinese_text(text: str, min_ratio: float = 0.2) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    cjk_count = len(CJK_RE.findall(candidate))
    return (cjk_count / max(len(candidate), 1)) >= min_ratio



class BotRuntime:
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

    async def post_init(self, _: Application) -> None:
        await self._state.init(global_pause_default=self._settings.global_pause)
        self._cleanup_task = asyncio.create_task(self._state_cleanup_loop())

    async def post_shutdown(self, _: Application) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        await self._router.close()
        await self._state.close()

    async def cmd_pause(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message or not update.effective_chat:
            return
        if not self._rate_limiter.check(update.effective_chat.id, "/ai_pause"):
            return
        await self._state.set_global_pause(True)
        await update.effective_message.reply_text(
            format_system(self._settings.translation_prefix, "Auto-translation paused.")
        )

    async def cmd_resume(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message or not update.effective_chat:
            return
        if not self._rate_limiter.check(update.effective_chat.id, "/ai_resume"):
            return
        await self._state.set_global_pause(False)
        await update.effective_message.reply_text(
            format_system(self._settings.translation_prefix, "Auto-translation resumed.")
        )

    async def cmd_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_message or not update.effective_chat:
            return
        if not self._rate_limiter.check(update.effective_chat.id, "/ai_status"):
            return
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
        text = (
            f"{self._settings.translation_prefix}[SYS]\n"
            f"status: {'paused' if paused else 'running'}\n"
            f"scope: private={'on' if self._settings.enable_private else 'off'}, "
            f"group={'on' if self._settings.enable_groups else 'off'}\n"
            + "\n".join(provider_lines)
        )
        await update.effective_message.reply_text(text)

    async def cmd_tr(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        if message is None or chat is None:
            return
        if not self._rate_limiter.check(chat.id, "/tr"):
            return
        if not self._chat_enabled(chat.type):
            return

        reply_to = message.reply_to_message
        if reply_to is None:
            await message.reply_text(
                format_system(
                    self._settings.translation_prefix,
                    "Usage: reply to a message, then send /tr or /tr <target_lang>.",
                )
            )
            return

        source_text = ((reply_to.text or reply_to.caption) or "").strip()
        if not source_text:
            await message.reply_text(
                format_system(
                    self._settings.translation_prefix,
                    "Replied message must contain text.",
                )
            )
            return
        if has_translation_prefix(source_text, self._settings.translation_prefix):
            return

        target_override = None
        if context.args:
            candidate = context.args[0].strip().lower()
            if not LANG_CODE_PATTERN.fullmatch(candidate):
                await message.reply_text(
                    format_system(
                        self._settings.translation_prefix,
                        "Invalid target language. Example: /tr en",
                    )
                )
                return
            target_override = candidate

        try:
            detection = self._detector.detect(source_text)
            source_lang = detection.lang_code if detection else LANG_AUTO
            if detection and detection.lang_code != LANG_ZH:
                await self._state.record_chat_language(chat.id, detection.lang_code)

            target_lang, direction = await self._resolve_manual_target(
                chat_id=chat.id,
                source_lang=source_lang,
                target_override=target_override,
            )

            translated = await self._translate(
                text=source_text,
                source_lang=source_lang if source_lang != "auto" else None,
                target_lang=target_lang,
                chat_id=chat.id,
                direction=direction,
            )
            response = format_manual(
                prefix=self._settings.translation_prefix,
                source_lang=source_lang,
                target_lang=target_lang,
                original=source_text,
                translated=translated,
            )
            await message.reply_text(response, reply_to_message_id=reply_to.message_id)
        except TranslationFailedError:
            await self._notify_failure(message)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            LOGGER.exception("Manual /tr handler failed: %s", exc)

    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if message is None or chat is None or user is None:
            return
        if user.is_bot:
            return
        text = (message.text or "").strip()
        if not text:
            return
        if has_translation_prefix(text, self._settings.translation_prefix):
            LOGGER.debug("Skipped (has translation prefix): %r", text[:50])
            return
        if text.startswith("/"):
            return
        if not self._chat_enabled(chat.type):
            LOGGER.debug("Skipped (chat type %s disabled)", chat.type)
            return
        if await self._state.is_global_paused():
            LOGGER.debug("Skipped (global pause)")
            return
        key = (chat.id, message.message_id)
        if key in self._processed:
            return
        self._processed[key] = True

        LOGGER.debug("Processing text from chat=%s: %r", chat.id, text[:80])

        try:
            detection = self._detector.detect(text)

            # Strong Chinese signal: translate to target language.
            if detection is not None and detection.lang_code == LANG_ZH:
                if detection.confidence >= self._settings.lang_confidence_threshold:
                    target_lang = await self._state.get_target_language(
                        chat_id=chat.id,
                        default_lang=self._settings.default_target_lang,
                        history_limit=self._settings.lang_history_limit,
                    )
                    if target_lang == LANG_ZH:
                        return
                    translated = await self._translate(
                        text=text,
                        source_lang=LANG_ZH,
                        target_lang=target_lang,
                        chat_id=chat.id,
                        direction="outgoing",
                    )
                    reply = format_outgoing(
                        prefix=self._settings.translation_prefix,
                        target_lang=target_lang,
                        original=text,
                        translated=translated,
                    )
                    await message.reply_text(reply, reply_to_message_id=message.message_id)
                    return

            # Heuristic fallback for short Chinese text.
            if looks_like_chinese_text(text):
                target_lang = await self._state.get_target_language(
                    chat_id=chat.id,
                    default_lang=self._settings.default_target_lang,
                    history_limit=self._settings.lang_history_limit,
                )
                if target_lang == LANG_ZH:
                    return
                translated = await self._translate(
                    text=text,
                    source_lang=LANG_ZH,
                    target_lang=target_lang,
                    chat_id=chat.id,
                    direction="outgoing",
                )
                reply = format_outgoing(
                    prefix=self._settings.translation_prefix,
                    target_lang=target_lang,
                    original=text,
                    translated=translated,
                )
            else:
                # Non-Chinese path: if language confidence is low, still translate with auto source.
                source_lang: str | None = None
                if (
                    detection is not None
                    and detection.lang_code != LANG_ZH
                    and detection.confidence >= self._settings.lang_confidence_threshold
                ):
                    source_lang = detection.lang_code
                    await self._state.record_chat_language(chat.id, detection.lang_code)
                else:
                    LOGGER.debug(
                        "Low/unknown confidence fallback in bot mode: detection=%s threshold=%.2f",
                        detection,
                        self._settings.lang_confidence_threshold,
                    )
                translated = await self._translate(
                    text=text,
                    source_lang=source_lang,
                    target_lang=LANG_ZH,
                    chat_id=chat.id,
                    direction="incoming",
                )
                reply = format_incoming(
                    prefix=self._settings.translation_prefix,
                    source_lang=source_lang or LANG_AUTO,
                    original=text,
                    translated=translated,
                )
            await message.reply_text(reply, reply_to_message_id=message.message_id)
        except TranslationFailedError:
            await self._notify_failure(message)
        except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
            LOGGER.exception("Bot message handler failed: %s", exc)

    async def _translate(
        self,
        text: str,
        source_lang: str | None,
        target_lang: str,
        chat_id: int,
        direction: Direction,
    ) -> str:
        chunks = split_text_by_limit(text, self._settings.max_text_chars)
        translated: list[str] = []
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
            translated.append(result.translated_text.strip())
        return "\n".join(part for part in translated if part)

    async def _resolve_manual_target(
        self, chat_id: int, source_lang: str, target_override: str | None
    ) -> tuple[str, Direction]:
        if target_override:
            direction: Direction = "incoming" if target_override == LANG_ZH else "outgoing"
            return target_override, direction
        if source_lang != LANG_ZH:
            return LANG_ZH, "incoming"
        target_lang = await self._state.get_target_language(
            chat_id=chat_id,
            default_lang=self._settings.default_target_lang,
            history_limit=self._settings.lang_history_limit,
        )
        if target_lang == LANG_ZH:
            target_lang = self._settings.default_target_lang
        return target_lang, "outgoing"

    async def _notify_failure(self, message) -> None:
        chat_id = message.chat_id
        if chat_id in self._failure_notice:
            return
        self._failure_notice[chat_id] = True
        await message.reply_text(
            format_system(self._settings.translation_prefix, "Translation failed temporarily. Try again later.")
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

    def _chat_enabled(self, chat_type: str) -> bool:
        if chat_type == "private":
            return self._settings.enable_private
        if chat_type in {"group", "supergroup"}:
            return self._settings.enable_groups
        return False


def _build_bot_app(settings: Settings) -> tuple[Application, BotRuntime]:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required for bot mode.")

    runtime = BotRuntime(settings)
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .connection_pool_size(16)
        .pool_timeout(20.0)
        .post_init(runtime.post_init)
        .post_shutdown(runtime.post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("ai_pause", runtime.cmd_pause))
    app.add_handler(CommandHandler("ai_resume", runtime.cmd_resume))
    app.add_handler(CommandHandler("ai_status", runtime.cmd_status))
    app.add_handler(CommandHandler("tr", runtime.cmd_tr))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), runtime.on_text))
    return app, runtime


def run_bot_mode(settings: Settings) -> None:
    app, _ = _build_bot_app(settings)
    app.run_polling(drop_pending_updates=True)


async def run_bot_async(settings: Settings) -> tuple[Application, BotRuntime]:
    """Start bot mode for the `both` runtime and return handles for shutdown."""
    app, runtime = _build_bot_app(settings)
    await app.initialize()
    await runtime.post_init(app)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    LOGGER.info("Bot mode started (async, alongside userbot).")
    return app, runtime


async def stop_bot_async(app: Application, runtime: BotRuntime) -> None:
    """Stop bot mode started by `run_bot_async`."""
    if app.updater and app.updater.running:
        await app.updater.stop()
    if app.running:
        await app.stop()
    await runtime.post_shutdown(app)
    await app.shutdown()
