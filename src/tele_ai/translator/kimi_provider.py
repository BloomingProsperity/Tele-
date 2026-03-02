from __future__ import annotations

import time

from openai import AsyncOpenAI

from tele_ai.constants import LANG_AUTO
from tele_ai.translator import TRANSLATION_SYSTEM_PROMPT
from tele_ai.translator.interface import TranslationRequest, TranslationResult
from tele_ai.utils import sanitize_user_text


class KimiProvider:
    def __init__(self, api_key: str, base_url: str, model: str, name: str = "kimi") -> None:
        self.name = name
        self._model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=20.0)

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        source = request.source_lang or LANG_AUTO
        user_prompt = (
            f"Source language: {source}\n"
            f"Target language: {request.target_lang}\n"
            "Output only translated text. No explanation.\n"
            "Text:\n"
            f"{sanitize_user_text(request.text)}"
        )

        started = time.perf_counter()
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not response.choices:
            raise RuntimeError(f"Kimi returned no choices for model={self._model}.")
        choice = response.choices[0]
        if choice.message is None:
            raise RuntimeError(f"Kimi returned choice with no message for model={self._model}.")
        translated_text = (choice.message.content or "").strip()
        if not translated_text:
            raise RuntimeError("Kimi returned empty translation content.")

        return TranslationResult(
            translated_text=translated_text,
            provider=self.name,
            source_lang=request.source_lang,
            target_lang=request.target_lang,
            latency_ms=latency_ms,
        )

    async def close(self) -> None:
        await self._client.close()
