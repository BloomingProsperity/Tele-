import pytest

from tele_ai.translator.interface import TranslationRequest, TranslationResult
from tele_ai.translator.router import TranslationFailedError, TranslatorRouter


class FakeProvider:
    def __init__(self, name: str, should_fail: bool, translated: str = "ok") -> None:
        self.name = name
        self._should_fail = should_fail
        self._translated = translated
        self.closed = False

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        if self._should_fail:
            raise RuntimeError(f"{self.name} failed")
        return TranslationResult(
            translated_text=self._translated,
            provider=self.name,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            latency_ms=10,
        )

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_router_fallback_success() -> None:
    events: list[tuple[str, bool]] = []
    primary = FakeProvider(name="nvidia", should_fail=True)
    fallback = FakeProvider(name="kimi", should_fail=False, translated="你好")
    router = TranslatorRouter(
        primary=primary,
        fallback=fallback,
        on_provider_result=lambda provider, ok: events.append((provider, ok)),
    )

    result = await router.translate_with_fallback(
        TranslationRequest(
            text="hello",
            source_lang="en",
            target_lang="zh",
            chat_id=1,
            direction="incoming",
        )
    )

    assert result.translated_text == "你好"
    assert events == [("nvidia", False), ("kimi", True)]


@pytest.mark.asyncio
async def test_router_both_failed() -> None:
    primary = FakeProvider(name="nvidia", should_fail=True)
    fallback = FakeProvider(name="kimi", should_fail=True)
    router = TranslatorRouter(primary=primary, fallback=fallback)

    with pytest.raises(TranslationFailedError):
        await router.translate_with_fallback(
            TranslationRequest(
                text="hello",
                source_lang="en",
                target_lang="zh",
                chat_id=1,
                direction="incoming",
            )
        )
