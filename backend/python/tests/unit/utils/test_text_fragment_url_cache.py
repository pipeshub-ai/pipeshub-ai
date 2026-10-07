"""Memoized `generate_text_fragment_url` must be indistinguishable from the uncached generator."""

import pytest

from app.utils import chat_helpers
from app.utils.chat_helpers import generate_text_fragment_url
from app.utils.text_fragments import TextFragmentGenerator, facade

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
    facade._FRAGMENT_URL_CACHE.clear()
    yield
    facade._FRAGMENT_URL_CACHE.clear()


@pytest.mark.parametrize("base", BASES)
@pytest.mark.parametrize("snippet", SNIPPETS)
def test_memoized_output_matches_uncached(base, snippet) -> None:
    expected = TextFragmentGenerator().build_url(base, snippet)
    assert generate_text_fragment_url(base, snippet) == expected
    assert generate_text_fragment_url(base, snippet) == expected


def test_cache_actually_serves_repeat_calls(monkeypatch) -> None:
    calls = []
    real = facade._default_generator.build_url

    def counting(base_url, snippet, source_format=None):
        calls.append((base_url, snippet))
        return real(base_url, snippet, source_format)

    monkeypatch.setattr(facade._default_generator, "build_url", counting)

    snippet = "the quick brown fox jumps over"
    first = chat_helpers.generate_text_fragment_url(BASE, snippet)
    second = chat_helpers.generate_text_fragment_url(BASE, snippet)

    assert first == second
    assert len(calls) == 1, "repeat call should hit the cache"


def test_source_format_is_part_of_the_cache_key() -> None:
    from app.utils.text_fragments import SourceFormat

    snippet = "**bold** opening words of a markdown paragraph here"
    as_markdown = generate_text_fragment_url(BASE, snippet, SourceFormat.MARKDOWN)
    as_plain = generate_text_fragment_url(BASE, snippet, SourceFormat.PLAIN)
    assert as_markdown != as_plain


def test_distinct_snippets_do_not_collide() -> None:
    a = generate_text_fragment_url(BASE, "alpha beta gamma delta")
    b = generate_text_fragment_url(BASE, "epsilon zeta eta theta")
    assert a != b


def test_distinct_base_urls_do_not_collide() -> None:
    snippet = "shared snippet text here"
    a = generate_text_fragment_url("https://a.example/x", snippet)
    b = generate_text_fragment_url("https://b.example/x", snippet)
    assert a != b
    assert a.startswith("https://a.example/x")
    assert b.startswith("https://b.example/x")


def test_cache_is_bounded() -> None:
    maxsize = facade._FRAGMENT_URL_CACHE.maxsize
    for i in range(maxsize + 50):
        generate_text_fragment_url(BASE, f"unique snippet number {i} here")
    assert len(facade._FRAGMENT_URL_CACHE) <= maxsize


def test_non_string_inputs_bypass_cache_and_never_raise() -> None:
    assert generate_text_fragment_url(BASE, None) == BASE
    assert generate_text_fragment_url(None, "some snippet words here") is None
    assert generate_text_fragment_url(123, "some snippet words here") == 123
    assert len(facade._FRAGMENT_URL_CACHE) == 0
