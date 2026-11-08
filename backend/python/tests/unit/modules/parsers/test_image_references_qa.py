"""Independent QA probes for ``image_references`` (SEC-1)."""
from __future__ import annotations

import pytest

from app.modules.parsers.markdown.image_references import extract_and_replace_images


@pytest.mark.parametrize(
    "md",
    [
        "![a](\n\nhttp://h/x.png)",
        "![a](\r\n\r\nhttp://h/x.png)",
        "![a](http://h/x.png\n\n)",
        "![a](http://h/x.png \"t\"\n\n)",
    ],
)
def test_a_blank_line_inside_the_parentheses_is_not_an_inline_image(md: str) -> None:
    # CommonMark ends the paragraph at a blank line, so markdown-it renders no
    # image here; reporting one makes the pipeline fetch a URL the author never
    # embedded.
    _, images = extract_and_replace_images(md)
    assert [i["url"] for i in images] == []


@pytest.mark.parametrize(
    "md",
    ["<img>", "<IMG>", "text <img> more", "<p><img></p>", "<img>\n", "<img><img>", "<img>src=x"],
)
def test_a_bare_img_tag_does_not_raise(md: str) -> None:
    # `<img>` (no attributes) is the most common bare tag; the scanner must
    # handle it as it did before the slash-separator change.
    modified, images = extract_and_replace_images(md)
    assert isinstance(modified, str)
    assert all(i["url"] == "" for i in images)
