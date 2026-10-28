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
