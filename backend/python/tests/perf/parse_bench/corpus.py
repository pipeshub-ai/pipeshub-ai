"""Deterministic, seeded corpus for the parse benchmark. No downloads.

The same ``--seed`` and parameters always give the same content, so a
golden file made on one checkout can be checked on another. PDFs are
generated with reportlab's ``invariant`` mode (no timestamps), DOCX zips get
a fixed entry time, and all randomness comes from one seeded generator.

Kinds (the PDF-weighted mix of the parallelism plan):

* ``digital``  text PDFs
* ``scanned``  page images with no text layer, so the parser sends them to OCR
* ``tables``   ruled tables, several per page
* ``figures``  drawn charts embedded as images, with captions
* ``damaged``  a digital PDF with a cut tail, a wrong ``startxref`` or junk in a stream
* ``large``    one PDF over 100 MB (incompressible images); ``--large-mb 0`` skips it
* ``docx``     Word files (the Docling path)
* ``md``, ``json``, ``txt``  small light files, for the I/O-heavy mixes

Run: ``python -m tests.perf.parse_bench.corpus --out DIR [--profile standard]``
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import shutil
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MANIFEST = "manifest.json"
MIB = 1024 * 1024

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
    "json": "application/json",
    "txt": "text/plain",
}

# Counts per kind for scale 1.0. ``pages`` is the page count of each file.
PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "digital": (1, 2), "scanned": (1, 2), "tables": (1, 2), "figures": (1, 2),
        "damaged": (1, 2), "docx": (1, 2), "md": 2, "json": 1, "txt": 1,
        "large_mb": 0, "scanned_boundary_pages": 0,
    },
    "standard": {
        "digital": (6, 10), "scanned": (4, 6), "tables": (5, 6), "figures": (4, 6),
        "damaged": (3, 6), "docx": (4, 12), "md": 20, "json": 6, "txt": 10,
        "large_mb": 105, "scanned_boundary_pages": 60,
    },
}


@dataclass
class Entry:
    path: str
    kind: str
    ext: str
    mime: str
    bytes: int
    sha256: str
    pages: int = 0


@dataclass
class Manifest:
    seed: int
    profile: str
    params: dict[str, Any]
    total_bytes: int = 0
    budget: int = 1 << 60
    entries: list[Entry] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=True)

    @property
    def corpus_id(self) -> str:
        """Changes when any file's content or the parameters change."""
        digest = hashlib.sha256()
        digest.update(json.dumps(self.params, sort_keys=True).encode())
        for entry in self.entries:
            digest.update(f"{entry.path}:{entry.sha256}".encode())
        return digest.hexdigest()[:16]


def load_manifest(corpus_dir: Path) -> Manifest:
    raw = json.loads((corpus_dir / MANIFEST).read_text())
    entries = [Entry(**e) for e in raw.pop("entries")]
    return Manifest(entries=entries, **raw)


# -- text ---------------------------------------------------------------------

_SYLLABLES = ("ka", "lo", "mi", "ra", "tu", "ve", "no", "sa", "di", "pe", "xo", "ul", "an", "er")


class Words:
    """A seeded vocabulary and sentence maker."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.vocab = sorted({
            "".join(rng.choice(_SYLLABLES) for _ in range(rng.randint(1, 3))) for _ in range(500)
        })

    def sentence(self, low: int = 6, high: int = 16) -> str:
        words = [self.rng.choice(self.vocab) for _ in range(self.rng.randint(low, high))]
        return words[0].capitalize() + " " + " ".join(words[1:]) + "."

    def paragraph(self, sentences: int = 5) -> str:
        return " ".join(self.sentence() for _ in range(sentences))

    def title(self) -> str:
        return " ".join(w.capitalize() for w in self.rng.sample(self.vocab, 3))


# -- PDF ----------------------------------------------------------------------


def _doc(buf: io.BytesIO):  # noqa: ANN202
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate

    return SimpleDocTemplate(buf, pagesize=A4, invariant=1, title="bench", author="bench")


def _styles():  # noqa: ANN202
    from reportlab.lib.styles import getSampleStyleSheet

    return getSampleStyleSheet()


def digital_pdf(words: Words, pages: int) -> bytes:
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    styles = _styles()
    story: list[Any] = []
    for page in range(pages):
        story.append(Paragraph(words.title(), styles["Heading1"]))
        for _ in range(4):
            story.append(Paragraph(words.paragraph(6), styles["BodyText"]))
            story.append(Spacer(1, 6))
        story.append(Paragraph(words.title(), styles["Heading2"]))
        story.extend(Paragraph(words.paragraph(5), styles["BodyText"]) for _ in range(3))
        if page != pages - 1:
            story.append(PageBreak())
    buf = io.BytesIO()
    _doc(buf).build(story)
    return buf.getvalue()


def tables_pdf(words: Words, pages: int) -> bytes:
    from reportlab.lib import colors
    from reportlab.platypus import PageBreak, Paragraph, Spacer, Table, TableStyle

    styles = _styles()
    story: list[Any] = []
    for page in range(pages):
        story.append(Paragraph(words.title(), styles["Heading2"]))
        for _ in range(2):
            header = [words.rng.choice(words.vocab).capitalize() for _ in range(5)]
            rows = [header] + [
                [words.rng.choice(words.vocab)] + [str(words.rng.randint(10, 99999)) for _ in range(4)]
                for _ in range(12)
            ]
            table = Table(rows, repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]))
            story += [table, Spacer(1, 14), Paragraph(words.paragraph(2), styles["BodyText"]), Spacer(1, 10)]
        if page != pages - 1:
            story.append(PageBreak())
    buf = io.BytesIO()
    _doc(buf).build(story)
    return buf.getvalue()


def _chart_png(rng: random.Random, size: tuple[int, int] = (900, 520)) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 20, size[0] - 20, size[1] - 40], outline="black")
    bars = rng.randint(5, 9)
    width = (size[0] - 100) // bars
    for i in range(bars):
        height = rng.randint(40, size[1] - 120)
        x0 = 60 + i * width
        colour = (rng.randint(30, 220), rng.randint(30, 220), rng.randint(30, 220))
        draw.rectangle([x0, size[1] - 50 - height, x0 + width - 12, size[1] - 50], fill=colour)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def figures_pdf(words: Words, pages: int) -> bytes:
    from reportlab.lib.units import cm
    from reportlab.platypus import Image, PageBreak, Paragraph, Spacer

    styles = _styles()
    story: list[Any] = []
    for page in range(pages):
        story.append(Paragraph(words.title(), styles["Heading2"]))
        for n in range(2):
            story.append(Image(io.BytesIO(_chart_png(words.rng)), width=14 * cm, height=8 * cm))
            story.append(Paragraph(f"Figure {page * 2 + n + 1}. {words.sentence()}", styles["Italic"]))
            story.append(Spacer(1, 8))
        story.append(Paragraph(words.paragraph(4), styles["BodyText"]))
        if page != pages - 1:
            story.append(PageBreak())
    buf = io.BytesIO()
    _doc(buf).build(story)
    return buf.getvalue()


def scanned_pdf(words: Words, pages: int) -> bytes:
    """Each page is one JPEG of rendered text lines and no text layer."""
    from PIL import Image, ImageDraw
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for _ in range(pages):
        img = Image.new("L", (1240, 1754), 245)
        draw = ImageDraw.Draw(img)
        for line in range(55):
            draw.text((90, 80 + line * 28), words.sentence(8, 14), fill=20)
        jpeg = io.BytesIO()
        img.save(jpeg, "JPEG", quality=70)
        jpeg.seek(0)
        pdf.drawImage(ImageReader(jpeg), 0, 0, width=A4[0], height=A4[1])
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def damaged_pdf(words: Words, pages: int, variant: int) -> bytes:
    data = bytearray(digital_pdf(words, pages))
    if variant % 3 == 0:  # tail cut off: no xref, no trailer
        return bytes(data[: int(len(data) * 0.6)])
    if variant % 3 == 1:  # startxref points nowhere
        idx = data.rfind(b"startxref")
        return bytes(data[: idx] + b"startxref\n999999999\n%%EOF\n")
    mid = len(data) // 2  # junk inside the body
    data[mid : mid + 200] = bytes((i * 37) % 256 for i in range(200))
    return bytes(data)


LARGE_FILE_FACTOR = 1.3  # PDF bytes per raw pixel byte (ASCII85 + Flate of noise), measured: 141.6 MB / 108 MiB


def large_pdf(seed: int, target_mb: int) -> bytes:
    """Images of seeded noise, which do not compress.

    Up to one 4096x3072 image (36 MiB of pixels) the image is shrunk so the pixel data is
    *target_mb*; above that whole images are added, so the file can be up to one image over.
    The file is about ``LARGE_FILE_FACTOR`` times the pixel bytes. ``generate`` checks the
    real size against ``--max-mb``.
    """
    import numpy as np
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    width, height = 4096, 3072
    per_image = width * height * 3
    count = max(1, math.ceil(target_mb * MIB / per_image))
    if count == 1:
        height = max(16, min(height, target_mb * MIB // (width * 3)))
    rng = np.random.default_rng(seed)
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for _ in range(count):
        noise = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
        pdf.drawImage(ImageReader(Image.fromarray(noise)), 20, 150, width=A4[0] - 40, height=400)
        pdf.drawString(60, 700, "Large file: scanned appendix")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


# -- DOCX and light files -----------------------------------------------------


def docx_file(words: Words, pages: int) -> bytes:
    import datetime

    from docx import Document

    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    doc.core_properties.author = "bench"
    for _ in range(pages):
        doc.add_heading(words.title(), level=1)
        for _ in range(4):
            doc.add_paragraph(words.paragraph(6))
        table = doc.add_table(rows=6, cols=4)
        table.style = "Table Grid"
        for row in table.rows:
            for cell in row.cells:
                cell.text = words.rng.choice(words.vocab)
        doc.add_picture(io.BytesIO(_chart_png(words.rng, (500, 300))))
    raw = io.BytesIO()
    doc.save(raw)
    return _fixed_zip(raw.getvalue())


def _fixed_zip(data: bytes) -> bytes:
    """Same entries, same order, one fixed timestamp: the bytes stop depending on the clock."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            clone = zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0))
            clone.compress_type = zipfile.ZIP_DEFLATED
            clone.external_attr = info.external_attr
            dst.writestr(clone, src.read(info.filename))
    return out.getvalue()


def markdown_file(words: Words) -> bytes:
    lines = [f"# {words.title()}", ""]
    for _ in range(words.rng.randint(4, 9)):
        lines += [f"## {words.title()}", "", words.paragraph(5), ""]
        lines += [f"- {words.sentence()}" for _ in range(3)] + [""]
        lines += ["```python", f"value = {words.rng.randint(1, 999)}", "```", ""]
    return "\n".join(lines).encode()


def json_file(words: Words, records: int) -> bytes:
    rows = [
        {"id": i, "name": words.title(), "tags": words.rng.sample(words.vocab, 3),
         "body": words.paragraph(2), "score": round(words.rng.random(), 4)}
        for i in range(records)
    ]
    return json.dumps({"items": rows}, indent=1).encode()


def text_file(words: Words) -> bytes:
    return "\n\n".join(words.paragraph(6) for _ in range(words.rng.randint(5, 20))).encode()


# -- driver -------------------------------------------------------------------


class CorpusTooLarge(ValueError):
    """The corpus would cross ``--max-mb``."""


def _put(out: Path, name: str, kind: str, ext: str, data: bytes, pages: int, manifest: Manifest) -> None:
    budget = manifest.budget
    if manifest.total_bytes + len(data) > budget:
        raise CorpusTooLarge(
            f"writing {name} ({len(data) / MIB:.1f} MiB) would take the corpus past --max-mb "
            f"({budget // MIB} MiB; {manifest.total_bytes / MIB:.1f} MiB written so far)"
        )
    (out / name).write_bytes(data)
    manifest.entries.append(Entry(
        path=name, kind=kind, ext=ext, mime=MIME[ext], bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), pages=pages,
    ))
    manifest.total_bytes += len(data)


def resolve_params(profile: str, scale: float, large_mb: int | None, boundary_pages: int | None) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
    params = dict(PROFILES[profile])
    for key, value in list(params.items()):
        if isinstance(value, tuple):
            params[key] = (max(1, round(value[0] * scale)), value[1])
        elif key in ("md", "json", "txt"):
            params[key] = max(1, round(value * scale))
    if large_mb is not None:
        params["large_mb"] = large_mb
    if boundary_pages is not None:
        params["scanned_boundary_pages"] = boundary_pages
    return params


def estimate_bytes(params: dict[str, Any]) -> int:
    """A conservative guess (measured rates plus 20 %), only to refuse early; ``_put`` is the real limit."""
    per_page = {"digital": 2_600, "tables": 3_000, "figures": 33_000, "scanned": 195_000, "damaged": 2_500}
    total = int(params["large_mb"] * MIB * LARGE_FILE_FACTOR)
    for kind, rate in per_page.items():
        count, pages = params[kind]
        total += count * pages * rate
    count, pages = params["docx"]
    total += count * pages * 5_500
    total += params["scanned_boundary_pages"] * per_page["scanned"]
    n = params["json"]
    total += (params["md"] * 4_500 + params["txt"] * 6_500) + 59_000 * n * (n + 1) // 2
    return total


def generate(
    out: Path,
    *,
    seed: int = 1,
    profile: str = "standard",
    scale: float = 1.0,
    large_mb: int | None = None,
    boundary_pages: int | None = None,
    max_mb: int = 1024,
) -> Manifest:
    params = resolve_params(profile, scale, large_mb, boundary_pages)
    estimate = estimate_bytes(params)
    if estimate > max_mb * MIB:
        raise CorpusTooLarge(
            f"the corpus would be about {estimate // MIB} MiB, over --max-mb {max_mb}; "
            "lower --scale or --large-mb, or raise --max-mb"
        )
    out.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved in only when the whole corpus fit, so a refused run
    # leaves the directory (including any file this run would have overwritten) as it was.
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=out))
    manifest = Manifest(seed=seed, profile=profile, params={**params, "scale": scale})
    manifest.budget = max_mb * MIB
    try:
        words = Words(random.Random(seed))
        makers = {"digital": digital_pdf, "tables": tables_pdf, "figures": figures_pdf, "scanned": scanned_pdf}
        for kind, make in makers.items():
            count, pages = params[kind]
            for i in range(count):
                _put(staging, f"{kind}-{i:02d}.pdf", kind, "pdf", make(words, pages), pages, manifest)
        boundary = params["scanned_boundary_pages"]
        if boundary:
            _put(staging, "scanned-boundary.pdf", "scanned", "pdf", scanned_pdf(words, boundary), boundary, manifest)
        count, pages = params["damaged"]
        for i in range(count):
            _put(staging, f"damaged-{i:02d}.pdf", "damaged", "pdf", damaged_pdf(words, pages, i), pages, manifest)
        if params["large_mb"]:
            if manifest.total_bytes + params["large_mb"] * MIB > manifest.budget:  # before building it
                raise CorpusTooLarge(f"the {params['large_mb']} MiB large PDF does not fit --max-mb {max_mb}")
            _put(staging, "large-00.pdf", "large", "pdf", large_pdf(seed, params["large_mb"]), 0, manifest)
        count, pages = params["docx"]
        for i in range(count):
            _put(staging, f"docx-{i:02d}.docx", "docx", "docx", docx_file(words, pages), pages, manifest)
        for i in range(params["md"]):
            _put(staging, f"doc-{i:02d}.md", "md", "md", markdown_file(words), 0, manifest)
        for i in range(params["json"]):
            _put(staging, f"data-{i:02d}.json", "json", "json", json_file(words, 200 * (i + 1)), 0, manifest)
        for i in range(params["txt"]):
            _put(staging, f"note-{i:02d}.txt", "txt", "txt", text_file(words), 0, manifest)
        (staging / MANIFEST).write_text(manifest.to_json())
        for path in sorted(staging.iterdir()):
            path.replace(out / path.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", choices=sorted(PROFILES), default="standard")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--scale", type=float, default=1.0, help="multiplies file counts")
    ap.add_argument("--large-mb", type=int, default=None, help="size of the over-100 MB PDF; 0 skips it")
    ap.add_argument("--boundary-pages", type=int, default=None,
                    help="pages of the scanned file at the OCR limit (VLM_OCR_MAX_PAGES is 500); 0 skips it")
    ap.add_argument("--max-mb", type=int, default=1024, help="refuse a corpus estimated above this")
    args = ap.parse_args(argv)
    try:
        manifest = generate(
            args.out, seed=args.seed, profile=args.profile, scale=args.scale,
            large_mb=args.large_mb, boundary_pages=args.boundary_pages, max_mb=args.max_mb,
        )
    except CorpusTooLarge as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"{len(manifest.entries)} files, {manifest.total_bytes / MIB:.1f} MiB, corpus_id {manifest.corpus_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
