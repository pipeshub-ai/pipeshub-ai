"""Docling layout on the ONNX engine against the PyTorch engine, on generated PDFs (CPU only).

The image carries the heron layout model twice: PyTorch weights for Docling's layout stage and the ONNX
export for the table detector (Docling's own ONNX engine used 1.3 to 1.7 times the CPU per page). This check
keeps the two serializations equivalent and reports the cost:

* the layout clusters (label, box) are the same for both engines (a failure), and
* CPU and wall per page and their ratios are printed. ``--max-cpu-ratio`` turns the CPU ratio into a failure;
  it is off by default.

    python docling_layout_engine_check.py <models-dir>

<models-dir> is the output of deployment/bake-docling-models.py: the PyTorch layout weights (pinned in
app.config.constants.docling_models) and the ONNX export are both read from it, so the pins that ship are the
ones compared. Prints CPU and wall seconds per page for both engines.
"""

import argparse
import io
import json
import statistics
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter


def make_pdfs() -> list[bytes]:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import (
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    lorem = "The quarterly report summarises throughput, latency and error budgets across all regions. " * 4
    pdfs = []
    for variant, size in enumerate((A4, letter, A4)):
        story = []
        for page in range(3):
            story += [Paragraph(f"Section {variant}.{page}: results", styles["Title"]), Paragraph(lorem, styles["BodyText"]), Spacer(1, 12)]
            rows = [["Region", "Requests", "p50 ms", "p99 ms", "Errors"]] + [
                [f"region-{variant}{page}{i}", str(1000 * (i + 1)), f"{10 + i}.5", f"{90 + 3 * i}.2", str(i % 3)] for i in range(8 + variant * 3)
            ]
            table = Table(rows)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
            story += [table, Spacer(1, 12), Paragraph(lorem, styles["BodyText"]), PageBreak()]
        buf = io.BytesIO()
        SimpleDocTemplate(buf, pagesize=size).build(story)
        pdfs.append(buf.getvalue())
    return pdfs


def converter(models_dir: Path, onnx: bool) -> "DocumentConverter":
    from docling.datamodel.accelerator_options import AcceleratorDevice
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.object_detection_engine_options import (
        OnnxRuntimeObjectDetectionEngineOptions,
    )
    from docling.datamodel.pipeline_options import (
        LayoutObjectDetectionOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions(do_ocr=False, do_table_structure=True, artifacts_path=models_dir)
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.accelerator_options.device = AcceleratorDevice.CPU
    opts.accelerator_options.num_threads = 4
    if onnx:
        opts.layout_options = LayoutObjectDetectionOptions(engine_options=OnnxRuntimeObjectDetectionEngineOptions())
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def run(conv, pdfs: list[bytes], passes: int) -> tuple[list[dict], float, float]:
    from docling.datamodel.base_models import DocumentStream

    def one_pass() -> tuple[list[dict], float, float, int]:
        pages_out, cpu, wall, n = [], 0.0, 0.0, 0
        for i, data in enumerate(pdfs):
            c0, w0 = time.process_time(), time.time()
            res = conv.convert(DocumentStream(name=f"d{i}.pdf", stream=io.BytesIO(data)))
            cpu += time.process_time() - c0
            wall += time.time() - w0
            for page in res.pages:
                clusters = sorted(
                    (str(cl.label), [round(cl.bbox.l, 1), round(cl.bbox.t, 1), round(cl.bbox.r, 1), round(cl.bbox.b, 1)])
                    for cl in page.predictions.layout.clusters
                )
                pages_out.append({"doc": i, "page": page.page_no, "clusters": clusters})
            n += len(res.pages)
        return pages_out, cpu, wall, n

    one_pass()  # warm-up: model load and first-call costs are not per-page cost
    out, cpus, walls = [], [], []
    for _ in range(passes):
        out, cpu, wall, n = one_pass()
        cpus.append(cpu / n)
        walls.append(wall / n)
    return out, statistics.median(cpus), statistics.median(walls)


def iou(a: list[float], b: list[float]) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 1.0


def has_full_matching(xs: list[list[float]], ys: list[list[float]], min_iou: float) -> bool:
    """True when every box in ``xs`` can be paired with its own box in ``ys`` at IoU >= ``min_iou``.

    Pairing by sorted position is wrong: a 0.1 shift in x sorts two same-label boxes the other way round and
    crosses the pairs. This is a maximum bipartite matching (augmenting paths), so the pairing is the best one."""
    if len(xs) != len(ys):
        return False
    owner: dict[int, int] = {}

    def assign(i: int, seen: set[int]) -> bool:
        for j, y in enumerate(ys):
            if j not in seen and iou(xs[i], y) >= min_iou:
                seen.add(j)
                if j not in owner or assign(owner[j], seen):
                    owner[j] = i
                    return True
        return False

    return all(assign(i, set()) for i in range(len(xs)))


def differences(a: list[dict], b: list[dict], min_iou: float) -> list[str]:
    bad = []
    for pa, pb in zip(a, b, strict=True):
        ca, cb = pa["clusters"], pb["clusters"]
        where = f"doc {pa['doc']} page {pa['page']}"
        if [c[0] for c in ca] != [c[0] for c in cb]:
            bad.append(f"{where}: labels {[c[0] for c in ca]} vs {[c[0] for c in cb]}")
            continue
        bad += [
            f"{where}: the {label} boxes do not pair one-to-one at IoU >= {min_iou}"
            for label in sorted({c[0] for c in ca})
            if not has_full_matching([c[1] for c in ca if c[0] == label], [c[1] for c in cb if c[0] == label], min_iou)
        ]
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("models_dir", type=Path)
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--max-cpu-ratio", type=float, default=0.0, help="fail above this ONNX/PyTorch CPU ratio; 0 = report only")
    ap.add_argument("--min-iou", type=float, default=0.99)
    ap.add_argument("--onnx-no-spin", action="store_true", help="diagnostic: turn off onnxruntime thread spinning (Docling does not expose it; on a loaded Mac it left ONNX CPU unchanged)")
    args = ap.parse_args()

    from app.config.constants.docling_models import (
        LAYOUT_HERON_FILES,
        LAYOUT_HERON_ONNX_FILES,
        LAYOUT_HERON_ONNX_REPO,
        LAYOUT_HERON_REPO,
    )

    for repo, files in ((LAYOUT_HERON_REPO, LAYOUT_HERON_FILES), (LAYOUT_HERON_ONNX_REPO, LAYOUT_HERON_ONNX_FILES)):
        missing = [f for f in files if not (args.models_dir / repo.replace("/", "--") / f).is_file()]
        if missing:
            sys.stderr.write(f"FAIL {repo}: {missing} not in {args.models_dir}; bake it with deployment/bake-docling-models.py\n")
            return 1
    if args.onnx_no_spin:
        import onnxruntime as ort

        real = ort.InferenceSession

        def no_spin_session(path: str, sess_options: object = None, *a: object, **k: object) -> object:
            sess_options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            sess_options.add_session_config_entry("session.inter_op.allow_spinning", "0")
            return real(path, sess_options, *a, **k)

        ort.InferenceSession = no_spin_session
    pdfs = make_pdfs()
    onnx_out, onnx_cpu, onnx_wall = run(converter(args.models_dir, onnx=True), pdfs, args.passes)
    torch_out, torch_cpu, torch_wall = run(converter(args.models_dir, onnx=False), pdfs, args.passes)

    n_clusters = sum(len(p["clusters"]) for p in onnx_out)
    ratio = onnx_cpu / torch_cpu
    sys.stdout.write(json.dumps({
        "pages": len(onnx_out), "clusters": n_clusters,
        "onnx": {"cpu_s_per_page": round(onnx_cpu, 3), "wall_s_per_page": round(onnx_wall, 3)},
        "pytorch": {"cpu_s_per_page": round(torch_cpu, 3), "wall_s_per_page": round(torch_wall, 3)},
        "cpu_ratio": round(ratio, 3),
        "wall_ratio": round(onnx_wall / torch_wall, 3),
    }, indent=2) + "\n")

    failures = differences(onnx_out, torch_out, args.min_iou)
    if n_clusters == 0:
        failures.append("no layout clusters found: the check did not exercise the model")
    if args.max_cpu_ratio and ratio > args.max_cpu_ratio:
        failures.append(f"ONNX CPU per page is {ratio:.2f}x PyTorch (limit {args.max_cpu_ratio:.2f}x)")
    for line in failures:
        sys.stderr.write(f"FAIL {line}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
