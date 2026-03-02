from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


Direction = Literal["incoming", "outgoing"]


@dataclass(slots=True)
class TranslationRequest:
    text: str
    source_lang: str | None
    target_lang: str
    chat_id: int
    direction: Direction
    context_hint: str | None = None


@dataclass(slots=True)
class TranslationResult:
    translated_text: str
    provider: str
    source_lang: str | None
    target_lang: str
    latency_ms: int


class TranslatorProvider(Protocol):
    name: str

    async def translate(self, request: TranslationRequest) -> TranslationResult:
        ...

    async def close(self) -> None:
        ...

