"""Format-aware, resource-aware concurrency sizing for the Parsing Service.

Two isolated pools instead of one shared gate:

* ``ParseTier.HEAVY``  -- CPU/memory-bound formats (pdf, doc(x), ppt(x),
  xls(x), images) that route through Docling / VLM OCR and can approach
  1-1.5 GiB RSS per in-flight parse.
* ``ParseTier.LIGHT``  -- fast, low-memory formats (txt, md, html, csv,
  json, yaml, sql, blocks, ...) that finish in milliseconds and would
  otherwise queue behind a heavy PDF on a single shared semaphore.

Pool sizes are derived from the *container's* CPU and memory limits (cgroup
v2, falling back to cgroup v1, falling back to ``psutil`` for native
macOS/Windows/Linux runs) so the same code auto-sizes correctly whether it's
running in Docker or directly on a developer machine. A single operator
override, ``MAX_CONCURRENT_PARSING``, still exists for when the operator
wants to pin the heavy-pool size explicitly instead of trusting the
auto-detected value.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)

# Each heavy parse (Docling layout model, VLM OCR page rasterization, etc.)
# can approach this much resident memory at its peak.
HEAVY_PARSE_MEMORY_GIB = 1.5
MIN_HEAVY_SLOTS = 1
MAX_HEAVY_SLOTS = 4
MIN_LIGHT_SLOTS = 4
MAX_LIGHT_SLOTS = 16
LIGHT_SLOTS_PER_CPU = 2
LIGHT_TO_HEAVY_RATIO = 4
DEFAULT_MEMORY_PRESSURE_THRESHOLD = 0.9

_CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
_CGROUP_V2_MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")
_CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
_CGROUP_V1_MEMORY_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
# cgroup v1/v2 report this sentinel (or a value in this neighborhood) when no
# memory limit is set, i.e. "unlimited" -- treat it the same as "absent".
_CGROUP_UNLIMITED_FLOOR_BYTES = 1 << 62


class ParseTier(str, Enum):
    """Which concurrency pool a format routes through."""

    HEAVY = "heavy"
    LIGHT = "light"


# Formats backed by Docling / VLM OCR / office-document conversion -- CPU and
# memory heavy, occasionally minutes-long for large scanned PDFs.
HEAVY_FORMATS: frozenset[str] = frozenset(
    {
        "pdf",
        "doc",
        "docx",
        "ppt",
        "pptx",
        "xls",
        "xlsx",
        "png",
        "jpg",
        "jpeg",
        "webp",
        "svg",
        "heic",
        "heif",
    }
)

# Fast, low-memory formats that parse in milliseconds and should never queue
# behind a heavy document.
LIGHT_FORMATS: frozenset[str] = frozenset(
    {
        "txt",
        "md",
        "mdx",
        "html",
        "htm",
        "csv",
        "tsv",
        "json",
        "yaml",
        "yml",
        "sql_table",
        "sql_view",
        "blocks",
    }
)


def classify_format(extension: str, mime_type: str) -> ParseTier:
    """Return the :class:`ParseTier` for a request's extension/mime-type.

    Unknown formats are classified ``HEAVY`` -- an unrecognized format is
    more likely a novel document type than a trivial one, and the smaller
    heavy pool is the safe side to err on.
    """
    ext = (extension or "").lower().lstrip(".")
    if ext in LIGHT_FORMATS:
        return ParseTier.LIGHT
    if ext in HEAVY_FORMATS:
        return ParseTier.HEAVY

    mime = (mime_type or "").lower()
    if mime.startswith("image/") or mime == "application/pdf":
        return ParseTier.HEAVY
    if mime.startswith("text/") or mime in ("application/json", "application/yaml"):
        return ParseTier.LIGHT

    return ParseTier.HEAVY


def _read_int_file(path: Path) -> int | None:
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    if value >= _CGROUP_UNLIMITED_FLOOR_BYTES:
        return None
    return value


def get_memory_limit_bytes() -> int | None:
    """Best-effort memory limit in bytes for the current process's cgroup/host.

    Resolution order: cgroup v2 -> cgroup v1 -> ``psutil`` host total. Cgroup
    is checked first because inside a container ``psutil`` reports the
    *host's* total memory, not the container's limit -- using it directly
    would oversize the pools on a memory-constrained container and defeat
    the OOM protection this module exists to provide.
    """
    limit = _read_int_file(_CGROUP_V2_MEMORY_MAX)
    if limit is not None:
        return limit

    limit = _read_int_file(_CGROUP_V1_MEMORY_LIMIT)
    if limit is not None:
        return limit

    try:
        return int(psutil.virtual_memory().total)
    except OSError:
        return None


def get_memory_usage_bytes() -> int | None:
    """Best-effort current memory usage in bytes, matching :func:`get_memory_limit_bytes`."""
    usage = _read_int_file(_CGROUP_V2_MEMORY_CURRENT)
    if usage is not None:
        return usage

    usage = _read_int_file(_CGROUP_V1_MEMORY_USAGE)
    if usage is not None:
        return usage

    try:
        vm = psutil.virtual_memory()
        return int(vm.total - vm.available)
    except OSError:
        return None


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def compute_parse_slots(
    cpu_count: int | None = None,
    mem_limit_bytes: int | None = None,
    override: str | None = None,
) -> tuple[int, int]:
    """Return ``(heavy_slots, light_slots)`` for the given resources.

    ``override`` is the raw ``MAX_CONCURRENT_PARSING`` value. When unset or
    ``"auto"`` (case-insensitive), slots are auto-sized from CPU/memory. When
    set to a positive integer, it pins ``heavy_slots`` directly (operators
    get one predictable knob instead of two) and ``light_slots`` scales off
    of it.
    """
    cpu_count = cpu_count or os.cpu_count() or 1

    if override and override.strip().lower() != "auto":
        try:
            heavy_slots = max(1, int(override))
        except ValueError:
            logger.warning(
                "Invalid MAX_CONCURRENT_PARSING=%r; falling back to auto-sizing",
                override,
            )
            heavy_slots = None
        else:
            light_slots = _clamp(
                heavy_slots * LIGHT_TO_HEAVY_RATIO, MIN_LIGHT_SLOTS, MAX_LIGHT_SLOTS
            )
            return heavy_slots, light_slots

    if mem_limit_bytes:
        mem_limit_gib = mem_limit_bytes / (1024**3)
        heavy_by_memory = int(mem_limit_gib // HEAVY_PARSE_MEMORY_GIB)
        heavy_slots = _clamp(
            min(cpu_count, heavy_by_memory), MIN_HEAVY_SLOTS, MAX_HEAVY_SLOTS
        )
    else:
        # No memory signal available -- fall back to CPU-only sizing.
        heavy_slots = _clamp(cpu_count, MIN_HEAVY_SLOTS, MAX_HEAVY_SLOTS)

    light_slots = _clamp(cpu_count * LIGHT_SLOTS_PER_CPU, MIN_LIGHT_SLOTS, MAX_LIGHT_SLOTS)
    return heavy_slots, light_slots


def memory_pressure_high(threshold: float = DEFAULT_MEMORY_PRESSURE_THRESHOLD) -> bool:
    """Return ``True`` when current memory usage exceeds *threshold* of the limit.

    Used as an admission guard before granting a heavy-pool slot: shedding
    load (503 + Retry-After) before the OOM killer intervenes lets the
    caller's existing retry/backoff and delayed re-queue absorb the burst
    instead of losing the in-flight parse and any others sharing the box.
    """
    limit = get_memory_limit_bytes()
    usage = get_memory_usage_bytes()
    if not limit or usage is None:
        return False
    return (usage / limit) >= threshold
