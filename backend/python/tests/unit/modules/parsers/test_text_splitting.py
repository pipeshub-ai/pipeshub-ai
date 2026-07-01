"""Unit tests for text_splitting helpers."""

from app.modules.parsers.text_splitting import (
    MAX_TEXT_BLOCK_CHARS,
    split_long_text,
)


class TestSplitLongText:
    def test_short_text_unchanged(self):
        text = "Hello world. Second sentence."
        assert split_long_text(text) == [text]

    def test_empty_text(self):
        assert split_long_text("") == []

    def test_splits_at_sentence_boundaries(self):
        sentence = "A" * 300_000 + "."
        text = " ".join([sentence] * 4)
        chunks = split_long_text(text, max_chars=500_000)
        assert len(chunks) >= 2
        assert all(len(chunk) <= 500_000 for chunk in chunks)
        assert "".join(chunks).replace(" ", "") == text.replace(" ", "")

    def test_hard_splits_sentence_without_boundaries(self):
        text = "x" * (MAX_TEXT_BLOCK_CHARS + 1)
        chunks = split_long_text(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == MAX_TEXT_BLOCK_CHARS
        assert len(chunks[1]) == 1

    def test_respects_custom_max_chars(self):
        text = "word " * 200
        chunks = split_long_text(text, max_chars=50)
        assert len(chunks) > 1
        assert all(len(chunk) <= 50 for chunk in chunks)
