"""The image sources of an HTML document, as the HTML parsers read them.

One function decides what an ``<img>`` points at, so a check made before storing
a document and the extraction made when it is indexed cannot disagree about
which URLs are in it. Only ``bs4`` is imported, so a worker process can use it
without loading the HTML converter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bs4.element import Tag


def img_source(img_tag: Tag) -> str:
    """The ``src`` of an ``<img>``, else the first ``srcset`` entry; empty when it has neither."""
    src = img_tag.get("src", "").strip()
    if not src:
        srcset = img_tag.get("srcset", "")
        if srcset:
            first_part = srcset.split(",")[0].split()
            if first_part:
                src = first_part[0].strip()
    return src


def iter_img_sources(html_content: str) -> Iterator[str]:
    """The source of every ``<img>`` of ``html_content``, in order, empty ones included."""
    for img_tag in BeautifulSoup(html_content, "html.parser").find_all("img"):
        yield img_source(img_tag)
