"""Per-record indexing stage timings.

A record that holds an index slot for minutes logs nothing between "blob
written" and "embeddings stored", so a stall cannot be placed from the logs.
This module times the stages a record passes through and writes one INFO line
per record with where the time went, plus a WARNING the moment a single step
runs past ``INDEXING_SLOW_STEP_SECONDS``.

The consumer wrapper opens a ``RecordTimeline`` and marks the slot and parse
boundaries; code deeper in the pipeline wraps its steps in ``stage(...)``,
which finds the timeline through a ContextVar. Tasks the pipeline spawns
(the concurrent embedding batches) copy the context, so they add to the same
timeline. Outside a record, ``stage`` still warns on a slow step.

Overhead is two ``perf_counter`` calls and a dict update per step.
``INDEXING_TIMING_LOGS=false`` turns the summary and the warnings off.
"""

from __future__ import annotations

import contextlib
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from app.utils.logger import create_logger

if TYPE_CHECKING:
    import logging
    from collections.abc import Iterator

__all__ = [
    "RecordTimeline",
    "begin_record",
    "current_timeline",
    "end_record",
    "record_stage",
    "slow_step_seconds",
    "stage",
    "timing_enabled",
]

_logger = create_logger("indexing_service")

_current: ContextVar["RecordTimeline | None"] = ContextVar(
    "indexing_record_timeline", default=None
)

_DEFAULT_SLOW_STEP_SECONDS = 30.0
# A record with hundreds of slow embedding batches would otherwise log
# hundreds of warnings; the summary line still counts every one.
_MAX_SLOW_WARNINGS_PER_STAGE = 3

# Wrapper boundaries, in the order a record passes them.
ADMITTED = "admitted"
PARSE_REQUESTED = "parse_requested"
PARSE_ADMITTED = "parse_admitted"
PARSED = "parsed"
RELEASED = "released"


def timing_enabled() -> bool:
    return os.getenv("INDEXING_TIMING_LOGS", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def slow_step_seconds() -> float:
    raw = os.getenv("INDEXING_SLOW_STEP_SECONDS")
    if not raw:
        return _DEFAULT_SLOW_STEP_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_SLOW_STEP_SECONDS
    return value if value > 0 else _DEFAULT_SLOW_STEP_SECONDS


class _StageStat:
    __slots__ = ("count", "total", "max", "slow")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0
        self.slow = 0


class RecordTimeline:
    """Timings for one delivery of one record.

    Boundaries (``mark``) are wall-clock points on the record's own path;
    stages (``add``) are steps that may run concurrently, so a stage's total
    can exceed the record's wall time — it is busy time, not elapsed time.
    """

    __slots__ = ("record_id", "event_type", "tier", "started", "_marks", "_stages")

    def __init__(
        self,
        record_id: str | None,
        event_type: str | None = None,
        tier: str | None = None,
        *,
        started: float | None = None,
    ) -> None:
        self.record_id = record_id
        self.event_type = event_type
        self.tier = tier
        self.started = time.perf_counter() if started is None else started
        self._marks: dict[str, float] = {}
        self._stages: dict[str, _StageStat] = {}

    def mark(self, name: str, at: float | None = None) -> None:
        """Record a boundary; only its first crossing counts."""
        if name not in self._marks:
            self._marks[name] = time.perf_counter() if at is None else at

    def has_mark(self, name: str) -> bool:
        return name in self._marks

    def add(self, name: str, seconds: float, *, slow: bool = False) -> int:
        """Add one step's duration; returns how many slow steps the stage has had."""
        stat = self._stages.get(name)
        if stat is None:
            stat = self._stages[name] = _StageStat()
        stat.count += 1
        stat.total += seconds
        if seconds > stat.max:
            stat.max = seconds
        if slow:
            stat.slow += 1
        return stat.slow

    def stage_totals(self) -> dict[str, tuple[int, float, float]]:
        return {
            name: (stat.count, stat.total, stat.max)
            for name, stat in self._stages.items()
        }

    def phases(self, now: float | None = None) -> dict[str, float]:
        """Elapsed time between the wrapper boundaries this record crossed."""
        end = time.perf_counter() if now is None else now
        marks = self._marks
        admitted = marks.get(ADMITTED)
        phases: dict[str, float] = {}
        if admitted is None:
            phases["wait_slot"] = end - self.started
            return phases
        phases["wait_slot"] = admitted - self.started
        released = marks.get(RELEASED, end)
        phases["held"] = released - admitted

        requested = marks.get(PARSE_REQUESTED)
        parse_admitted = marks.get(PARSE_ADMITTED)
        parsed = marks.get(PARSED)
        if requested is not None:
            phases["pre_parse"] = requested - admitted
            if parse_admitted is not None:
                phases["parse_wait"] = parse_admitted - requested
                # No PARSED: the record timed out or was cancelled mid-parse.
                phases["parse"] = (parsed or released) - parse_admitted
            else:
                phases["parse_wait"] = (parsed or released) - requested
        if parsed is not None:
            phases["post_parse"] = released - parsed
        return phases

    def summary(self, outcome: str, now: float | None = None) -> str:
        end = time.perf_counter() if now is None else now
        phases = self.phases(end)
        head = " ".join(f"{name}={secs:.1f}s" for name, secs in phases.items())
        ranked = sorted(self._stages.items(), key=lambda item: item[1].total, reverse=True)
        stages = " ".join(
            f"{name}={stat.total:.1f}s"
            + (f"/{stat.count}(max {stat.max:.1f}s)" if stat.count > 1 else "")
            for name, stat in ranked
        )
        slow = ",".join(f"{name}:{stat.slow}" for name, stat in ranked if stat.slow)
        label = ", ".join(part for part in (self.event_type, self.tier, outcome) if part)
        line = (
            f"⏱ Indexing timings for record {self.record_id} ({label}): "
            f"total={end - self.started:.1f}s {head}"
        )
        if stages:
            line += f" | {stages}"
        if slow:
            line += f" | slow steps: {slow}"
        return line


def current_timeline() -> RecordTimeline | None:
    return _current.get()


def begin_record(
    record_id: str | None,
    event_type: str | None = None,
    tier: str | None = None,
    *,
    started: float | None = None,
) -> tuple[RecordTimeline, Token]:
    """Open a timeline and make it current for this task and the tasks it spawns."""
    timeline = RecordTimeline(record_id, event_type, tier, started=started)
    return timeline, _current.set(timeline)


def end_record(
    timeline: RecordTimeline,
    token: Token | None,
    *,
    outcome: str,
    logger: logging.Logger | None = None,
) -> None:
    """Close the timeline and log its summary line, if the record was admitted."""
    if token is not None:
        # ValueError: reset from a different context than the one that set
        # it; the value is dropped with that context anyway.
        with contextlib.suppress(ValueError):
            _current.reset(token)
    if not timing_enabled() or not timeline.has_mark(ADMITTED):
        return
    with contextlib.suppress(Exception):
        (logger or _logger).info("%s", timeline.summary(outcome))


def record_stage(
    name: str,
    seconds: float,
    *,
    detail: str | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Add a measured step to the current record and warn if it was slow."""
    threshold = slow_step_seconds()
    slow = seconds >= threshold
    timeline = _current.get()
    slow_count = timeline.add(name, seconds, slow=slow) if timeline is not None else 1
    if not slow or not timing_enabled() or slow_count > _MAX_SLOW_WARNINGS_PER_STAGE:
        return
    record_id = timeline.record_id if timeline is not None else None
    with contextlib.suppress(Exception):
        (logger or _logger).warning(
            "⏱ Slow indexing step %s took %.1fs (threshold %.0fs)%s%s",
            name,
            seconds,
            threshold,
            f" for record {record_id}" if record_id else "",
            f": {detail}" if detail else "",
        )


@contextmanager
def stage(
    name: str,
    *,
    detail: str | None = None,
    logger: logging.Logger | None = None,
) -> Iterator[None]:
    """Time the enclosed step as ``name`` on the current record.

    Timed whether the step succeeds or raises, so a step that fails after
    a long wait still shows where that wait was.
    """
    started = time.perf_counter()
    try:
        yield
    finally:
        record_stage(name, time.perf_counter() - started, detail=detail, logger=logger)
