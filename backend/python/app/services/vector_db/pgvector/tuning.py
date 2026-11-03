"""The knobs of the search path. Every one has a type, a default, a range and an environment variable; a value that is missing, malformed
or out of range falls back to the default with a warning, so a typo in a config never takes search down.

Defaults are the provisional numbers of the design (dev-host measurements); ``pipeshub doctor`` style re-measurement on the target
hardware replaces them by configuration, not by code.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from typing import Any

logger = logging.getLogger(__name__)

# name: (default, minimum, maximum). The type is the default's.
_KNOBS: dict[str, tuple[float, float, float]] = {
    "n_e0": (40_000, 10, 5_000_000),                 # exact-scan ceiling: candidates an exact scan reads inside its budget
    "n_own": (10_000, 10, 5_000_000),                  # an org that owns its leaf is served by the vector index above this many candidates ...
    "own_min_share": (0.1, 0.0, 1.0),                  # ... when the filter keeps at least this share of the leaf (a narrow filter stays exact)
    "promotion_share": (0.05, 0.001, 1.0),              # an org above n_e0 holding this share of its shared leaf awaits promotion
    "r_min": (800, 50, 20_000),                         # rerank depth: max(r_min, r_factor * leg)
    "r_factor": (20, 1, 200),
    "deep_r_min": (4_000, 50, 50_000),                  # the same for an org that awaits promotion
    "deep_r_factor": (50, 1, 500),
    "ef_cap": (1_000, 40, 1_000),                       # pgvector's hnsw.ef_search maximum is 1000
    "scan_work_mem_mb": (64, 4, 1_024),                 # work_mem of a vector-index statement ...
    "scan_mem_multiplier": (4, 1, 1_024),               # ... times this = the graph-walk memory; the product never goes below 256 MB
    "e0_slots": (64, 1, 4_096),                         # concurrent large exact scans per database instance
    "h_slots": (64, 1, 4_096),                          # concurrent vector-index scans per database instance
    "admission_wait_ms": (25, 0, 1_000),                # how long a search waits for a slot before the degraded path
    "slot_min_candidates": (10_000, 0, 5_000_000),      # an exact scan below this many candidates takes no slot
    "e0_timeout_ms": (300, 10, 60_000),
    "h_timeout_ms_40": (1_000, 10, 60_000),             # H timeout by leg size: up to 40, up to 120, above
    "h_timeout_ms_120": (1_500, 10, 60_000),
    "h_timeout_ms_600": (3_000, 10, 60_000),
    "degraded_timeout_ms": (150, 10, 60_000),
    "degraded_rows": (10_000, 100, 5_000_000),          # candidates the degraded path may examine (a quarter of n_e0)
    "tombstone_hash_max": (50_000, 0, 5_000_000),       # up to this many tombstones they are tested as one hashed set (work_mem raised); above, by anti-join
    "verdict_ttl_s": (60.0, 0.0, 86_400.0),             # how often a process re-reads the stored quantisation verdict of a space it serves
    "check_slots": (2, 1, 64),                          # the sampler and the quantisation check share this small pool, apart from live searches
    "check_backoff_s": (3_600.0, 1.0, 604_800.0),       # wait after a quantisation check that could not finish
    "check_recheck_s": (604_800.0, 60.0, 31_536_000.0), # a finished check is repeated after this long
    "connector_ttl_s": (10.0, 0.0, 3_600.0),            # how long the org's connector set (for dropping the arms of an all-connectors user) is reused
    "connector_fail_ttl_s": (300.0, 0.0, 86_400.0),     # after a check that could not finish, the arms stay applied this long without trying again
    "connector_check_ms": (150, 10, 10_000),            # budget of that lookup; over it, the arms are applied
    "lexical_overfetch": (4, 1, 100),                   # lexical rows asked for, per row wanted, when a post-filter on entity or block columns applies
    "estimate_ttl_s": (60.0, 0.0, 86_400.0),            # how long a candidate count is reused
    "leaf_ttl_s": (300.0, 0.0, 86_400.0),               # how long a leaf's size is reused
    "request_concurrency": (2, 1, 32),                  # requests of one call that run at once
    "sampler_rate": (0.01, 0.0, 1.0),                   # share of dense legs re-run exactly (0 switches the sampler off)
    "sampler_max_per_s": (5, 1, 1_000),
    "sampler_queue": (64, 1, 100_000),
    "sampler_timeout_ms": (30_000, 100, 600_000),
    "validate_sample": (50, 5, 1_000),                  # queries of the bit-quantisation check
    "validate_floor": (0.95, 0.5, 1.0),                 # recall below this: the space is served exactly
}
_FLAGS = {"validate_quantization": True, "eliminate_all_apps": True, "fused_search": True}


def parse_flag(name: str, raw: object, *, default: bool) -> bool:
    """A switch: empty or unset is the default, 1/true/yes/on and 0/false/no/off (any case) are the two answers, anything else is a
    configuration error naming the setting (a misspelt "off" must not silently mean the default)."""
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text == "":
        return default
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be one of 1, true, yes, on, 0, false, no, off; got {raw!r}")


@dataclass(frozen=True)
class Tuning:
    n_e0: int = 40_000
    n_own: int = 10_000
    own_min_share: float = 0.1
    promotion_share: float = 0.05
    r_min: int = 800
    r_factor: int = 20
    deep_r_min: int = 4_000
    deep_r_factor: int = 50
    ef_cap: int = 1_000
    scan_work_mem_mb: int = 64
    scan_mem_multiplier: int = 4
    e0_slots: int = 64
    h_slots: int = 64
    admission_wait_ms: int = 25
    slot_min_candidates: int = 10_000
    e0_timeout_ms: int = 300
    h_timeout_ms_40: int = 1_000
    h_timeout_ms_120: int = 1_500
    h_timeout_ms_600: int = 3_000
    degraded_timeout_ms: int = 150
    degraded_rows: int = 10_000
    tombstone_hash_max: int = 50_000
    verdict_ttl_s: float = 60.0
    check_slots: int = 2
    check_backoff_s: float = 3_600.0
    check_recheck_s: float = 604_800.0
    connector_ttl_s: float = 10.0
    connector_fail_ttl_s: float = 300.0
    connector_check_ms: int = 150
    lexical_overfetch: int = 4
    estimate_ttl_s: float = 60.0
    leaf_ttl_s: float = 300.0
    request_concurrency: int = 2
    sampler_rate: float = 0.01
    sampler_max_per_s: int = 5
    sampler_queue: int = 64
    sampler_timeout_ms: int = 30_000
    validate_sample: int = 50
    validate_floor: float = 0.95
    validate_quantization: bool = True
    fused_search: bool = True            # a hybrid search is one statement in one unit of work (False: the legs run as separate statements)
    eliminate_all_apps: bool = True       # a user who can read every connector searches the whole org, without a membership probe per row

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Tuning:
        data = data or {}
        out: dict[str, Any] = {}
        for name, (default, lo, hi) in _KNOBS.items():
            raw = data.get(name, os.getenv(f"PGVECTOR_{name.upper()}"))
            if raw is None or raw == "":
                continue
            try:
                value = float(raw)
                if isinstance(default, int):
                    if value != int(value):
                        raise ValueError("not a whole number")
                    value = int(value)
                if not lo <= value <= hi or value != value:
                    raise ValueError("out of range")
            except (TypeError, ValueError):
                logger.warning("pgvector search setting %s=%r is not a number in %s..%s; using %s", name, raw, lo, hi, default)
                continue
            out[name] = value
        for name, default in _FLAGS.items():
            out[name] = parse_flag(f"PGVECTOR_{name.upper()}", data.get(name, os.getenv(f"PGVECTOR_{name.upper()}")), default=default)
        return cls(**out)

    # ---- derived ----
    def rerank_depth(self, leg: int, *, deep: bool = False) -> int:
        return max(self.deep_r_min, self.deep_r_factor * leg) if deep else max(self.r_min, self.r_factor * leg)

    def scan_memory(self) -> tuple[int, int]:
        """(work_mem in MB, hnsw.scan_mem_multiplier) of a vector-index statement: always at least 256 MB of graph-walk memory,
        whatever the organisation's own work_mem limit is (a smaller scan memory silently cuts recall)."""
        return self.scan_work_mem_mb, max(self.scan_mem_multiplier, -(-256 // self.scan_work_mem_mb))

    def h_timeout_ms(self, leg: int) -> int:
        return self.h_timeout_ms_40 if leg <= 40 else self.h_timeout_ms_120 if leg <= 120 else self.h_timeout_ms_600


assert {f.name for f in fields(Tuning)} == set(_KNOBS) | set(_FLAGS)      # a knob added in one place and not the other
