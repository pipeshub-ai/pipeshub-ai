"""Fakes for the hybrid emit tests: a stand-in table-structure service that reads a grid off the request's tokens."""
from __future__ import annotations

import json
import time
from typing import Any

from app.modules.parsers.pdf import table_hybrid as th
from app.modules.parsers.pdf.table_hybrid_client import BatchOutcome
from app.modules.parsers.pdf.table_hybrid_emit import ModelCfg
from app.services.docling.table_structure_schema import (
    ModelInfo,
    TableResponse,
    TableResult,
    config_hash,
)


def measured_model() -> ModelInfo:
    return ModelInfo(
        docling_ibm_models=th.MODEL_LIBRARY_VERSION, mode=th.MODEL_MODE, weights_sha256=th.MODEL_WEIGHTS_SHA256,
        model_revision=th.MODEL_REVISION, token_definition=th.TOKEN_DEFINITION,
        config_sha256=config_hash(th.MODEL_LIBRARY_VERSION, th.MODEL_MODE, th.MODEL_WEIGHTS_SHA256, th.MODEL_REVISION, th.TOKEN_DEFINITION),
    )


def grid_cells(tokens: list[dict[str, Any]]) -> tuple[int, int, list[dict[str, Any]]]:
    """Rows by vertical gaps and columns by horizontal gaps of the tokens (a stand-in for the model's structure)."""
    if not tokens:
        return 0, 0, []
    cy = lambda t: (t["bbox"][1] + t["bbox"][3]) / 2  # noqa: E731
    h = sorted(t["bbox"][3] - t["bbox"][1] for t in tokens)[len(tokens) // 2]
    rows: list[list[dict[str, Any]]] = []
    for t in sorted(tokens, key=cy):
        if rows and abs(cy(t) - sum(cy(x) for x in rows[-1]) / len(rows[-1])) <= 0.6 * h:
            rows[-1].append(t)
        else:
            rows.append([t])
    spans = sorted((t["bbox"][0], t["bbox"][2]) for t in tokens)
    cols: list[list[float]] = []
    for a, b in spans:
        if cols and a <= cols[-1][1] + 1.0 * h:
            cols[-1][1] = max(cols[-1][1], b)
        else:
            cols.append([a, b])
    cells: dict[tuple[int, int], list[int]] = {}
    for r, row in enumerate(rows):
        for t in row:
            c = next(i for i, (a, b) in enumerate(cols) if a - 1e-6 <= t["bbox"][0] <= b + 1e-6)
            cells.setdefault((r, c), []).append(t["id"])
    out = [{"r0": r, "r1": r + 1, "c0": c, "c1": c + 1, "bbox": None, "tokens": ids} for (r, c), ids in sorted(cells.items())]
    return len(rows), len(cols), out


class FakeModelClient:
    """``post_batch`` like ``SyncTableClient``. ``script`` maps a table id (``p<page>:b<n>``) to ``ok`` (default),
    ``cap_hit``, ``err:<text>``, ``malformed``, ``drop:<token id>`` (the grid leaves a word out), ``cells`` (a callable
    ``tokens -> (n_rows, n_cols, cells)``); ``outcome`` makes the whole request fail with that kind."""

    def __init__(self, script: dict[str, Any] | None = None, outcome: str | None = None, model: ModelInfo | None = None) -> None:
        self.script = script or {}
        self.outcome = outcome
        self.model = model or measured_model()
        self.requests: list[dict[str, Any]] = []
        self.calls = 0

    def post_batch(self, meta: dict[str, Any], files: dict[str, bytes], *, deadline_ms: int, timeout_s: float) -> BatchOutcome:
        self.calls += 1
        self.requests.append({"meta": json.loads(json.dumps(meta)), "deadline_ms": deadline_ms, "timeout_s": timeout_s, "files": sorted(files)})
        if self.outcome is not None:
            return BatchOutcome(self.outcome)
        tables = []
        for spec in meta["tables"]:
            what = self.script.get(spec["id"], "ok")
            if callable(what):
                nr, nc, cells = what(spec["tokens"])
                tables.append(TableResult(id=spec["id"], status="ok", steps=5, cpu_ms=1.0, wall_ms=1.0, n_rows=nr, n_cols=nc, cells=cells))
            elif what == "cap_hit":
                tables.append(TableResult(id=spec["id"], status="cap_hit", steps=spec["cap"]))
            elif isinstance(what, str) and what.startswith("err:"):
                tables.append(TableResult(id=spec["id"], status="error", err=what[4:]))
            elif what == "malformed":
                tables.append(TableResult(id=spec["id"], status="ok", n_rows=1, n_cols=1, cells=[{"r0": 0, "r1": 1, "c0": 0, "c1": 9, "tokens": []}]))
            else:
                nr, nc, cells = grid_cells(spec["tokens"])
                if isinstance(what, str) and what.startswith("drop:"):
                    gone = int(what[5:])
                    for c in cells:
                        c["tokens"] = [t for t in c["tokens"] if t != gone]
                    cells = [c for c in cells if c["tokens"]]
                tables.append(TableResult(id=spec["id"], status="ok", steps=5, cpu_ms=1.0, wall_ms=1.0, n_rows=nr, n_cols=nc, cells=cells))
        return BatchOutcome("ok", TableResponse(tables=tables, model=self.model), 200)


def cfg(**kw: Any) -> ModelCfg:  # noqa: ANN401
    base = {"base_url": "http://svc", "record_id": "rec-1", "request_id": "req-1", "cutoff_epoch": 4e9, "max_tables": 300, "doc_seconds": 600.0, "timeout_s": 120.0}
    base.update(kw)
    return ModelCfg(**base)


# --- emitter-level helpers (no PDF) -------------------------------------------------------------------------------------

BOX = (40.0, 20.0, 300.0, 120.0)


def make_emitter(client: Any, **cfg_kw: Any):  # noqa: ANN201, ANN401
    from app.modules.parsers.pdf.table_hybrid_emit import TableEmitter
    from tests.support.table_shadow import blank_renderer

    return TableEmitter(cfg(**cfg_kw), "unused.pdf", client=client, renderer=blank_renderer)


def evidence_region(bbox: tuple[float, float, float, float], words: list[dict[str, Any]] | None = None):  # noqa: ANN201
    """An evidence table region whose grid holds the words inside its bbox (one row), as a read of the same words would."""
    from app.modules.parsers.pdf.opencv_layout_analyzer import (
        LayoutRegion,
        LayoutRegionType,
    )

    inside = [str(w["text"]) for w in (words or []) if bbox[0] <= (w["x0"] + w["x1"]) / 2 <= bbox[2] and bbox[1] <= (w["top"] + w["bottom"]) / 2 <= bbox[3]]
    return LayoutRegion(type=LayoutRegionType.TABLE, bbox=bbox, text="ev", table_grid=[inside or ["ev"], ["ev"]] if not inside else [inside[:1], inside[1:] or [""]], from_evidence=True)


def run_page(
    em: Any, words: list[dict[str, Any]], boxes: list[tuple[float, float, float, float]] | None = None, *, kinds: list[str] | None = None,  # noqa: ANN401
    ev_boxes: list[tuple[float, float, float, float]] | None = None, page_index: int = 0, page: Any = None, n_pages: int = 1,  # noqa: ANN401
) -> list[Any]:
    from tests.support.table_shadow import PAGE_H, PAGE_W, FakeDecision

    boxes = boxes if boxes is not None else [BOX]
    ev_boxes = ev_boxes if ev_boxes is not None else boxes
    kinds = kinds or ["group"] * len(boxes)
    if not em._n_pages:
        em.begin(n_pages)
    decisions = [FakeDecision(b, k) for b, k in zip(boxes, kinds, strict=True)]
    out = em.swap_page(page_index, page, PAGE_W, PAGE_H, decisions, [evidence_region(b, words) for b in ev_boxes], words)
    em.finalize_page()
    return out


def reasons(em: Any) -> list[str]:  # noqa: ANN401
    return [r["reason"] for r in em.rows]


class FakePage:
    """The few attributes ``_page_strokes`` reads: drawn lines and rectangles."""

    def __init__(self, lines: list[dict[str, float]] | None = None, rects: list[dict[str, Any]] | None = None) -> None:
        self.lines = lines or []
        self.rects = rects or []


# --- whole-page helpers (generated PDFs through the product path) ---------------------------------------------------------

import collections  # noqa: E402
import hashlib  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "table_hybrid"
W, H = 612.0, 792.0


def fixture_pdf() -> tuple[bytes, dict[str, list[list[float]]]]:
    import json

    return (FIXTURES / "tables.pdf").read_bytes(), json.loads((FIXTURES / "tables.boxes.json").read_text())


def fake_boxes(boxes: dict[str, list[list[float]]]):  # noqa: ANN201
    def detect(page, pdf_path=None, raster_cache=None):  # noqa: ANN202
        bs = boxes.get(str(page.page_number), [])
        return {"table": [((b[0] / W, b[1] / H, b[2] / W, b[3] / H), 0.9) for b in bs], "picture": []} if bs else None

    return detect


def canonical(pages: list[Any]) -> str:
    """A hash of everything the emitters read from the regions, stable across Python versions."""
    import json

    out = [[(r.type.name, [round(v, 2) for v in r.bbox], r.text, r.table_grid, r.list_items, r.caption) for r in p.regions] for p in pages]
    return hashlib.sha256(json.dumps(out, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def word_counter(pages: list[Any]) -> collections.Counter:
    """Content words (letters and digits) in every region's text, grid cells and list items."""
    c: collections.Counter = collections.Counter()
    for p in pages:
        for r in p.regions:
            parts = [r.text or "", *(r.list_items or [])]
            c.update(m for part in parts for m in re.findall(r"[A-Za-z0-9]+", part))
    return c


def make_pdf(draw, path: Path) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter, invariant=1)
    draw(c)
    c.save()
    return path.read_bytes()


class SupervisorClient:
    """``post_batch`` against the real table-structure model: the service's own supervisor and child process, one table at a
    time, without the HTTP layer. Used by the real-model tests and the gate runner."""

    def __init__(self, weights: str, threads: int | None = None) -> None:
        import asyncio

        from app.services.docling.table_structure import TableSupervisor

        self._loop = asyncio.new_event_loop()  # the child's pipes belong to the loop that started it
        self.sup = TableSupervisor(weights_dir=lambda _mode: weights, memory_available=lambda: 10**12, model_timeout_ceiling=120.0)
        self.calls = 0
        self.history: list[float] = []  # the model's own wall seconds of each table
        self.cpu_history: list[float] = []  # the model's own cpu seconds of each table
        self.round_trip: list[float] = []  # seconds from the request to the answer of each table, as the caller sees it
        self.threads_used: list[int] = []  # the threads the model ran each table with

    def post_batch(self, meta: dict[str, Any], files: dict[str, bytes], *, deadline_ms: int, timeout_s: float) -> BatchOutcome:
        self.calls += 1
        tables = []
        for spec in meta["tables"]:
            t0 = time.perf_counter()
            raw = self._loop.run_until_complete(self.sup.run_table({k: v for k, v in spec.items() if k != "image"}, files[spec["image"]]))
            self.round_trip.append(time.perf_counter() - t0)
            if raw.get("threads") is not None:
                self.threads_used.append(raw["threads"])
            if raw.get("cpu_ms") is not None:
                self.cpu_history.append(raw["cpu_ms"] / 1000.0)
            tables.append(TableResult(id=spec["id"], **raw))
            if raw.get("wall_ms") is not None:
                self.history.append(raw["wall_ms"] / 1000.0)
        info = self.sup.model_info
        return BatchOutcome("ok", TableResponse(tables=tables, model=info if info is not None else measured_model()), 200)

    def close(self) -> None:
        self._loop.run_until_complete(self.sup.shutdown())
        self._loop.close()


def prose_page(c) -> None:
    """A text-heavy page for the unchanged-modes pins: a heading, paragraphs, short stray lines, a list and a ruled table."""
    c.setFont("Helvetica-Bold", 14)
    c.drawString(72, 740, "Quarterly results")
    c.setFont("Helvetica", 10)
    y = 712
    for para in range(3):
        for line in range(5):
            c.drawString(72, y, f"Paragraph {para} line {line} has enough words to read as running prose across the page.")
            y -= 13
        y -= 9
    c.setFont("Helvetica", 7)
    for k, t in enumerate(["see note", "(a)", "unaudited"]):
        c.drawString(430 + 20 * k, y + 30 - 9 * k, t)
    c.setFont("Helvetica", 10)
    for k in range(4):
        c.drawString(90, y - 14 * k, f"• list item number {k} with a few more words")
    y -= 80
    rows = [["Region", "2023", "2022"]] + [[f"Area {i}", f"{1000 + 37 * i:,}", f"{900 + 31 * i:,}"] for i in range(6)]
    for r, row in enumerate(rows):
        for k, t in enumerate(row):
            c.drawString(76 + 150 * k, y - 15 * r - 11, t)
    for r in range(len(rows) + 1):
        c.line(72, y - 15 * r, 72 + 450, y - 15 * r)
    for k in range(4):
        c.line(72 + 150 * k, y, 72 + 150 * k, y - 15 * len(rows))
    c.setFont("Helvetica", 8)
    c.drawString(72, 40, "Footer line one")
    c.drawString(480, 40, "Page 1")
