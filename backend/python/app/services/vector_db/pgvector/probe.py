"""What this PostgreSQL + pgvector can be trusted to do.

``evaluate`` is pure (versions in, flags or a refusal out). The versions come from the
database runtime's capability probe (``app.core.db``), which reads ``extversion`` and the
presence of ``pg_trgm``, never the platform name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.vector_db.pgvector.errors import PgVectorUnsupportedError

Version = tuple[int, int, int]


@dataclass(frozen=True)
class PgCapabilities:
    pg_major: int
    pgvector_version: str
    halfvec: bool
    # bit HNSW with Hamming distance; off on PG18 below 0.8.3 (regression).
    bit_hnsw: bool
    iterative_scan: bool
    # Parallel HNSW builds are only safe from 0.8.2.
    parallel_hnsw_build: bool
    # Below 0.8.4 HNSW vacuum can leave the graph unrepaired: tombstone instead.
    hnsw_safe_mode: bool


def parse_version(text: str) -> Version:
    nums = [int(n) for n in re.findall(r"\d+", text)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))  # type: ignore[return-value]


def evaluate(pg_major: int, extversion: str, *, strict: bool = False) -> PgCapabilities:
    v = parse_version(extversion)
    if v < (0, 7, 0):
        raise PgVectorUnsupportedError(
            f"pgvector {extversion} is too old: 0.7.0 or newer is required"
        )
    if strict and v < (0, 8, 0):
        raise PgVectorUnsupportedError(
            f"pgvector {extversion}: strict mode needs 0.8.0 or newer (iterative scan)"
        )
    if strict and v < (0, 8, 2):
        raise PgVectorUnsupportedError(
            f"pgvector {extversion}: strict mode needs 0.8.2 or newer (parallel HNSW build fix)"
        )
    return PgCapabilities(
        pg_major=pg_major,
        pgvector_version=extversion,
        halfvec=True,
        bit_hnsw=not (pg_major >= 18 and v < (0, 8, 3)),
        iterative_scan=v >= (0, 8, 0),
        parallel_hnsw_build=v >= (0, 8, 2),
        hnsw_safe_mode=v < (0, 8, 4),
    )
