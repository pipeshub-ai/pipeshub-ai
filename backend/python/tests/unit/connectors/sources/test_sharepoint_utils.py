from unittest.mock import MagicMock

import pytest
from bs4 import BeautifulSoup

from app.connectors.sources.microsoft.sharepoint_online.utils import (
    ATTR_PREFIXES_TO_REMOVE,
    ATTRS_TO_REMOVE,
    clean_html_output,
    clean_inline_styles,
    remove_empty_divs,
    strip_sharepoint_attributes,
)


class TestStripSharepointAttributes:
    def test_removes_exact_match_attrs(self):
        html = '<div data-sp-canvasdataversion="1.0" data-sp-webpartid="abc">Hello</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2
        div = soup.find("div")
        assert "data-sp-canvasdataversion" not in div.attrs
        assert "data-sp-webpartid" not in div.attrs

    def test_removes_prefix_match_attrs(self):
        html = '<div data-sp-custom="x" data-cke-foo="y" data-pnp-bar="z" data-ms-thing="w">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 4
        assert len(soup.find("div").attrs) == 0

    def test_preserves_non_sharepoint_attrs(self):
        html = '<div class="normal" id="test" data-custom="keep">Content</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0
        div = soup.find("div")
        assert div["class"] == ["normal"]
        assert div["id"] == "test"
        assert div["data-custom"] == "keep"

    def test_handles_empty_soup(self):
        soup = BeautifulSoup("", "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0

    def test_handles_text_only(self):
        soup = BeautifulSoup("Just text no tags", "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0

    def test_removes_aria_attrs(self):
        html = '<span aria-label="test" aria-hidden="true">Text</span>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2

    def test_handles_nested_elements(self):
        html = '<div data-sp-rte="1"><span data-cke-saved-href="url">Nested</span></div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2

    def test_multiple_tags(self):
        html = '<div data-sp-canvascontrol="a"></div><p data-sp-controldata="b"></p>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2


class TestRemoveEmptyDivs:
    def test_removes_single_empty_div(self):
        html = "<div></div><p>Keep me</p>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 1
        assert soup.find("div") is None
        assert soup.find("p").text == "Keep me"

    def test_preserves_div_with_text(self):
        html = "<div>Content</div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0
        assert soup.find("div").text == "Content"

    def test_preserves_div_with_img(self):
        html = '<div><img src="test.png"/></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_video(self):
        html = '<div><video src="test.mp4"></video></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_iframe(self):
        html = '<div><iframe src="test.html"></iframe></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_table(self):
        html = "<div><table><tr><td>data</td></tr></table></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_input(self):
        html = '<div><input type="text"/></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_button(self):
        html = "<div><button>Click</button></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_svg(self):
        html = "<div><svg></svg></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_removes_nested_empty_divs(self):
        html = "<div><div></div></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed >= 2

    def test_removes_whitespace_only_div(self):
        html = "<div>   \n  </div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 1

    def test_handles_no_divs(self):
        html = "<p>Paragraph</p><span>Span</span>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_multi_pass_cleanup(self):
        html = "<div><div><div></div></div></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed >= 3
        assert soup.find("div") is None


class TestCleanInlineStyles:
    def test_removes_ms_styles(self):
        html = '<div style="-ms-text-size-adjust:100%; color:red;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "-ms-" not in soup.find("div")["style"]
        assert "color:red" in soup.find("div")["style"]

    def test_removes_webkit_styles(self):
        html = '<div style="-webkit-appearance:none; font-size:12px;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "-webkit-" not in soup.find("div")["style"]
        assert "font-size:12px" in soup.find("div")["style"]

    def test_removes_mso_styles(self):
        html = '<div style="mso-font-alt:Arial; color:blue;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "mso-" not in soup.find("div")["style"]
        assert "color:blue" in soup.find("div")["style"]

    def test_removes_style_attr_when_empty(self):
        html = '<div style="-ms-text-size-adjust:100%;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "style" not in soup.find("div").attrs

    def test_cleans_extra_semicolons(self):
        html = '<div style="-ms-x:1;; color:red;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        style = soup.find("div")["style"]
        assert ";;" not in style

    def test_no_style_elements_unchanged(self):
        html = "<div>No styles</div>"
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "style" not in soup.find("div").attrs

    def test_multiple_vendor_prefixes(self):
        html = '<p style="-ms-a:1; -webkit-b:2; mso-c:3; display:block;">Content</p>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        style = soup.find("p")["style"]
        assert "-ms-" not in style
        assert "-webkit-" not in style
        assert "mso-" not in style
        assert "display:block" in style


class TestCleanHtmlOutput:
    def test_full_cleanup_pipeline(self):
        html = (
            '<div data-sp-canvasdataversion="1">'
            '<div style="-ms-x:1;">'
            "</div>"
            "</div>"
            "<p>Content</p>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "data-sp-canvasdataversion" not in result
        assert "Content" in result

    def test_with_logger(self):
        html = '<div data-sp-rte="1"><div></div></div>'
        soup = BeautifulSoup(html, "html.parser")
        logger = MagicMock()
        result = clean_html_output(soup, logger=logger)
        assert logger.debug.call_count == 2

    def test_without_logger(self):
        html = "<div><p>Hello</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "Hello" in result

    def test_collapses_blank_lines(self):
        html = "<p>A</p>\n\n\n\n<p>B</p>"
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "\n\n" not in result

    def test_returns_string(self):
        soup = BeautifulSoup("<p>Test</p>", "html.parser")
        result = clean_html_output(soup)
        assert isinstance(result, str)

    def test_empty_input(self):
        soup = BeautifulSoup("", "html.parser")
        result = clean_html_output(soup)
        assert isinstance(result, str)


class TestConstants:
    def test_attrs_to_remove_is_set(self):
        assert isinstance(ATTRS_TO_REMOVE, set)
        assert "data-sp-canvasdataversion" in ATTRS_TO_REMOVE
        assert "data-sp-webpartdata" in ATTRS_TO_REMOVE
        assert "aria-label" in ATTRS_TO_REMOVE

    def test_attr_prefixes_is_tuple(self):
        assert isinstance(ATTR_PREFIXES_TO_REMOVE, tuple)
        assert "data-sp-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-cke-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-pnp-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-ms-" in ATTR_PREFIXES_TO_REMOVE

# =============================================================================
# Merged from test_sharepoint_utils_full_coverage.py
# =============================================================================

class TestStripSharepointAttributesFullCoverage:
    def test_removes_exact_match_attrs(self):
        html = '<div data-sp-canvasdataversion="1.0" data-sp-webpartid="abc">Hello</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2
        div = soup.find("div")
        assert "data-sp-canvasdataversion" not in div.attrs
        assert "data-sp-webpartid" not in div.attrs

    def test_removes_prefix_match_attrs(self):
        html = '<div data-sp-custom="x" data-cke-foo="y" data-pnp-bar="z" data-ms-thing="w">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 4
        assert len(soup.find("div").attrs) == 0

    def test_preserves_non_sharepoint_attrs(self):
        html = '<div class="normal" id="test" data-custom="keep">Content</div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0
        div = soup.find("div")
        assert div["class"] == ["normal"]
        assert div["id"] == "test"
        assert div["data-custom"] == "keep"

    def test_handles_empty_soup(self):
        soup = BeautifulSoup("", "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0

    def test_handles_text_only(self):
        soup = BeautifulSoup("Just text no tags", "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 0

    def test_removes_aria_attrs(self):
        html = '<span aria-label="test" aria-hidden="true">Text</span>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2

    def test_handles_nested_elements(self):
        html = '<div data-sp-rte="1"><span data-cke-saved-href="url">Nested</span></div>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2

    def test_multiple_tags(self):
        html = '<div data-sp-canvascontrol="a"></div><p data-sp-controldata="b"></p>'
        soup = BeautifulSoup(html, "html.parser")
        count = strip_sharepoint_attributes(soup)
        assert count == 2


class TestRemoveEmptyDivsFullCoverage:
    def test_removes_single_empty_div(self):
        html = "<div></div><p>Keep me</p>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 1
        assert soup.find("div") is None
        assert soup.find("p").text == "Keep me"

    def test_preserves_div_with_text(self):
        html = "<div>Content</div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0
        assert soup.find("div").text == "Content"

    def test_preserves_div_with_img(self):
        html = '<div><img src="test.png"/></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_video(self):
        html = '<div><video src="test.mp4"></video></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_iframe(self):
        html = '<div><iframe src="test.html"></iframe></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_table(self):
        html = "<div><table><tr><td>data</td></tr></table></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_input(self):
        html = '<div><input type="text"/></div>'
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_button(self):
        html = "<div><button>Click</button></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_preserves_div_with_svg(self):
        html = "<div><svg></svg></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_removes_nested_empty_divs(self):
        html = "<div><div></div></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed >= 2

    def test_removes_whitespace_only_div(self):
        html = "<div>   \n  </div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 1

    def test_handles_no_divs(self):
        html = "<p>Paragraph</p><span>Span</span>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed == 0

    def test_multi_pass_cleanup(self):
        html = "<div><div><div></div></div></div>"
        soup = BeautifulSoup(html, "html.parser")
        removed = remove_empty_divs(soup)
        assert removed >= 3
        assert soup.find("div") is None


class TestCleanInlineStylesFullCoverage:
    def test_removes_ms_styles(self):
        html = '<div style="-ms-text-size-adjust:100%; color:red;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "-ms-" not in soup.find("div")["style"]
        assert "color:red" in soup.find("div")["style"]

    def test_removes_webkit_styles(self):
        html = '<div style="-webkit-appearance:none; font-size:12px;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "-webkit-" not in soup.find("div")["style"]
        assert "font-size:12px" in soup.find("div")["style"]

    def test_removes_mso_styles(self):
        html = '<div style="mso-font-alt:Arial; color:blue;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "mso-" not in soup.find("div")["style"]
        assert "color:blue" in soup.find("div")["style"]

    def test_removes_style_attr_when_empty(self):
        html = '<div style="-ms-text-size-adjust:100%;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "style" not in soup.find("div").attrs

    def test_cleans_extra_semicolons(self):
        html = '<div style="-ms-x:1;; color:red;">Hi</div>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        style = soup.find("div")["style"]
        assert ";;" not in style

    def test_no_style_elements_unchanged(self):
        html = "<div>No styles</div>"
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        assert "style" not in soup.find("div").attrs

    def test_multiple_vendor_prefixes(self):
        html = '<p style="-ms-a:1; -webkit-b:2; mso-c:3; display:block;">Content</p>'
        soup = BeautifulSoup(html, "html.parser")
        clean_inline_styles(soup)
        style = soup.find("p")["style"]
        assert "-ms-" not in style
        assert "-webkit-" not in style
        assert "mso-" not in style
        assert "display:block" in style


class TestCleanHtmlOutputFullCoverage:
    def test_full_cleanup_pipeline(self):
        html = (
            '<div data-sp-canvasdataversion="1">'
            '<div style="-ms-x:1;">'
            "</div>"
            "</div>"
            "<p>Content</p>"
        )
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "data-sp-canvasdataversion" not in result
        assert "Content" in result

    def test_with_logger(self):
        html = '<div data-sp-rte="1"><div></div></div>'
        soup = BeautifulSoup(html, "html.parser")
        logger = MagicMock()
        result = clean_html_output(soup, logger=logger)
        assert logger.debug.call_count == 2

    def test_without_logger(self):
        html = "<div><p>Hello</p></div>"
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "Hello" in result

    def test_collapses_blank_lines(self):
        html = "<p>A</p>\n\n\n\n<p>B</p>"
        soup = BeautifulSoup(html, "html.parser")
        result = clean_html_output(soup)
        assert "\n\n" not in result

    def test_returns_string(self):
        soup = BeautifulSoup("<p>Test</p>", "html.parser")
        result = clean_html_output(soup)
        assert isinstance(result, str)

    def test_empty_input(self):
        soup = BeautifulSoup("", "html.parser")
        result = clean_html_output(soup)
        assert isinstance(result, str)


class TestConstantsFullCoverage:
    def test_attrs_to_remove_is_set(self):
        assert isinstance(ATTRS_TO_REMOVE, set)
        assert "data-sp-canvasdataversion" in ATTRS_TO_REMOVE
        assert "data-sp-webpartdata" in ATTRS_TO_REMOVE
        assert "aria-label" in ATTRS_TO_REMOVE

    def test_attr_prefixes_is_tuple(self):
        assert isinstance(ATTR_PREFIXES_TO_REMOVE, tuple)
        assert "data-sp-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-cke-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-pnp-" in ATTR_PREFIXES_TO_REMOVE
        assert "data-ms-" in ATTR_PREFIXES_TO_REMOVE
