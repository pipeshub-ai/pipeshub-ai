"""Peak memory and wall time of a chat PDF attachment: the inspection, then the whole chat path.

Each measurement runs in a fresh child process, as a parse worker would, and its
peak RSS is read from the kernel (``wait4`` rusage), so one run never sees
another's high-water mark. Prints one JSON line per run. Not collected by pytest.

    python tests/perf/bench_chat_pdf.py --checkout <repo>/backend/python \\
        --kind text --pages 1000 --step inspect --runs 3 --workdir /tmp/x

Steps:
  inspect  needs_ocr_and_page_count alone (the first parse-pool job of an upload).
  chat     what the upload route does for one PDF: the inspection, then either
           the page render for OCR or the layout parse plus block build.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()


def make_pdf(kind: str, pages: int, path: Path) -> None:
    """A generated text PDF, or an image-only one (every page a full-page picture)."""
    import io

    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    canvas_ = canvas.Canvas(str(path))
    if kind == "image":
        import numpy as np
        from PIL import Image

        noise = np.random.default_rng(0).integers(0, 255, (1100, 780, 3), dtype="uint8")
        buf = io.BytesIO()
        Image.fromarray(noise).save(buf, format="JPEG", quality=60)
        image = ImageReader(io.BytesIO(buf.getvalue()))
    for page in range(pages):
        if kind == "text":
            canvas_.setFont("Helvetica", 11)
            for row in range(60):
                canvas_.drawString(40, 800 - row * 13, f"Page {page} row {row}: the quick brown fox jumps over the lazy dog " * 1)
        else:
            canvas_.drawImage(image, 0, 0, width=595, height=842)
        canvas_.showPage()
    canvas_.save()


def _child(step: str, pdf: str) -> None:
    from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
    from app.modules.parsers.pdf.pdf_inspection import needs_ocr_and_page_count
    from app.modules.parsers.pdf.pdf_rasterizer import render_all_pages_as_png

    content = Path(pdf).read_bytes()
    needs_ocr, pages = needs_ocr_and_page_count(content)
    result = {"needs_ocr": needs_ocr, "pages": pages}
    if step == "chat":
        # Read from source: importing the route would add its imports to the measured peak.
        import re

        found = re.search(r"^CHAT_PDF_PAGE_CAP = (\d+)", Path("app/api/routes/chatbot.py").read_text(), re.M)
        CHAT_PDF_PAGE_CAP = int(found.group(1)) if found else None  # None: a checkout from before the cap
        if CHAT_PDF_PAGE_CAP is not None and pages > CHAT_PDF_PAGE_CAP:
            result["rejected"] = True
        elif needs_ocr:
            result["rendered"] = len(render_all_pages_as_png(content, 144))
        else:
            import logging

            from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
                PDFPlumberOpenCVProcessor,
            )

            parsed = parse_pdf_layout(content)
            processor = PDFPlumberOpenCVProcessor(logger=logging.getLogger("bench"), config=None)
            blocks = asyncio.run(processor.create_blocks(parsed, skip_llm_enrichment=True))
            result["blocks"] = len(blocks.blocks)
    print(json.dumps(result))


def _run(checkout: str, step: str, pdf: Path, tmp: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": checkout, "TMPDIR": str(tmp)}
    started = time.monotonic()
    proc = subprocess.Popen(
        ["taskset", "-c", "0-3", sys.executable, str(HERE), "--child", step, str(pdf)],
        cwd=checkout, env=env, stdout=subprocess.PIPE, text=True,
    )
    out = proc.stdout.read()
    _, status, usage = os.wait4(proc.pid, 0)
    wall = time.monotonic() - started
    return {"ok": status == 0, "wall_s": wall, "peak_rss_mib": usage.ru_maxrss / 1024, **json.loads(out.strip().splitlines()[-1])}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3])
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--kind", choices=["text", "image"], required=True)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--step", choices=["inspect", "chat"], required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    pdf = work / f"{args.kind}-{args.pages}.pdf"
    if not pdf.exists():
        make_pdf(args.kind, args.pages, pdf)
    runs = [_run(args.checkout, args.step, pdf, work) for _ in range(args.runs)]
    print(json.dumps({
        "kind": args.kind, "pages": args.pages, "step": args.step,
        "peak_rss_mib_median": round(statistics.median(r["peak_rss_mib"] for r in runs), 1),
        "wall_s_median": round(statistics.median(r["wall_s"] for r in runs), 2),
        "ok": all(r["ok"] for r in runs), "last": runs[-1],
    }))


if __name__ == "__main__":
    main()
