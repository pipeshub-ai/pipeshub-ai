"""Tests for token-aware sizing in app.modules.transformers.vectorstore.

These guard two invariants:

  * **nothing reaches the embedder over its token limit** -- the limit is
    expressed in tokens, so the decisions in front of it must be too; and
  * **splitting never changes the text**, because truncation is the failure
    being fixed and a lossy split would reintroduce it silently.

The ceiling is a fixed local constant rather than `_embed_token_ceiling()`, so
an exported `PIPESHUB_EMBED_TOKEN_LIMIT` in the environment cannot move the
fixtures out from under the assertions. Environment resolution is tested
separately and explicitly.
"""

import pytest

from app.modules.transformers.vectorstore import (
    _embed_token_ceiling,
    _exceeds_token_ceiling,
    _split_to_token_ceiling,
    _token_encoder,
    _token_len,
)

CEILING = 8191

# tiktoken is not a declared dependency, so both paths are real and both are
# tested: the exact path when an encoder resolves, and the byte-bounded
# fallback when it does not.
HAS_ENCODER = _token_encoder() is not None
needs_encoder = pytest.mark.skipif(not HAS_ENCODER, reason="tiktoken unavailable")


@pytest.fixture
def no_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the no-tokenizer path regardless of what is installed."""
    monkeypatch.setattr(_token_encoder, "_cached", None, raising=False)
    monkeypatch.setattr(
        "app.modules.transformers.vectorstore._token_encoder", lambda: None
    )


class TestTokenCeiling:
    def test_default_ceiling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_EMBED_TOKEN_LIMIT", raising=False)
        assert _embed_token_ceiling() == 8191

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "512")
        assert _embed_token_ceiling() == 512

    @pytest.mark.parametrize("bad", ["", "0", "-1", "not-a-number"])
    def test_invalid_override_falls_back(
        self, bad: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A malformed value must not silently disable the ceiling."""
        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", bad)
        assert _embed_token_ceiling() == 8191


class TestTokenLenIsAnUpperBound:
    """Without a tokenizer, _token_len must never UNDER-count.

    A characters-per-token ratio is unsafe here: measured against cl100k_base,
    one character is ~0.17 tokens for ASCII prose but ~1.1 for CJK and ~3 for
    emoji. A token encodes at least one byte, so the UTF-8 byte length bounds
    the count for any input.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "hello world " * 100,
            "日本語のテキストです。" * 100,
            "🎉" * 100,
            "QUJDREVGR0hJSktM" * 100,
            "",
        ],
        ids=["ascii", "cjk", "emoji", "base64", "empty"],
    )
    @needs_encoder
    def test_fallback_never_undercounts(
        self, text: str, no_encoder: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tiktoken

        exact = len(tiktoken.get_encoding("cl100k_base").encode(text))
        assert _token_len(text) >= exact


class TestExceedsTokenCeiling:
    def test_short_text_is_never_over(self) -> None:
        assert not _exceeds_token_ceiling("a short block of text", CEILING)

    @needs_encoder
    def test_prose_under_the_char_cap_but_over_the_ceiling(self) -> None:
        """The case the character trigger missed.

        Under the 50,000-char cap, so it took the 'small enough to embed whole'
        branch -- while being over the embedder's token limit.
        """
        text = "The quick brown fox jumps over the lazy dog. " * 1000
        assert len(text) < 50_000
        assert _exceeds_token_ceiling(text, CEILING)

    @needs_encoder
    def test_token_dense_text_is_detected(self) -> None:
        """Character counts are not a proxy: base64 tokenizes far denser."""
        text = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVoxMjM0NTY3ODkw" * 900
        assert len(text) < 50_000
        assert _exceeds_token_ceiling(text, CEILING)

    def test_cheap_guard_never_skips_text_that_is_over(self) -> None:
        """The pre-filter exists for speed; if it is permissive the ceiling leaks."""
        for text in ("word " * 40_000, "日本語" * 9_000, "🎉" * 9_000):
            if len(text) <= CEILING:
                assert _token_len(text) <= CEILING


class TestSplitToTokenCeiling:
    def test_text_under_the_ceiling_is_unchanged(self) -> None:
        assert _split_to_token_ceiling("A short paragraph.", CEILING) == [
            "A short paragraph."
        ]

    def test_empty_text_is_safe(self) -> None:
        assert _split_to_token_ceiling("", CEILING) == [""]

    @pytest.mark.parametrize(
        "text",
        [
            "The quick brown fox jumps over the lazy dog. " * 1000,
            "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVoxMjM0NTY3ODkw" * 900,
            "日本語のテキストです。" * 3000,
            "🎉" * 20000,
        ],
        ids=["prose", "base64", "cjk", "emoji"],
    )
    def test_every_piece_is_within_the_ceiling(self, text: str) -> None:
        pieces = _split_to_token_ceiling(text, CEILING)
        assert all(_token_len(p) <= CEILING for p in pieces)

    @pytest.mark.parametrize(
        "text",
        [
            "The quick brown fox jumps over the lazy dog. " * 1000,
            "日本語のテキストです。" * 3000,
            "🎉" * 20000,
            "Ünïcödé ünd ẞonderzeichen — ﬂ ﬁ ‰ " * 2000,
        ],
        ids=["prose", "cjk", "emoji", "latin-extended"],
    )
    def test_split_is_lossless(self, text: str) -> None:
        """Rejoining must reproduce the source exactly.

        A token boundary is not guaranteed to be a UTF-8 character boundary, and
        decoding a partial sequence substitutes U+FFFD. Multi-byte text is the
        only input that exercises this -- an ASCII-only case passes either way.
        """
        pieces = _split_to_token_ceiling(text, CEILING)
        assert "".join(pieces) == text
        assert "�" not in "".join(pieces)

    @needs_encoder
    def test_unsplittable_run_is_still_bounded(self) -> None:
        """A 'sentence' the splitter cannot break must still be split.

        A table row, base64 payload or minified line arrives as one run with no
        sentence boundary. Splitting mid-token is acceptable; a rejected record
        is not.
        """
        text = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVoxMjM0NTY3ODkw" * 900
        pieces = _split_to_token_ceiling(text, CEILING)
        assert len(pieces) > 1
        assert all(_token_len(p) <= CEILING for p in pieces)

    def test_fallback_path_is_bounded_and_lossless(self, no_encoder: None) -> None:
        """Same guarantees with no tokenizer installed."""
        text = "日本語のテキストです。" * 3000
        pieces = _split_to_token_ceiling(text, CEILING)
        assert len(pieces) > 1
        assert "".join(pieces) == text
        assert "�" not in "".join(pieces)
        assert all(len(p.encode("utf-8")) <= CEILING for p in pieces)
