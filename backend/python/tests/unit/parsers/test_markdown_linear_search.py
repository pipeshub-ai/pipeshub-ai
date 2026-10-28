"""The image searches must match what the plain regexes match, in linear time."""
from __future__ import annotations

import functools
import gzip
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers.markdown import linear_image_search as lis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# The Markdown image pattern is checked against a frozen record of what the
# original exponential regex returned (see ``_oracle``); it is no longer
# compiled here. The HTML pattern is compared against its live reference.
REFERENCE = {
    "MD_IMAGE_NESTED": (lis.MD_IMAGE_NESTED, None),
    "HTML_IMG_ALT": (
        lis.HTML_IMG_ALT,
        re.compile(r'<img\s[^>]*alt=["\']([^"\']*)["\'][^>]*>', re.IGNORECASE),
    ),
}

PIECES = [
    "![", "[", "]", "](", ")", "(", " ", "  ", '"', "'", "a", "alt", "alt=", "data:", "<img ",
    "<IMG\t", ">", "\n", "x", ":", "<![CDATA[", "][", "]:", "t", "<!--", "\\",
]


ORACLE_PATH = Path(__file__).parent / "fixtures" / "md_image_nested_oracle.json.gz"


@functools.lru_cache(maxsize=1)
def _oracle() -> dict:
    with gzip.open(ORACLE_PATH, "rt", encoding="utf-8") as f:
        return json.load(f)


def _enc(m: re.Match[str] | None) -> list | None:
    return m and [m.start(), m.end(), list(m.groups())]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _same(name: str, text: str, want: dict | None = None) -> None:
    """Compare finditer and search at fixed offsets; ``want`` is the frozen MD record."""
    ours, ref = REFERENCE[name]
    got = [_enc(m) for m in ours.finditer(text)]
    offsets = range(0, min(len(text), 40), 7)
    got_search = [_enc(ours.search(text, pos)) for pos in offsets]
    if ref is None:
        assert want is not None and want["sha"] == _sha(text), (name, text[:200])
        want_find, want_search = want["finditer"], want["search"]
    else:
        want_find = [_enc(m) for m in ref.finditer(text)]
        want_search = [_enc(ref.search(text, pos)) for pos in offsets]
    assert got == want_find, (name, text[:200])
    assert got_search == want_search, (name, text[:200])


def _frozen(name: str, record: dict | None) -> dict | None:
    return record if REFERENCE[name][1] is None else None


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_parity_on_tracked_markdown_files(name: str) -> None:
    # The corpus is frozen in the fixture, so it cannot drift as docs change.
    for rec in _oracle()["files"]:
        _same(name, rec["text"], _frozen(name, rec))


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_parity_on_seeded_random_input(name: str) -> None:
    rng = random.Random(20260428)
    seeded = _oracle()["seeded"]
    for i in range(4000):
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 40)))
        _same(name, text, _frozen(name, seeded[i]))


def test_parity_on_realistic_images() -> None:
    rec = _oracle()["realistic"]
    text = (
        'a ![alt [x] y](http://u/p.png "t") b ![](data:image/png;base64,AAA "t" ) '
        '![r][id] <img src="a" alt="A > B"> <IMG ALT=\'q\' src=x>\n[id]: http://x "T"\n'
    )
    assert text == rec["text"]
    for name in REFERENCE:
        _same(name, text, _frozen(name, rec))


# Each family is a repeated opener that used to cost O(n^2) in the regex.
FAMILIES = [
    "<![CDATA[", "![", "![a", "![a[", "![x[y]", "![a](", "![a](x", "![a](x ", "![a](data:x ",
    '![a](x "', '![a](data:x   "t" ) ', '![a](data: "t" ) ', '![a](x    "t" ) ', '![a](data:"q   "t" ) ', "![a][", "[a\n", "<img ", '<img alt="', '<img alt="x" ',
    # A failing opener followed, later in the text, by a valid image.
    "![ ![x](u)", "![ ![x](u) ", "<img <img alt='a'>", "![a](data:x ![b](u)",
]

# Repeated opener plus a tail that looks like a title but ends ``" )``, so the
# ``data:`` destination cannot match from any opener.
TAILED_FAMILIES = [
    ("![a](data:", ' "t" )'), ("![a](data:x", ' "t" )'), ('![a](data:"q', ' "t" )'),
    ("![a](data:  ", '"t" )'), ("![a](data:", '"t"  )'),
]


def _cpu(fn, text: str) -> float:
    start = time.process_time()
    fn(text)
    return time.process_time() - start


@pytest.mark.parametrize("name", sorted(REFERENCE))
@pytest.mark.parametrize("unit", FAMILIES)
def test_doubling_the_input_does_not_quadruple_the_time(name: str, unit: str) -> None:
    ours = REFERENCE[name][0]
    small, large = unit * (24_000 // len(unit)), unit * (96_000 // len(unit))
    run = lambda t: list(ours.finditer(t))  # noqa: E731
    # Best of three, so one scheduling hiccup cannot fail the ratio.
    t_small = min(_cpu(run, small) for _ in range(3))
    t_large = min(_cpu(run, large) for _ in range(3))
    # Four times the input: linear is 4x and quadratic 16x. A 2x step left too
    # little room between them on a busy two-core runner (2.6x measured once).
    assert t_large <= max(t_small, 0.004) * 8, (name, unit, t_small, t_large)
    # The ratio alone can miss a pattern that is already slow at the small size.
    assert t_large < 1.5, (name, unit, t_small, t_large)


@pytest.mark.parametrize("unit,tail", TAILED_FAMILIES)
def test_data_destination_with_unmatched_title_tail_is_linear(unit: str, tail: str) -> None:
    ours = lis.MD_IMAGE_NESTED
    small, large = unit * (24_000 // len(unit)) + tail, unit * (96_000 // len(unit)) + tail
    run = lambda t: list(ours.finditer(t))  # noqa: E731
    t_small = min(_cpu(run, small) for _ in range(3))
    t_large = min(_cpu(run, large) for _ in range(3))
    assert t_large <= max(t_small, 0.004) * 8, (unit, t_small, t_large)
    assert t_large < 1.5, (unit, t_small, t_large)


def test_one_scan_is_shared_by_searches_over_the_same_text() -> None:
    # ``search`` called in a loop (as the segment splitter does) must not lose
    # its caches between matches.
    ours = lis.MD_IMAGE_NESTED
    text = "![ ![x](u)" * 3000
    def loop(t: str) -> None:
        scan, pos = ours.scan(t), 0
        while (m := ours.search(t, pos, scan)) is not None:
            pos = m.end()
    small, large = text, text * 4
    t_small = min(_cpu(loop, small) for _ in range(3))
    t_large = min(_cpu(loop, large) for _ in range(3))
    assert t_large <= max(t_small, 0.004) * 8, (t_small, t_large)


class _SlowMarkdownParser:
    def __init__(self, **_: object) -> None: ...

    def extract_and_replace_images(self, text: str) -> tuple[str, list]:
        time.sleep(0.4)  # stands in for CPU work that holds the GIL
        return text, []


async def _worst_loop_gap(coro_factory: Callable[[], Awaitable[None]]) -> float:
    import asyncio

    worst = 0.0
    stop = False

    async def heartbeat() -> None:
        nonlocal worst
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    task = asyncio.create_task(heartbeat())  # test-local task, awaited below
    await asyncio.sleep(0.05)
    await coro_factory()
    stop = True
    await task
    return worst


async def test_large_markdown_image_extraction_does_not_block_the_event_loop(monkeypatch) -> None:
    import logging

    from app.modules.parsers.blocks import blocks_parser

    monkeypatch.setattr(blocks_parser, "MarkdownParser", _SlowMarkdownParser)
    monkeypatch.setattr(blocks_parser, "ImageParser", lambda *_a, **_k: object())
    parser = object.__new__(blocks_parser.BlocksParser)
    parser.logger = logging.getLogger("test")
    parser.config_service = None
    worst = await _worst_loop_gap(lambda: parser._process_blockgroup_images("x" * (300 * 1024)))
    assert worst < 0.2, worst


class _SlowHtmlParser:
    def clean_html(self, html: str) -> str:
        return html

    def extract_and_replace_images(self, html: str) -> tuple[str, list]:
        time.sleep(0.4)
        return html, []

    async def parse_to_blocks(self, *_a: object, **_k: object) -> None:
        raise _Stop


class _Stop(Exception):
    pass


async def test_large_html_image_extraction_does_not_block_the_event_loop() -> None:
    import logging
    from types import SimpleNamespace

    from app.modules.parsers.blocks import blocks_parser

    parser = object.__new__(blocks_parser.BlocksParser)
    parser.logger = logging.getLogger("test")
    parser.config_service = None
    group = SimpleNamespace(data="x" * (300 * 1024), index=0, name="g")

    async def run() -> None:
        with pytest.raises(_Stop):
            await parser._process_single_blockgroup_html(group, "r", _SlowHtmlParser())

    worst = await _worst_loop_gap(run)
    assert worst < 0.2, worst


WS_PIECES = [
    "![a](data:", "![a](", " ", " ", "\t", "\n", '"t"', '"', "x", ")", "(", "]", "[", "![", "data:",
    '"t" )', "a)", ' "t"',
]


@pytest.mark.parametrize("name", ["MD_IMAGE_NESTED"])
def test_parity_with_whitespace_runs_before_titles_and_inside_data_uris(name: str) -> None:
    rng = random.Random(77)
    recorded = {r["index"]: r for r in _oracle()["whitespace"]}
    seen = 0
    for i in range(20000):
        text = "".join(rng.choice(WS_PIECES) for _ in range(rng.randint(1, 14)))
        if len(re.findall(r"\s", text)) > 16:
            assert i not in recorded, i
            continue
        _same(name, text, recorded[i])
        seen += 1
    assert seen == len(recorded)


def test_oracle_fixture_covers_every_parity_input() -> None:
    data = _oracle()
    assert len(data["files"]) == 231
    assert len(data["seeded"]) == data["header"]["seeded_draws"] == 4000
    assert len(data["whitespace"]) == 20000
    assert data["realistic"]["text"]
    assert all(r["sha"] == _sha(r["text"]) for r in data["files"])
    # Provenance only: the exponential pattern must not be compiled anywhere.
    assert "pattern" in data["header"] and "note" in data["header"]


@pytest.mark.parametrize("spaces", [22, 24, 26, 1000, 100_000])
@pytest.mark.parametrize(
    "template",
    [
        '![a](data:{ws}"t" )',
        '![a](data:x{ws}"t" )',
        '![a](data:"q{ws}"t" ) ',
        '![a](data:{ws}"t")',
        '![a](data:{ws}x)',
        '![a](x{ws}"t" )',
    ],
)
def test_whitespace_run_in_a_destination_is_not_exponential(template: str, spaces: int) -> None:
    text = template.format(ws=" " * spaces)
    for fn in (lis.MD_IMAGE_NESTED.search, lambda t: list(lis.MD_IMAGE_NESTED.finditer(t))):
        elapsed = min(_cpu(fn, text) for _ in range(3))
        assert elapsed < 0.05, (template, spaces, elapsed)
