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

The ``cost-*`` profiles (``cost-smoke``, ``cost-standard``, ``cost-huge``) label every file with its
cost class instead, the oracle the end-to-end bench reports latency by: ``small-text``,
``small-pdf``, ``office``, ``scanned``, ``large-pdf`` and ``huge-pdf``. ``pages`` is the unit count
of the file: PDF pages, DOCX pages, PPTX slides, XLSX data rows are not pages so it is 1.

Run: ``python -m tests.perf.parse_bench.corpus --out DIR [--profile standard]``
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import math
import random
import re
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
    "html": "text/html",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

# Cost-class corpora. Per class: file count, or (count, pages) for page-based classes
# (``small-pdf``, ``scanned`` and ``large-pdf`` take the *largest* page count and spread the files over 1..max).
COST_CLASSES = ("small-text", "small-pdf", "office", "scanned", "large-pdf", "huge-pdf")
COST_PROFILES: dict[str, dict[str, Any]] = {
    "cost-smoke": {
        "small-text": 3, "small-pdf": (2, 4), "office": 3, "scanned": (1, 2),
        "large-pdf": (0, 60), "huge-pdf": (0, 1000),
    },
    "cost-standard": {
        "small-text": 20, "small-pdf": (12, 20), "office": 9, "scanned": (4, 10),
        "large-pdf": (2, 60), "huge-pdf": (0, 1000),
    },
    # B-01: the huge documents come first in the schedule; the smalls are the probes.
    "cost-huge": {
        "small-text": 20, "small-pdf": (24, 20), "office": 9, "scanned": (4, 10),
        "large-pdf": (2, 120), "huge-pdf": (10, 1000),
    },
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
    # One long file of each page-heavy kind: the working set of a parse worker on a big document.
    "workset60": {
        "digital": (1, 60), "scanned": (1, 60), "tables": (1, 60), "figures": (1, 60),
        "damaged": (1, 2), "docx": (1, 2), "md": 1, "json": 1, "txt": 1,
        "large_mb": 0, "scanned_boundary_pages": 0,
    },
    "workset300": {
        "digital": (1, 300), "scanned": (1, 300), "tables": (1, 300), "figures": (1, 300),
        "damaged": (1, 2), "docx": (1, 2), "md": 1, "json": 1, "txt": 1,
        "large_mb": 0, "scanned_boundary_pages": 0,
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


def seed_stamp(seed: int = 0) -> datetime.datetime:
    """The one timestamp every generated file carries: a pure function of the seed, never of the clock."""
    return datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC) + datetime.timedelta(seconds=seed % 31_536_000)


def docx_file(words: Words, pages: int, stamp: datetime.datetime | None = None) -> bytes:
    from docx import Document

    stamp = stamp or seed_stamp()
    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = stamp
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
    return _fixed_zip(raw.getvalue(), stamp)


_CORE_DATE = re.compile(r"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)")


def _fixed_zip(data: bytes, stamp: datetime.datetime | None = None) -> bytes:
    """Same entries, same order, one timestamp taken from the seed: the bytes never depend on the clock.

    Libraries stamp the save time into ``docProps/core.xml`` (openpyxl overwrites whatever was set), so the
    package's created/modified dates are rewritten here too, and the zip entries carry the same moment.
    """
    stamp = stamp or seed_stamp()
    iso = stamp.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in sorted(src.infolist(), key=lambda i: i.filename):
            clone = zipfile.ZipInfo(info.filename, date_time=(stamp.year, stamp.month, stamp.day,
                                                              stamp.hour, stamp.minute, stamp.second - stamp.second % 2))
            clone.compress_type = zipfile.ZIP_DEFLATED
            clone.external_attr = info.external_attr
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = _CORE_DATE.sub(lambda m: f"{m.group(1)}{iso}{m.group(2)}", body.decode("utf-8")).encode("utf-8")
            dst.writestr(clone, body)
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


# -- sized light files (the per-kind memory calibration) -------------------------


def sized_markdown(words: Words, target_bytes: int) -> bytes:
    """Markdown of about *target_bytes*: headings, paragraphs, lists, code fences and a table."""
    parts: list[bytes] = []
    total = 0
    while total < target_bytes:
        chunk = markdown_file(words) + b"\n| name | value |\n| --- | --- |\n" + b"".join(
            f"| {words.title()} | {words.rng.randint(1, 9999)} |\n".encode() for _ in range(6)
        )
        parts.append(chunk)
        total += len(chunk)
    return b"\n".join(parts)[:target_bytes]


def sized_csv(words: Words, target_bytes: int) -> bytes:
    """A CSV of about *target_bytes*: an id, text, a number and a date per row."""
    rows = ["id,name,description,amount,date"]
    total = len(rows[0])
    i = 0
    while total < target_bytes:
        line = f'{i},{words.title()},"{words.sentence()}",{words.rng.random() * 1000:.2f},2026-{1 + i % 12:02d}-{1 + i % 28:02d}'
        rows.append(line)
        total += len(line) + 1
        i += 1
    return "\n".join(rows).encode()


def sized_code(words: Words, target_bytes: int) -> bytes:
    """Python source of about *target_bytes*: classes with methods, docstrings and branches."""
    out: list[str] = ["import os", ""]
    total = 0
    i = 0
    while total < target_bytes:
        name = f"{words.title().replace(' ', '')}{i}"
        body = [
            f"class {name}:",
            f'    """{words.sentence()}"""',
            "",
            "    def __init__(self, value: int) -> None:",
            "        self.value = value",
            "",
        ]
        for j in range(4):
            body += [
                f"    def method_{j}(self, x: int) -> int:",
                f'        """{words.sentence()}"""',
                f"        if x > {words.rng.randint(1, 99)}:",
                f"            return self.value + x * {j + 1}",
                f"        return os.getpid() - {words.rng.randint(1, 99)}",
                "",
            ]
        text = "\n".join(body) + "\n"
        out.append(text)
        total += len(text)
        i += 1
    return "\n".join(out).encode()[:target_bytes]


def _xlsx_rows(seed: int, rows: int) -> bytes:
    import openpyxl

    rng = random.Random(seed)
    book = openpyxl.Workbook(write_only=True)
    per_sheet = 100_000
    for index in range(max(1, math.ceil(rows / per_sheet))):
        sheet = book.create_sheet(f"S{index}")
        sheet.append([f"c{c}" for c in range(12)])
        for _ in range(min(per_sheet, rows - index * per_sheet)):
            sheet.append([rng.random() * 1e6 for _ in range(8)] + [f"t{rng.randint(0, 10**9)}" for _ in range(4)])
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def sized_xlsx(seed: int, target_bytes: int) -> bytes:
    """A workbook of about *target_bytes* (zipped): 8 numeric and 4 text columns per row, several sheets."""
    sample_rows = 20_000
    per_row = len(_xlsx_rows(seed, sample_rows)) / sample_rows
    return _xlsx_rows(seed, max(sample_rows, int(target_bytes / per_row)))


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
    if profile in COST_PROFILES:
        return generate_cost(out, seed=seed, profile=profile, scale=scale, max_mb=max_mb)
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


# -- cost-class corpus ----------------------------------------------------------


def html_file(words: Words) -> bytes:
    body = "".join(f"<h2>{words.title()}</h2><p>{words.paragraph(5)}</p>" for _ in range(words.rng.randint(3, 8)))
    return f"<!doctype html><html><head><title>{words.title()}</title></head><body>{body}</body></html>".encode()


def light_pdf(words: Words, pages: int) -> bytes:
    """A long text PDF that is cheap to build (about 25 lines a page): the huge-document kind."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4, invariant=1)
    for page in range(pages):
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(50, 800, f"{words.title()} (section {page + 1})")
        pdf.setFont("Helvetica", 9)
        for line in range(48):
            pdf.drawString(50, 780 - line * 15, words.sentence(8, 14))
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


def xlsx_small(words: Words, rows: int, stamp: datetime.datetime | None = None) -> bytes:
    """A workbook of *rows* data rows on one sheet (the ``office`` class keeps it at 200 or fewer)."""
    import openpyxl

    stamp = stamp or seed_stamp()
    book = openpyxl.Workbook()
    book.properties.created = book.properties.modified = stamp
    book.properties.creator = "bench"
    sheet = book.active
    sheet.append(["id", "name", "note", "amount"])
    for i in range(rows):
        sheet.append([i, words.title(), words.sentence(), round(words.rng.random() * 1000, 2)])
    raw = io.BytesIO()
    book.save(raw)
    return _fixed_zip(raw.getvalue(), stamp)


_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _rels(*items: tuple[str, str, str]) -> str:
    body = "".join(f'<Relationship Id="{i}" Type="{_REL}/{t}" Target="{target}"/>' for i, t, target in items)
    return f'{_XML}<Relationships xmlns="{_PKG_REL}">{body}</Relationships>'


_THEME = (
    _XML + f'<a:theme xmlns:a="{_NS_A}" name="bench"><a:themeElements>'
    '<a:clrScheme name="bench"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F1F1F"/></a:dk2><a:lt2><a:srgbClr val="EEEEEE"/></a:lt2>'
    + "".join(f'<a:accent{n}><a:srgbClr val="{c}"/></a:accent{n}>' for n, c in enumerate(
        ("4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47"), 1))
    + '<a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="bench"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="bench"><a:fillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + '</a:fillStyleLst><a:lnStyleLst>' + '<a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>' * 3
    + '</a:lnStyleLst><a:effectStyleLst>' + '<a:effectStyle><a:effectLst/></a:effectStyle>' * 3
    + '</a:effectStyleLst><a:bgFillStyleLst>' + '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>' * 3
    + '</a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'
)
_EMPTY_TREE = (
    '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
    '<p:grpSpPr/></p:spTree></p:cSld>'
)


def _slide_xml(title: str, lines: list[str]) -> str:
    def shape(shape_id: int, name: str, texts: list[str], x: int, y: int, cx: int, cy: int) -> str:
        paras = "".join(f"<a:p><a:r><a:rPr lang=\"en-US\"/><a:t>{_xml_escape(t)}</a:t></a:r></a:p>" for t in texts)
        return (
            f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{name}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody></p:sp>'
        )

    return (
        f'{_XML}<p:sld xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:p="{_NS_P}"><p:cSld><p:spTree>'
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        + shape(2, "Title", [title], 457200, 274638, 8229600, 800000)
        + shape(3, "Body", lines, 457200, 1200000, 8229600, 4500000)
        + '</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def pptx_file(words: Words, slides: int, stamp: datetime.datetime | None = None) -> bytes:
    """A minimal hand-built OOXML presentation: *slides* slides of a title and three text lines each.

    One master, one layout and one theme, written entry by entry with one fixed timestamp, so the
    bytes depend only on the seed.
    """
    ct = (
        f'{_XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>'
        + "".join(
            f'<Override PartName="/ppt/slides/slide{n}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            for n in range(1, slides + 1))
        + "</Types>"
    )
    slide_ids = "".join(f'<p:sldId id="{255 + n}" r:id="rId{n + 2}"/>' for n in range(1, slides + 1))
    presentation = (
        f'{_XML}<p:presentation xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:p="{_NS_P}">'
        f'<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>'
        "</p:presentation>"
    )
    parts: dict[str, str] = {
        "[Content_Types].xml": ct,
        "_rels/.rels": _rels(("rId1", "officeDocument", "ppt/presentation.xml")),
        "ppt/presentation.xml": presentation,
        "ppt/_rels/presentation.xml.rels": _rels(
            ("rId1", "slideMaster", "slideMasters/slideMaster1.xml"),
            ("rId2", "theme", "theme/theme1.xml"),
            *((f"rId{n + 2}", "slide", f"slides/slide{n}.xml") for n in range(1, slides + 1)),
        ),
        "ppt/slideMasters/slideMaster1.xml": (
            f'{_XML}<p:sldMaster xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:p="{_NS_P}">{_EMPTY_TREE}'
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
            'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>'
        ),
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": _rels(
            ("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")),
        "ppt/slideLayouts/slideLayout1.xml": (
            f'{_XML}<p:sldLayout xmlns:a="{_NS_A}" xmlns:r="{_NS_R}" xmlns:p="{_NS_P}" type="blank">{_EMPTY_TREE}'
            "<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>"
        ),
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": _rels(("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")),
        "ppt/theme/theme1.xml": _THEME,
    }
    for n in range(1, slides + 1):
        parts[f"ppt/slides/slide{n}.xml"] = _slide_xml(words.title(), [words.sentence() for _ in range(3)])
        parts[f"ppt/slides/_rels/slide{n}.xml.rels"] = _rels(("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"))
    stamp = stamp or seed_stamp()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():  # [Content_Types].xml first, as Office writes it
            info = zipfile.ZipInfo(name, date_time=(stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute,
                                                    stamp.second - stamp.second % 2))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode())
    return out.getvalue()


def _spread(index: int, maximum: int, low: int = 1) -> int:
    """A deterministic page count for file *index*, cycling through ``low..maximum``."""
    return low + (index * 7) % max(1, maximum - low + 1)


def resolve_cost_params(profile: str, scale: float) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for cls, value in COST_PROFILES[profile].items():
        if isinstance(value, tuple):
            params[cls] = (max(0, round(value[0] * scale)) if value[0] else 0, value[1])
        else:
            params[cls] = max(1, round(value * scale))
    return params


def generate_cost(out: Path, *, seed: int, profile: str, scale: float, max_mb: int) -> Manifest:
    """The cost-class corpus: every entry's ``kind`` is its class, ``pages`` its unit count."""
    params = resolve_cost_params(profile, scale)
    out.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=out))
    manifest = Manifest(seed=seed, profile=profile, params={**params, "scale": scale})
    manifest.budget = max_mb * MIB
    try:
        words = Words(random.Random(seed))
        stamp = seed_stamp(seed)
        for i in range(params["small-text"]):
            which = i % 3
            if which == 0:
                _put(staging, f"small-text-{i:02d}.txt", "small-text", "txt", text_file(words), 0, manifest)
            elif which == 1:
                size = 2_000 + words.rng.randint(0, 38_000)
                _put(staging, f"small-text-{i:02d}.md", "small-text", "md", sized_markdown(words, size), 0, manifest)
            else:
                _put(staging, f"small-text-{i:02d}.html", "small-text", "html", html_file(words), 0, manifest)
        count, top = params["small-pdf"]
        for i in range(count):
            pages = _spread(i, top)
            _put(staging, f"small-pdf-{i:02d}.pdf", "small-pdf", "pdf", digital_pdf(words, pages), pages, manifest)
        for i in range(params["office"]):
            which = i % 3
            if which == 0:
                pages = _spread(i, 20, low=2)
                _put(staging, f"office-{i:02d}.docx", "office", "docx", docx_file(words, min(pages, 8), stamp), min(pages, 8), manifest)
            elif which == 1:
                _put(staging, f"office-{i:02d}.xlsx", "office", "xlsx", xlsx_small(words, 20 + (i * 37) % 181, stamp), 1, manifest)
            else:
                slides = _spread(i, 20, low=5)
                _put(staging, f"office-{i:02d}.pptx", "office", "pptx", pptx_file(words, slides, stamp), slides, manifest)
        count, top = params["scanned"]
        for i in range(count):
            pages = _spread(i, top, low=2)
            _put(staging, f"scanned-{i:02d}.pdf", "scanned", "pdf", scanned_pdf(words, pages), pages, manifest)
        count, pages = params["large-pdf"]
        for i in range(count):
            make = digital_pdf if i % 2 == 0 else tables_pdf
            _put(staging, f"large-pdf-{i:02d}.pdf", "large-pdf", "pdf", make(words, pages), pages, manifest)
        count, pages = params["huge-pdf"]
        for i in range(count):
            _put(staging, f"huge-pdf-{i:02d}.pdf", "huge-pdf", "pdf", light_pdf(words, pages), pages, manifest)
        (staging / MANIFEST).write_text(manifest.to_json())
        for path in sorted(staging.iterdir()):
            path.replace(out / path.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", choices=sorted([*PROFILES, *COST_PROFILES]), default="standard")
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
