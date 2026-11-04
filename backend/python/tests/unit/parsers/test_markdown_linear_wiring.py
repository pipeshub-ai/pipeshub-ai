"""The linear image search must be the one the converter and the pipeline really use."""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from app.modules.parsers.markdown import linear_image_search as lis
from app.modules.parsers.markdown import markdown_to_blocks as m2b

APP = Path(__file__).resolve().parents[3] / "app"


def test_converter_uses_the_linear_searches() -> None:
    assert m2b._MD_IMAGE_RE is lis.MD_IMAGE_NESTED
    assert m2b._HTML_IMG_ALT_RE is lis.HTML_IMG_ALT


def _cpu(text: str) -> float:
    start = time.process_time()
    m2b._split_raw_markdown_into_segments(text)
    return time.process_time() - start


@pytest.mark.parametrize("unit", ["<![CDATA[", '<img alt="', "![a]("])
def test_segment_splitting_is_linear_in_the_input(unit: str) -> None:
    small, large = unit * (24_000 // len(unit)), unit * (96_000 // len(unit))
    t_small = min(_cpu(small) for _ in range(3))
    t_large = min(_cpu(large) for _ in range(3))
    # Four times the input: linear is 4x, quadratic 16x (a 2x step measured 2.65x in CI).
    assert t_large < 0.05 or t_large <= max(t_small, 0.01) * 8, (unit, t_small, t_large)
    # The ratio alone can miss a pattern that is already slow at the small size.
    assert t_large < 1.5, (unit, t_small, t_large)


@pytest.mark.parametrize("path", ["events/processor.py", "modules/parsers/blocks/blocks_parser.py"])
def test_image_extraction_is_always_offloaded(path: str) -> None:
    """A direct ``x.extract_and_replace_images(...)`` call would run on the event loop."""
    tree = ast.parse((APP / path).read_text(encoding="utf-8"))
    direct = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "extract_and_replace_images"
    ]
    assert direct == [], f"{path}: extract_and_replace_images called directly at lines {direct}"
