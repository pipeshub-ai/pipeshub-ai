import pytest

from app.utils.text_fragments.models import TextDirective
from app.utils.text_fragments.url import (
    append_directive,
    has_fragment_directive,
    split_fragment_directive,
    strip_fragment_directive,
)

DIRECTIVE = TextDirective(start="hello world")


class TestSplit:
    @pytest.mark.parametrize(
        ("url", "page", "directive"),
        [
            ("https://x.io/a", "https://x.io/a", None),
            ("https://x.io/a#sec", "https://x.io/a#sec", None),
            ("https://x.io/a#:~:text=foo", "https://x.io/a", "text=foo"),
            ("https://x.io/a#sec:~:text=foo", "https://x.io/a#sec", "text=foo"),
            ("https://x.io/a#:~:", "https://x.io/a", None),
            ("https://x.io/a#:~:text=a&text=b", "https://x.io/a", "text=a&text=b"),
            ("https://x.io/a#:~:text=:~:text=b", "https://x.io/a", "text=:~:text=b"),
            ("https://x.io/s?q=a:~:b", "https://x.io/s?q=a:~:b", None),
            ("https://x.io/s?q=a:~:b#:~:text=hi", "https://x.io/s?q=a:~:b", "text=hi"),
            ("https://x.io/p:~:q/a#sec:~:text=hi", "https://x.io/p:~:q/a#sec", "text=hi"),
        ],
    )
    def test_split(self, url: str, page: str, directive: str | None) -> None:
        assert split_fragment_directive(url) == (page, directive)

    def test_strip_returns_page_only(self) -> None:
        assert strip_fragment_directive("https://x.io/a#m1:~:text=foo%2Dbar") == "https://x.io/a#m1"

    def test_has_directive(self) -> None:
        assert has_fragment_directive("https://x.io/#:~:text=a")
        assert not has_fragment_directive("https://x.io/#anchor")
        assert not has_fragment_directive("https://x.io/s?q=a:~:b")


class TestAppend:
    def test_delimiter_in_query_is_not_an_existing_directive(self) -> None:
        assert (
            append_directive("https://x.io/s?q=a:~:b", DIRECTIVE)
            == "https://x.io/s?q=a:~:b#:~:text=hello%20world"
        )

    def test_adds_hash_and_delimiter_when_no_fragment(self) -> None:
        assert append_directive("https://x.io/a", DIRECTIVE) == "https://x.io/a#:~:text=hello%20world"

    def test_keeps_existing_anchor(self) -> None:
        assert (
            append_directive("https://x.io/a#sec", DIRECTIVE)
            == "https://x.io/a#sec:~:text=hello%20world"
        )

    def test_existing_directive_is_left_unchanged(self) -> None:
        url = "https://x.io/a#:~:text=other"
        assert append_directive(url, DIRECTIVE) == url

    def test_append_then_strip_round_trips(self) -> None:
        for base in ("https://x.io/a", "https://x.io/a#sec"):
            assert strip_fragment_directive(append_directive(base, DIRECTIVE)) == base
