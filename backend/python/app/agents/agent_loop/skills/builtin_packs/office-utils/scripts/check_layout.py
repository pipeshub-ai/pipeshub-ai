#!/usr/bin/env python3
"""Check a generated document's layout before you deliver it.

Text extraction (pdftotext, pypdf, python-pptx) only proves the words are in the
file; it cannot see that they were drawn in the wrong place. This script looks at
where things land on the page.

  .pdf   renders every page to PNG (open them if you can view images) and flags:
         off_page, outside_margin, text_overlap, low_contrast, hidden_text,
         empty_box, blank_page
  .pptx  no renderer in this environment; checks shape geometry: off_page,
         text_overlap, blank_page
  .docx  Word lays out flowing text itself; checks what can still overflow:
         wide_table, wide_image, blank_page

Usage: python check_layout.py <file> [--pages-dir DIR] [--margin PT] [--json]
Exit code 0 when clean, 1 when a fault was found, 2 when the file cannot be read.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy
    from pdfplumber.page import Page

# Tuned on real reports: low enough to ignore glyphs that merely touch, kerning and
# light captions; high enough that any text actually drawn on other text is caught.
OVERLAP_SHARE = 0.15
MIN_CONTRAST = 2.0
INK_TOLERANCE = 90
MIN_INK_SHARE = 0.004
BOX_MIN_AREA = 2500.0
BOX_MIN_SIDE = 20.0
BOX_MAX_PAGE_SHARE = 0.85
BOX_FILL_SHARE = 0.8
RENDER_SCALE = 2.0
MAX_LISTED = 8

HINTS = {
    "off_page": "move it inside the page, or give it a width so it wraps",
    "outside_margin": "text at x=0 or y=0 usually means the position never reached the library: in pdfkit, "
    "doc.text(str, x, y, {width}), never doc.text(str, {x, y, width})",
    "text_overlap": "two things were drawn at the same place: compute each block's height "
    "(pdfkit doc.heightOfString) and move the next one below it",
    "low_contrast": "the text sits on a fill of nearly its own colour, often because it landed somewhere "
    "other than its panel: check its position, then its colour",
    "hidden_text": "a shape drawn after the text covers it: draw backgrounds first, then text",
    "empty_box": "the panel's content was drawn elsewhere, or never: check the positions of what belongs in it",
    "blank_page": "remove the page, or check why its content went to another page",
    "wide_table": "make the column widths sum to the page width minus its margins",
    "wide_image": "scale the image to the text width",
}


@dataclass
class Fault:
    kind: str
    page: int
    detail: str
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class Report:
    path: str
    pages: int = 0
    rendered: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    faults: list[Fault] = field(default_factory=list)


def _area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float, float]:
    return (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))


def _overlap_share(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    smaller = min(_area(a), _area(b))  # type: ignore[arg-type]
    return _area(_intersection(a, b)) / smaller if smaller > 0 else 0.0


def _contains(outer: tuple[float, ...], point: tuple[float, float]) -> bool:
    return outer[0] < point[0] < outer[2] and outer[1] < point[1] < outer[3]


def _centre(b: tuple[float, ...]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _rounded(b: tuple[float, ...]) -> tuple[float, float, float, float]:
    return (round(b[0], 1), round(b[1], 1), round(b[2], 1), round(b[3], 1))


def _overlaps(boxes: list[tuple[str, tuple[float, float, float, float]]], page: int) -> list[Fault]:
    faults: list[Fault] = []
    ordered = sorted(boxes, key=lambda item: item[1][1])
    for i, (text_a, a) in enumerate(ordered):
        for text_b, b in ordered[i + 1:]:
            if b[1] >= a[3]:
                break
            if _overlap_share(a, b) >= OVERLAP_SHARE:
                faults.append(Fault("text_overlap", page, f"{text_a!r} overlaps {text_b!r}", _rounded(a)))
    return faults


# PDF


def _rgb(colour: object) -> tuple[float, float, float] | None:
    if not isinstance(colour, (tuple, list)) or not all(isinstance(c, (int, float)) for c in colour):
        return None
    if len(colour) == 1:
        return (float(colour[0]),) * 3
    if len(colour) == 3:
        return (float(colour[0]), float(colour[1]), float(colour[2]))
    if len(colour) == 4:
        c, m, y, k = (float(v) for v in colour)
        return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _lines(words: list[dict]) -> list[tuple[str, tuple[float, float, float, float], tuple[float, float, float] | None]]:
    """Words joined into the runs they were drawn as, so a fault names a phrase, not one word."""
    runs: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        last = runs[-1][-1] if runs else None
        if (
            last is not None
            and abs(last["top"] - word["top"]) < 1.0
            and 0 <= word["x0"] - last["x1"] < word["size"] * 1.5
            and last.get("non_stroking_color") == word.get("non_stroking_color")
        ):
            runs[-1].append(word)
        else:
            runs.append([word])
    result = []
    for run in runs:
        box = (min(w["x0"] for w in run), min(w["top"] for w in run), max(w["x1"] for w in run), max(w["bottom"] for w in run))
        result.append((" ".join(w["text"] for w in run), box, _rgb(run[0].get("non_stroking_color"))))
    return result


def _box_like(shape: dict) -> bool:
    if shape["object_type"] == "rect":
        return True
    points = shape.get("pts") or []
    if len(points) < 4:
        return False
    # A rounded rectangle's path points fill its bounding box; a circle's four extremes make a diamond (half).
    polygon = sum(points[i][0] * points[i - 1][1] - points[i - 1][0] * points[i][1] for i in range(len(points)))
    box = (shape["x0"], shape["top"], shape["x1"], shape["bottom"])
    return abs(polygon) / 2 >= BOX_FILL_SHARE * _area(box)


def _panels(page: Page, words: list[dict], number: int) -> list[Fault]:
    page_area = float(page.width) * float(page.height)
    shapes = [s for s in list(page.rects) + list(page.curves) if s.get("fill") or s.get("stroke")]
    others = [(s["x0"], s["top"], s["x1"], s["bottom"]) for s in shapes + list(page.lines) + list(page.images)]
    faults: list[Fault] = []
    for shape in shapes:
        box = (shape["x0"], shape["top"], shape["x1"], shape["bottom"])
        width, height = box[2] - box[0], box[3] - box[1]
        if min(width, height) < BOX_MIN_SIDE or _area(box) < BOX_MIN_AREA or _area(box) > BOX_MAX_PAGE_SHARE * page_area:
            continue
        if not _box_like(shape):
            continue
        inner = (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1)
        holds_text = any(_contains(inner, _centre((w["x0"], w["top"], w["x1"], w["bottom"]))) for w in words)
        holds_graphic = any(_area(o) < _area(box) * 0.95 and _contains(inner, _centre(o)) for o in others)
        if not holds_text and not holds_graphic:
            faults.append(Fault("empty_box", number, f"a {width:.0f}x{height:.0f} pt box has nothing inside", _rounded(box)))
    return faults


def _ink(pixels: numpy.ndarray, box: tuple[float, ...], colour: tuple[float, float, float]) -> tuple[tuple[float, ...] | None, float]:
    import numpy as np

    x0, y0, x1, y1 = (int(round(v * RENDER_SCALE)) for v in box)
    crop = pixels[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return None, 1.0
    pad = 3
    ring = pixels[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad].reshape(-1, 3)
    values, counts = np.unique(ring, axis=0, return_counts=True)
    background = tuple(float(v) / 255 for v in values[counts.argmax()])
    target = np.array(colour) * 255
    near = np.linalg.norm(crop.reshape(-1, 3).astype(float) - target, axis=1) < INK_TOLERANCE
    return background, float(near.mean())


def _check_pdf(path: Path, report: Report, pages_dir: Path, margin: float) -> None:
    import numpy as np
    import pdfplumber
    import pypdfium2

    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered = pypdfium2.PdfDocument(str(path))
    report.checked = [
        "rendered every page to PNG", "text inside the page and its margins", "text drawn over other text",
        "text contrast against what is under it", "boxes and panels with nothing inside", "blank pages",
    ]
    with pdfplumber.open(str(path)) as pdf:
        report.pages = len(pdf.pages)
        for index, page in enumerate(pdf.pages):
            number = index + 1
            image = rendered[index].render(scale=RENDER_SCALE).to_pil().convert("RGB")
            png = pages_dir / f"{path.stem}-page-{number}.png"
            image.save(png)
            report.rendered.append(str(png))
            pixels = np.asarray(image)
            width, height = float(page.width), float(page.height)
            words = page.extract_words(extra_attrs=["non_stroking_color", "size"], keep_blank_chars=False)
            lines = _lines(words)

            for text, box, _ in lines:
                if box[0] < -1 or box[1] < -1 or box[2] > width + 1 or box[3] > height + 1:
                    report.faults.append(Fault("off_page", number, f"{text!r} runs off the page", _rounded(box)))
                elif box[0] < margin or box[1] < margin or box[2] > width - margin or box[3] > height - margin:
                    report.faults.append(Fault("outside_margin", number, f"{text!r} is within {margin:.0f} pt of the edge", _rounded(box)))

            report.faults.extend(_overlaps([(t, b) for t, b, _ in lines], number))

            for text, box, colour in lines:
                if colour is None:
                    continue
                background, share = _ink(pixels, box, colour)
                if background is None:
                    continue
                ratio = _contrast(colour, background)
                if ratio < MIN_CONTRAST:
                    report.faults.append(Fault("low_contrast", number, f"{text!r} has contrast {ratio:.1f}:1 against what is under it", _rounded(box)))
                elif share < MIN_INK_SHARE:
                    report.faults.append(Fault("hidden_text", number, f"{text!r} is covered by something drawn over it", _rounded(box)))

            report.faults.extend(_panels(page, words, number))

            gray = pixels.mean(axis=2)
            values, counts = np.unique(gray.astype(int), return_counts=True)
            inked = float((np.abs(gray - values[counts.argmax()]) > 8).mean())
            if not words and not page.images and inked < 0.005:
                report.faults.append(Fault("blank_page", number, "the page is blank"))


# PPTX and DOCX


def _check_pptx(path: Path, report: Report) -> None:
    from pptx import Presentation

    deck = Presentation(str(path))
    width, height = float(deck.slide_width or 0) / 12700, float(deck.slide_height or 0) / 12700
    report.pages = len(deck.slides)
    report.checked = ["shapes inside the slide", "text boxes drawn over other text boxes", "blank slides",
                      "not rendered: no renderer in this environment"]
    for number, slide in enumerate(deck.slides, start=1):
        texts: list[tuple[str, tuple[float, float, float, float]]] = []
        shapes = [s for s in slide.shapes if s.left is not None and s.width is not None]
        for shape in shapes:
            box = (shape.left / 12700, shape.top / 12700, (shape.left + shape.width) / 12700, (shape.top + shape.height) / 12700)
            if box[0] < -1 or box[1] < -1 or box[2] > width + 1 or box[3] > height + 1:
                report.faults.append(Fault("off_page", number, f"shape {shape.name!r} runs off the slide", _rounded(box)))
            text = shape.text_frame.text.strip() if shape.has_text_frame else ""
            if text:
                texts.append((text[:40], box))
        report.faults.extend(_overlaps(texts, number))
        if not shapes:
            report.faults.append(Fault("blank_page", number, "the slide is blank"))


def _check_docx(path: Path, report: Report) -> None:
    from docx import Document

    document = Document(str(path))
    report.pages = 0
    report.checked = ["tables and images fit the text width of their section", "the document is not empty",
                      "not rendered: Word lays out flowing text, and there is no renderer in this environment"]
    section = document.sections[0]
    usable = (section.page_width or 0) - (section.left_margin or 0) - (section.right_margin or 0)
    for number, table in enumerate(document.tables, start=1):
        total = sum((column.width or 0) for column in table.columns)
        if usable and total > usable * 1.02:
            report.faults.append(Fault("wide_table", 0, f"table {number} is {total / 12700:.0f} pt wide; the text width is {usable / 12700:.0f} pt"))
    for number, shape in enumerate(document.inline_shapes, start=1):
        if usable and (shape.width or 0) > usable * 1.02:
            report.faults.append(Fault("wide_image", 0, f"image {number} is wider than the text width"))
    if not any(p.text.strip() for p in document.paragraphs) and not document.tables and not document.inline_shapes:
        report.faults.append(Fault("blank_page", 0, "the document is empty"))


def check(path: str | Path, pages_dir: str | Path | None = None, margin: float = 18.0) -> Report:
    source = Path(path)
    report = Report(path=str(source))
    kind = source.suffix.lower()
    if kind == ".pdf":
        _check_pdf(source, report, Path(pages_dir or Path("layout_check") / source.stem), margin)
    elif kind == ".pptx":
        _check_pptx(source, report)
    elif kind == ".docx":
        _check_docx(source, report)
    else:
        raise ValueError(f"unsupported file type {kind!r}: expected .pdf, .pptx or .docx")
    return report


def _render(report: Report) -> str:
    lines = [f"{report.path}: {report.pages} page(s)", "checked: " + "; ".join(report.checked)]
    lines += [f"rendered: {png}" for png in report.rendered]
    if not report.faults:
        lines.append("layout: clean")
        return "\n".join(lines) + "\n"
    kinds: dict[str, list[Fault]] = {}
    for fault in report.faults:
        kinds.setdefault(fault.kind, []).append(fault)
    lines.append(f"layout: {len(report.faults)} fault(s): " + ", ".join(f"{k} x{len(v)}" for k, v in sorted(kinds.items())))
    for kind, faults in sorted(kinds.items()):
        for fault in faults[:MAX_LISTED]:
            where = f"page {fault.page}" if fault.page else "document"
            at = f" at {fault.bbox}" if fault.bbox else ""
            lines.append(f"  {kind}: {where}: {fault.detail}{at}")
        if len(faults) > MAX_LISTED:
            lines.append(f"  {kind}: ... and {len(faults) - MAX_LISTED} more")
        lines.append(f"  fix: {HINTS[kind]}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file")
    parser.add_argument("--pages-dir", help="where page PNGs go (default ./layout_check/<name>); keep it out of out/")
    parser.add_argument("--margin", type=float, default=18.0, help="smallest gap, in points, between text and the page edge")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = check(args.file, args.pages_dir, args.margin)
    except Exception as error:  # any read failure is the same answer: fix the file first
        sys.stderr.write(f"cannot check {args.file}: {error}\n")
        return 2
    if args.json:
        sys.stdout.write(json.dumps(asdict(report), indent=2) + "\n")
    else:
        sys.stdout.write(_render(report))
    return 1 if report.faults else 0


if __name__ == "__main__":
    sys.exit(main())
