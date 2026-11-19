"""The emit phase against the real table-structure model: the fixture boxes through the whole page pass.

Cells carry the page's own words, no word is lost or doubled against the evidence result, no made-up bullet or dot leader
is in a cell, and the same input gives the same output twice. These load the model; without the weights they fail in CI.
"""
from __future__ import annotations

import pytest

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_hybrid_emit as emit
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from app.modules.parsers.pdf.opencv_layout_analyzer import LayoutRegionType
from tests.support.table_emit import (
    SupervisorClient,
    canonical,
    cfg,
    fake_boxes,
    fixture_pdf,
    make_pdf,
    word_counter,
)

PDF, BOXES = fixture_pdf()


@pytest.fixture(scope="module")
def client(weights):  # noqa: ANN201
    c = SupervisorClient(weights)
    yield c
    c.close()


@pytest.fixture(autouse=True)
def setup(monkeypatch: pytest.MonkeyPatch, client: SupervisorClient) -> None:
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes(BOXES))
    real = emit.TableEmitter.__init__
    monkeypatch.setattr(emit.TableEmitter, "__init__", lambda self, c, p, **kw: real(self, c, p, **dict(kw, client=client)))


def run(mode: str, monkeypatch: pytest.MonkeyPatch, pdf: bytes = PDF):  # noqa: ANN201
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    return parse_pdf_layout(pdf, None, cfg() if mode == "hybrid" else None)


def swapped(pages) -> list:
    return [r for p in pages for r in p.regions if r.type == LayoutRegionType.TABLE and r.from_model_grid]


def test_fixture_boxes_carry_the_models_grids_with_our_words(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = run("evidence", monkeypatch)
    hybrid = run("hybrid", monkeypatch)
    tables = swapped(hybrid)
    assert len(tables) >= 6
    first = swapped(hybrid[:1])[0]
    assert first.table_grid[0] == ["Item", "2023", "2022", "Change", "Note"] and first.table_grid[1][1] == "$1,037"
    ev, hy = word_counter(evidence), word_counter(hybrid)
    assert not (ev - hy) and not (hy - ev)


def test_no_dot_leader_in_any_cell(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    def draw(c) -> None:
        c.setFont("Helvetica", 9)
        x, top, col_w, row_h = 72, 120, [190, 80, 80], 15
        rows = [["Part", "Page", "Count"], *[[f"Section {i} " + "." * 12, str(i * 7), str(i * 3 + 1)] for i in range(1, 9)]]
        for r, row in enumerate(rows):
            xc = x
            for k, t in enumerate(row):
                c.drawString(xc + 3, 792 - (top + (r + 1) * row_h) + 4, t)
                xc += col_w[k]
        for r in range(len(rows) + 1):
            c.line(x, 792 - (top + r * row_h), x + sum(col_w), 792 - (top + r * row_h))
        xx = x
        for w in [0, *col_w]:
            xx += w
            c.line(xx, 792 - top, xx, 792 - (top + row_h * len(rows)))

    pdf = make_pdf(draw, tmp_path / "leaders.pdf")
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes({"1": [[68, 116, 428, 259]]}))
    tables = swapped(run("hybrid", monkeypatch, pdf))
    assert len(tables) == 1 and tables[0].table_grid[1][0] == "Section 1"
    assert all("...." not in cell and "•" not in cell for row in tables[0].table_grid for cell in row)


def test_the_same_input_gives_the_same_output_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    assert canonical(run("hybrid", monkeypatch)) == canonical(run("hybrid", monkeypatch))
