"""What indexing and chat attachments need to know about a PDF before parsing it, read in a parse worker.

These functions run in a ``parse_pool`` worker process (see ``untrusted_pdf``),
which imports this module by name: keep its imports to the PDF libraries and
modules as light as them.
"""
from __future__ import annotations

import io
import logging
import math
import random
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pdfplumber
import pypdfium2
from pdfminer.pdfdevice import PDFDevice
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.utils import apply_matrix_pt

from app.modules.parsers.encryption import open_pdfium_document
from app.modules.parsers.pdf.ocr_handler import OCRStrategy

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pdfminer.pdfinterp import PDFResourceManager
    from pdfplumber.page import Page

_logger = logging.getLogger(__name__)

# A PDF needs OCR when at least this share of its (sampled) pages do.
OCR_PAGE_SHARE = 0.5

# Pages with a text layer that are checked to decide whether a PDF needs OCR.
OCR_SAMPLE_PAGES = 30

# A page of a document that has text is read by OCR on its own when its text layer holds fewer characters
# than this and it carries heavy drawn content: text drawn as outlines has no text layer, only curves.
TEXTLESS_PAGE_MAX_CHARS = 100
# Paths (curves, rectangles and lines) on such a page; a ruled form or a chart has tens, drawn glyphs have thousands.
TEXTLESS_PAGE_MIN_DRAWN = 200
# Or the page is mostly one picture or a few: the share of the page they cover.
TEXTLESS_PAGE_IMAGE_SHARE = 0.75
# Image coverage is measured on a grid of this many cells per side (1 MB, whatever the image count).
_COVERAGE_GRID = 1000


@dataclass(frozen=True)
class PdfStructure:
    page_count: int
    # Opening the file and listing its pages, which every later pdfplumber
    # read of these bytes pays again.
    open_seconds: float


def open_pdf(content: bytes) -> PdfStructure:
    """Open *content* with pdfplumber and list its pages."""
    started = time.monotonic()
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return PdfStructure(page_count=len(pdf.pages), open_seconds=time.monotonic() - started)


def detect_ocr(content: bytes, sample_pages: int) -> bool:
    """Whether at least half of a sample of *content*'s pages need OCR."""
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = pdf.pages
        return pages_need_ocr([pages[i] for i in sample_page_indices(len(pages), sample_pages)])


@dataclass(frozen=True)
class PdfFacts:
    """The cheap facts a cost class is decided from: the page count and whether the text layer is missing."""

    page_count: int
    scanned: bool


def quick_facts(content: bytes, sample_pages: int = 8) -> PdfFacts:
    """Page count and text layer from pdfium alone: no layout analysis, and only a sample of pages is read.

    A file is scanned when at least half of the sampled pages have no text layer. Raises on a file pdfium
    cannot open (the caller falls back to the size prior and the full inspection later handles the file).
    """
    document = pypdfium2.PdfDocument(content)
    try:
        total = len(document)
        if total == 0:
            return PdfFacts(0, False)
        empty = 0
        indices = sample_page_indices(total, sample_pages)
        for index in indices:
            page = document[index]
            textpage = page.get_textpage()
            try:
                if textpage.count_chars() == 0:
                    empty += 1
            finally:
                textpage.close()
                page.close()
        return PdfFacts(total, empty / len(indices) >= OCR_PAGE_SHARE)
    finally:
        document.close()


def text_layer_char_counts(content: bytes) -> list[int] | None:
    """pdfium's count of text-layer characters on each page; None when pdfium cannot read it."""
    try:
        document = pypdfium2.PdfDocument(content)
        try:
            counts: list[int] = []
            for page in document:
                textpage = page.get_textpage()
                try:
                    counts.append(textpage.count_chars())
                finally:
                    textpage.close()
                    page.close()
            return counts
        finally:
            document.close()
    except Exception:
        _logger.warning(
            "Could not read the PDF's text layer with pdfium; checking every page with pdfplumber",
            exc_info=True,
        )
        return None


def needs_ocr_and_page_count(content: bytes, max_pages: int | None = None) -> tuple[bool, int]:
    """Whether at least half of *content*'s pages need OCR, and its page count.

    A document of more than *max_pages* pages is answered ``(False, page count)``
    from the page count alone: counting text-layer characters walks every page
    (about 11 s for 4,000 pages), and a caller that refuses such a document needs
    the count before its deadline, not the OCR verdict.

    A page with no text layer always needs OCR (``needs_ocr`` sees zero text
    density), so pdfium's character counts settle those without pdfplumber,
    which can spend minutes walking a page whose glyphs are drawn as vector paths.
    Of the pages that have text, at most ``OCR_SAMPLE_PAGES`` evenly spread ones
    are checked, each released as soon as it is: pdfplumber keeps every parsed
    char and layout of a page until it is closed, which for a long document
    otherwise grows with the page count. A document of that many text pages or
    fewer is checked in full, exactly as before. Past that, the verdict is an
    estimate from the sample (a sampling limit: a document whose share of pages
    needing OCR is near one half can be called either way). Pages without a text
    layer are always counted exactly.
    """
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = pdf.pages
        total = len(pages)
        if total == 0:
            return False, 0
        if max_pages is not None and total > max_pages:
            return False, total
        chars_per_page = text_layer_char_counts(content)
        counts: Sequence[int | None] = chars_per_page or ()
        if len(counts) != total:
            counts = [None] * total
        textless = sum(1 for chars in counts if chars == 0)
        if textless / total >= OCR_PAGE_SHARE:
            return True, total
        with_text = [index for index, chars in enumerate(counts) if chars != 0]
        sampled = [with_text[i] for i in sample_page_indices(len(with_text), OCR_SAMPLE_PAGES)]
        sampled_ocr = 0
        for index in sampled:
            try:
                if OCRStrategy.needs_ocr(pages[index], _logger):
                    sampled_ocr += 1
            finally:
                pages[index].close()
        # A sample stands for all the pages with text.
        ocr_with_text = (
            sampled_ocr if len(sampled) == len(with_text) else len(with_text) * sampled_ocr / len(sampled)
        )
    return (textless + ocr_with_text) / total >= OCR_PAGE_SHARE, total


def sample_page_indices(page_count: int, limit: int) -> list[int]:
    """Every page up to *limit*; past it, *limit* distinct pages, first and last included.

    Deciding OCR from a sample keeps the cost of the decision flat: each page
    checked runs pdfminer's full layout analysis, and the decision can need
    half the pages of a long document.

    The pages between come one from each of *limit* - 2 equal runs of pages,
    at an offset drawn per run, not at a fixed stride or any other regular
    sequence: a regular placement can line up with a layout that repeats (say
    two scanned pages in every three) and see only one kind of page. A drawn
    offset sees each page of its run equally often, so the share it finds is
    the document's own on average, whatever the layout. The draw is seeded by
    the page count, so a document always gets the same sample. The result is
    still a sample: past *limit* pages the share is an estimate, and a document
    whose true share sits near one half can be called either way.
    """
    limit = max(1, limit)
    if page_count <= limit:
        return list(range(page_count))
    if limit == 1:
        return [0]
    draw = random.Random(page_count)
    runs = limit - 2
    middle = page_count - 2
    chosen = [0]
    for run in range(runs):
        start = 1 + run * middle // runs
        end = 1 + (run + 1) * middle // runs
        chosen.append(draw.randrange(start, end))
    chosen.append(page_count - 1)
    return chosen


def pages_need_ocr(pages: Sequence[Page]) -> bool:
    """True when at least half of *pages* need OCR. Stops as soon as the answer is known."""
    if not pages:
        return False
    threshold = math.ceil(len(pages) * OCR_PAGE_SHARE)
    ocr_pages = 0
    for checked, page in enumerate(pages, start=1):
        if OCRStrategy.needs_ocr(page, _logger):
            ocr_pages += 1
            if ocr_pages >= threshold:
                return True
        if ocr_pages + (len(pages) - checked) < threshold:
            return False
    return ocr_pages >= threshold


def repair_pdf(content: bytes) -> bytes:
    """*content* rewritten by pdfium, with a cross-reference table that matches the file.

    pdfium reconstructs a damaged table from the objects themselves and drops
    trailing garbage, which pdfminer otherwise reads one byte at a time.
    Raises ``PasswordProtectedError`` when the file needs a password, and
    ``pypdfium2.PdfiumError`` when pdfium cannot open it for another reason.
    """
    document = open_pdfium_document(content)
    try:
        rewritten = io.BytesIO()
        document.save(rewritten)
    finally:
        document.close()
    return rewritten.getvalue()


@dataclass(frozen=True)
class DrawnContent:
    """What a page draws besides text, counted without building a layout object for it.

    ``images`` are the pictures' boxes ``(x0, y0, x1, y1)`` on a ``width`` x ``height`` page whose origin
    is the page's lower-left corner, whatever its MediaBox origin or rotation.
    """

    paths: int
    images: tuple[tuple[float, float, float, float], ...]
    width: float
    height: float


class _DrawnContentCounter(PDFDevice):
    """Counts painted sub-paths and records picture boxes; never builds an ``LTCurve`` or ``LTImage``.

    A page with text drawn as outlines can paint hundreds of thousands of paths, and pdfplumber's
    ``page.curves`` builds an object for each. The sub-path split follows
    ``pdfminer.converter.PDFLayoutAnalyzer.paint_path``, so ``paths`` equals
    ``len(page.lines) + len(page.rects) + len(page.curves)``.
    """

    def __init__(self, rsrcmgr: PDFResourceManager) -> None:
        super().__init__(rsrcmgr)
        self.paths = 0
        self.images: list[tuple[float, float, float, float]] = []

    # pdfminer calls this with positional flags, so the signature is its own.
    def paint_path(self, gstate: object, stroke: bool, fill: bool, evenodd: bool, path: list) -> None:  # noqa: FBT001
        shape = "".join(segment[0] for segment in path)
        if shape[:1] != "m":
            return
        if shape.count("m") > 1:
            for match in re.finditer(r"m[^m]+", shape):
                self.paint_path(gstate, stroke, fill, evenodd, path[match.start(0):match.end(0)])
        else:
            self.paths += 1

    def render_image(self, name: str, stream: object) -> None:
        # An image fills the unit square of the current transformation, as pdfminer's LTImage does.
        corners = [apply_matrix_pt(self.ctm, point) for point in ((0, 0), (1, 0), (0, 1), (1, 1))]
        xs, ys = [x for x, _ in corners], [y for _, y in corners]
        self.images.append((min(xs), min(ys), max(xs), max(ys)))


def drawn_content(page: Page) -> DrawnContent:
    """The paths and pictures *page* draws, read with pdfminer's interpreter and no layout objects.

    pdfminer shifts the page to its MediaBox origin and turns it by its rotation before drawing, so the
    boxes are measured on the page as shown.
    """
    page_obj = page.page_obj
    x0, y0, x1, y1 = (float(v) for v in page_obj.mediabox)
    width, height = x1 - x0, y1 - y0
    if page_obj.rotate in (90, 270):
        width, height = height, width
    rsrcmgr = page.pdf.rsrcmgr
    device = _DrawnContentCounter(rsrcmgr)
    PDFPageInterpreter(rsrcmgr, device).process_page(page_obj)
    return DrawnContent(paths=device.paths, images=tuple(device.images), width=width, height=height)


def textless_page_score(content: DrawnContent, chars: int) -> int:
    """How strongly a page reads as a page whose content has no text layer; 0 when it does not.

    A page qualifies when its text layer is short (under ``TEXTLESS_PAGE_MAX_CHARS`` characters) and it
    either paints more than ``TEXTLESS_PAGE_MIN_DRAWN`` paths (curves, rectangles and lines: text drawn as
    outlines), or its images cover at least ``TEXTLESS_PAGE_IMAGE_SHARE`` of the page. The score ranks the
    qualifying pages when there are more than may be read: the count of paths, or the image area in
    thousandths of the page.
    """
    if chars >= TEXTLESS_PAGE_MAX_CHARS:
        return 0
    if content.paths > TEXTLESS_PAGE_MIN_DRAWN:
        return content.paths
    share = image_share(content)
    if share >= TEXTLESS_PAGE_IMAGE_SHARE:
        return max(1, int(share * 1000))
    return 0


def image_share(content: DrawnContent) -> float:
    """The share of the page its images cover, each clipped to the page and overlaps counted once.

    Measured on a ``_COVERAGE_GRID`` x ``_COVERAGE_GRID`` grid of the page: right to within a thousandth of
    each side, and bounded in time and memory however many images the page holds.
    """
    width, height = content.width, content.height
    if width <= 0 or height <= 0 or not content.images:
        return 0.0
    grid = _COVERAGE_GRID
    covered = np.zeros((grid, grid), dtype=bool)

    def cell(value: float, side: float) -> int:
        return round(min(max(value, 0.0), side) / side * grid)

    for x0, y0, x1, y1 in content.images:
        left, right = cell(x0, width), cell(x1, width)
        low, high = cell(y0, height), cell(y1, height)
        if right > left and high > low:
            covered[low:high, left:right] = True
    return int(covered.sum()) / (grid * grid)


def textless_pages(content: bytes, max_pages: int) -> tuple[list[int], int]:
    """The 1-based numbers of the pages of *content* whose content has no text layer, and how many there are.

    Run in a parse worker. pdfium's character counts pick the pages with little text without parsing them
    (so a long text document pays almost nothing); only those are opened with pdfplumber. At most
    *max_pages* are returned (the highest-scoring ones, listed in page order); the second value is the
    number found, so the caller can tell that the cap was hit. A file whose text layer cannot be counted
    returns nothing.
    """
    counts = text_layer_char_counts(content)
    if not counts:
        return [], 0
    candidates = [index for index, chars in enumerate(counts) if chars < TEXTLESS_PAGE_MAX_CHARS]
    if not candidates:
        return [], 0
    scored: list[tuple[int, int]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        pages = pdf.pages
        for index in candidates:
            if index >= len(pages):
                break
            try:
                score = textless_page_score(drawn_content(pages[index]), counts[index])
            except Exception:
                _logger.warning("Could not inspect a page for drawn content", exc_info=True)
                score = 0
            finally:
                pages[index].close()
            if score:
                scored.append((score, index))
    found = len(scored)
    if found > max_pages:
        scored.sort(key=lambda item: (-item[0], item[1]))
        scored = scored[: max(0, max_pages)]
    return sorted(index + 1 for _, index in scored), found


def extract_pages(content: bytes, page_numbers: Sequence[int]) -> bytes:
    """A PDF holding only the 1-based *page_numbers* of *content*, in the order given."""
    source = open_pdfium_document(content)
    try:
        target = pypdfium2.PdfDocument.new()
        try:
            target.import_pages(source, [n - 1 for n in page_numbers])
            out = io.BytesIO()
            target.save(out)
            return out.getvalue()
        finally:
            target.close()
    finally:
        source.close()
