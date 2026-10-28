"""SVGs from documents are rasterised in a process that can be stopped."""
from __future__ import annotations

import base64
import time

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers import parse_pool
from app.modules.parsers.image_parser import svg_worker
from app.modules.parsers.image_parser.image_parser import ImageParser, check_svg_budget

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
GOOD = f'<svg {NS} width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'


def _b64(svg: str) -> str:
    return base64.b64encode(svg.encode()).decode()


def _slow_but_countable(n: int) -> str:
    # n uses of one symbol: well inside every static count, but cairosvg is slow on it.
    return (
        f'<svg {NS} width="200" height="200"><defs><symbol id="s"><circle r="2"/></symbol></defs>'
        + "".join(f'<use href="#s" x="{i % 190}" y="{i % 97}" width="4" height="4"/>' for i in range(n))
        + "</svg>"
    )


def test_an_ordinary_svg_renders_in_the_child_process() -> None:
    assert ImageParser.svg_base64_to_png_base64(_b64(GOOD)).startswith("iVBOR")


def test_a_child_that_runs_past_its_deadline_is_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svg_worker, "SVG_DEADLINE_SECONDS", 1.0)
    start = time.monotonic()
    with pytest.raises(DocumentProcessingError, match="timed out"):
        svg_worker.run_bounded(_b64(_slow_but_countable(40000)), timeout=1.0)
    assert time.monotonic() - start < 4.0


def test_the_memory_cap_stops_an_allocation_and_is_lifted_afterwards() -> None:
    import resource

    before = resource.getrlimit(resource.RLIMIT_AS)
    with svg_worker.memory_cap(extra_bytes=16 * 1024 * 1024), pytest.raises(MemoryError):
        bytearray(512 * 1024 * 1024)
    assert resource.getrlimit(resource.RLIMIT_AS) == before
    assert len(bytearray(64 * 1024 * 1024)) > 0


@pytest.mark.parametrize(
    "svg",
    [
        f'<svg {NS}><g id="a"><use href="  #b "/></g><g id="b"><use xlink:href=" #a"/></g></svg>',
        f'<svg {NS}><pattern id="p"><rect fill="url( #p )"/></pattern></svg>',
        "<svg><g></svg>",
        f'<svg {NS}><use xlink:href="#a"/></svg>'.replace(NS, 'xmlns="http://www.w3.org/2000/svg"'),
    ],
)
def test_the_budget_counts_padded_references_and_refuses_what_it_cannot_parse(svg: str) -> None:
    with pytest.raises(DocumentProcessingError):
        check_svg_budget(svg)


def test_a_padded_href_chain_is_counted() -> None:
    parts = ['<g id="g0"><rect width="1" height="1"/></g>'] + [
        f'<g id="g{i}"><use href=" #g{i - 1}"/><use href=" #g{i - 1}"/></g>' for i in range(1, 30)
    ]
    with pytest.raises(DocumentProcessingError, match="too much"):
        check_svg_budget(f'<svg {NS}>' + "".join(parts) + '<use href="#g29"/></svg>')


@pytest.mark.parametrize("attr", ["fill", "mask", "marker-start", "clip-path", "filter"])
def test_a_url_reference_chain_is_counted(attr: str) -> None:
    parts = ['<g id="q0"><rect width="1" height="1"/></g>'] + [
        f'<g id="q{i}"><rect {attr}="url(#q{i - 1})"/><rect {attr}="url(#q{i - 1})"/></g>' for i in range(1, 30)
    ]
    with pytest.raises(DocumentProcessingError, match="too much"):
        check_svg_budget(f'<svg {NS}>' + "".join(parts) + f'<rect {attr}="url(#q29)"/></svg>')


@pytest.mark.asyncio
async def test_without_a_pool_the_async_path_uses_a_bounded_child() -> None:
    assert parse_pool.parse_pool_workers() == 0
    assert (await ImageParser.svg_base64_to_png_base64_async(_b64(GOOD))).startswith("iVBOR")
    with pytest.raises(DocumentProcessingError):
        await ImageParser.svg_base64_to_png_base64_async(_b64("<svg><g></svg>"))


@pytest.mark.asyncio
async def test_a_hostile_data_uri_is_skipped_and_the_rest_still_render() -> None:
    hostile = "data:image/svg+xml;base64," + _b64(f'<svg {NS}><g id="a"><use href=" #a"/></g></svg>')
    good = "data:image/svg+xml;base64," + _b64(GOOD)
    result = await ImageParser.urls_to_base64([hostile, good])
    assert result[0] is None
    assert result[1] is not None
    assert result[1].startswith("data:image/png;base64,")


REAL_WORLD = {
    "icon with gradient and clip": (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24"><defs>'
        '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f00"/>'
        '<stop offset="1" stop-color="#00f"/></linearGradient><clipPath id="c"><circle cx="12" cy="12" r="10"/></clipPath>'
        '</defs><rect width="24" height="24" fill="url(#g)" clip-path="url(#c)"/></svg>'
    ),
    "sprite symbols with an undeclared xlink prefix": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="16"><defs><symbol id="dot" viewBox="0 0 8 8">'
        '<circle cx="4" cy="4" r="3"/></symbol></defs>'
        + "".join(f'<use xlink:href="#dot" x="{i * 16}" width="16" height="16"/>' for i in range(3))
        + "</svg>"
    ),
    "chart with text and many marks": (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200"><style>.t{font:10px sans-serif}</style>'
        + "".join(f'<rect x="{i * 6}" y="{100 - i}" width="4" height="{i}" fill="#369"/>' for i in range(50))
        + '<text class="t" x="10" y="190">axis</text></svg>'
    ),
}


@pytest.mark.parametrize("name", sorted(REAL_WORLD))
def test_ordinary_svgs_still_render(name: str) -> None:
    assert ImageParser.svg_base64_to_png_base64(_b64(REAL_WORLD[name])).startswith("iVBOR")
