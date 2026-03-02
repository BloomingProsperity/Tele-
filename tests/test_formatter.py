from tele_ai.formatter import (
    format_incoming,
    format_manual,
    format_outgoing,
    format_system,
    has_translation_prefix,
    is_command,
)


def test_command_detection() -> None:
    assert is_command("/ai_pause")
    assert not is_command("hello")


def test_prefix_detection() -> None:
    prefix = "[AI-TR]"
    assert has_translation_prefix("[AI-TR][IN][en->zh]\n...", prefix)
    assert not has_translation_prefix("normal message", prefix)


def test_formatter_output() -> None:
    assert format_incoming("[AI-TR]", "en", "hello", "nihao") == "nihao"
    assert format_outgoing("[AI-TR]", "en", "nihao", "hello") == "hello"
    assert format_manual("[AI-TR]", "es", "zh", "hola", "nihao") == "nihao"
    assert format_system("[AI-TR]", "ok") == "[AI-TR][SYS] ok"
