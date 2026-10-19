"""Estimate how much memory Qdrant needs to keep search fast, and compare it
with what Qdrant has.

PipesHub keeps every bulk Qdrant structure memory-mapped (see
``CollectionConfig``), so running short of memory does not fail; it slows
search. Every query walks the quantized vectors, the HNSW graph, the
sparse index and the payload index of the filtered fields. When those do not fit
in the page cache that Qdrant's memory limit leaves after its own process
memory, each new query reads them back from disk, and concurrent queries queue
on that disk. The full-precision vectors are not in this working set: a query
reads only the few it rescores.

The per-point figures below were measured on a 5.08M-point collection (1536
dimensions, int8 scalar quantization, HNSW m=16, five keyword payload indexes).
The estimate lands within about 5% of the file sizes on disk, on the low side,
because points deleted but not yet vacuumed are not counted. Sizing guidance
built on the same figures: docs/deployment/vector-db-sizing.md.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Optional

from app.services.vector_db.models import (
    CollectionMemoryFootprint,
    VectorDBMemoryReport,
)

GIB = 1024**3

# Qdrant's own process memory on a large collection (id tracker, segment
# metadata, allocator), outside the page cache the indexes need.
BASE_BYTES = 2 * GIB
# Scalar quantization stores one byte per dimension plus one float per vector.
SCALAR_CORRECTION_BYTES_PER_POINT = 4
# HNSW links per point at m=16; the graph grows linearly with m.
HNSW_BYTES_PER_POINT_AT_M16 = 60
SPARSE_INDEX_BYTES_PER_POINT = 50
# One keyword payload index entry, counted per (indexed field, point) pair.
PAYLOAD_INDEX_BYTES_PER_FIELD_POINT = 65
# Below this the index is too small for a shortfall to matter, and the fixed
# base figure (sized for large collections) would otherwise dominate the sum.
MIN_INDEX_BYTES_TO_WARN = 1 * GIB

_FLOAT32_BYTES = 4
_BINARY_BITS = {"one_bit": 1.0, "one_and_half_bits": 1.5, "two_bits": 2.0}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgtp]?)(i?)(b?)\s*$", re.IGNORECASE)
_UNIT_POWER = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4, "p": 5}

STATUS_OK = "ok"
STATUS_INSUFFICIENT = "insufficient"
STATUS_UNKNOWN = "unknown"

SOURCE_CONFIGURED = "configured"
SOURCE_HOST = "host"


def parse_memory_size(value: Optional[str]) -> Optional[int]:
    """Parse a container memory limit such as ``6G``, ``4Gi`` or ``512m``.

    Units are binary, as Docker reads them, whether or not they carry ``i``.
    Kubernetes reads a bare ``G`` as decimal, so a Helm value written that way
    comes out about 7% larger than Kubernetes enforces; the chart's own values
    use ``Gi``. Returns None for an empty or unreadable value.
    """
    if value is None:
        return None
    match = _SIZE_RE.match(str(value))
    if not match:
        return None
    number, unit, _binary, _suffix = match.groups()
    size = int(float(number) * (1024 ** _UNIT_POWER[unit.lower()]))
    return size if size > 0 else None


def _quantized_bytes_per_point(dimension: int, quantization: Mapping[str, Any] | None) -> tuple[str, int, bool]:
    """Return (kind, bytes per point, always_ram) for one dense vector."""
    if not quantization:
        # Without quantization the full vectors are what HNSW search reads.
        return "none", dimension * _FLOAT32_BYTES, False
    if "scalar" in quantization:
        cfg = quantization.get("scalar") or {}
        return "scalar", dimension + SCALAR_CORRECTION_BYTES_PER_POINT, bool(cfg.get("always_ram"))
    if "product" in quantization:
        cfg = quantization.get("product") or {}
        ratio_text = str(cfg.get("compression") or "x4").lower().lstrip("x")
        ratio = int(ratio_text) if ratio_text.isdigit() and int(ratio_text) > 0 else 4
        return "product", math.ceil(dimension * _FLOAT32_BYTES / ratio), bool(cfg.get("always_ram"))
    if "binary" in quantization:
        cfg = quantization.get("binary") or {}
        bits = _BINARY_BITS.get(str(cfg.get("encoding") or "one_bit"), 1.0)
        return "binary", math.ceil(dimension * bits / 8), bool(cfg.get("always_ram"))
    return "none", dimension * _FLOAT32_BYTES, False


def _dense_vectors(params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    vectors = params.get("vectors") or {}
    if not isinstance(vectors, Mapping):
        return []
    if "size" in vectors:
        return [vectors]
    return [v for v in vectors.values() if isinstance(v, Mapping) and "size" in v]


def collection_footprint(name: str, info: Mapping[str, Any]) -> CollectionMemoryFootprint:
    """Estimate one collection's search working set from ``GET /collections/{name}``.

    ``info`` is that response's ``result`` object.
    """
    points = int(info.get("points_count") or 0)
    config = info.get("config") or {}
    params = config.get("params") or {}
    collection_quantization = config.get("quantization_config")
    collection_m = (config.get("hnsw_config") or {}).get("m", 16)

    quantized = 0
    hnsw = 0
    kinds: list[str] = []
    always_ram = False
    for vector in _dense_vectors(params):
        dimension = int(vector.get("size") or 0)
        kind, per_point, pinned = _quantized_bytes_per_point(
            dimension, vector.get("quantization_config") or collection_quantization
        )
        kinds.append(kind)
        always_ram = always_ram or pinned
        quantized += points * per_point
        m = (vector.get("hnsw_config") or {}).get("m", collection_m)
        hnsw += int(points * HNSW_BYTES_PER_POINT_AT_M16 * (m or 0) / 16)

    sparse_vectors = params.get("sparse_vectors") or {}
    sparse = points * SPARSE_INDEX_BYTES_PER_POINT * len(sparse_vectors)

    payload_schema = info.get("payload_schema") or {}
    field_points = sum(
        int((schema or {}).get("points") or 0) for schema in payload_schema.values()
    )
    payload = field_points * PAYLOAD_INDEX_BYTES_PER_FIELD_POINT

    return CollectionMemoryFootprint(
        name=name,
        points=points,
        quantization="+".join(sorted(set(kinds))) or "none",
        always_ram=always_ram,
        quantized_vectors_bytes=quantized,
        hnsw_bytes=hnsw,
        sparse_index_bytes=sparse,
        payload_index_bytes=payload,
    )


def node_share(info: Mapping[str, Any], nodes: int) -> float:
    """Fraction of a collection the busiest node holds.

    A node holds ``ceil(shards * replicas / nodes)`` shard copies at most, each
    ``1 / shards`` of the collection. On a single node this is 1.
    """
    params = (info.get("config") or {}).get("params") or {}
    shards = max(int(params.get("shard_number") or 1), 1)
    replicas = max(int(params.get("replication_factor") or 1), 1)
    nodes = max(int(nodes or 1), 1)
    copies = min(math.ceil(shards * replicas / nodes), shards)
    return copies / shards


def assess_memory(
    collections: Mapping[str, Mapping[str, Any]],
    *,
    configured_limit_bytes: Optional[int],
    host_ram_bytes: Optional[int],
    nodes: int = 1,
) -> VectorDBMemoryReport:
    """Compare the per-node working set of every collection with Qdrant's memory.

    ``configured_limit_bytes`` is Qdrant's container limit as the deployment
    states it (QDRANT_MEMORY_LIMIT). ``host_ram_bytes`` is the machine's RAM as
    Qdrant reports it, which ignores container limits, so on its own it can only
    prove a shortfall, never that memory is enough: with only the host figure
    the status is ``insufficient`` or ``unknown``, never ``ok``.
    """
    footprints = [collection_footprint(name, info) for name, info in collections.items()]
    index_bytes = int(
        sum(
            fp.index_bytes * node_share(collections[fp.name], nodes)
            for fp in footprints
        )
    )
    needed = index_bytes + BASE_BYTES
    points = sum(fp.points for fp in footprints)

    available: Optional[int] = None
    source: Optional[str] = None
    if configured_limit_bytes:
        available, source = configured_limit_bytes, SOURCE_CONFIGURED
        if host_ram_bytes and host_ram_bytes < configured_limit_bytes:
            available, source = host_ram_bytes, SOURCE_HOST
    elif host_ram_bytes:
        available, source = host_ram_bytes, SOURCE_HOST

    if available is None:
        status = STATUS_UNKNOWN
    elif needed > available and index_bytes >= MIN_INDEX_BYTES_TO_WARN:
        status = STATUS_INSUFFICIENT
    elif source == SOURCE_HOST and not configured_limit_bytes:
        status = STATUS_UNKNOWN
    else:
        status = STATUS_OK

    return VectorDBMemoryReport(
        status=status,
        needed_bytes=needed,
        index_bytes=index_bytes,
        base_bytes=BASE_BYTES,
        available_bytes=available,
        available_source=source,
        points=points,
        nodes=max(int(nodes or 1), 1),
        collections=footprints,
    )
