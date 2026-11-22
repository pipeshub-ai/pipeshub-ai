"""The mixed-kinds corpus and the memory oracle of the per-kind charges (PX-2e, T88 and T89).

``generate`` writes 40 records of the kinds a laptop sees together: PDF layout, 1, 5 and 20 MB
markdown, CSV, source code, a 20 MB workbook, a 60-page scan (OCR) and DOCX. ``oracle`` reads a
driver result of ``--mix kinds`` and compares the process tree's resident memory, sample by sample,
with the sum of the charges of the jobs running at that moment (from ``resource_governor/kinds.py``).

    python -m tests.perf.parse_bench.kinds_mix generate --out DIR
    python -m tests.perf.parse_bench.kinds_mix oracle RESULT.json [--limit-gb 4]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

from tests.perf.parse_bench import corpus

GIB = 2**30
MIB = 2**20
# kind -> (count, extension, mime, maker size in bytes or pages)
MIX = {
    "figures": (6, "pdf"), "scanned": (4, "pdf"), "docx": (6, "docx"), "md1": (4, "md"), "md5": (6, "md"),
    "md20": (2, "md"), "csv5": (6, "csv"), "code5": (4, "py"), "xlsx20": (2, "xlsx"),
}
MIMES = {
    "pdf": "application/pdf", "md": "text/markdown", "csv": "text/csv", "py": "text/x-python",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def generate(out: Path, seed: int = 1) -> int:
    out.mkdir(parents=True, exist_ok=True)
    words = corpus.Words(random.Random(seed))
    manifest = corpus.Manifest(seed=seed, profile="kinds-mix", params={"mix": {k: v[0] for k, v in MIX.items()}})
    xlsx = None
    for kind, (count, ext) in MIX.items():
        for i in range(count):
            if kind == "figures":
                data, pages = corpus.figures_pdf(words, 60), 60
            elif kind == "scanned":
                data, pages = corpus.scanned_pdf(words, 60), 60
            elif kind == "docx":
                data, pages = corpus.docx_file(words, 12), 12
            elif kind.startswith("md"):
                data, pages = corpus.sized_markdown(words, int(kind[2:]) * MIB), 0
            elif kind == "csv5":
                data, pages = corpus.sized_csv(words, 5 * MIB), 0
            elif kind == "code5":
                data, pages = corpus.sized_code(words, 5 * MIB), 0
            else:
                xlsx = xlsx or corpus.sized_xlsx(seed, 20 * MIB)
                data, pages = xlsx, 0
            name = f"{kind}-{i:02d}.{ext}"
            (out / name).write_bytes(data)
            manifest.entries.append(corpus.Entry(
                path=name, kind=kind, ext=ext, mime=MIMES[ext], bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(), pages=pages,
            ))
            manifest.total_bytes += len(data)
    (out / corpus.MANIFEST).write_text(manifest.to_json())
    print(f"{len(manifest.entries)} files, {manifest.total_bytes / MIB:.0f} MiB, corpus_id {manifest.corpus_id}")
    return 0


def kind_of(entry_kind: str):  # noqa: ANN201
    from app.services.resource_governor.kinds import ParseKind

    return {
        "figures": ParseKind.PDF_LAYOUT, "scanned": ParseKind.RENDER, "docx": ParseKind.DOCLING_DOCX,
        "csv5": ParseKind.CSV, "code5": ParseKind.CODE, "xlsx20": ParseKind.EXCEL_LOAD,
    }.get(entry_kind, ParseKind.TEXT)


def job_charge(entry_kind: str, size: int) -> int:
    """What the lane charges plus what the parent's heavy permit holds for one job of this record."""
    from app.services.resource_governor.kinds import (
        ParseKind,
        lane_charge_bytes,
        parent_working_set_gb,
    )

    kind = kind_of(entry_kind)
    lane = 0 if kind is ParseKind.DOCLING_DOCX else lane_charge_bytes(kind, size)
    return lane + int(parent_working_set_gb(kind) * GIB)


def oracle(result: dict, limit_gb: float | None) -> dict:
    """Peak tree RSS against 0.85 x limit and against idle parent + 1.10 x the charges running at that time."""
    series = result["oracle"]["tree_series"]  # [t, tree_rss]
    jobs = [(o["start"], o["end"], job_charge(o["kind"], o["bytes"])) for o in result["oracle"]["jobs"]]
    idle = result["metrics"]["memory"]["parent_idle_rss_mib"] * MIB
    worst, worst_at, peak = 0.0, None, 0
    for t, rss in series:
        total = sum(c for s, e, c in jobs if s <= t <= e)
        peak = max(peak, rss)
        if total and rss - idle > 0:
            ratio = (rss - idle) / total
            if ratio > worst:
                worst, worst_at = ratio, (round(t, 1), round(rss / MIB), round(total / MIB))
    out = {
        "peak_tree_rss_mib": round(peak / MIB, 1), "idle_parent_mib": round(idle / MIB, 1),
        "max_ratio_over_idle_to_charges": round(worst, 3), "at_t_rss_charges_mib": worst_at,
        "oracle_ok": worst <= 1.10,
    }
    if limit_gb:
        out["limit_gb"] = limit_gb
        out["peak_over_limit"] = round(peak / (limit_gb * GIB), 3)
        out["limit_ok"] = peak <= 0.85 * limit_gb * GIB
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("--seed", type=int, default=1)
    o = sub.add_parser("oracle")
    o.add_argument("result", type=Path)
    o.add_argument("--limit-gb", type=float, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "generate":
        return generate(args.out, args.seed)
    print(json.dumps(oracle(json.loads(args.result.read_text()), args.limit_gb), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
