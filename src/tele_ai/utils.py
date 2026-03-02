"""Shared utility functions."""

from __future__ import annotations

import time
from collections import defaultdict


def split_text_by_limit(text: str, max_chars: int) -> list[str]:
    """Split text into chunks respecting a character limit, preferring line boundaries."""
    candidate = text.strip()
    if not candidate:
        return []
    if len(candidate) <= max_chars:
        return [candidate]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    lines = candidate.splitlines() or [candidate]
    for line in lines:
        segment = line or ""

        if len(segment) > max_chars:
            if current:
                chunks.append("\n".join(current).strip())
                current = []
                current_len = 0
            for index in range(0, len(segment), max_chars):
                chunks.append(segment[index : index + max_chars])
            continue

        added = len(segment) + (1 if current else 0)
        if current_len + added <= max_chars:
            current.append(segment)
            current_len += added
            continue

        if current:
            chunks.append("\n".join(current).strip())
        current = [segment]
        current_len = len(segment)

    if current:
        chunks.append("\n".join(current).strip())

    return [chunk for chunk in chunks if chunk]


def sanitize_user_text(text: str) -> str:
    """Wrap user text in XML-style delimiters to prevent prompt injection.

    This does NOT alter the text content. It adds delimiters that the
    system prompt refers to, so the LLM can distinguish user content
    from instructions.
    """
    return f"<user_text>\n{text}\n</user_text>"


class CommandRateLimiter:
    """Simple per-chat rate limiter for bot commands."""

    def __init__(self, cooldown_seconds: float = 5.0) -> None:
        self._cooldown = cooldown_seconds
        self._last_use: defaultdict[tuple[int, str], float] = defaultdict(float)

    def check(self, chat_id: int, command: str) -> bool:
        """Return True if allowed, False if rate-limited."""
        key = (chat_id, command)
        now = time.monotonic()
        if now - self._last_use[key] < self._cooldown:
            return False
        self._last_use[key] = now
        return True
