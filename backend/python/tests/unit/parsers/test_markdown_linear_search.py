"""The image searches must match what the plain regexes match, in linear time."""
from __future__ import annotations

import random
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.modules.parsers.markdown import linear_image_search as lis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

REFERENCE = {
    "MD_IMAGE_NESTED": (
        lis.MD_IMAGE_NESTED,
        re.compile(
            r'!\[((?:[^\[\]]|\[[^\]]*\])*)\]\('
            r'(data:(?:[^"\s)]|\s+(?!"[^"]*"\s*\))|"[^")]*)*|[^\s)]+)'
            r'(?:\s+"[^"]*")?\)'
        ),
    ),
    "HTML_IMG_ALT": (
        lis.HTML_IMG_ALT,
        re.compile(r'<img\s[^>]*alt=["\']([^"\']*)["\'][^>]*>', re.IGNORECASE),
    ),
}

PIECES = [
    "![", "[", "]", "](", ")", "(", " ", "  ", '"', "'", "a", "alt", "alt=", "data:", "<img ",
    "<IMG\t", ">", "\n", "x", ":", "<![CDATA[", "][", "]:", "t", "<!--", "\\",
]


def _same(name: str, text: str) -> None:
    ours, ref = REFERENCE[name]
    got = [(m.span(), m.groups()) for m in ours.finditer(text)]
    want = [(m.span(), m.groups()) for m in ref.finditer(text)]
    assert got == want, (name, text[:200])
    for pos in range(0, min(len(text), 40), 7):
        a, b = ours.search(text, pos), ref.search(text, pos)
        assert (a and (a.span(), a.groups())) == (b and (b.span(), b.groups())), (name, pos, text[:200])


def _tracked_markdown() -> list[Path]:
    root = Path(__file__).resolve().parents[5]
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "*.md"], capture_output=True, text=True, check=False
    ).stdout.split()
    return [root / p for p in out]


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_parity_on_tracked_markdown_files(name: str) -> None:
    files = _tracked_markdown()
    if not files:
        pytest.skip("not a git checkout")
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _same(name, text)


@pytest.mark.parametrize("name", sorted(REFERENCE))
def test_parity_on_seeded_random_input(name: str) -> None:
    rng = random.Random(20260428)
    for _ in range(4000):
        text = "".join(rng.choice(PIECES) for _ in range(rng.randint(1, 40)))
        _same(name, text)


def test_parity_on_realistic_images() -> None:
    text = (
        'a ![alt [x] y](http://u/p.png "t") b ![](data:image/png;base64,AAA "t" ) '
        '![r][id] <img src="a" alt="A > B"> <IMG ALT=\'q\' src=x>\n[id]: http://x "T"\n'
    )
    for name in REFERENCE:
        _same(name, text)


# Each family is a repeated opener that used to cost O(n^2) in the regex.
FAMILIES = [
    "<![CDATA[", "![", "![a", "![a[", "![x[y]", "![a](", "![a](x", "![a](x ", "![a](data:x ",
    '![a](x "', "![a][", "[a\n", "<img ", '<img alt="', '<img alt="x" ',
]


def _cpu(fn, text: str) -> float:
    start = time.process_time()
    fn(text)
    return time.process_time() - start


@pytest.mark.parametrize("name", sorted(REFERENCE))
@pytest.mark.parametrize("unit", FAMILIES)
def test_doubling_the_input_does_not_quadruple_the_time(name: str, unit: str) -> None:
    ours = REFERENCE[name][0]
    small, large = unit * (24_000 // len(unit)), unit * (48_000 // len(unit))
    run = lambda t: list(ours.finditer(t))  # noqa: E731
    # Best of three, so one scheduling hiccup cannot fail the ratio.
    t_small = min(_cpu(run, small) for _ in range(3))
    t_large = min(_cpu(run, large) for _ in range(3))
    # Under ~20 ms the ratio is noise; above it, linear is 2x and quadratic 4x.
    assert t_large < 0.05 or t_large <= max(t_small, 0.01) * 2.5, (name, unit, t_small, t_large)


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
