"""Worker wall time of the layout parse on the fixture's table pages, evidence against hybrid, in one process that has the
environment of a lane worker. Run by ``test_hybrid_emit_timing.py`` (and by hand: ``python -m tests.table_model.emit_timing_run
<weights dir> [repeats]``). Prints one JSON line.

Per table page: the wall time of ``parse_pdf_layout`` for that page alone, median of the repeats after one warm-up; the model
wait is the sum of the request wall times the emitter recorded, the model seconds per table are the model's own wall time.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 3


def main() -> None:
    import io

    import pypdfium2 as pdfium

    from app.modules.parsers.pdf import opencv_layout_analyzer as ola
    from app.modules.parsers.pdf import table_hybrid_emit as emit
    from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
    from tests.support.table_emit import SupervisorClient, cfg, fake_boxes, fixture_pdf

    weights = sys.argv[1]
    pdf, boxes = fixture_pdf()
    ola._detect_model_boxes = fake_boxes(boxes)
    client = SupervisorClient(weights)
    real = emit.TableEmitter.__init__

    def init(self, c, p, **kw):  # noqa: ANN202
        real(self, c, p, **dict(kw, client=client))

    emit.TableEmitter.__init__ = init
    doc = pdfium.PdfDocument(pdf)
    pages = []
    for i in range(len(doc)):
        one = pdfium.PdfDocument.new()
        one.import_pages(doc, [i])
        buf = io.BytesIO()
        one.save(buf)
        pages.append(buf.getvalue())
    rows = []
    model_wall: list[float] = []
    for pdf_page in pages:
        walls: dict[str, list[float]] = {"evidence": [], "hybrid": []}
        cpu: dict[str, list[float]] = {"evidence": [], "hybrid": []}
        for rep in range(REPEATS + 1):
            for mode in ("evidence", "hybrid"):
                os.environ["PDF_TABLE_EXTRACTION"] = mode
                t0, c0 = time.perf_counter(), time.process_time()
                parse_pdf_layout(pdf_page, None, cfg() if mode == "hybrid" else None)
                if rep:
                    walls[mode].append(time.perf_counter() - t0)
                    cpu[mode].append(time.process_time() - c0)
        rows.append({"evidence": statistics.median(walls["evidence"]), "hybrid": statistics.median(walls["hybrid"]), "cpu_evidence": statistics.median(cpu["evidence"]), "cpu_hybrid": statistics.median(cpu["hybrid"])})
    model_wall.extend(client.history)  # the model's own wall seconds of each table it was asked for
    out = {
        "threads_env": os.environ.get("PIPESHUB_LANE_THREADS"),
        "table_pages": len(rows),
        "wall_evidence_median": statistics.median(r["evidence"] for r in rows),
        "wall_hybrid_median": statistics.median(r["hybrid"] for r in rows),
        "added_per_page_median": statistics.median(r["hybrid"] - r["evidence"] for r in rows),
        "added_per_page_max": max(r["hybrid"] - r["evidence"] for r in rows),
        "cpu_added_per_page_median": statistics.median(r["cpu_hybrid"] - r["cpu_evidence"] for r in rows),
        "model_seconds_per_table_mean": statistics.mean(model_wall) if model_wall else None,
        "model_seconds_per_table_median": statistics.median(model_wall) if model_wall else None,
        "tables_timed": len(model_wall),
    }
    client.close()
    print("TIMING " + json.dumps(out))


if __name__ == "__main__":
    main()
