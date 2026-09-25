"""Markdown and plain-text files are indexed with their text exactly as written."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.blocks import BlocksContainer, BlockSubType, BlockType
from app.modules.parsers.markdown.docling_markdown_parser import (
    _extract_and_replace_images,
)
from app.modules.parsers.markdown.markdown_it_parser import MarkdownItParser

from .samples import all_text

SOURCE = """> Never skip backups.

R&D budget: a < b and c > d.

```java
List<String> names = new ArrayList<>();
if (a && b) { run(); }
```

See <https://example.com/docs> for more.
"""


async def _parse(content: str, name: str = "notes.md") -> BlocksContainer:
    return (await MarkdownItParser().parse(content.encode(), name)).block_container


async def test_code_symbols_and_links_are_kept_verbatim() -> None:
    container = await _parse(SOURCE)
    code = next(b for b in container.blocks if b.sub_type == BlockSubType.CODE)
    assert code.data == "List<String> names = new ArrayList<>();\nif (a && b) { run(); }"
    text = all_text(container)
    assert "R&D budget: a < b and c > d." in text
    assert "<https://example.com/docs>" in text
    assert "</https:>" not in text and "</string>" not in text


async def test_blockquote_is_still_a_quote() -> None:
    container = await _parse(SOURCE)
    assert any(b.sub_type == BlockSubType.QUOTE and "Never skip backups." in b.data for b in container.blocks)


async def test_plain_text_with_angle_brackets_is_untouched() -> None:
    text = "Replace <customer name> with the account owner.\nUse x -> y && y -> z.\n"
    container = await _parse(text, "howto.txt")
    assert all_text(container) == text.strip()


def test_image_extraction_leaves_text_without_images_unchanged() -> None:
    modified, images = _extract_and_replace_images(SOURCE)
    assert modified == SOURCE
    assert images == []


async def test_html_and_markdown_images_are_still_labelled_and_resolved() -> None:
    md = (
        'Intro <img src="https://example.com/a.png" alt="diagram"> text & more\n\n'
        "![chart](https://example.com/b.png)\n"
    )
    modified, images = _extract_and_replace_images(md)
    assert [(i["url"], i["new_alt_text"]) for i in images] == [
        ("https://example.com/b.png", "Image_1"),
        ("https://example.com/a.png", "Image_2"),
    ]
    assert "text & more" in modified and 'alt="Image_2"' in modified

    data_uris = ["data:image/png;base64,QUJD", "data:image/png;base64,REVG"]
    with patch(
        "app.modules.parsers.markdown.markdown_it_parser.ImageParser.urls_to_base64",
        AsyncMock(return_value=data_uris),
    ):
        container = (await MarkdownItParser().parse(md.encode(), "doc.md")).block_container
    image_uris = sorted(b.data["uri"] for b in container.blocks if b.type == BlockType.IMAGE)
    assert image_uris == sorted(data_uris)
    assert "text & more" in all_text(container)


@pytest.mark.parametrize(
    "separator",
    ["\x0c", " ", " ", "\x85", "\x0b", "\x1c"],
    ids=["form-feed", "line-separator", "paragraph-separator", "next-line", "vertical-tab", "file-separator"],
)
async def test_unusual_line_breaks_do_not_drop_lists_or_quotes(separator: str) -> None:
    # Text exported from PDFs and old systems carries page breaks and Unicode
    # line separators inside paragraphs.
    text = (
        f"Page one ends here.{separator}Page two starts here.\n\n"
        "- alpha item\n- beta item\n- gamma item\n\n"
        "> quoted wisdom\n\n"
        "Closing line.\n"
    )
    container = await _parse(text, "export.txt")
    items = [b.data for b in container.blocks if b.sub_type == BlockSubType.LIST_ITEM]
    assert items == ["alpha item", "beta item", "gamma item"]
    quotes = [b.data for b in container.blocks if b.sub_type == BlockSubType.QUOTE]
    assert quotes == ["> quoted wisdom"]
    assert "Closing line." in all_text(container)


def test_malformed_img_tag_is_left_as_written() -> None:
    md = 'Broken <img<b> tag and a real <img src="https://example.com/c.png"> one.'
    modified, images = _extract_and_replace_images(md)
    assert "Broken <img<b> tag" in modified
    assert [i["url"] for i in images] == ["https://example.com/c.png"]


def test_unclosed_img_does_not_swallow_the_following_prose() -> None:
    md = 'See <img src="a.png" for details, and note that a > b.\n\nNext paragraph.'
    modified, images = _extract_and_replace_images(md)
    assert modified == md
    assert images == []


def test_greater_than_inside_a_quoted_alt_still_relabels_the_image() -> None:
    md = 'Before <img alt="revenue > cost" src="https://example.com/c.png"> after.'
    modified, images = _extract_and_replace_images(md)
    assert [(i["url"], i["alt_text"]) for i in images] == [("https://example.com/c.png", "revenue > cost")]
    assert modified.startswith("Before <img ") and modified.endswith(" after.")
    assert 'alt="Image_1"' in modified and "revenue" not in modified


@pytest.mark.parametrize(
    "tag",
    ["<IMG SRC='https://example.com/d.png'>", '<img src="https://example.com/d.png"/>',
     "<img\n  src=https://example.com/d.png\n  alt=chart>"],
    ids=["uppercase-single-quotes", "self-closing", "multi-line-unquoted"],
)
def test_valid_img_tag_forms_are_relabelled(tag: str) -> None:
    modified, images = _extract_and_replace_images(f"x {tag} y")
    assert [i["url"] for i in images] == ["https://example.com/d.png"]
    assert modified.startswith("x <img") and modified.endswith(" y")
