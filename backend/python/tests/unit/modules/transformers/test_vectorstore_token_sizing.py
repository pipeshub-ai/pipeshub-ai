"""Tests for token-aware sizing in app.modules.transformers.vectorstore.

These guard one invariant: **nothing reaches the embedder over its token
limit.** The limit is expressed in tokens, so every sizing decision in front of
it has to be, and character counts are not a proxy — token density varies by
more than 6x between English prose and base64.
"""

import pytest

from app.modules.transformers.vectorstore import (
    _CHARS_PER_TOKEN_FALLBACK,
    _embed_token_ceiling,
    _exceeds_token_ceiling,
    _split_to_token_ceiling,
    _token_len,
)

CEILING = _embed_token_ceiling()


class TestTokenCeiling:
    def test_default_ceiling_is_positive(self) -> None:
        assert CEILING > 0

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "512")
        assert _embed_token_ceiling() == 512

    @pytest.mark.parametrize("bad", ["", "0", "-1", "not-a-number"])
    def test_invalid_override_falls_back_to_default(
        self, bad: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed env var must not silently disable the ceiling."""
        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", bad)
        assert _embed_token_ceiling() == 8191


class TestExceedsTokenCeiling:
    def test_short_text_is_never_over(self) -> None:
        assert not _exceeds_token_ceiling("a short block of text", CEILING)

    def test_prose_over_the_ceiling_is_detected(self) -> None:
        """~45k characters of English prose is ~10k tokens.

        This is the case the character trigger missed: under the 50,000-char
        cap, so it took the 'small enough to embed whole' branch, while being
        over the embedder's token limit.
        """
        text = "The quick brown fox jumps over the lazy dog. " * 1000
        assert len(text) < 50_000
        assert _exceeds_token_ceiling(text, CEILING)

    def test_token_dense_text_is_detected(self) -> None:
        """Character counts are not a proxy for token counts.

        Base64 tokenizes far denser than prose: this is ~43k characters and
        well over 3x the ceiling in tokens, in a string a character rule calls
        small.
        """
        text = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVoxMjM0NTY3ODkw" * 900
        assert len(text) < 50_000
        assert _exceeds_token_ceiling(text, CEILING)
        assert _token_len(text) > 2 * CEILING

    def test_cheap_guard_matches_the_real_count(self) -> None:
        """The length pre-filter must never skip text that is actually over.

        It exists so the common case does not pay for tokenization; if it is
        wrong in the permissive direction the whole ceiling leaks.
        """
        text = "word " * 40_000
        cheap_says_safe = len(text) <= CEILING * _CHARS_PER_TOKEN_FALLBACK
        assert not (cheap_says_safe and _token_len(text) > CEILING)


class TestSplitToTokenCeiling:
    def test_text_under_the_ceiling_is_returned_unchanged(self) -> None:
        text = "A short paragraph."
        assert _split_to_token_ceiling(text, CEILING) == [text]

    def test_every_piece_is_within_the_ceiling(self) -> None:
        text = "The quick brown fox jumps over the lazy dog. " * 1000
        pieces = _split_to_token_ceiling(text, CEILING)
        assert len(pieces) > 1
        assert all(_token_len(p) <= CEILING for p in pieces)

    def test_unsplittable_run_is_still_bounded(self) -> None:
        """A single 'sentence' the splitter cannot break must still be split.

        A table row, a base64 payload or a minified line arrives as one run with
        no sentence boundary. Splitting mid-token is acceptable here because the
        alternative is a clipped tail that is not retrievable at all.
        """
        text = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVoxMjM0NTY3ODkw" * 900
        pieces = _split_to_token_ceiling(text, CEILING)
        assert len(pieces) > 1
        assert all(_token_len(p) <= CEILING for p in pieces)

    def test_no_content_is_dropped(self) -> None:
        """Splitting must preserve the text; truncation is the bug being fixed."""
        text = "The quick brown fox jumps over the lazy dog. " * 1000
        assert "".join(_split_to_token_ceiling(text, CEILING)) == text

    def test_empty_text_is_safe(self) -> None:
        assert _split_to_token_ceiling("", CEILING) == [""]
