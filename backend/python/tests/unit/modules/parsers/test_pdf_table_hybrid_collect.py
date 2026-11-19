"""The worker half of the shadow run never fails the parse: a failing render, an empty crop, an unwritable spool."""
from __future__ import annotations

import os
from pathlib import Path
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


def bullet(x0: float, top: float, font: str | None = "_synthetic_bullet") -> dict:
    w = {"text": "•", "x0": x0, "x1": x0 + 1.2, "top": top, "bottom": top + 1.2}
    if font:
        w["fontname"] = font
    return w


def test_made_up_vector_bullets_are_never_tokens_and_their_origin_is_recorded(spool) -> None:
    """The shape of a shaded row of figures: a bullet the layout pass drew from a vector shape sits left of a number."""
    box = BOX
    words = grid_words(box)
    words.append({"text": "1,515", "x0": 200.0, "x1": 230.0, "top": 30.0, "bottom": 40.0})
    synthetic = bullet(190.0, 33.0)
    col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer)
    col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision(box)], [], None, [*words, synthetic])
    rec = read_index(spool)[0]
    assert rec["reason"] == "none" and col.sent == 1
    assert "•" not in [x[0] for x in rec["words"]] and "1,515" in [x[0] for x in rec["words"]]
    assert rec["ntok"] == len(words) and rec["synthetic"] == [[190.0, 33.0, 191.2, 34.2]]
    # the same box without the bullet plans the same request
    other = spool + "-b"
    os.mkdir(other)
    flow.TableJobCollector(other, "x.pdf", renderer=blank_renderer).collect_page(0, PAGE_W, PAGE_H, [FakeDecision(box)], [], None, words)
    assert {k: read_index(other)[0][k] for k in ("cap", "lines", "cols_est", "ntok")} == {k: rec[k] for k in ("cap", "lines", "cols_est", "ntok")}


def test_a_bullet_that_is_in_the_text_layer_stays_a_token(spool) -> None:
    box = BOX
    words = [*grid_words(box), bullet(190.0, 33.0, font="Arial")]
    flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer).collect_page(0, PAGE_W, PAGE_H, [FakeDecision(box)], [], None, words)
    rec = read_index(spool)[0]
    assert "•" in [x[0] for x in rec["words"]] and rec["synthetic"] == []


def test_the_strokes_of_a_ruled_page_are_spooled_for_the_crossing_count(spool) -> None:
    import json

    import pdfplumber

    fixtures = Path(__file__).resolve().parents[3] / "fixtures" / "table_hybrid"
    boxes = json.loads((fixtures / "tables.boxes.json").read_text())
    with pdfplumber.open(str(fixtures / "tables.pdf")) as pdf:
        page = pdf.pages[0]
        words = page.extract_words(extra_attrs=["fontname", "size"])
        col = flow.TableJobCollector(spool, "x.pdf", renderer=blank_renderer)
        col.collect_page(0, PAGE_W, PAGE_H, [FakeDecision(tuple(boxes["1"][0]))], [], None, words, page)
    rec = read_index(spool)[0]
    assert len(rec["rules"]["h"]) >= 9 and len(rec["rules"]["v"]) >= 6  # the ruled table of page 1
