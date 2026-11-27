"""Per-record indexing timings, and the record's one ``index.end`` line
(contract: docs/logging.md, "Indexing").

The consumer wrapper opens a ``RecordTimeline`` per delivery and marks the slot
and parse boundaries; code deeper in the pipeline wraps its steps in
``stage(...)`` and :func:`note`s what it learned (a skip reason, sizes,
counts). Both find the timeline through a ContextVar, so tasks the pipeline
spawns (the concurrent embedding batches) add to the same one. Inner layers
raise what they cannot handle without logging it; the consumer notes its
decision (``disposition``, ``attempt``, ``reason``) once the broker call
returns, and :func:`end_record` writes one line with all of it: what happened,
why, the timings and where the time went.

A single step that runs past ``INDEXING_SLOW_STEP_SECONDS`` logs an
``index.slow_step`` WARNING the moment it ends (also outside a record);
``INDEXING_TIMING_LOGS=false`` silences those warnings only.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from app import platform_events
from app.core import org_context
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.messaging import broker_events
from app.services.messaging.error_classifier import is_record_deleted, is_source_refusal
from app.utils.error_class import (
    OUR_BUGS,
    classify_error,
    has_chain,
    innermost_type,
    level_for,
    log_unit_end,
    quotes_content,
)
from app.utils.logger import create_logger
from app.utils.logging_fields import log_event

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "INDEX_END",
    "RecordTimeline",
    "begin_record",
    "current_timeline",
    "describe_record",
    "end_record",
    "note",
    "record_stage",
    "slow_step_seconds",
    "stage",
    "timing_enabled",
]

_logger = create_logger("indexing_service")

_current: ContextVar["RecordTimeline | None"] = ContextVar(
    "indexing_record_timeline", default=None
)

INDEX_END = "index.end"

_DEFAULT_SLOW_STEP_SECONDS = 30.0
# A record with hundreds of slow embedding batches would otherwise log
# hundreds of warnings; the end line still counts every one.
_MAX_SLOW_WARNINGS_PER_STAGE = 3
# Stages under this much busy time are left off the end line.
_MIN_STAGE_SECONDS = 0.05

# The consumers' internal outcome -> the default (disposition, reason); noted facts override both.
_DEFAULTS: dict[str, tuple[str, str | None]] = {
    "ok": ("ack", None),
    "failed": ("retry", "exception"),
    "stopped": ("retry", "shutdown"),
    "requeued": ("retry", "requeued"),
    "contended": ("retry", "contended"),
}
# The broker call failed after the record was indexed: the redelivery is deduped.
_SETTLE_FAILED = frozenset({"ack_failed", "commit_failed", "claim_lost"})
# Pages fail concurrently (the page fan-out): the first page noted is the one whose exception propagated.
_FIRST_WINS = ("exc", "page")
_NOT_A_FAULT = frozenset({"shutdown", "cancelled"})

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
    __slots__ = ("count", "total", "max", "slow", "failed")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.max = 0.0
        self.slow = 0
        self.failed = 0


STAGE_COMPLETED = "indexing.stage.completed"
# The stages a listener hears of, in the order a record passes them; a step that fits none is ``other``.
STAGES = ("queued", "fetch", "parse", "extract", "embed", "store", "other")
# The pipeline's own step names, by their first part: which of STAGES each belongs to.
_STAGE_OF_STEP: dict[str, str] = {
    "download": "fetch",
    "extract": "extract",
    "chunk": "extract",
    "images": "extract",
    "entities.extract": "extract",
    "entities.resolve": "extract",
    "embed": "embed",
    "vector": "store",
    "blob": "store",
    "artifacts": "store",
    "entities": "store",
    "graph": "store",
    "swap": "store",
    "reconcile": "store",
}
UNKNOWN_CONNECTOR = "unknown"
_MAX_CONNECTORS = 200
_CONNECTOR_SHAPE = re.compile(r"[A-Za-z][A-Za-z0-9_ .-]{0,63}")
_connectors: set[str] = set()


def stage_of(step: str) -> str:
    """Which of :data:`STAGES` a pipeline step (``vector.upsert``, ``entities.extract``) belongs to."""
    return _STAGE_OF_STEP.get(step) or _STAGE_OF_STEP.get(step.split(".", 1)[0], "other")


def connector_type(value: object) -> str:
    """A message's ``connectorName`` as a value from a bounded set: a short name set by code, else ``unknown``."""
    if not isinstance(value, str) or not value:
        return UNKNOWN_CONNECTOR
    if value in _connectors:
        return value
    if len(_connectors) >= _MAX_CONNECTORS or not _CONNECTOR_SHAPE.fullmatch(value):
        return UNKNOWN_CONNECTOR
    _connectors.add(value)
    return value


class RecordTimeline:
    """Timings for one delivery of one record.

    Boundaries (``mark``) are wall-clock points on the record's own path;
    stages (``add``) are steps that may run concurrently, so a stage's total
    can exceed the record's wall time — it is busy time, not elapsed time.
    """

    __slots__ = (
        "record_id", "event_type", "tier", "started", "facts", "ended", "_marks", "_stages",
        "connector", "broker", "topic", "lag_ms",
    )

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
        # For the platform events only (``indexing.stage.completed``, ``message.consumed``); never on the log line.
        self.connector: object = None
        self.broker: str | None = None
        self.topic = ""
        self.lag_ms: int | None = None
        self.facts: dict[str, object] = {}
        self.ended = False
        self._marks: dict[str, float] = {}
        self._stages: dict[str, _StageStat] = {}

    def note(self, **facts: object) -> bool:
        """Facts for the end line. ``None`` values are skipped; a later fact wins, except ``exc`` and ``page``,
        where the first one (the record's own failure, and where it happened) wins. False once the record has ended."""
        if self.ended:
            return False
        for first_wins in _FIRST_WINS:
            if first_wins in self.facts:
                facts.pop(first_wins, None)
        self.facts.update((key, value) for key, value in facts.items() if value is not None)
        return True

    def mark(self, name: str, at: float | None = None) -> None:
        """Record a boundary; only its first crossing counts."""
        if name not in self._marks:
            self._marks[name] = time.perf_counter() if at is None else at

    def has_mark(self, name: str) -> bool:
        return name in self._marks

    def add(self, name: str, seconds: float, *, slow: bool = False, failed: bool = False) -> int:
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
        if failed:
            stat.failed += 1
        return stat.slow

    def stage_facts(self, now: float | None = None) -> list[tuple[str, float, int, bool]]:
        """``(stage, seconds, steps, ok)`` for each of :data:`STAGES` this record spent time in, in that order.

        ``queued`` is the wait for an indexing slot and for a parse slot; ``parse`` the parse itself (ok once it
        returned); the others add up the busy time of the pipeline steps that belong to them (ok when none raised)."""
        phases = self.phases(now)
        found: dict[str, list] = {}
        queued = phases.get("wait_slot", 0.0) + (phases.get("parse_wait", 0.0) if "parse" in phases else 0.0)
        found["queued"] = [queued, 1, ADMITTED in self._marks]
        if "parse" in phases:
            found["parse"] = [phases["parse"], 1, PARSED in self._marks]
        for name, stat in self._stages.items():
            entry = found.setdefault(stage_of(name), [0.0, 0, True])
            entry[0] += stat.total
            entry[1] += stat.count
            entry[2] = entry[2] and not stat.failed
        return [(name, *found[name]) for name in STAGES if name in found]

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

    def timing_fields(self, now: float | None = None) -> dict[str, object]:
        """``*_ms`` per phase, ``stages`` ranked by busy time, ``slow_steps``: the end line's timing fields."""
        phases = self.phases(now)
        ranked = sorted(self._stages.items(), key=lambda item: item[1].total, reverse=True)
        stages = " ".join(
            f"{name}={stat.total:.1f}s" + (f"/{stat.count}(max {stat.max:.1f}s)" if stat.count > 1 else "")
            for name, stat in ranked
            if stat.total >= _MIN_STAGE_SECONDS
        )
        slow = ",".join(f"{name}:{stat.slow}" for name, stat in ranked if stat.slow)
        return {
            **{f"{name}_ms": round(seconds * 1000) for name, seconds in phases.items()},
            "stages": stages or None,
            "slow_steps": slow or None,
        }


def current_timeline() -> RecordTimeline | None:
    return _current.get()


def current_record_id() -> str | None:
    """The id of the record being indexed in this task (or one it spawned), None outside one."""
    timeline = _current.get()
    if timeline is None:
        return None
    # A noted id wins, as on index.end (PostgreSQL opens the timeline before it has parsed the record).
    record_id = timeline.facts.get("record_id") or timeline.record_id
    return str(record_id) if record_id else None


def note(**facts: object) -> bool:
    """Facts for the current record's ``index.end`` line (see :meth:`RecordTimeline.note`).
    False, and nothing noted, outside a record or after it ended."""
    timeline = _current.get()
    return timeline is not None and timeline.note(**facts)


def begin_record(
    record_id: str | None,
    event_type: str | None = None,
    tier: str | None = None,
    *,
    started: float | None = None,
    connector: object = None,
    broker: str | None = None,
    topic: str = "",
    published_at_ms: object = None,
) -> tuple[RecordTimeline, Token]:
    """Open a timeline and make it current for this task and the tasks it spawns.

    ``connector``: the message's connector type, for the stage events. ``broker`` (kafka, redis), ``topic`` and the
    broker's own timestamp of the message: given them, the end of the record is also announced as ``message.consumed``."""
    timeline = RecordTimeline(record_id, event_type, tier, started=started)
    timeline.connector = connector
    timeline.broker = broker
    timeline.topic = topic
    timeline.lag_ms = broker_events.lag_ms(published_at_ms)
    return timeline, _current.set(timeline)


def describe_record(*, connector: object = None) -> None:
    """What a consumer learned about the current record after it opened the timeline. No-op outside a record."""
    timeline = _current.get()
    if timeline is not None and connector is not None:
        timeline.connector = connector


def end_record(
    timeline: RecordTimeline,
    token: Token | None,
    *,
    outcome: str,
    logger: logging.Logger | None = None,
) -> None:
    """Close the timeline and write its one ``index.end`` line, admitted or not; then drop the facts
    (a task that copied the context must not keep the exception, and its frames, alive). Never raises.

    ``outcome`` is the consumer's own: ok, failed, stopped, requeued or contended."""
    if token is not None:
        # ValueError: reset from a different context than the one that set
        # it; the value is dropped with that context anyway.
        with contextlib.suppress(ValueError):
            _current.reset(token)
    try:
        _announce_end(timeline, _write_end(timeline, outcome, logger or _logger))
    except Exception:
        pass
    finally:
        timeline.ended = True
        timeline.facts.clear()


def _announce_end(timeline: RecordTimeline, settled: tuple[str, object, object]) -> None:
    """The record's stages as ``indexing.stage.completed`` events and, for a timeline opened with its broker, the
    delivery as ``message.consumed``. Nothing is built when nobody listens."""
    outcome, disposition, record_id = settled
    now = time.perf_counter()
    if timeline.broker is not None:
        broker_events.consumed(
            timeline.broker,
            timeline.topic,
            timeline.event_type,
            outcome=outcome if disposition == "ack" else str(disposition),
            duration_seconds=now - timeline.started,
            lag=timeline.lag_ms,
        )
    if not platform_events.has_listeners(STAGE_COMPLETED):
        return
    org = org_context.peek()
    org_id = org.org_id if org is not None else None
    connector = connector_type(timeline.connector)
    record_id = str(record_id) if record_id else None
    for name, seconds, steps, ok in timeline.stage_facts(now):
        platform_events.emit(
            "indexing.stage.completed",
            stage=name,
            connector=connector,
            org_id=org_id,
            record_id=record_id,
            duration_seconds=seconds,
            count=steps,
            outcome="success" if ok else "failure",
        )


def _write_end(timeline: RecordTimeline, internal: str, logger: logging.Logger) -> tuple[str, object, object]:
    """Writes the line; returns the record's ``(outcome, disposition, record id)`` as the line says them."""
    facts = dict(timeline.facts)
    exc = facts.pop("exc", None)
    exc = exc if isinstance(exc, BaseException) else None
    noted_outcome = facts.pop("outcome", None)
    end_level = facts.pop("end_level", None)
    disposition, reason = _DEFAULTS.get(internal, ("retry", internal))
    reason = facts.pop("reason", reason)
    # Cancelled before a broker call confirmed a settlement: redelivered, whatever the handler reached.
    disposition = facts.pop("disposition", "retry" if reason == "cancelled" else disposition)
    cls = _error_class(exc) if exc is not None else None
    outcome = _outcome(internal, disposition, reason, exc, noted_outcome)
    # Deleted while it was being indexed: nobody acts on it.
    level = logging.INFO if is_record_deleted(exc) else _level(outcome, disposition, reason, cls)
    if isinstance(end_level, int) and outcome == "skipped" and reason == "requeued":
        level = end_level  # a hand-back's own level: a wake that repeats an earlier pause is DEBUG
    if reason == "terminal" and exc is not None and is_source_refusal(exc):
        reason = "source_refused"  # the source's 4xx with a stored reason, at its class's level
    fields = {
        "outcome": outcome,
        "disposition": disposition,
        "reason": reason,
        "record_id": timeline.record_id,
        "event_type": timeline.event_type,
        "tier": timeline.tier,
        "duration_ms": round((time.perf_counter() - timeline.started) * 1000),
        **timeline.timing_fields(),
        "cause_type": innermost_type(exc) if exc is not None and has_chain(exc) else None,
        "code": code if isinstance(code := getattr(exc, "code", None), str) else None,
        **facts,  # noted facts win, record_id and event_type included (a stage job's record)
    }
    settled = (outcome, disposition, fields["record_id"])
    log_unit_end(
        logger, INDEX_END, exc, level=level, error_class=cls,
        include_error_text=exc is None or not quotes_content(exc), **fields,
    )
    return settled


def _error_class(exc: BaseException) -> str:
    # A bug in our own parse code raises straight into the processor's wrapper; a library that wraps its own
    # KeyError (docling: a zip renamed .docx) is the user's content. Logging only: retries do not read this.
    if isinstance(exc, DocumentProcessingError) and isinstance(exc.__cause__, OUR_BUGS):
        return "internal"
    return classify_error(exc)


def _outcome(
    internal: str, disposition: object, reason: object, exc: BaseException | None, noted: object
) -> str:
    if internal == "ok" and (reason in _SETTLE_FAILED or reason == "cancelled"):
        return "skipped" if noted == "skipped" else "success"
    if disposition == "ack":
        return "skipped" if noted == "skipped" else "success"
    if disposition == "dead_letter" or exc is not None or internal in ("failed", "stopped"):
        return "failure"
    return "skipped"  # handed back without an attempt counted: requeued, contended


def _level(outcome: str, disposition: object, reason: object, cls: str | None) -> int:
    if outcome != "failure" and reason in _SETTLE_FAILED:
        return logging.WARNING
    if outcome != "failure":
        return logging.WARNING if reason in ("requeued", "claim_lost") else logging.INFO
    if reason in _NOT_A_FAULT or cls == "cancelled":
        return logging.INFO
    if disposition == "dead_letter" and reason != "terminal":
        return logging.ERROR  # gave up on a record a retry would not fix: a human reindexes it
    return max(logging.WARNING, level_for(cls)) if cls else logging.WARNING


def record_stage(
    name: str,
    seconds: float,
    *,
    detail: str | None = None,
    logger: logging.Logger | None = None,
    failed: bool = False,
) -> None:
    """Add a measured step to the current record and warn if it was slow. ``failed``: the step raised."""
    threshold = slow_step_seconds()
    slow = seconds >= threshold
    timeline = _current.get()
    slow_count = timeline.add(name, seconds, slow=slow, failed=failed) if timeline is not None else 1
    if not slow or not timing_enabled() or slow_count > _MAX_SLOW_WARNINGS_PER_STAGE:
        return
    with contextlib.suppress(Exception):
        log_event(
            logger or _logger,
            "index.slow_step",
            level=logging.WARNING,
            step=name,
            duration_ms=round(seconds * 1000),
            threshold_ms=round(threshold * 1000),
            detail=detail,
            # A noted id wins, as on index.end (PostgreSQL opens the timeline before it has parsed the record).
            record_id=(timeline.facts.get("record_id") or timeline.record_id) if timeline is not None else None,
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
    failed = True
    try:
        yield
        failed = False
    finally:
        record_stage(name, time.perf_counter() - started, detail=detail, logger=logger, failed=failed)
