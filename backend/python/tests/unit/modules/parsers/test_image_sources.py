"""``iter_img_sources``: what an ``<img>`` points at, for the check at push time and the extraction at index time alike."""

from __future__ import annotations

import pytest

from app.modules.parsers.html_parser.image_sources import iter_img_sources
from app.modules.parsers.html_parser.selectolax_html_parser import SelectolaxHtmlParser

PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="


@pytest.mark.parametrize(
    ("html", "sources"),
    [
        ('<img src="a.png"><p>x</p><img src=" b.png ">', ["a.png", "b.png"]),
        ('<img srcset="c.png 1x, d.png 2x">', ["c.png"]),
        ('<img src="" srcset="e.png">', ["e.png"]),
        ('<img src="f.png" srcset="g.png">', ["f.png"]),
        ("<img alt='none'>", [""]),
        ("<IMG SRC=h.png>", ["h.png"]),
        ('<img src="i.png"junk><img/ src="j.png">', ["i.png", "j.png"]),
        ("<p>no images</p>", []),
    ],
)
def test_the_source_of_every_img_in_order(html: str, sources: list[str]) -> None:
    assert list(iter_img_sources(html)) == sources


def test_the_extractor_reports_exactly_the_sources_that_are_not_inline_raster_images() -> None:
    html = f'<img src="a.png"><img src="{PIXEL}"><img srcset="b.png 1x"><img alt="x"><img src="data:image/svg+xml;base64,PHN2Zy8+">'
    reported = [image["url"] for image in SelectolaxHtmlParser().extract_and_replace_images(html)[1]]
    assert reported == [s for s in iter_img_sources(html) if s and not s.startswith(PIXEL[:11] + "png")]
