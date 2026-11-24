"""``PARSE_ADMISSION=classes`` in the indexing process: the switch, the estimate, the gates.

Everything the consumers and the parse pool need to admit heavy-format work by cost class lives here, so
the existing admission functions change by a guard and a call. With the flag off nothing in this module
runs: ``classes_enabled()`` is the only thing the old paths call.

* The mode and its refusal: ``PARSE_ADMISSION`` is ``legacy`` (default) or ``classes``; ``classes`` with
  ``PARSE_LANES=on`` is a startup error that names both.
* The class of a record: from its format and size (``estimate_for``). The class is the dispatch level of a
  heavy-format record; a light-format record stays ``ParseTier.LIGHT``.
* One ``ClassGate`` per pool per event loop: ``index_heavy`` and ``docling`` order waiters only (the
  governor's gate behind them keeps the limits, the brakes and the statistics), ``heavy_parse`` orders
  and, when the member budget is on, reserves the CPU tokens once.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import os
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.capacity.classes import BIG_CLASSES, CostClass
from app.modules.parse_engine.admission import AdmissionNumbers, LiveAdmissionConfig
from app.modules.parse_engine.budget_port import Demand, Family
from app.modules.parse_engine.class_gate import (
    ClassGate,
    GateRequest,
    PoolCapacity,
    request_from_estimate,
)
from app.modules.parse_engine.cost import (
    CostEstimate,
    CostInputs,
    FormatKind,
    cost_config_from_env,
    estimate,
)
from app.services.messaging.config import messaging_env
from app.services.resource_governor.kinds import ParseKind, classify_kind
from app.services.resource_governor.lanes import parse_lanes_mode
from app.services.resource_governor.models import ParseTier, Pool
from app.services.resource_governor.tiers import classify
from app.utils.env_config import env_int

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.services.resource_governor import ResourceGovernor

_logger = logging.getLogger(__name__)

MODE_ENV = "PARSE_ADMISSION"
MODES = ("legacy", "classes")
_MIB = 1 << 20
INSPECT_MEMORY_BYTES = int(0.1 * (1 << 30))
SMALL_MEMORY_AHEAD_BYTES = 1 << 30  # a small record's memory reservation goes ahead of queued big ones up to this
DOWNLOAD_AHEAD_SMALL_DEFAULT = 4
DOWNLOAD_AHEAD_BIG_DEFAULT = 2

__all__ = [
    "NEVER",
    "AdmissionConfigError",
    "ClassContext",
    "as_tier",
    "assert_compatible",
    "attach_config_service",
    "class_context",
    "classes_enabled",
    "docling_gate",
    "download_ahead",
    "estimate_for",
    "index_gate",
    "is_class",
    "lane_demand",
    "level_for",
    "level_name",
    "memory_goes_ahead",
    "parse_gate",
    "pool_width",
    "refresh_numbers",
]


class AdmissionConfigError(RuntimeError):
    """The flags cannot be combined; raised at startup."""


class _Never:
    """A cancel token that never fires: a wait that task cancellation alone can end."""

    @staticmethod
    def cancelled() -> bool:
        return False


NEVER = _Never()


def admission_mode(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = (env.get(MODE_ENV) or "legacy").strip().lower()
    if value not in MODES:
        _logger.warning("%s=%r is not one of %s; using legacy", MODE_ENV, value, MODES)
        return "legacy"
    return value


def classes_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return admission_mode(environ) == "classes"


BIG_WAIT_ENV = "PARSE_BIG_WAIT_SECONDS"
BIG_WAIT_DEFAULT_S = 600.0


def big_wait_seconds(environ: Mapping[str, str] | None = None) -> float:
    """How long a large or huge record waits for its turn at the gates before it goes back to the broker."""
    env = os.environ if environ is None else environ
    raw = (env.get(BIG_WAIT_ENV) or "").strip()
    if not raw:
        return BIG_WAIT_DEFAULT_S
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        _logger.warning("%s=%r is not a positive number; using %s", BIG_WAIT_ENV, raw, BIG_WAIT_DEFAULT_S)
        return BIG_WAIT_DEFAULT_S
    return value


def assert_compatible(environ: Mapping[str, str] | None = None) -> None:
    if classes_enabled(environ) and parse_lanes_mode(environ) == "on":
        raise AdmissionConfigError(
            "PARSE_LANES=on and PARSE_ADMISSION=classes cannot both be enabled: the cost classes replace the lanes. "
            "Unset PARSE_LANES or set PARSE_ADMISSION=legacy."
        )


# -- the class of a record ---------------------------------------------------------------------------


def format_kind(extension: str | None, mime_type: str | None) -> FormatKind | None:
    """The estimator's format of a heavy-format record; ``None`` for a light-format one (no class)."""
    if classify(extension, mime_type) is ParseTier.LIGHT:
        return None
    return {
        ParseKind.PDF: FormatKind.PDF,
        ParseKind.PDF_LAYOUT: FormatKind.PDF,
        ParseKind.DOCLING_PDF: FormatKind.PDF,
        ParseKind.DOCLING_DOCX: FormatKind.DOCX,
        ParseKind.EXCEL_LOAD: FormatKind.XLSX,
    }.get(classify_kind(extension, mime_type), FormatKind.OTHER)


def estimate_for(
    extension: str | None, mime_type: str | None, size_bytes: int | None = None, **inspected: Any  # noqa: ANN401
) -> CostEstimate | None:
    kind = format_kind(extension, mime_type)
    if kind is None:
        return None
    return estimate(CostInputs(kind=kind, size_bytes=size_bytes, **inspected), _cost_config())


_COST_CONFIG: list[Any] = []


def _cost_config() -> Any:  # noqa: ANN401
    if not _COST_CONFIG:
        _COST_CONFIG.append(cost_config_from_env())
    return _COST_CONFIG[0]


def level_for(extension: str | None, mime_type: str | None, size_bytes: int | None = None) -> ParseTier | CostClass:
    """The dispatch level: the cost class of a heavy-format record, ``ParseTier.LIGHT`` for a light one."""
    est = estimate_for(extension, mime_type, size_bytes)
    return ParseTier.LIGHT if est is None else est.cls


def is_class(level: object) -> bool:
    return isinstance(level, CostClass)


def as_tier(level: ParseTier | CostClass | None) -> ParseTier | None:
    """The governor's tier of a level: every cost class is heavy-format work."""
    return ParseTier.HEAVY if isinstance(level, CostClass) else level


def level_name(level: ParseTier | CostClass) -> str:
    return level.value


@dataclass(frozen=True)
class ClassContext:
    """What a gate needs to know about the record asking: who, since when, how urgent, what format."""

    org: str = ""
    extension: str | None = None
    mime_type: str | None = None
    priority: bool = False
    first_seen_ms: int | None = None  # epoch ms the record first queued for parsing; a hand-back keeps it
    pages: int | None = None  # inspected facts (START_INSPECT): they replace the size prior of the estimate
    scanned: bool | None = None
    cells: int | None = None  # a workbook's cell area

    def facts(self) -> dict[str, Any]:
        """The inspected facts the estimator takes, none when nothing was inspected."""
        out: dict[str, Any] = {}
        if self.pages is not None:
            out["pages"] = self.pages
        if self.scanned is not None:
            out["scanned"] = self.scanned
        if self.cells is not None:
            out["cells"] = self.cells
        return out

    def inspected(
        self, pages: int | None = None, *, scanned: bool | None = None, cells: int | None = None
    ) -> ClassContext:
        """This context with what the inspection found; unchanged when it found nothing."""
        if pages is None and scanned is None and cells is None:
            return self
        return dataclasses.replace(
            self,
            pages=pages if pages is not None else self.pages,
            scanned=scanned,
            cells=cells if cells is not None else self.cells,
        )

    def first_seen_s(self, clock: Callable[[], float] = time.monotonic) -> float | None:
        if self.first_seen_ms is None:
            return None
        return clock() - max(0.0, time.time() - self.first_seen_ms / 1000.0)


def class_context(payload: Mapping[str, Any] | None, *, priority: bool = False) -> ClassContext | None:
    """The context of a record event, or ``None`` when classes are off."""
    if not classes_enabled():
        return None
    data = payload or {}
    raw = data.get("parseEnqueuedAtMs")
    return ClassContext(
        # The outermost level of the fairness key (the org by default): a scheduling key for round-robin, not an authorisation input.
        org=str(data.get(messaging_env.fair_scheduling_key_fields[0]) or ""),
        extension=str(data.get("extension") or "") or None,
        mime_type=str(data.get("mimeType") or "") or None,
        priority=priority,
        first_seen_ms=raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None,
    )


# -- the pools ----------------------------------------------------------------------------------------


def pool_width(cpu_bound_ceiling: int) -> int:
    """``W``: the plan's ``parse_executor_slots`` when there is one, else what the legacy pool takes."""
    try:
        from app.capacity.runtime import current_plan

        plan = current_plan()
        if plan is not None and plan.widths is not None:
            return max(1, plan.widths.parse_executor_slots)
    except Exception:
        _logger.debug("no capacity plan for the pool width", exc_info=True)
    configured = env_int("PARSE_POOL_WORKERS", -1)
    if configured < 0:
        return max(1, min(4, cpu_bound_ceiling // 2))
    return min(configured, cpu_bound_ceiling)


def lane_demand(kind: object | None, cls: CostClass | None) -> Demand:
    """What a pool lane reserves when it is given no permit."""
    if kind is not None and str(getattr(kind, "value", kind)) == "inspect":
        return Demand(Family.INSPECT, 1, INSPECT_MEMORY_BYTES, CostClass.SMALL)
    return Demand(Family.PARSE, 1, 0, cls or CostClass.SMALL)


# -- the gates ------------------------------------------------------------------------------------------


class _Registry:
    config = LiveAdmissionConfig(None)
    # Per event loop (a gate wakes its waiters from the loop they wait on), dropped with the loop.
    gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, ClassGate]] = weakref.WeakKeyDictionary()
    ahead: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, Any]] = weakref.WeakKeyDictionary()


def attach_config_service(config_service: Any) -> None:  # noqa: ANN401
    """Let the admission numbers be read from the configuration store (``/services/indexing/parseAdmission``)."""
    _Registry.config = LiveAdmissionConfig(config_service)


async def refresh_numbers() -> AdmissionNumbers:
    """Re-read the live numbers (at most every 30 s); a store that cannot be read keeps the last good value."""
    return await _Registry.config.refresh()


def _numbers() -> AdmissionNumbers:
    return _Registry.config.peek()


def current_numbers() -> AdmissionNumbers:
    return _Registry.config.peek()


def _gate(name: str, build: Callable[[], ClassGate]) -> ClassGate:
    per_loop = _Registry.gates.setdefault(asyncio.get_running_loop(), {})
    gate = per_loop.get(name)
    if gate is None:
        gate = per_loop[name] = build()
    return gate


def index_gate(governor: ResourceGovernor) -> ClassGate:
    """Orders the waiters of the heavy index pool; the governor's gate behind it enforces the limit."""
    return _gate(
        "index_heavy",
        lambda: ClassGate(
            "index_heavy", numbers=_numbers, capacity=lambda: PoolCapacity(max(1, governor.limit(Pool.INDEX_HEAVY)))
        ),
    )


def docling_width(governor: ResourceGovernor) -> int:
    try:
        from app.capacity.runtime import current_plan

        plan = current_plan()
        if plan is not None and plan.widths is not None:
            return max(1, plan.widths.docling_workers_max)
    except Exception:
        _logger.debug("no capacity plan for the Docling width", exc_info=True)
    return max(1, env_int("PARSE_DOCLING_WIDTH", max(2, governor.ceilings.heavy)))


def docling_gate(governor: ResourceGovernor) -> ClassGate:
    """The ticket a Docling-routed small PDF queues for: a Docling permit, no CPU parse slot."""
    return _gate(
        "docling", lambda: ClassGate("docling", numbers=_numbers, capacity=lambda: PoolCapacity(docling_width(governor)))
    )


def parse_gate(governor: ResourceGovernor, budget: Any | None) -> ClassGate:  # noqa: ANN401
    """Orders CPU parse steps in the governor's permits (the unit the pool behind it counts in), so a large
    parse that costs two permits is two here too. With the member budget it also reserves the step's tokens,
    once; the ledger enforces its held floors when it grants them."""
    return _gate(
        "heavy_parse",
        lambda: ClassGate(
            "heavy_parse", numbers=_numbers, budget=budget,
            capacity=lambda: PoolCapacity(max(1, governor.limit(Pool.HEAVY_PARSE))),
        ),
    )


def remote_gate(size: int) -> ClassGate:
    """Orders parses that run in a remote parsing service, by class: the same admission rules as the local parse
    gate, over the configured remote concurrency. With classes on it replaces the FIFO remote slot pool (a parse
    takes a place here or there, never both), so a small parse is not queued behind large ones."""
    from app.services.parsing.remote_concurrency import remote_parse_concurrency

    return _gate(
        "remote_parse",
        lambda: ClassGate(
            "remote_parse", numbers=_numbers, capacity=lambda: PoolCapacity(max(1, remote_parse_concurrency() or size))
        ),
    )


def download_ahead(level: CostClass) -> Any:  # noqa: ANN401
    """Places among records that hold downloaded bytes without a parse permit: one set per class."""
    from app.services.resource_governor.download_ahead import DownloadAhead

    name = "small" if level is CostClass.SMALL else "big"
    per_loop = _Registry.ahead.setdefault(asyncio.get_running_loop(), {})
    ahead = per_loop.get(name)
    if ahead is None:
        limit = (
            env_int("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", DOWNLOAD_AHEAD_SMALL_DEFAULT)
            if name == "small"
            else env_int("INDEX_HEAVY_DOWNLOAD_AHEAD", DOWNLOAD_AHEAD_BIG_DEFAULT)
        )
        ahead = per_loop[name] = DownloadAhead(limit)
    return ahead


def memory_goes_ahead(level: CostClass | None, cost_bytes: int) -> bool:
    """A small record's memory reservation (up to 1 GiB) is served ahead of queued big ones."""
    return level is CostClass.SMALL and 0 < cost_bytes <= SMALL_MEMORY_AHEAD_BYTES


def request_for(
    ctx: ClassContext, est: CostEstimate, *, family: Family | None = None, cpu: int = 1, tokens: int | None = None
) -> GateRequest:
    req = request_from_estimate(est, org=ctx.org, priority=ctx.priority, cpu=cpu)
    if tokens is not None:
        req = dataclasses.replace(req, tokens=tokens)
    first = ctx.first_seen_s()
    updates: dict[str, Any] = {}
    if first is not None:
        updates["first_seen_s"] = first
    if family is not None:
        updates["family"] = family
    if updates:
        req = dataclasses.replace(req, **updates)
    return req


def class_of(ctx: ClassContext | None, size_bytes: int | None) -> CostClass:
    """The class of the record *ctx* describes at *size_bytes*, with the facts inspection found: small when it
    is not a heavy format or unknown."""
    if ctx is None:
        return CostClass.SMALL
    est = estimate_for(ctx.extension, ctx.mime_type, size_bytes, **ctx.facts())
    return CostClass.SMALL if est is None else est.cls


async def order_index(governor: ResourceGovernor, level: CostClass, ctx: ClassContext | None) -> Any:  # noqa: ANN401
    """Wait for this record's turn at the heavy index pool: small ahead of large, org round-robin inside big."""
    await refresh_numbers()
    ctx = ctx or ClassContext()
    req = GateRequest(cls=level, org=ctx.org, priority=ctx.priority, first_seen_s=ctx.first_seen_s())
    return await index_gate(governor).acquire(req, cancel=NEVER)


def reclass(ordered: Any, ctx: ClassContext | None, size_bytes: int | None) -> CostClass | None:  # noqa: ANN401
    """The size is known now: move the index permit to the class it belongs to, in place (no requeue)."""
    if ordered is None or ctx is None:
        return None
    cls = class_of(ctx, size_bytes)
    ordered.upgrade(cls)
    return cls


def parse_engine_stats() -> dict[str, Any]:
    """The ``/health`` ``parseEngine`` block: per class, how many wait, how many run, and the oldest waiter's age;
    summed over every gate on every loop (the oldest is the maximum)."""
    classes: dict[str, dict[str, int | float]] = {
        c.value: {"waiting": 0, "in_flight": 0, "oldest_wait_s": 0.0} for c in CostClass
    }
    gates: dict[str, dict[str, dict[str, int | float]]] = {}
    for per_loop in list(_Registry.gates.values()):
        for name, gate in list(per_loop.items()):
            stats = gate.stats()
            gates[name] = stats
            for cls, row in stats.items():
                agg = classes[cls]
                agg["waiting"] += row["waiting"]
                agg["in_flight"] += row["running"]
                agg["oldest_wait_s"] = max(agg["oldest_wait_s"], row["oldest_wait_s"])
    return {"admission": admission_mode(), "classes": classes, "gates": gates, "config_error": _Registry.config.last_error}


def reset_for_tests() -> None:
    _Registry.gates.clear()
    _Registry.ahead.clear()
    _Registry.config = LiveAdmissionConfig(None)
    _COST_CONFIG.clear()


__all__ += ["parse_engine_stats", "BIG_CLASSES", "current_numbers", "class_of", "order_index", "reclass", "admission_mode", "docling_width", "format_kind", "request_for", "reset_for_tests"]
