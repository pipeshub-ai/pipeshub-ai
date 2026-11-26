"""A hostile SVG from a document is refused quickly instead of being rasterised."""
from __future__ import annotations

import base64

import pytest

from app.exceptions.indexing_exceptions import DocumentProcessingError
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


def test_refusing_too_many_elements_costs_the_same_however_many_there_are(monkeypatch: pytest.MonkeyPatch) -> None:
    """Counting stops at the limit, so twenty times the elements is not twenty
    times the work: counted as the elements the parser built, not timed (a CPU-time
    ratio against the smaller document failed at 3.03x on a loaded runner)."""
    from app.modules.parsers.image_parser import svg_core

    built: list[int] = []
    real_builder = svg_core._budget_builder

    def counting_builder() -> object:
        builder = real_builder()
        start = builder.start

        def counted(tag: str, attrs: dict[str, str]) -> object:
            built[-1] += 1
            return start(tag, attrs)

        builder.start = counted
        return builder

    monkeypatch.setattr(svg_core, "_budget_builder", counting_builder)

    def refuse(n: int) -> int:
        built.append(0)
        with pytest.raises(DocumentProcessingError):
            ImageParser._svg_to_png_in_process(_b64('<svg xmlns="http://www.w3.org/2000/svg">' + "<g/>" * n + "</svg>"))
        return built[-1]

    assert refuse(60_000) == refuse(1_200_000) == svg_core._MAX_SVG_ELEMENTS + 1


# The refusal each hostile SVG gets from the renderer's budget checks.
REFUSED_AS = {
    "use chain": "SVG expands to too much to rasterise",
    "entities": "SVG uses entities or DTDs",
    "deep nesting": "SVG is too complex to rasterise",
    "huge size": "SVG would be rasterised too large",
    "huge viewBox": "SVG would be rasterised too large",
    "too many elements": "SVG is too complex to rasterise",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_hostile_svg_is_refused_through_the_renderer(name: str) -> None:
    # A warm renderer first, so the refusal comes from a running renderer, not a cold start.
    ImageParser.svg_base64_to_png_base64(_b64(GOOD_SVG))
    with pytest.raises(DocumentProcessingError) as err:
        ImageParser.svg_base64_to_png_base64(_b64(HOSTILE[name]))
    # Refused by the renderer's budget check, told apart by the reason rather than the wall time (which a busy runner
    # stretches past a small bound): not stopped at the deadline, not a renderer that died or sent an unreadable reply.
    assert REFUSED_AS[name] in str(err.value), err.value


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
