from typing import Never

import pytest

from app.utils.text_fragments import (
    SourceFormat,
    TextDirective,
    TextFragmentConfig,
    TextFragmentGenerator,
)
from app.utils.text_fragments.codec import parse_fragment_directive
from app.utils.text_fragments.url import split_fragment_directive

BASE = "https://example.com/doc"
generator = TextFragmentGenerator()


def _directive_of(url: str) -> TextDirective:
    _, raw = split_fragment_directive(url)
    assert raw is not None, url
    (directive,) = parse_fragment_directive(raw)
    return directive


class TestRenderedBlocks:
    def test_markdown_syntax_is_not_part_of_the_text(self) -> None:
        assert generator.rendered_blocks("**Q3** results", SourceFormat.MARKDOWN) == ["Q3 results"]

    def test_plain_format_keeps_syntax(self) -> None:
        assert generator.rendered_blocks("**Q3** results", SourceFormat.PLAIN) == ["**Q3** results"]

    def test_html_format(self) -> None:
        assert generator.rendered_blocks("<p>a&nbsp;b</p><p>c</p>", SourceFormat.HTML) == ["a b", "c"]

    def test_unspecified_format_uses_configured_default(self) -> None:
        plain_default = TextFragmentGenerator(TextFragmentConfig(default_format=SourceFormat.PLAIN))
        assert plain_default.rendered_blocks("**x** yz") == ["**x** yz"]
        assert generator.rendered_blocks("**x** yz") == ["x yz"]


class TestBuildUrl:
    def test_leading_symbols_do_not_drop_words(self) -> None:
        url = generator.build_url(BASE, "**Q3** results - the e-mail from Zürich isn't ready")
        directive = _directive_of(url)
        assert directive.start == "Q3 results - the e-mail from Zürich isn't ready" or directive.end
        assert directive.start.startswith("Q3 results")

    def test_short_single_block_is_an_exact_match(self) -> None:
        url = generator.build_url(BASE, "Quarterly revenue grew")
        assert url == f"{BASE}#:~:text=Quarterly%20revenue%20grew"

    def test_long_snippet_becomes_a_range(self) -> None:
        snippet = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        directive = _directive_of(generator.build_url(BASE, snippet))
        assert directive == TextDirective(start="alpha beta gamma delta", end="iota kappa lambda mu")

    def test_terms_never_span_blocks(self) -> None:
        snippet = "# Heading words here\n\nBody paragraph that goes on for quite a few more words than eight"
        directive = _directive_of(generator.build_url(BASE, snippet))
        assert directive.start == "Heading words here"
        assert directive.end == "than eight" or directive.end.endswith("than eight")

    def test_hyphen_is_encoded(self) -> None:
        url = generator.build_url(BASE, "state-of-the-art engine")
        assert "-" not in url.split(":~:", 1)[1]
        assert _directive_of(url).start == "state-of-the-art engine"

    def test_existing_anchor_is_kept(self) -> None:
        url = generator.build_url(f"{BASE}#intro", "some words here")
        assert url == f"{BASE}#intro:~:text=some%20words%20here"

    def test_url_with_directive_is_unchanged(self) -> None:
        url = f"{BASE}#:~:text=other"
        assert generator.build_url(url, "some words here") == url

    @pytest.mark.parametrize("snippet", ["", "   ", "!!!", "a b", "```\n```", "![img](x.png)"])
    def test_returns_base_when_no_usable_text(self, snippet: str) -> None:
        assert generator.build_url(BASE, snippet) == BASE

    def test_empty_base_url(self) -> None:
        assert generator.build_url("", "some words here") == ""

    def test_non_string_inputs(self) -> None:
        assert generator.build_url(BASE, None) == BASE  # type: ignore[arg-type]
        assert generator.build_url(None, "words words") is None  # type: ignore[arg-type]

    def test_output_is_ascii(self) -> None:
        url = generator.build_url(BASE, "Ünïcödé 日本語 text 🎉 here")
        assert url.isascii()

    def test_never_raises_when_a_collaborator_fails(self) -> None:
        class Boom:
            def build(self, blocks) -> Never:
                raise RuntimeError("boom")

        failing = TextFragmentGenerator(strategy=Boom())
        assert failing.build_url(BASE, "some words here") == BASE

    def test_strategy_returning_none_leaves_url_alone(self) -> None:
        class Decline:
            def build(self, blocks) -> None:
                return None

        assert TextFragmentGenerator(strategy=Decline()).build_url(BASE, "some words here") == BASE


class TestBuildDirective:
    def test_ineligible_blocks_are_skipped(self) -> None:
        directive = generator.build_directive("a\n\nreal content block here")
        assert directive == TextDirective(start="real content block here")

    def test_cjk_terms_are_never_cut_inside_a_run(self) -> None:
        sentence = "日本語のテキストは単語の間に空白がありません"
        directive = generator.build_directive((sentence + "。") * 4)
        assert directive == TextDirective(start=sentence, end=sentence)

    def test_cjk_terms_stop_at_the_character_budget(self) -> None:
        runs = ["日本語", "のテキ", "ストは", "単語の", "間に空", "白があ", "りませ", "んのでこ", "れが末尾"]
        directive = generator.build_directive("、".join(runs))
        assert directive == TextDirective(
            start="、".join(runs[:4]), end="、".join(runs[-3:])
        )
