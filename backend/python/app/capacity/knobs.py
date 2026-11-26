"""The registry of operator-tunable capacity settings, and the fixed caps with their sources.

One declaration per knob. ``runtime_threads`` deletes every registry knob whose value is blank
from ``os.environ`` before anything reads it, so ``KEY=${KEY:-}`` in compose and an empty value in
a Helm chart both mean "not set". The ``passthrough`` set says where the knob must be written
down for operators; ``tests/unit/capacity/test_knob_passthrough.py`` checks every place.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

SOURCE_CLASSES = ("cpu", "memory", "cell", "downstream", "unit", "override")

COMPOSE = "compose"
HELM = "helm"
ENTRYPOINT_UNSET = "entrypoint_unset"
_ALL = frozenset({COMPOSE, HELM})


@dataclass(frozen=True)
class Knob:
    name: str
    type: type  # int | float | str
    legacy_default: object  # what an unset knob means today; None when the code derives it
    source_class: str  # what the value is when the operator does not set it
    doc: str
    passthrough: frozenset[str] = _ALL
    compose_default: str = ""  # the word after ``:-`` in compose


@dataclass(frozen=True)
class Cap:
    """A cap in the code that is not an operator knob, and what it is sized from."""

    name: str
    module: str
    value: object
    source_class: str
    reason: str


# Names the compose entrypoint unsets when blank. Images from before the blank-unset in
# ``runtime_threads`` crash on ``int("")``, so the loop stays for the knobs they read.
_LEGACY_ENTRYPOINT = frozenset({
    "MAX_CONCURRENT_PARSING",
    "MAX_CONCURRENT_INDEXING",
    "MAX_PENDING_INDEXING_TASKS",
    "EMBEDDING_SERVER_MAX_CONCURRENCY",
    "EMBEDDING_BATCH_CONCURRENCY",
    "MAX_CONCURRENT_INDEXING_LLM_CALLS",
})
_UNSET = _ALL | {ENTRYPOINT_UNSET}


def _k(
    name: str,
    typ: type,
    default: object,
    cls: str,
    doc: str,
    passthrough: frozenset[str] = _ALL,
    compose_default: str = "",
) -> Knob:
    if name in _LEGACY_ENTRYPOINT:
        passthrough = passthrough | {ENTRYPOINT_UNSET}
    return Knob(name, typ, default, cls, doc, frozenset(passthrough), compose_default)


KNOBS: tuple[Knob, ...] = (
    # Operator caps on the governor's ceilings.
    _k("MAX_CONCURRENT_PARSING", int, None, "override", "Cap on parse permits (heavy and light)."),
    _k("MAX_CONCURRENT_INDEXING", int, None, "override", "Cap on in-flight indexing records."),
    _k("MAX_PENDING_INDEXING_TASKS", int, None, "downstream", "Cap on records queued for indexing."),
    _k("GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU", float, 1.0, "cpu", "Heavy parse slots per CPU."),
    _k("GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU", float, 10.0, "cpu", "Light parse slots per CPU."),
    _k("GOVERNOR_LIGHT_PARSE_MAX", int, 256, "cell", "Absolute cap on the light parse ceiling."),
    _k("GOVERNOR_INDEX_HEADROOM", float, 2.0, "cpu", "In-flight records per parse slot."),
    _k("GOVERNOR_INDEX_MAX", int, 512, "cell", "Absolute cap on each index tier."),
    _k("GOVERNOR_INDEX_HEAVY_WORKING_SET_GB", float, None, "unit", "Memory assumed per in-flight heavy record."),
    _k("GOVERNOR_INDEX_LIGHT_WORKING_SET_GB", float, None, "unit", "Memory assumed per in-flight light record."),
    _k("GOVERNOR_HEAVY_PARSE_WORKING_SET_GB", float, None, "unit", "Memory assumed per in-flight heavy parse."),
    _k("GOVERNOR_BASELINE_MEMORY_MB", float, None, "memory", "Idle footprint of co-located services."),
    # Worker and process counts.
    _k("PARSE_POOL_WORKERS", int, None, "cpu", "Worker processes for large text, code and CSV parses."),
    _k("QUERY_UVICORN_WORKERS", int, None, "cpu", "Query service worker processes."),
    _k("INDEXING_UVICORN_WORKERS", int, 1, "cpu", "Indexing worker processes (cells).", frozenset({COMPOSE}), "1"),
    _k("EMBEDDING_SERVER_MAX_CONCURRENCY", int, None, "cpu", "Embedding server encode slots."),
    _k("EMBEDDING_SERVER_QUERY_RESERVED_SLOTS", int, None, "cpu", "Encode slots kept for search queries."),
    _k("EMBEDDING_BATCH_CONCURRENCY", int, None, "downstream", "Embedding requests in flight per batch."),
    _k("MAX_CONCURRENT_INDEXING_LLM_CALLS", int, 24, "downstream", "LLM calls in flight per LLM-calling service."),
    # The profile flag and its two calibration knobs. ``legacy`` (the default) is today's sizing.
    _k("PIPESHUB_CAPACITY_PROFILE", str, "legacy", "override", "Sizing profile: legacy (default) or auto."),
    _k("PIPESHUB_CAPACITY_CELL_CPUS", float, 16.0, "cell", "CPUs one indexing process (cell) is planned for."),
    _k("PIPESHUB_CPU_OVERSUB", float, 1.25, "unit", "Running threads per CPU the member budget admits."),
    # Budget overrides.
    _k("PIPESHUB_CPU_BUDGET", float, None, "override", "CPUs the plan may use, instead of the cgroup's."),
    _k("PIPESHUB_MEMORY_BUDGET", int, None, "override", "Bytes the plan may use, instead of the cgroup's."),
    # Read by the plan only; documented in docs/indexing-service.md, not passed through compose.
    Knob("PIPESHUB_NUMA_PINNING", str, "off", "override", "auto pins each indexing cell to one NUMA node.", frozenset()),
    # Thread env. Compose and the image set a default of their own, so only the blank-unset applies.
    Knob("OMP_NUM_THREADS", int, None, "unit", "Threads per ML library call.", frozenset()),
    Knob("MKL_NUM_THREADS", int, None, "unit", "MKL threads; follows OMP_NUM_THREADS.", frozenset()),
    Knob("OPENBLAS_NUM_THREADS", int, None, "unit", "OpenBLAS threads; follows OMP_NUM_THREADS.", frozenset()),
)

REGISTRY: Mapping[str, Knob] = {k.name: k for k in KNOBS}
assert len(REGISTRY) == len(KNOBS), "duplicate knob"
assert all(k.source_class in SOURCE_CLASSES for k in KNOBS)


def entrypoint_unset_names() -> frozenset[str]:
    return frozenset(k.name for k in KNOBS if ENTRYPOINT_UNSET in k.passthrough)


def drop_blank_knobs(env: MutableMapping[str, str] | None = None) -> tuple[str, ...]:
    """Delete every registry knob whose value is empty or white space. Returns the names removed."""
    target = os.environ if env is None else env
    removed = [k.name for k in KNOBS if k.name in target and not target[k.name].strip()]
    for name in removed:
        del target[name]
    return tuple(removed)


def knob_value(env: Mapping[str, str], name: str) -> object | None:
    """The operator's value, parsed; ``None`` when unset or blank. A malformed value raises ``ValueError``."""
    knob = REGISTRY[name]
    raw = (env.get(name) or "").strip()
    if not raw:
        return None
    return knob.type(raw)


# Fixed caps in the governor that are not operator knobs. Each states what it is sized from:
# a cap with no source here is a cap nobody can scale (the policy file pins the values).
CAPS: tuple[Cap, ...] = (
    Cap("INDEX_TOTAL_MAX", "policy", 96, "downstream",
        "bounds the records inside a graph write at once under the default Neo4j pool"),
    Cap("INDEX_TOTAL_MIN", "policy", 24, "cpu", "keeps a small host's pipeline full"),
    Cap("INDEX_SLOTS_PER_CPU", "policy", 6.0, "cpu", "in-flight records per CPU"),
    Cap("INDEX_MAX_PER_TIER", "policy", 512, "cell", "per-loop bound of one tier"),
    Cap("INDEX_MIN_PER_TIER", "policy", 8, "cpu", "keeps a tiny host from serialising a tier"),
    Cap("LIGHT_PARSE_MAX", "policy", 256, "cell", "in-flight HTTP requests of one event loop"),
    Cap("INDEX_HEAVY_MAX_SHARE", "policy", 2.0 / 3.0, "cpu", "heavy's share of the index total"),
    Cap("EMBEDDING_CPU_RESERVATION", "policy", 2.0, "cpu", "CPUs kept for the local embedding model"),
    Cap("EMBEDDING_CPU_RESERVATION_MAX_FRACTION", "policy", 0.25, "cpu", "the reservation's share of the quota"),
)


def capacity_profile(env: Mapping[str, str] | None = None) -> str:
    """``"auto"`` only when ``PIPESHUB_CAPACITY_PROFILE`` says so; anything else is ``"legacy"``."""
    source = os.environ if env is None else env
    return "auto" if (source.get("PIPESHUB_CAPACITY_PROFILE") or "").strip().lower() == "auto" else "legacy"


def positive_float(raw: object, *, maximum: float = math.inf) -> float | None:
    """``raw`` as a finite number above zero (and at most ``maximum``), else ``None``."""
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and 0 < value <= maximum else None


_BYTE_SUFFIXES = {"Ki": 1 << 10, "Mi": 1 << 20, "Gi": 1 << 30, "Ti": 1 << 40}


def parse_bytes(raw: object) -> int | None:
    """A positive byte count: ASCII digits, optionally with a Kubernetes ``Ki``/``Mi``/``Gi``/``Ti`` suffix."""
    text = str(raw or "").strip()
    mult = 1
    for suffix, factor in _BYTE_SUFFIXES.items():
        if text.endswith(suffix):
            text, mult = text[: -len(suffix)], factor
            break
    if not (text.isascii() and text.isdigit()):
        return None
    return int(text) * mult or None
