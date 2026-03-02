from __future__ import annotations


def is_command(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("/")


def has_translation_prefix(text: str, prefix: str) -> bool:
    return text.strip().startswith(prefix)


def format_incoming(prefix: str, source_lang: str, original: str, translated: str) -> str:
    return translated


def format_outgoing(prefix: str, target_lang: str, original: str, translated: str) -> str:
    return translated


def format_manual(prefix: str, source_lang: str, target_lang: str, original: str, translated: str) -> str:
    return translated


def format_system(prefix: str, message: str) -> str:
    return f"{prefix}[SYS] {message}"

