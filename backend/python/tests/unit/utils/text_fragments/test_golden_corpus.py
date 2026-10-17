"""Golden corpus: generated URLs are stable, and a browser-model matcher highlights what we meant.

Regenerate snapshots with `scripts/text_fragments/regen_golden.py`; see docs/text-fragments.md.
"""

import json
import re
import unicodedata

import pytest
from markdown_it import MarkdownIt

from app.models.blocks import BlockType
from app.modules.parsers.html_parser.html_to_blocks import HtmlToBlocksConverter
from app.utils.chat_helpers import _fragment_source_format
from app.utils.text_fragments import SourceFormat, TextFragmentGenerator
from tests.support.text_fragment_corpus import (
    HTML_DIR,
    Case,
    dump_cases,
    generate_url,
    highlight_of,
    load_cases,
)
from tests.support.text_fragment_matcher import highlight_for_url
from tests.support.text_fragment_matcher import rendered_blocks as reference_blocks

CASES = load_cases()
generator = TextFragmentGenerator()


def _case_ids(cases: list[Case]) -> list[str]:
    return [case.id for case in cases]


class TestCorpusHygiene:
    def test_ids_are_unique(self) -> None:
        assert len(set(_case_ids(CASES))) == len(CASES)

    def test_every_fixture_page_is_used_and_exists(self) -> None:
        used = {case.fixture for case in CASES}
        on_disk = {path.stem for path in HTML_DIR.glob("*.html")}
        assert used == on_disk

    def test_every_case_has_a_snapshot(self) -> None:
        assert [case.id for case in CASES if case.expected_url is None] == []

    def test_covers_every_source_format(self) -> None:
        assert {case.format for case in CASES} == set(SourceFormat)


@pytest.mark.parametrize("case", CASES, ids=_case_ids(CASES))
class TestGolden:
    def test_url_matches_snapshot(self, case: Case) -> None:
        assert generate_url(case) == case.expected_url

    def test_url_is_ascii_and_keeps_the_page(self, case: Case) -> None:
        url = generate_url(case)
        assert url.isascii()
        assert url.startswith(case.base_url.split(":~:")[0])

    def test_browser_highlight_matches_expectation(self, case: Case) -> None:
        assert highlight_of(case, case.expected_url) == case.expected_highlight

    def test_highlight_is_nonempty_whenever_a_directive_was_added(self, case: Case) -> None:
        if ":~:" in (case.expected_url or ""):
            assert case.expected_highlight


class TestMatcherRejectsBrokenDirectives:
    """The oracle must be able to fail, or the golden checks above prove nothing."""

    PAGE = "<p>The e-mail from Zürich isn't ready</p><p>Second paragraph here</p>"

    def test_term_cut_inside_a_word_does_not_match(self) -> None:
        assert highlight_for_url(self.PAGE, "https://x.test/#:~:text=ail%20from") is None

    def test_unencoded_hyphen_invalidates_the_directive(self) -> None:
        assert highlight_for_url(self.PAGE, "https://x.test/#:~:text=The%20e-mail") is None

    def test_encoded_hyphen_matches(self) -> None:
        assert highlight_for_url(self.PAGE, "https://x.test/#:~:text=The%20e%2Dmail") == "The e-mail"

    def test_term_may_not_span_blocks(self) -> None:
        url = "https://x.test/#:~:text=isn%27t%20ready%20Second%20paragraph"
        assert highlight_for_url(self.PAGE, url) is None

    def test_range_across_blocks_matches(self) -> None:
        url = "https://x.test/#:~:text=isn%27t%20ready,Second%20paragraph"
        assert highlight_for_url(self.PAGE, url) == "isn't ready Second paragraph"

    def test_matching_ignores_case_and_accents(self) -> None:
        assert highlight_for_url(self.PAGE, "https://x.test/#:~:text=zurich") == "Zürich"


    def test_term_ending_before_a_combining_mark_still_matches_the_whole_word(self) -> None:
        assert highlight_for_url("<p>Cafe\u0301 next</p>", "https://x.test/#:~:text=Caf%C3%A9") == "Cafe\u0301"


class TestDumpCases:
    def test_supplementary_invisible_characters_round_trip(self) -> None:
        raw = [{"snippet": "a\U0001d167b\U000e0001c\u200bd\u00a0e"}]
        assert json.loads(dump_cases(raw)) == raw


_MARKDOWN_CASES = [case for case in CASES if case.format is SourceFormat.MARKDOWN]


@pytest.mark.parametrize("case", _MARKDOWN_CASES, ids=_case_ids(_MARKDOWN_CASES))
def test_markdown_extractor_agrees_with_a_real_markdown_renderer(case: Case) -> None:
    """The rendered text of a snippet, as the markdown-it HTML renderer plus an independent HTML walk see it."""
    html = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(case.snippet)
    expected = [re.sub(r"\s+", " ", block).strip() for block in reference_blocks(html)]
    actual = generator.rendered_blocks(case.snippet, SourceFormat.MARKDOWN)
    assert _squash(actual) == _squash(expected)


def _squash(blocks: list[str]) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", "".join(blocks)))


# Text the real parser indexes but a reader never sees on the page.
_NOT_VISIBLE_ON_PAGE = (
    "Quarterly report",
    "Structured content",
    "International content",
    "Hidden text should never be highlighted",
    "Display none text should never be highlighted",
)


def _production_blocks(fixture: str) -> list[tuple[str, dict]]:
    """(block type, block dict) for every text block the real HTML parser emits for a fixture page."""
    html = (HTML_DIR / f"{fixture}.html").read_text(encoding="utf-8")
    container = HtmlToBlocksConverter().convert(html)
    return [
        (block.type.value, {"format": block.format, "data": block.data})
        for block in container.blocks
        if block.type == BlockType.TEXT and isinstance(block.data, str)
    ]


_FIXTURES = sorted(path.stem for path in HTML_DIR.glob("*.html"))


@pytest.mark.parametrize("fixture", _FIXTURES)
def test_blocks_from_the_production_html_parser_are_highlightable_on_their_own_page(fixture: str) -> None:
    """End to end: real parser -> real call-site format choice -> generator -> reference browser.

    Guards the fixtures against drifting from what production parsing emits, and the
    generator against block text shapes the parser really produces (markdown headings
    glued to paragraphs, nested lists, split image paragraphs, code).
    """
    html = (HTML_DIR / f"{fixture}.html").read_text(encoding="utf-8")
    checked = 0
    for block_type, block in _production_blocks(fixture):
        text = block["data"]
        if text.strip() in _NOT_VISIBLE_ON_PAGE:
            continue
        source_format = _fragment_source_format(block, block_type)
        url = generator.build_url("https://pages.example.test/p.html", text, source_format)
        if ":~:" not in url:
            continue
        highlight = highlight_for_url(html, url)
        assert highlight, f"{fixture}: no highlight for block {text!r}\n  url={url}"
        checked += 1
    assert checked >= 5
