"""The worker half of the shadow run never fails the parse: a failing render, an empty crop, an unwritable spool."""
from __future__ import annotations

import os
from typing import Never

import numpy as np
import pytest

from app.modules.parsers.pdf import table_hybrid_flow as flow
from tests.support.table_shadow import (
    PAGE_H,
    PAGE_W,
    FakeDecision,
    blank_renderer,
    grid_words,
    read_index,
)

BOX = (40.0, 20.0, 300.0, 120.0)


def collect(spool, renderer=blank_renderer, boxes=(BOX,)) -> flow.TableJobCollector:
    col = flow.TableJobCollector(spool, "x.pdf", renderer=renderer)
    words = [w for b in boxes for w in grid_words(b)]
    col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision(b) for b in boxes], [], None, words)  # must not raise
    return col


@pytest.fixture
def spool(tmp_path) -> str:
    d = tmp_path / "s"
    d.mkdir()
    return str(d)


def test_a_render_that_raises_is_recorded_for_the_box_and_the_parse_goes_on(spool) -> None:
    def broken(page_index) -> Never:
        raise RuntimeError("pdfium failed")

    col = collect(spool, broken, boxes=(BOX, (40.0, 220.0, 300.0, 320.0)))
    assert [r["reason"] for r in read_index(spool)] == ["collect_error", "collect_error"] and col.sent == 0


def test_an_empty_crop_is_a_collect_error(spool) -> None:
    """A render too small to hold the box gives an empty crop."""
    def tiny(page_index) -> tuple[np.ndarray, float, float]:
        return np.zeros((4, 4, 3), np.uint8), PAGE_W, PAGE_H

    collect(spool, tiny)
    assert [r["reason"] for r in read_index(spool)] == ["collect_error"]


def test_an_unwritable_spool_is_a_collect_error_not_an_exception(tmp_path) -> None:
    missing = str(tmp_path / "gone" / "deeper")  # a directory that does not exist: no file can be written
    col = collect(missing)
    assert col.errors >= 1 and col.sent == 0


def test_a_read_only_spool_directory_is_survived(spool) -> None:
    os.chmod(spool, 0o500)
    try:
        if os.access(spool, os.W_OK):
            pytest.skip("the process can write to a read-only directory (running as root)")
        col = collect(spool)
        assert col.errors >= 1 and col.sent == 0
    finally:
        os.chmod(spool, 0o700)


def test_a_page_level_failure_never_escapes_the_collector(spool) -> None:
    col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer)
    col.collect_page(0, PAGE_W, PAGE_H, [object()], [], None, [])  # a decision with no box
    assert col.errors >= 1


def test_a_page_of_another_size_is_a_collect_error(spool) -> None:
    def other(page_index) -> tuple[np.ndarray, float, float]:
        return np.full((100, 100, 3), 255, np.uint8), 50.0, 50.0

    collect(spool, other)
    assert [r["reason"] for r in read_index(spool)] == ["collect_error"]


def test_the_request_the_worker_builds_is_a_page_resize_then_crop() -> None:
    img = np.random.default_rng(1).integers(0, 255, (int(PAGE_H * 2), int(PAGE_W * 2), 3), dtype=np.uint8)
    png, sf, tb, (h, w) = flow.build_request(img, BOX)
    assert sf == pytest.approx(1024 / img.shape[0])
    assert tb[0] == pytest.approx(round(BOX[0]) * 2 * sf) and tb[3] == pytest.approx(round(BOX[3]) * 2 * sf)
    assert h == round(tb[3]) - round(tb[1]) and h <= 1024
    import cv2

    back = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    rz, _ = flow.resize_page(img)
    assert np.array_equal(back, rz[round(tb[1]) : round(tb[3]), round(tb[0]) : round(tb[2])])
