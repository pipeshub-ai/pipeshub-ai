"""Independent QA probes for the SVG budget (SEC-1 round 2).

Each input is a few KB at most, passes the element/``<use>`` counts, and makes
cairosvg spend seconds that double with every extra level. They are sized to
take a few seconds on an unbudgeted renderer; at depth 30 they would not finish.
"""
from __future__ import annotations

import base64
import contextlib
import time

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.image_parser import svg_worker

NS = 'xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'


def _svg(body: str) -> str:
    return f'<svg {NS} width="200" height="200">{body}</svg>'


def _patterns(d: int) -> str:
    leaf = '<pattern id="q0" width="10" height="10" patternUnits="userSpaceOnUse"><rect width="5" height="5"/></pattern>'
    parts = [leaf] + [
        f'<pattern id="q{i}" width="10" height="10" patternUnits="userSpaceOnUse">'
        f'<rect width="5" height="5" fill="url(#q{i-1})"/><rect x="5" y="5" width="5" height="5" fill="url(#q{i-1})"/></pattern>'
        for i in range(1, d)
    ]
    parts.append(f'<rect width="200" height="200" fill="url(#q{d-1})"/>')
    return _svg("".join(parts))


def _masks(d: int) -> str:
    parts = ['<mask id="m0"><rect width="1" height="1" fill="white"/></mask>'] + [
        f'<mask id="m{i}"><rect width="100" height="100" fill="white" mask="url(#m{i-1})"/>'
        f'<rect width="100" height="100" fill="white" mask="url(#m{i-1})"/></mask>'
        for i in range(1, d)
    ]
    parts.append(f'<rect width="200" height="200" mask="url(#m{d-1})"/>')
    return _svg("".join(parts))


def _markers(d: int) -> str:
    parts = ['<marker id="k0" markerWidth="2" markerHeight="2"><circle r="1"/></marker>'] + [
        f'<marker id="k{i}" markerWidth="2" markerHeight="2"><path d="M0 0 L1 1 L2 2" '
        f'marker-start="url(#k{i-1})" marker-mid="url(#k{i-1})" marker-end="url(#k{i-1})"/></marker>'
        for i in range(1, d)
    ]
    parts.append(f'<path d="M0 0 L100 100 L200 0" marker-start="url(#k{d-1})" stroke="red"/>')
    return _svg("".join(parts))


def _padded_href_use(d: int) -> str:
    # cairosvg strips the href, so " #g" is followed; the budget reads it as no reference.
    parts = ['<g id="g0"><rect width="1" height="1"/></g>'] + [
        f'<g id="g{i}"><use href=" #g{i-1}"/><use href=" #g{i-1}"/></g>' for i in range(1, d)
    ]
    parts.append(f'<use href=" #g{d-1}"/>')
    return _svg("".join(parts))


def _many_uses(n: int) -> str:
    return _svg(
        '<defs><symbol id="s"><circle r="2" cx="2" cy="2"/></symbol></defs>'
        + "".join(f'<use href="#s" x="{i % 190}" y="{(i * 7) % 190}" width="4" height="4"/>' for i in range(n))
    )


CASES = {
    "pattern fan-out via fill url()": _patterns(12),
    "mask fan-out via mask url()": _masks(12),
    "marker fan-out via marker-* url()": _markers(10),
    "use with a padded href": _padded_href_use(12),
    "6000 uses of one symbol": _many_uses(6000),
}


DEADLINE = 2.0


@pytest.mark.parametrize("name", sorted(CASES))
def test_an_svg_under_the_counts_is_still_bounded(name: str) -> None:
    """These pass the static counts by design; only the renderer's deadline bounds
    them. The render runs in a child process, so this is wall-clock time: the
    parent's own CPU time would stay near zero however long the child ran."""
    svg = CASES[name]
    assert len(svg) < 1_000_000
    start = time.monotonic()
    with contextlib.suppress(DocumentProcessingError):
        svg_worker.run_bounded(base64.b64encode(svg.encode()).decode(), timeout=DEADLINE)
    assert time.monotonic() - start < DEADLINE + 1.0
