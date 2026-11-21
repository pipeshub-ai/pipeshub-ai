"""A hostile SVG from a document is refused quickly instead of being rasterised."""
from __future__ import annotations

import base64

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.modules.parsers.image_parser import svg_worker
from app.modules.parsers.image_parser.image_parser import ImageParser
from tests.support.cpu_budget import thread_cpu


def _b64(svg: str) -> str:
    return base64.b64encode(svg.encode()).decode()


def _use_chain(depth: int) -> str:
    parts = ['<g id="g0"><rect width="1" height="1"/></g>']
    parts.extend(
        f'<g id="g{i}"><use href="#g{i - 1}"/><use href="#g{i - 1}"/></g>' for i in range(1, depth)
    )
    parts.append(f'<use href="#g{depth - 1}"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">' + "".join(parts) + "</svg>"


GOOD_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="red"/></svg>'

HOSTILE = {
    "use chain": _use_chain(60),
    "entities": (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><svg xmlns="http://www.w3.org/2000/svg"><text>&c;</text></svg>'
    ),
    "deep nesting": '<svg xmlns="http://www.w3.org/2000/svg">' + "<g>" * 300 + "</g>" * 300 + "</svg>",
    "huge size": '<svg xmlns="http://www.w3.org/2000/svg" width="200000" height="200000"></svg>',
    "huge viewBox": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900000 900000"></svg>',
    "too many elements": '<svg xmlns="http://www.w3.org/2000/svg">' + "<g/>" * 60000 + "</svg>",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_svg_is_refused_quickly_by_the_static_checks(name: str) -> None:
    """The refusal itself, in this process: no renderer start-up in the timing.
    Half a second here; the bound is half the render deadline, so it still shows
    the refusal comes long before a render could, on a slow shared runner too."""
    with thread_cpu() as cpu, pytest.raises(DocumentProcessingError):
        ImageParser._svg_to_png_in_process(_b64(HOSTILE[name]))
    assert cpu.seconds < 5.0


def test_refusing_too_many_elements_costs_the_same_however_many_there_are() -> None:
    """Counting stops at the limit, so twenty times the elements is not twenty
    times the work."""

    def refuse(n: int) -> float:
        svg = _b64('<svg xmlns="http://www.w3.org/2000/svg">' + "<g/>" * n + "</svg>")
        best = float("inf")
        for _ in range(3):
            with thread_cpu() as cpu, pytest.raises(DocumentProcessingError):
                ImageParser._svg_to_png_in_process(svg)
            best = min(best, cpu.seconds)
        return best

    at_limit, far_past = refuse(60_000), refuse(1_200_000)
    assert far_past < max(at_limit, 0.05) * 3, (at_limit, far_past)


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_svg_is_refused_through_the_renderer(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # The refusal happens in the child, so it is told by its path, not its duration: the renderer's static
    # checks answer with their own reason and the renderer stays; a render that ran into the deadline is
    # "timed out" and kills it.
    pool = svg_worker._Renderers()
    monkeypatch.setattr(svg_worker, "_renderers", pool)
    try:
        ImageParser.svg_base64_to_png_base64(_b64(GOOD_SVG))
        (warm,) = pool._idle
        with pytest.raises(DocumentProcessingError) as in_process:
            ImageParser._svg_to_png_in_process(_b64(HOSTILE[name]))
        with pytest.raises(DocumentProcessingError) as through_renderer:
            ImageParser.svg_base64_to_png_base64(_b64(HOSTILE[name]))
        assert str(through_renderer.value) == f"DocumentProcessingError: {in_process.value}"
        assert pool._idle == [warm] and warm.alive()
    finally:
        pool.close_all()


def test_an_oversized_svg_is_refused_before_it_is_parsed() -> None:
    big = '<svg xmlns="http://www.w3.org/2000/svg"><!--' + "x" * (6 * 1024 * 1024) + "--></svg>"
    with pytest.raises(DocumentProcessingError):
        ImageParser.svg_base64_to_png_base64(_b64(big))


def test_an_ordinary_svg_still_rasterises() -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="red"/></svg>'
    assert ImageParser.svg_base64_to_png_base64(_b64(svg))


@pytest.mark.asyncio
async def test_a_hostile_data_uri_gives_no_image_through_the_url_path() -> None:
    uri = "data:image/svg+xml;base64," + _b64(HOSTILE["use chain"])
    assert await ImageParser.urls_to_base64([uri], allow_remote_fetch=True) == [None]
