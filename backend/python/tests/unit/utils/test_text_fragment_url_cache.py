"""Memoized `generate_text_fragment_url` must be indistinguishable from the raw builder."""

import pytest

from app.utils import chat_helpers
from app.utils.chat_helpers import (
    _build_text_fragment_url,
    _FRAGMENT_URL_CACHE,
    generate_text_fragment_url,
)

BASE = "https://example.com/doc/1"

SNIPPETS = [
    "The quick brown fox jumps over the lazy dog",
    "  leading and trailing whitespace  ",
    "trailing punctuation should be stripped!!!",
    "single",
    "",
    "   ",
    "!!!???",
    "Ünïcodé wörds thät need éncoding here",
    "emoji 🎉 mixed with words that follow after",
    "a" * 5000 + " long tail words here",
    "one two",
    "line one\nline two continues here\nline three",
    "Hyphenated-words and don't contractions appear",
]

BASES = [
    BASE,
    "https://example.com/doc/1#section",
    "https://example.com/doc/1#:~:text=already,fragment",
    "",
]


@pytest.fixture(autouse=True)
def _clear_cache():
    _FRAGMENT_URL_CACHE.clear()
    yield
    _FRAGMENT_URL_CACHE.clear()


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("snippet", SNIPPETS)
def test_memoized_output_matches_uncached(base, snippet) -> None:
    expected = _build_text_fragment_url(base, snippet)
    assert generate_text_fragment_url(base, snippet) == expected
    # second call is served from the cache and must be identical
    assert generate_text_fragment_url(base, snippet) == expected


def test_cache_actually_serves_repeat_calls(monkeypatch) -> None:
    calls = []
    real = chat_helpers._build_text_fragment_url

    def counting(base_url, text_snippet):
        calls.append((base_url, text_snippet))
        return real(base_url, text_snippet)

    monkeypatch.setattr(chat_helpers, "_build_text_fragment_url", counting)

    snippet = "the quick brown fox jumps over"
    first = chat_helpers.generate_text_fragment_url(BASE, snippet)
    second = chat_helpers.generate_text_fragment_url(BASE, snippet)

    assert first == second
    assert len(calls) == 1, "repeat call should hit the cache"


def test_distinct_snippets_do_not_collide() -> None:
    a = generate_text_fragment_url(BASE, "alpha beta gamma delta")
    b = generate_text_fragment_url(BASE, "epsilon zeta eta theta")
    assert a != b
    assert a == _build_text_fragment_url(BASE, "alpha beta gamma delta")
    assert b == _build_text_fragment_url(BASE, "epsilon zeta eta theta")


def test_distinct_base_urls_do_not_collide() -> None:
    snippet = "shared snippet text here"
    a = generate_text_fragment_url("https://a.example/x", snippet)
    b = generate_text_fragment_url("https://b.example/x", snippet)
    assert a != b
    assert a.startswith("https://a.example/x")
    assert b.startswith("https://b.example/x")


def test_cache_is_bounded() -> None:
    maxsize = chat_helpers._FRAGMENT_URL_CACHE_MAXSIZE
    for i in range(maxsize + 50):
        generate_text_fragment_url(BASE, f"unique snippet number {i} here")
    assert len(_FRAGMENT_URL_CACHE) <= maxsize


def test_cache_entries_expire(monkeypatch) -> None:
    clock = {"t": 1000.0}
    monkeypatch.setattr(chat_helpers.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(chat_helpers, "_FRAGMENT_URL_CACHE_TTL_SECONDS", 10.0)

    snippet = "the quick brown fox jumps over"
    first = generate_text_fragment_url(BASE, snippet)
    assert len(_FRAGMENT_URL_CACHE) == 1

    clock["t"] += 11.0
    calls = []
    real = chat_helpers._build_text_fragment_url

    def counting(base_url, text_snippet):
        calls.append(1)
        return real(base_url, text_snippet)

    monkeypatch.setattr(chat_helpers, "_build_text_fragment_url", counting)
    second = generate_text_fragment_url(BASE, snippet)
    assert second == first
    assert len(calls) == 1


def test_non_string_inputs_bypass_cache_and_preserve_behaviour() -> None:
    # None snippet: builder short-circuits to base_url, and nothing is cached.
    assert generate_text_fragment_url(BASE, None) == _build_text_fragment_url(BASE, None)
    assert not _FRAGMENT_URL_CACHE

    # Non-str base_url raises out of the builder today (the membership test is
    # outside its try block); the wrapper must not mask that.
    with pytest.raises(TypeError):
        generate_text_fragment_url(123, "some snippet words here")
