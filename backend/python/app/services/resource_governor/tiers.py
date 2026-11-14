"""Format classification for parse-admission routing.

Maps a document's extension/mime type to a :class:`ParseTier` so heavy
(CPU/memory bound, Docling/OCR-backed) work never queues behind light
(fast, low-memory) work and vice versa. Mirrors the format routing already
built into ``ParserRegistry`` (see ``app/parsing_main.py::_build_registry``)
— this module only adds a *concurrency* dimension on top of it.
"""
from __future__ import annotations

from app.config import file_types
from app.services.resource_governor.models import ParseTier, Pool

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
    return ParseTier(file_types.tier(extension, mime_type))


def parse_cost(tier: ParseTier, size_bytes: int | None) -> int:
    """Admission cost in permits: 1 normally, 2 for an extra-large heavy doc."""
    if tier is ParseTier.HEAVY and size_bytes is not None and size_bytes > XL_HEAVY_BYTES:
        return 2
    return 1


def gate_pool(tier: ParseTier) -> Pool:
    """Which parse admission pool a tier routes to."""
    return Pool.HEAVY_PARSE if tier is ParseTier.HEAVY else Pool.LIGHT_PARSE


def index_pool(tier: ParseTier) -> Pool:
    """Which active-pipeline admission pool a tier routes to.

    Routed by the same ``classify`` result as ``gate_pool``, but read from
    the record event's own ``extension``/``mimeType`` — the index permit is
    taken before the handler runs, so the tier cannot come from the handler's
    START_PARSING event the way the parse permit's does. Unknown formats
    classify as HEAVY, so anything unrecognisable draws on the smaller,
    slower budget rather than the fast one.
    """
    return Pool.INDEX_HEAVY if tier is ParseTier.HEAVY else Pool.INDEX_LIGHT
