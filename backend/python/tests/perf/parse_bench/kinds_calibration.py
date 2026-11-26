"""Per-kind memory calibration of the lane charges (PX-2e).

For each kind and size band this starts a fresh parse worker, runs the job the pool would
run, and samples the resident memory of the worker's whole process group (the worker and a
raster child) every 20 ms until the reply. The median of the runs' peaks is what the charge
in ``resource_governor/kinds.py`` has to cover (and not exceed by more than 1.5 x).
It also records how far the *parent* grows while it receives the result, which is what the
heavy permit's base (0.25 GiB) has to cover.

Run it pinned and on a quiet host::

    taskset -c 0-3 python -m tests.perf.parse_bench.kinds_calibration --out DIR [--runs 3] [--cells a,b]

It writes ``DIR/PX-2e-calibration.json`` after every cell, so an interrupted run keeps what it
measured. The test ``test_kinds.py::test_charge_matches_the_recorded_calibration`` reads that file.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.perf.parse_bench import corpus

if TYPE_CHECKING:
    from collections.abc import Callable

MIB = 2**20
SAMPLE_SECONDS = 0.02
LOADAVG_LIMIT = 4.0


def _rss_self() -> int:
    with open("/proc/self/statm") as handle:
        return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")


class _Peak(threading.Thread):
    """Samples the worker group's resident bytes and this process's own."""

    def __init__(self, pgid: int) -> None:
        super().__init__(daemon=True)
        self.pgid = pgid
        self.group_peak = 0
        self.parent_peak = 0
        self._stop_event = threading.Event()

    def run(self) -> None:
        from app.modules.parsers.parse_pool_lanes import group_rss_bytes

        while not self._stop_event.is_set():
            self.group_peak = max(self.group_peak, group_rss_bytes({self.pgid}).get(self.pgid, 0))
            self.parent_peak = max(self.parent_peak, _rss_self())
            time.sleep(SAMPLE_SECONDS)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2)


@dataclass
class Cell:
    name: str
    kind: str
    pages: int
    build: Callable[[], bytes]
    jobs: Callable[[bytes, Path], list[tuple[Callable[..., object], tuple]]]
    note: str = ""


def _jobs_layout(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.pdf.layout_pages import parse_pdf_layout

    return [(parse_pdf_layout, (data,))]


def _jobs_render(batches: int) -> Callable[[bytes, Path], list[tuple[Callable[..., object], tuple]]]:
    def make(data: bytes, tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
        from app.modules.parsers.pdf import pdf_rasterizer as r

        path = tmp / "scan.pdf"
        path.write_bytes(data)
        return [
            (r._worker_render_batch_from_path, (str(path), list(range(start + 1, start + 21)), 200))
            for start in range(0, batches * 20, 20)
        ]

    return make


def _jobs_text(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.markdown.markdown_it_parser import (
        _extract_and_replace_images,
    )
    from app.modules.parsers.markdown.markdown_to_blocks import (
        convert_markdown_to_blocks,
    )

    text = data.decode()
    return [(_extract_and_replace_images, (text,)), (convert_markdown_to_blocks, (text, {}, None))]


def _jobs_csv(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.csv.table_detection import read_csv_tables

    return [(read_csv_tables, (data, ",", '"'))]


def _jobs_code(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.code_parser.code_file_parser import parse_code_to_blocks

    return [(parse_code_to_blocks, (data, "calibration.py", "calibration.py", "python"))]


def _jobs_xlsx(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.excel.limits import (
        excel_load_max_bytes,
    )
    from app.modules.parsers.excel.workbook_model import load_workbook_model

    return [(load_workbook_model, (data, excel_load_max_bytes(), 10**10, 480 * MIB, 240))]


def _jobs_inspect(data: bytes, _tmp: Path) -> list[tuple[Callable[..., object], tuple]]:
    from app.modules.parsers.pdf.pdf_inspection import detect_ocr, open_pdf, repair_pdf

    # What indexing runs through ``untrusted_pdf``: open, sample up to 30 pages for OCR, repair.
    # (The chat attachment path walks every page, on the interactive pool, which is not charged.)
    return [(open_pdf, (data,)), (detect_ocr, (data, 30)), (repair_pdf, (data,))]


def cells() -> list[Cell]:
    def words(seed: int = 1) -> corpus.Words:
        return corpus.Words(random.Random(seed))

    return [
        Cell("pdf_figures_60", "pdf_layout", 60, lambda: corpus.figures_pdf(words(), 60), _jobs_layout),
        Cell("pdf_figures_300", "pdf_layout", 300, lambda: corpus.figures_pdf(words(), 300), _jobs_layout),
        Cell("pdf_tables_300", "pdf_layout", 300, lambda: corpus.tables_pdf(words(), 300), _jobs_layout),
        Cell("pdf_digital_1000", "pdf_layout", 1000, lambda: corpus.digital_pdf(words(), 1000), _jobs_layout),
        Cell("pdf_large_113mb", "pdf_layout", 0, lambda: corpus.large_pdf(1, 87), _jobs_layout,
             "image-only, incompressible; file size is in the record"),
        Cell("md_1mb", "text", 0, lambda: corpus.sized_markdown(words(), 1 * MIB), _jobs_text),
        Cell("md_5mb", "text", 0, lambda: corpus.sized_markdown(words(), 5 * MIB), _jobs_text),
        Cell("md_20mb", "text", 0, lambda: corpus.sized_markdown(words(), 20 * MIB), _jobs_text),
        Cell("csv_5mb", "csv", 0, lambda: corpus.sized_csv(words(), 5 * MIB), _jobs_csv),
        Cell("csv_20mb", "csv", 0, lambda: corpus.sized_csv(words(), 20 * MIB), _jobs_csv),
        Cell("code_5mb", "code", 0, lambda: corpus.sized_code(words(), 5 * MIB), _jobs_code),
        Cell("xlsx_1mb", "excel_load", 0, lambda: corpus.sized_xlsx(1, 1 * MIB), _jobs_xlsx),
        Cell("xlsx_20mb", "excel_load", 0, lambda: corpus.sized_xlsx(1, 20 * MIB), _jobs_xlsx),
        Cell("render_scan_60", "render", 60, lambda: corpus.scanned_pdf(words(), 60), _jobs_render(3)),
        Cell("render_scan_300", "render", 300, lambda: corpus.scanned_pdf(words(), 300), _jobs_render(15)),
        Cell("inspect_scan_60", "inspect", 60, lambda: corpus.scanned_pdf(words(), 60), _jobs_inspect),
        Cell("inspect_digital_1000", "inspect", 1000, lambda: corpus.digital_pdf(words(), 1000), _jobs_inspect),
    ]


def run_once(cell: Cell, data: bytes, tmp: Path) -> dict[str, int | float]:
    import gc
    import tracemalloc

    from app.modules.parsers.parse_pool import _Worker

    jobs = cell.jobs(data, tmp)
    gc.collect()
    baseline = _rss_self()
    worker = _Worker()
    sampler = _Peak(worker.process.pid)
    sampler.start()
    started = time.monotonic()
    try:
        worker.receive()  # the worker's hello
        tracemalloc.start()
        retained = []
        for fn, args in jobs:
            worker.send((fn, args))
            ok, payload, trace = worker.receive()
            if not ok:
                raise RuntimeError(f"{cell.name}: {payload!r}\n{trace}")
            retained.append(payload)  # the parent holds the result, as the parser does until it builds blocks
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del retained
    finally:
        sampler.stop()
        worker.stop()
    # What the parent allocates to receive and hold the result (Python objects and arrays, by tracemalloc),
    # against its resident growth: the larger of the two is the parent's cost of the parse.
    return {
        "peak_bytes": sampler.group_peak,
        "parent_growth_bytes": max(traced_peak, sampler.parent_peak - baseline),
        "parent_traced_peak_bytes": traced_peak,
        "parent_rss_growth_bytes": max(0, sampler.parent_peak - baseline),
        "seconds": round(time.monotonic() - started, 2),
    }


def quiet(limit: float) -> None:
    while os.getloadavg()[0] >= limit:
        print(f"load average {os.getloadavg()[0]:.1f} >= {limit}; waiting", file=sys.stderr, flush=True)
        time.sleep(15)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--cells", default="", help="comma-separated cell names; default all")
    ap.add_argument("--loadavg", type=float, default=LOADAVG_LIMIT)
    args = ap.parse_args(argv)
    wanted = {c for c in args.cells.split(",") if c}
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "PX-2e-calibration.json"
    result: dict[str, Any] = json.loads(target.read_text()) if target.exists() else {}
    result.setdefault("cells", {})
    result["host"] = {
        "cpus_pinned": len(os.sched_getaffinity(0)),
        "python": sys.version.split()[0],
        "runs": args.runs,
        "page_size": os.sysconf("SC_PAGE_SIZE"),
    }
    for cell in cells():
        if wanted and cell.name not in wanted:
            continue
        quiet(args.loadavg)
        with tempfile.TemporaryDirectory(prefix="px2e-cal-") as tmp_name:
            tmp = Path(tmp_name)
            data = cell.build()
            runs = [run_once(cell, data, tmp) for _ in range(args.runs)]
        peaks = [int(r["peak_bytes"]) for r in runs]
        growth = [int(r["parent_growth_bytes"]) for r in runs]
        result["cells"][cell.name] = {
            "kind": cell.kind,
            "size_bytes": len(data),
            "pages": cell.pages,
            "note": cell.note,
            "runs": runs,
            "median_peak_bytes": int(statistics.median(peaks)),
            "median_parent_growth_bytes": int(statistics.median(growth)),
        }
        target.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        print(f"{cell.name}: peak {statistics.median(peaks) / MIB:.0f} MiB, size {len(data) / MIB:.1f} MiB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
