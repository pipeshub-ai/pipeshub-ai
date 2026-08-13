"""Format classification for parse-admission routing.

Maps a document's extension/mime type to a :class:`ParseTier` so heavy
(CPU/memory bound, Docling/OCR-backed) work never queues behind light
(fast, low-memory) work and vice versa. Mirrors the format routing already
built into ``ParserRegistry`` (see ``app/parsing_main.py::_build_registry``)
— this module only adds a *concurrency* dimension on top of it.
"""
from __future__ import annotations

from app.services.resource_governor.models import ParseTier, Pool

# CPU- and/or memory-heavy: Docling conversions, LibreOffice-backed doc/ppt
# shims, and OCR/VLM image pipelines.
HEAVY_EXTENSIONS: frozenset[str] = frozenset({
    "pdf",
    "doc", "docx",
    "ppt", "pptx",
    "xls", "xlsx",
    "png", "jpg", "jpeg", "webp", "svg",
})

HEAVY_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "image/png", "image/jpeg", "image/webp", "image/svg+xml",
})

# Fast, low-memory, non-Docling parsers: plain text/markup, tabular text,
# structured data, connector "blocks" payloads (Jira/Confluence-shaped), and
# source code (parsed the same way as plain text).
LIGHT_EXTENSIONS: frozenset[str] = frozenset({
    "txt", "md", "mdx", "html", "htm", "csv", "tsv", "json", "yaml", "yml",
    "sql_table", "sql_view",
    "c", "h", "cpp", "cc", "cxx", "hpp", "hxx", "cs", "java", "py",
    "js", "jsx", "mjs", "cjs", "ts", "tsx", "go", "rs", "rb", "php",
    "swift", "kt", "kts", "dart", "sh", "bash",
})

LIGHT_MIME_TYPES: frozenset[str] = frozenset({
    "text/plain", "text/markdown", "text/html",
    "text/csv", "text/tab-separated-values",
    "application/json", "application/yaml", "application/x-yaml",
    "application/blocks",
})

# Above this size a "heavy" document is treated as extra-large: it consumes
# two HEAVY_PARSE permits instead of one, since Docling/OCR RSS scales with
# page count and DPI, not just format (plan section 4, XL_HEAVY_BYTES).
XL_HEAVY_BYTES = 25 * 1024 * 1024


def classify(extension: str | None, mime_type: str | None) -> ParseTier:
    """Return the parse tier for a document.

    Extension takes priority over mime type (more specific than a generic
    ``application/octet-stream``); unknown formats default to HEAVY — the
    safe direction, since an unrecognised format is more likely to need a
    slow/expensive fallback parser than to be simple structured text.
    """
    ext = (extension or "").lower().lstrip(".")
    mime = (mime_type or "").lower()

    if ext in HEAVY_EXTENSIONS:
        return ParseTier.HEAVY
    if ext in LIGHT_EXTENSIONS:
        return ParseTier.LIGHT
    if mime in HEAVY_MIME_TYPES:
        return ParseTier.HEAVY
    if mime in LIGHT_MIME_TYPES:
        return ParseTier.LIGHT
    return ParseTier.HEAVY


def parse_cost(tier: ParseTier, size_bytes: int | None) -> int:
    """Admission cost in permits: 1 normally, 2 for an extra-large heavy doc."""
    if tier is ParseTier.HEAVY and size_bytes is not None and size_bytes > XL_HEAVY_BYTES:
        return 2
    return 1


def gate_pool(tier: ParseTier) -> Pool:
    """Which parse admission pool a tier routes to."""
    return Pool.HEAVY_PARSE if tier is ParseTier.HEAVY else Pool.LIGHT_PARSE
