import pytest

from app.utils.text_fragments.extractors import (
    ExtractorRegistry,
    HtmlBlockExtractor,
    MarkdownBlockExtractor,
    PlainTextBlockExtractor,
)
from app.utils.text_fragments.models import SourceFormat


class TestPlainTextBlockExtractor:
    extractor = PlainTextBlockExtractor()

    def test_splits_on_line_breaks(self) -> None:
        assert self.extractor.extract("one\ntwo\r\n\r\nthree") == ["one", "two", "three"]

    def test_splits_on_ellipsis_between_passages(self) -> None:
        assert self.extractor.extract("first part... second part \u2026 third") == [
            "first part",
            "second part",
            "third",
        ]

    def test_keeps_markdown_syntax_verbatim(self) -> None:
        assert self.extractor.extract("**bold** text") == ["**bold** text"]

    def test_blank_input(self) -> None:
        assert self.extractor.extract("  \n \n") == []


class TestMarkdownBlockExtractor:
    extractor = MarkdownBlockExtractor()

    def test_strips_inline_syntax(self) -> None:
        assert self.extractor.extract("**Q3** results - the *e-mail* is `ready`") == [
            "Q3 results - the e-mail is ready"
        ]

    def test_links_keep_text_not_target(self) -> None:
        assert self.extractor.extract("see [the docs](https://example.com/x) now") == ["see the docs now"]

    def test_images_contribute_no_text_and_split_the_run(self) -> None:
        assert self.extractor.extract("before ![alt text](img.png) after") == ["before ", " after"]
        assert self.extractor.extract("before <img src='x.png'> after") == ["before ", " after"]

    def test_headings_paragraphs_and_list_items_are_separate_blocks(self) -> None:
        text = "# Title\n\nFirst paragraph.\n\n- item one\n- item two\n"
        assert self.extractor.extract(text) == ["Title", "First paragraph.", "item one", "item two"]

    def test_soft_break_joins_lines_into_one_block(self) -> None:
        assert self.extractor.extract("line one\nline two") == ["line one line two"]

    def test_hard_break_and_br_start_new_blocks(self) -> None:
        assert self.extractor.extract("line one  \nline two") == ["line one", "line two"]
        assert self.extractor.extract("line one<br>line two") == ["line one", "line two"]

    def test_table_cells_are_separate_blocks(self) -> None:
        text = "| Name | Role |\n| --- | --- |\n| Ada | Engineer |\n"
        assert self.extractor.extract(text) == ["Name", "Role", "Ada", "Engineer"]

    def test_code_fence_lines_are_blocks_without_fence_markers(self) -> None:
        text = "```python\nx = 1\ny = 2\n```\n"
        assert self.extractor.extract(text) == ["x = 1", "y = 2"]

    def test_html_block_is_delegated(self) -> None:
        assert self.extractor.extract("<div>raw <b>html</b></div>") == ["raw html"]

    def test_entities_are_decoded(self) -> None:
        assert self.extractor.extract("fish &amp; chips") == ["fish & chips"]

    def test_empty(self) -> None:
        assert self.extractor.extract("") == []


class TestHtmlBlockExtractor:
    extractor = HtmlBlockExtractor()

    def test_block_tags_split_blocks_and_inline_tags_do_not(self) -> None:
        html = "<p>Hello <b>big</b> world</p><p>Second</p>"
        assert self.extractor.extract(html) == ["Hello big world", "Second"]

    def test_replaced_elements_split_the_run_of_text(self) -> None:
        assert self.extractor.extract("<p>before <img src='x.png' alt='alt'> after</p>") == ["before ", " after"]

    def test_br_splits_a_block(self) -> None:
        assert self.extractor.extract("<p>a<br>b</p>") == ["a", "b"]

    def test_table_cells_are_blocks(self) -> None:
        html = "<table><tr><td>A1</td><td>B1</td></tr></table>"
        assert self.extractor.extract(html) == ["A1", "B1"]

    @pytest.mark.parametrize(
        "html",
        [
            "<script>var x = 1;</script><p>kept</p>",
            "<style>p{color:red}</style><p>kept</p>",
            "<p hidden>gone</p><p>kept</p>",
            '<p style="display: none">gone</p><p>kept</p>',
            '<p style="visibility:hidden">gone</p><p>kept</p>',
            "<p>kept</p><noscript>gone</noscript>",
        ],
    )
    def test_skips_invisible_content(self, html: str) -> None:
        assert self.extractor.extract(html) == ["kept"]

    def test_entities_are_decoded(self) -> None:
        assert self.extractor.extract("<p>R&amp;D &lt;team&gt;</p>") == ["R&D <team>"]

    def test_empty(self) -> None:
        assert self.extractor.extract("") == []


class TestExtractorRegistry:
    def test_defaults_cover_every_format(self) -> None:
        registry = ExtractorRegistry.with_defaults()
        assert isinstance(registry.get(SourceFormat.MARKDOWN), MarkdownBlockExtractor)
        assert isinstance(registry.get(SourceFormat.HTML), HtmlBlockExtractor)
        assert isinstance(registry.get(SourceFormat.PLAIN), PlainTextBlockExtractor)

    def test_none_resolves_to_default(self) -> None:
        assert isinstance(ExtractorRegistry.with_defaults().get(None), MarkdownBlockExtractor)
        plain_default = ExtractorRegistry.with_defaults(SourceFormat.PLAIN)
        assert isinstance(plain_default.get(None), PlainTextBlockExtractor)

    def test_unregistered_format_falls_back_to_default(self) -> None:
        plain = PlainTextBlockExtractor()
        registry = ExtractorRegistry({SourceFormat.PLAIN: plain}, SourceFormat.PLAIN)
        assert registry.get(SourceFormat.HTML) is plain

    def test_default_must_be_registered(self) -> None:
        with pytest.raises(ValueError):
            ExtractorRegistry({SourceFormat.PLAIN: PlainTextBlockExtractor()}, SourceFormat.HTML)
