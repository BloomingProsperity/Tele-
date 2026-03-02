from tele_ai.utils import split_text_by_limit


def test_split_text_short_message() -> None:
    assert split_text_by_limit("hello", 10) == ["hello"]


def test_split_text_long_message() -> None:
    text = "a" * 25
    chunks = split_text_by_limit(text, 10)
    assert chunks == ["aaaaaaaaaa", "aaaaaaaaaa", "aaaaa"]


def test_split_text_multiline() -> None:
    text = "line1\nline2\nline3"
    chunks = split_text_by_limit(text, 11)
    assert chunks == ["line1\nline2", "line3"]

