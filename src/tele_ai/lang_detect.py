from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from lingua import LanguageDetectorBuilder

from tele_ai.constants import LANG_ZH


CJK_PATTERN = re.compile(r"[\u3400-\u9FFF]")


@dataclass(slots=True)
class DetectionResult:
    lang_code: str
    confidence: float


def normalize_lang_code(code: str) -> str:
    normalized = code.lower().strip()
    if normalized.startswith("zh"):
        return LANG_ZH
    return normalized


class LanguageDetector:
    def __init__(self) -> None:
        self._detector = LanguageDetectorBuilder.from_all_spoken_languages().build()

    def detect(self, text: str) -> Optional[DetectionResult]:
        candidate = text.strip()
        if not candidate:
            return None

        cjk_count = len(CJK_PATTERN.findall(candidate))
        if cjk_count > 0:
            ratio = cjk_count / max(len(candidate), 1)
            if ratio >= 0.25:
                return DetectionResult(lang_code=LANG_ZH, confidence=0.99)

        confidences = self._detector.compute_language_confidence_values(candidate)
        if not confidences:
            return None

        best = confidences[0]
        iso = getattr(best.language, "iso_code_639_1", None)
        if iso is None:
            return None
        iso_code = iso.name.lower()
        return DetectionResult(
            lang_code=normalize_lang_code(iso_code),
            confidence=float(best.value),
        )
