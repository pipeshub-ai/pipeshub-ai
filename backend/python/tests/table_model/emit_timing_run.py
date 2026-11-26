"""Worker wall time of the layout parse on the fixture's table pages, evidence against hybrid, in one process that has the
environment of a lane worker. Run by ``test_hybrid_emit_timing.py`` (and by hand: ``python -m tests.table_model.emit_timing_run
<weights dir> [repeats]``). Prints one JSON line.

Per table page: the wall time of ``parse_pdf_layout`` for that page alone, median of the repeats after one warm-up; the model
wait is the sum of the request wall times the emitter recorded, the model seconds per table are the model's own wall time.
"""
from __future__ import annotations

import contextlib
import json
import os
import statistics
import sys
import time

REPEATS = int(sys.argv[2]) if len(sys.argv) > 2 else 3


def host_facts() -> dict:
    """The machine and the numeric library the model runs on (read once, outside every timed window)."""
    import platform

    facts: dict = {"cpu_count": os.cpu_count(), "machine": platform.machine()}
    with contextlib.suppress(AttributeError, OSError):
        facts["cpus_allowed"] = len(os.sched_getaffinity(0))
    try:
        with open("/proc/cpuinfo") as fh:
            names = [ln.split(":", 1)[1].strip() for ln in fh if ln.startswith("model name")]
        facts["cpu_model"] = names[0] if names else None
    except OSError:
        facts["cpu_model"] = None
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            facts["cgroup_cpu_max"] = fh.read().strip()
    except OSError:
        pass
    try:
        import torch

        facts["torch"] = torch.__version__
        facts["torch_num_threads_here"] = torch.get_num_threads()
        facts["torch_config"] = torch.__config__.show()
    except Exception as exc:
        facts["torch"] = type(exc).__name__
    facts["thread_env"] = {k: os.environ[k] for k in sorted(os.environ) if k.endswith("_NUM_THREADS") or k in ("PIPESHUB_LANE_THREADS",)}
    return facts


def main() -> None:
    import io

    import pypdfium2 as pdfium

    from app.modules.parsers.pdf import opencv_layout_analyzer as ola
    from app.modules.parsers.pdf import table_hybrid_emit as emit
    from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
    from tests.support.table_emit import SupervisorClient, cfg, fake_boxes, fixture_pdf

    weights = sys.argv[1]
    pdf, boxes = fixture_pdf()
    client = SupervisorClient(weights)
    t_load = time.perf_counter()
    client._loop.run_until_complete(client.sup._start())  # child process start and model load, before any page
    load_s = time.perf_counter() - t_load
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
    for i, pdf_page in enumerate(pages):
        # each page is parsed as a one-page PDF: its page number is 1, so it gets its own boxes under that number
        ola._detect_model_boxes = fake_boxes({"1": boxes.get(str(i + 1), [])})
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
        "model_load_s": load_s,
        "first_table_model_s": model_wall[0] if model_wall else None,
        "model_s_after_first_median": statistics.median(model_wall[1:]) if len(model_wall) > 1 else None,
        "model_cpu_s_median": statistics.median(client.cpu_history) if client.cpu_history else None,
        "round_trip_s_median": statistics.median(client.round_trip[1:]) if len(client.round_trip) > 1 else None,
        "child_starts": client.sup.stats["starts"],
        "child_respawns": client.sup.stats["respawns"],
        "child_threads": client.sup.threads,
        "host": host_facts(),
    }
    client.close()
    print("TIMING " + json.dumps(out))


if __name__ == "__main__":
    main()
