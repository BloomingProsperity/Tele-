from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable

from tele_ai.translator.interface import (
    TranslationRequest,
    TranslationResult,
    TranslatorProvider,
)

ProviderResultCallback = Callable[[str, bool], Awaitable[None] | None]


class TranslationFailedError(RuntimeError):
    def __init__(self, primary_error: Exception, fallback_error: Exception) -> None:
        super().__init__("Both translation providers failed.")
        self.primary_error = primary_error
        self.fallback_error = fallback_error


class TranslatorRouter:
    def __init__(
        self,
        primary: TranslatorProvider,
        fallback: TranslatorProvider,
        on_provider_result: ProviderResultCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._on_provider_result = on_provider_result
        self._logger = logger or logging.getLogger(__name__)

    async def translate_with_fallback(self, request: TranslationRequest) -> TranslationResult:
        try:
            result = await self._primary.translate(request)
            await self._notify(self._primary.name, True)
            return result
        # Intentionally broad: any provider failure should trigger fallback.
        except Exception as primary_error:  # noqa: BLE001
            await self._notify(self._primary.name, False)
            self._logger.warning(
                "Primary provider '%s' failed for chat_id=%s, direction=%s: %s",
                self._primary.name,
                request.chat_id,
                request.direction,
                primary_error,
            )
            try:
                result = await self._fallback.translate(request)
                await self._notify(self._fallback.name, True)
                return result
            # Intentionally broad: capture all fallback failures for reporting.
            except Exception as fallback_error:  # noqa: BLE001
                await self._notify(self._fallback.name, False)
                self._logger.error(
                    "Fallback provider '%s' also failed for chat_id=%s, direction=%s: %s",
                    self._fallback.name,
                    request.chat_id,
                    request.direction,
                    fallback_error,
                )
                raise TranslationFailedError(primary_error, fallback_error) from fallback_error

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()

    async def _notify(self, provider: str, success: bool) -> None:
        if self._on_provider_result is None:
            return
        maybe_coro = self._on_provider_result(provider, success)
        if inspect.isawaitable(maybe_coro):
            await maybe_coro

