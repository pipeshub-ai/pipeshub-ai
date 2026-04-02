"""Unit tests for app.modules.parsers.markdown.mdx_parser — MDX-to-Markdown conversion."""

import pytest

from app.modules.parsers.markdown.mdx_parser import MDXParser


@pytest.fixture
def parser():
    return MDXParser()


# ---------------------------------------------------------------------------
# Constructor / initialization
# ---------------------------------------------------------------------------

class TestMDXParserInit:
    def test_allowed_tags(self, parser):
        assert "CodeGroup" in parser.allowed_tags
        assert "Info" in parser.allowed_tags
        assert "AccordionGroup" in parser.allowed_tags
        assert "Accordion" in parser.allowed_tags

    def test_patterns_are_strings(self, parser):
        assert isinstance(parser.jsx_block_pattern, str)
        assert isinstance(parser.self_closing_pattern, str)
        assert isinstance(parser.orphan_closing_tag_pattern, str)
        assert isinstance(parser.extra_newlines_pattern, str)
        assert isinstance(parser.accordion_tag_with_title_pattern, str)


# ---------------------------------------------------------------------------
# Accordion handling
# ---------------------------------------------------------------------------

class TestAccordionHandling:
    def test_accordion_with_title_converted_to_heading(self, parser):
        mdx = b'<Accordion title="My Section">Some content here</Accordion>'
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "### My Section" in text
        assert "Some content here" in text
        # The JSX tag itself should be removed
        assert "<Accordion" not in text
        assert "</Accordion>" not in text

    def test_accordion_with_single_quotes(self, parser):
        mdx = b"<Accordion title='Title Here'>Body text</Accordion>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "### Title Here" in text
        assert "Body text" in text

    def test_accordion_multiline_content(self, parser):
        mdx = b"<Accordion title=\"Multi\">\nLine 1\nLine 2\n</Accordion>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "### Multi" in text
        assert "Line 1" in text
        assert "Line 2" in text


# ---------------------------------------------------------------------------
# Info tag handling
# ---------------------------------------------------------------------------

class TestInfoTagHandling:
    def test_info_tag_converted_to_blockquote(self, parser):
        mdx = b"<Info>Important note</Info>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "> **Note:**" in text
        assert "Important note" in text
        assert "<Info>" not in text

    def test_info_tag_multiline(self, parser):
        mdx = b"<Info>Line one\nLine two</Info>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "> **Note:**" in text


# ---------------------------------------------------------------------------
# CodeGroup tag handling
# ---------------------------------------------------------------------------

class TestCodeGroupHandling:
    def test_code_group_preserves_inner_content(self, parser):
        mdx = b"<CodeGroup>```python\nprint('hi')\n```</CodeGroup>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "print('hi')" in text
        assert "<CodeGroup>" not in text

    def test_code_group_with_multiple_blocks(self, parser):
        mdx = b"<CodeGroup>```js\nconsole.log('a')\n```\n```py\nprint('b')\n```</CodeGroup>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "console.log('a')" in text
        assert "print('b')" in text


# ---------------------------------------------------------------------------
# AccordionGroup tag handling
# ---------------------------------------------------------------------------

class TestAccordionGroupHandling:
    def test_accordion_group_preserves_inner_content(self, parser):
        mdx = b"<AccordionGroup>Inner group content</AccordionGroup>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "Inner group content" in text
        assert "<AccordionGroup>" not in text


# ---------------------------------------------------------------------------
# Unknown / non-allowed JSX tag removal
# ---------------------------------------------------------------------------

class TestUnknownTagRemoval:
    def test_unknown_block_tag_removed(self, parser):
        mdx = b"<CustomComponent>stuff</CustomComponent>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "<CustomComponent>" not in text
        assert "stuff" not in text  # Content of unknown tags is stripped

    def test_self_closing_tag_removed(self, parser):
        mdx = b"Some text <MyWidget prop='val' /> more text"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "<MyWidget" not in text
        assert "/>" not in text
        assert "Some text" in text
        assert "more text" in text

    def test_orphan_closing_tag_removed(self, parser):
        mdx = b"text before </SomeTag> text after"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "</SomeTag>" not in text
        assert "text before" in text
        assert "text after" in text


# ---------------------------------------------------------------------------
# Whitespace cleanup
# ---------------------------------------------------------------------------

class TestWhitespaceCleanup:
    def test_extra_newlines_collapsed(self, parser):
        mdx = b"Hello\n\n\n\n\nWorld"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        # Multiple blank lines should be collapsed to at most 2 newlines
        assert "\n\n\n" not in text
        assert "Hello" in text
        assert "World" in text

    def test_leading_trailing_whitespace_stripped(self, parser):
        mdx = b"   \n\n  Content here  \n\n  "
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert text == "Content here"


# ---------------------------------------------------------------------------
# Mixed content
# ---------------------------------------------------------------------------

class TestMixedContent:
    def test_regular_markdown_preserved(self, parser):
        mdx = b"# Heading\n\nParagraph text.\n\n- List item 1\n- List item 2"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "# Heading" in text
        assert "Paragraph text." in text
        assert "- List item 1" in text

    def test_mixed_mdx_and_markdown(self, parser):
        mdx = (
            b"# Title\n\n"
            b"<Info>A note</Info>\n\n"
            b"Regular paragraph.\n\n"
            b"<SelfClose />\n\n"
            b"End."
        )
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "# Title" in text
        assert "> **Note:**" in text
        assert "A note" in text
        assert "Regular paragraph." in text
        assert "<SelfClose" not in text
        assert "End." in text

    def test_empty_input(self, parser):
        result = parser.convert_mdx_to_md(b"")
        assert result == b""

    def test_plain_text_passthrough(self, parser):
        mdx = b"Just plain text, no MDX at all."
        result = parser.convert_mdx_to_md(mdx)
        assert result == b"Just plain text, no MDX at all."


# ---------------------------------------------------------------------------
# Default passthrough branch in handle_allowed_tags
# ---------------------------------------------------------------------------

class TestDefaultPassthrough:
    def test_accordion_in_allowed_tags_default_branch(self, parser):
        """Accordion tags without title attr are handled by the block JSX regex.
        Since Accordion is in allowed_tags but not Info/CodeGroup/AccordionGroup,
        it falls to the default passthrough."""
        # First, the accordion_tag_with_title_pattern handles titled accordions.
        # A bare <Accordion> without title goes through handle_allowed_tags
        # and hits the default return (just inner content).
        mdx = b"<Accordion>Plain accordion content</Accordion>"
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "Plain accordion content" in text


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_non_utf8_input_raises(self, parser):
        """Non-decodable bytes should raise an Exception."""
        bad_bytes = b"\xff\xfe"  # Not valid UTF-8
        with pytest.raises(Exception, match="Error converting MDX to Markdown"):
            parser.convert_mdx_to_md(bad_bytes)

    def test_error_message_includes_cause(self, parser):
        """The raised exception should include the original error message."""
        bad_bytes = b"\xff\xfe"
        with pytest.raises(Exception) as exc_info:
            parser.convert_mdx_to_md(bad_bytes)
        assert "Error converting MDX to Markdown" in str(exc_info.value)

    def test_error_has_chained_cause(self, parser):
        """The raised exception should have a __cause__ (from ... syntax)."""
        bad_bytes = b"\xff\xfe"
        with pytest.raises(Exception) as exc_info:
            parser.convert_mdx_to_md(bad_bytes)
        assert exc_info.value.__cause__ is not None


# ---------------------------------------------------------------------------
# UTF-8 encoding round-trip
# ---------------------------------------------------------------------------

class TestUTF8Encoding:
    def test_unicode_content_preserved(self, parser):
        mdx = "Unicode: \u00e9\u00e8\u00ea \u00fc\u00f6\u00e4 \u4e16\u754c".encode("utf-8")
        result = parser.convert_mdx_to_md(mdx)
        text = result.decode("utf-8")
        assert "\u00e9\u00e8\u00ea" in text
        assert "\u4e16\u754c" in text

    def test_output_is_bytes(self, parser):
        result = parser.convert_mdx_to_md(b"some text")
        assert isinstance(result, bytes)
