"""What a parse job holds in memory, by kind of job.

One figure per job (the lane working set, the heavy-permit working set) is right
for one kind of file and wrong for the others: a 300 KB markdown parse holds a
few tens of MiB, a PDF layout parse 1.2 GiB, an Excel load up to 1.5 GiB. Each
byte is charged once, by the gate that owns the place it lives:

* file bytes in the parent: the record reservation (``memory_budget``);
* the parent's fixed working set while it parses: the heavy parse permit, a base
  plus, for a kind above the base, an excess reserved before the permit;
* the worker process (and its raster child): lane admission, a charge by kind.

Everything here is pure (the environment is read only through the Excel limits
and ``GOVERNOR_LANE_WORKING_SET_GB``); the stateful parts are the ledger and the
charge scales at the bottom.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from enum import StrEnum

from app.modules.parsers.excel.limits import (
    excel_document_memory_bytes,
    excel_load_max_bytes,
)
from app.services.resource_governor.policy import (
    DOCLING_WORKING_SET_DEFAULT_GB,
    PDF_PARENT_WORKING_SET_GB,
    docling_working_set_gb,
)

_logger = logging.getLogger(__name__)

GIB = 2**30
MIB = 2**20

FALLBACK_LANE_GB = 1.25
FALLBACK_PARENT_GB = 1.5
PDF_LAYOUT_LANE_GB = 1.25
RENDER_LANE_GB = 0.65
TEXT_BASE_GB = 0.064
TEXT_MIN_GB = 0.10
TEXT_MAX_GB = 1.75
LANE_CHARGE_FLOOR_BYTES = 64 * MIB
# The parent's working set while a PDF is parsed: PdfPlumber (base + 0.25 GiB of result
# building) or, because the parser is chosen later, the OCR worst case.
PDF_PERMIT_GB = 0.50
# The longest a record that holds a parse permit waits for memory before it gives the permit back.
EXCESS_WAIT_SECONDS = 30.0
LANE_WORKING_SET_ENV = "GOVERNOR_LANE_WORKING_SET_GB"
# A kind's charge scale is raised when a worker's measured tree exceeds its charge by this much.
OVERRUN_TOLERANCE = 0.20
MAX_CHARGE_SCALE = 4.0


class ParseKind(StrEnum):
    PDF_LAYOUT = "pdf_layout"  # lane job: PdfPlumber layout parse (worker + raster child)
    RENDER = "render"  # lane job: a batch of page images for OCR
    TEXT = "text"  # lane job: markdown, plain text, html, json
    CSV = "csv"
    CODE = "code"
    EXCEL_LOAD = "excel_load"  # lane job: the openpyxl load, worker retired after it
    DOCLING_DOCX = "docling_docx"  # local Docling conversion in the parent
    DOCLING_PDF = "docling_pdf"  # the Docling service's own conversion
    INSPECT = "inspect"  # lane job: PDF inspection
    PDF = "pdf"  # a PDF as the permit sees it (the parser is chosen later)
    UNKNOWN = "unknown"


# Per-kind slope of the text, CSV and code charge, in bytes of worker memory per byte of file.
# Fitted to the calibration run (PX-2e-calibration.json): peak = base + slope x size, plus 10 to 25 %.
TEXT_SLOPES: dict[ParseKind, float] = {
    ParseKind.TEXT: 80.0,
    ParseKind.CSV: 16.0,
    ParseKind.CODE: 64.0,
}

# What the parent itself holds while a text, CSV or code record parses (the text and its blocks), in
# bytes per byte of file plus a base: the worst run of each calibration cell, rounded up. Markdown
# grew 59, 260 and 991 MB at 1, 5 and 20 MB; CSV 31 and 122 MB at 5 and 20 MB; code 8 MB at 5 MB.
TEXT_PARENT_SLOPES: dict[ParseKind, float] = {
    ParseKind.TEXT: 52.0,
    ParseKind.CSV: 6.5,
    ParseKind.CODE: 2.0,
}
TEXT_PARENT_BASE_GB = 16 * MIB / GIB

_PDF_EXTENSIONS = frozenset({"pdf"})
_EXCEL_EXTENSIONS = frozenset({"xlsx", "xls", "xlsm"})
_DOCLING_EXTENSIONS = frozenset({"docx", "doc", "pptx", "ppt"})
_CSV_EXTENSIONS = frozenset({"csv", "tsv"})
_TEXT_EXTENSIONS = frozenset({"txt", "md", "mdx", "html", "htm", "json", "yaml", "yml", "sql_table", "sql_view"})
_CODE_EXTENSIONS = frozenset({
    "c", "h", "cpp", "cc", "cxx", "hpp", "hxx", "cs", "java", "py", "js", "jsx", "mjs", "cjs", "ts",
    "tsx", "go", "rs", "rb", "php", "swift", "kt", "kts", "dart", "sh", "bash",
})
_MIME_KINDS: dict[str, ParseKind] = {
    "application/pdf": ParseKind.PDF,
    "application/vnd.ms-excel": ParseKind.EXCEL_LOAD,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ParseKind.EXCEL_LOAD,
    "application/msword": ParseKind.DOCLING_DOCX,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ParseKind.DOCLING_DOCX,
    "application/vnd.ms-powerpoint": ParseKind.DOCLING_DOCX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ParseKind.DOCLING_DOCX,
    "text/csv": ParseKind.CSV,
    "text/tab-separated-values": ParseKind.CSV,
    "text/plain": ParseKind.TEXT,
    "text/markdown": ParseKind.TEXT,
    "text/mdx": ParseKind.TEXT,
    "text/html": ParseKind.TEXT,
    "application/json": ParseKind.TEXT,
    "application/yaml": ParseKind.TEXT,
    "application/x-yaml": ParseKind.TEXT,
}


def classify_kind(extension: str | None, mime_type: str | None) -> ParseKind:
    """The kind of a record, from its extension, else its mime type, as ``tiers.classify`` does.

    Anything not recognised is ``UNKNOWN``, which is charged the old safe figures.
    """
    ext = (extension or "").lower().lstrip(".")
    for names, kind in (
        (_PDF_EXTENSIONS, ParseKind.PDF),
        (_EXCEL_EXTENSIONS, ParseKind.EXCEL_LOAD),
        (_DOCLING_EXTENSIONS, ParseKind.DOCLING_DOCX),
        (_CSV_EXTENSIONS, ParseKind.CSV),
        (_TEXT_EXTENSIONS, ParseKind.TEXT),
        (_CODE_EXTENSIONS, ParseKind.CODE),
    ):
        if ext in names:
            return kind
    return _MIME_KINDS.get((mime_type or "").lower(), ParseKind.UNKNOWN)


def explicit_lane_working_set_bytes(environ: dict[str, str] | None = None) -> int | None:
    """``GOVERNOR_LANE_WORKING_SET_GB`` in bytes when the operator set it: one flat charge for every kind."""
    env = os.environ if environ is None else environ
    raw = (env.get(LANE_WORKING_SET_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return int(value * GIB) if value > 0 and math.isfinite(value) else None


def _upper_bound(size: int) -> int:
    return max(int(FALLBACK_LANE_GB * GIB) + max(0, size), excel_load_max_bytes())


def lane_charge_bytes(
    kind: ParseKind | None,
    size: int,
    permit_covered: bool = False,  # noqa: FBT001, FBT002
    *,
    scale: float = 1.0,
    flat_bytes: int | None = None,
) -> int:
    """Bytes a worker (and its raster child) is charged to lane admission for one job of *kind*.

    This is the high-water charge the worker keeps while it lives. A missing or unknown kind is
    the fallback, never less. *flat_bytes* is the operator's one figure for every kind.
    """
    if flat_bytes is not None:
        return flat_bytes
    size = max(0, int(size))
    kind = kind or ParseKind.UNKNOWN
    if kind is ParseKind.PDF_LAYOUT:
        raw = PDF_LAYOUT_LANE_GB * GIB + size
    elif kind is ParseKind.RENDER:
        raw = RENDER_LANE_GB * GIB
    elif kind in TEXT_SLOPES:
        raw = TEXT_BASE_GB * GIB + TEXT_SLOPES[kind] * size
        raw = min(max(raw * scale, TEXT_MIN_GB * GIB), TEXT_MAX_GB * GIB)
        return int(raw)
    elif kind is ParseKind.EXCEL_LOAD:
        return 0 if permit_covered else int(excel_load_max_bytes() * scale)
    else:  # INSPECT, UNKNOWN and every kind that does not run in a lane
        raw = FALLBACK_LANE_GB * GIB
    return int(min(max(raw * scale, LANE_CHARGE_FLOOR_BYTES), _upper_bound(size) * max(1.0, scale)))


def window_charge_bytes(
    kind: ParseKind | None,
    size: int,
    permit_covered: bool = False,  # noqa: FBT001, FBT002
    *,
    scale: float = 1.0,
    flat_bytes: int | None = None,
) -> int:
    """Bytes a spawn adds to the domain's in-flight window.

    The same as the worker's charge, except for an Excel load a permit covers: it is not resident
    worker memory, but the permit's reservation is process-local, so the other processes see the
    load in the window for the warm-up period.
    """
    if flat_bytes is None and permit_covered and kind is ParseKind.EXCEL_LOAD:
        return int(min(excel_load_max_bytes(), excel_document_memory_bytes()) * scale)
    return lane_charge_bytes(kind, size, permit_covered, scale=scale, flat_bytes=flat_bytes)


def parent_working_set_gb(kind: ParseKind | None, size_bytes: int | None = None) -> float:
    """GiB the parent holds while a record of *kind* parses: what its heavy permit has to cover.

    A text, CSV or code record holds a base plus a multiple of its size (the parent builds the whole
    text and its blocks); without a size only the base is known. An unknown kind is the old safe
    figure.
    """
    kind = kind or ParseKind.UNKNOWN
    if kind in TEXT_PARENT_SLOPES:
        size = max(0, int(size_bytes or 0))
        return TEXT_PARENT_BASE_GB + TEXT_PARENT_SLOPES[kind] * size / GIB
    if kind in (ParseKind.PDF, ParseKind.PDF_LAYOUT, ParseKind.RENDER, ParseKind.INSPECT):
        return PDF_PERMIT_GB
    if kind is ParseKind.EXCEL_LOAD:
        return excel_document_memory_bytes() / GIB
    if kind in (ParseKind.DOCLING_DOCX, ParseKind.DOCLING_PDF):
        return docling_working_set_gb()
    return FALLBACK_PARENT_GB


def permit_excess_bytes(
    kind: ParseKind | None, base_gb: float, source: str, size_bytes: int | None = None
) -> int:
    """Bytes a record of *kind* reserves beyond the file bytes so that its permit covers its parent.

    ``max(0, parent working set - the permit's base)``. Only the measured base leaves anything to
    reserve: an explicit operator figure is one flat number for every record, the Docling service
    prices every permit as a conversion, and the fallback is already the old safe figure.
    """
    if source != "measured":
        return 0
    return max(0, int((parent_working_set_gb(kind, size_bytes) - base_gb) * GIB))


class ChargeScales:
    """Per-kind multipliers that only ever go up.

    The calibration is a fixture made on one host; this is what notices a worker that holds more
    than its charge on another one. The reaper reports each worker's measured tree against the
    charge it was admitted with; a kind that exceeds it by more than ``OVERRUN_TOLERANCE`` has its
    scale raised to cover what was seen, once logged, and ``/health`` shows it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scales: dict[ParseKind, float] = {}
        self._observed: dict[ParseKind, int] = {}

    def scale(self, kind: ParseKind | None) -> float:
        with self._lock:
            return self._scales.get(kind or ParseKind.UNKNOWN, 1.0)

    def observe(
        self, kind: ParseKind | None, rss_bytes: int, charged_bytes: int, charged_scale: float = 1.0
    ) -> bool:
        """Note one worker's measured tree against the charge it was admitted with (which already
        carried *charged_scale*). True when this raised the kind's scale."""
        kind = kind or ParseKind.UNKNOWN
        if charged_bytes <= 0 or rss_bytes <= charged_bytes * (1.0 + OVERRUN_TOLERANCE):
            return False
        with self._lock:
            current = self._scales.get(kind, 1.0)
            self._observed[kind] = max(self._observed.get(kind, 0), rss_bytes)
            wanted = min(MAX_CHARGE_SCALE, charged_scale * rss_bytes / charged_bytes)
            if wanted <= current:
                return False
            self._scales[kind] = wanted
        _logger.warning(
            "A %s parse worker held %d MiB against a charge of %d MiB; charges for this kind are scaled by %.2f",
            kind.value, rss_bytes // MIB, charged_bytes // MIB, wanted,
        )
        return True

    def stats(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            return {
                kind.value: {"scale": scale, "observed_peak_bytes": self._observed.get(kind, 0)}
                for kind, scale in self._scales.items()
            }

    def reset(self) -> None:
        with self._lock:
            self._scales.clear()
            self._observed.clear()


SCALES = ChargeScales()

__all__ = [
    "DOCLING_WORKING_SET_DEFAULT_GB",
    "PDF_PARENT_WORKING_SET_GB",
    "EXCESS_WAIT_SECONDS",
    "SCALES",
    "ChargeScales",
    "ParseKind",
    "classify_kind",
    "explicit_lane_working_set_bytes",
    "lane_charge_bytes",
    "TEXT_PARENT_SLOPES",
    "parent_working_set_gb",
    "permit_excess_bytes",
    "window_charge_bytes",
]
