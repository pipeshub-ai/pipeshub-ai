"""Stopping paid work for a record that was deleted while its run was in flight.

A record deleted mid-run (an upload removed, a connector scope narrowed) keeps its parse running until its deadline, and
every paid call and store after the delete is wasted: the delete marker already refuses the stores. This module is the
check each paid call and store makes, and what a stop means for the run.

**The marker is advisory, the graph decides.** The delete marker (``pc:deleted:{org:record}``, value: the delete time in ms,
or ``1`` from an older release) can be rewritten after a record was re-created under the same id (a redelivered or
duplicated delete of the earlier incarnation), so a marker never stops a run by itself in the consumer. A run keeps, per
marker key, the marker string it saw when it started; a string that differs is a reason to read the record. Only a
successful read decides:

* the record is gone, or in the trash: orphan;
* the record is live but its ``createdAtTimestamp`` is not the one the run started with: a newer incarnation (a delete and
  a quick re-push while this run was in flight): this run serves a deleted incarnation, orphan;
* otherwise live, and only then do the watermarks advance to the strings just read.

A failed read never advances a watermark; the next check retries it (at most once per ``RETRY_S``), and the call goes on
meanwhile (stopping a live record's parse is the worse error; the fence still refuses the stores). The parsing service has
no graph: on a marker string that differs from the one the request carried it answers 410 and the consumer confirms
(``confirm_gone``); it never drains on a 410 without a successful read.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from app import platform_events
from app.exceptions.indexing_exceptions import RecordDeletedError
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.paid_cache.locks import deleted_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.services.paid_cache.locks import LockBackend

log = logging.getLogger(__name__)

#: A graph read is made at most this often when no marker changed (a marker Redis lost cannot say so).
RECHECK_S = 30.0
#: After a failed read the next one waits this long.
RETRY_S = 30.0
#: The graph read has a bound of its own, far below the store's client timeout (minutes), so a slow graph never holds a live
#: record's paid calls. A keyed point read takes milliseconds, a paid call (an OCR page, an LLM or embedding call) seconds to
#: minutes: 3 s is two orders over the read and, with the single flight and ``RETRY_S``, costs a live run at most one 3 s wait
#: per 30 s however many calls run in parallel, and only while the graph is sick.
PROBE_TIMEOUT_S = 3.0
#: Parses repeated after a 410 for a record that proved live; the next one disables the parsing service's stop.
MAX_REPARSES = 2
#: How long a delivery handed back for an unconfirmed 410 waits before it is redelivered.
HAND_BACK_DELAY_S = 15.0


class RecordGone(RecordDeletedError):
    """The record this run serves was deleted: nothing more is paid or stored for it. A ``RecordDeletedError``, so the
    consumer treats it as the expected outcome it is: terminal, no status written, nothing to retry."""

    def __init__(self, record_id: str, why: str = "") -> None:
        super().__init__(record_id)
        if why:
            self.message = f"{self.message}: {why}"
            self.args = (self.message,)


class RecordHandBack(RequeueWithoutAttempt):
    """A 410 whose confirming graph read failed: the delivery is handed back (no attempt counted). The redelivery reads
    the record fresh, and a record that is gone drains there."""

    counts_toward_backstop = True
    pauses_consumer = False
    give_up_label = "a parse the service refused whose record could not be read"


@dataclass(frozen=True)
class ProbeResult:
    """What one graph read of the record says."""

    found: bool
    live: bool = True
    created: Any = None
    incarnation: Any = None


@dataclass
class DeliveryGuard:
    """The per-delivery state of the existence checks. The consumer's has a ``probe``; the parsing service's has none."""

    org_id: str
    record_id: str
    wm_record: str | None = None
    wm_org: str | None = None
    created: Any = None
    #: The record's ``incarnationId`` and its ``createdAtTimestamp`` as the graph read at run start gave them, whatever the
    #: origin. ``incarnation`` is None for a record stored before the field existed, or when it is not written yet.
    incarnation: Any = None
    created_at_start: Any = None
    probe: Callable[[], Awaitable[ProbeResult]] | None = None
    #: The parsing service's guard: the request said not to stop (a record re-parsed too often after a 410, or an older caller).
    stop_disabled: bool = False
    #: The consumer's guard after the re-parse cap: the next parse request carries ``stop_disabled``. Only the parsing
    #: service's stop is disabled; the consumer's own checks and the fence remain (design 3.3).
    service_stop_disabled: bool = False
    probe_timeout_s: float = PROBE_TIMEOUT_S
    #: The run started under a marker: its first check reads the record whatever the markers say.
    confirm_first: bool = False
    gone: bool = False
    hand_back: bool = False
    closed: bool = False
    reparses: int = 0
    graph_reads: int = 0
    clock: Callable[[], float] = time.monotonic
    #: The lock backend that holds the markers (None: no cache, only the graph decides).
    backend_of: Callable[[], Awaitable[LockBackend | None]] | None = None
    _last_read: float = field(default=float("-inf"), repr=False)
    _retry_at: float = field(default=float("-inf"), repr=False)
    _generation: int = field(default=0, repr=False)
    _lock: asyncio.Lock | None = field(default=None, repr=False)

    def mark_read(self) -> None:
        """The run's own start read of the record counts as the first graph read."""
        self._last_read = self.clock()

    def matches(self, org_id: str, record_id: str) -> bool:
        return self.org_id == org_id and self.record_id == record_id

    def _read_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _markers(self, backend: LockBackend | None) -> tuple[str | None, str | None] | None:
        """The two markers, as two single-key reads (never a multi-key command: the keys hash to different slots).
        None when Redis cannot be read (or there is no backend): the call goes on and the watermarks stay."""
        if backend is None:
            return None
        try:
            return (
                await backend.marker_value(deleted_name(self.org_id, self.record_id)),
                await backend.marker_value(deleted_name(self.org_id)),
            )
        except Exception as exc:
            platform_events.emit("paid_result.existence.checked", source="marker", result="error")
            log.debug("Paid-result delete marker unreadable (%s); the existence check goes on", type(exc).__name__)
            return None

    def _mark_gone(self, source: str, result: str = "orphan") -> bool:
        self.gone = True
        platform_events.emit("paid_result.existence.checked", source=source, result=result)
        return True

    async def check(self, backend: LockBackend | None) -> bool:
        """True when the record this run serves is gone. Cheap on the live path: two Redis ``GET``s, and a graph read
        only when a marker changed, when the run started under one, or every ``RECHECK_S``."""
        if self.gone:
            platform_events.emit("paid_result.existence.checked", source="sticky", result="orphan")
            return True
        if self.stop_disabled or self.closed:
            return False
        markers = await self._markers(backend)
        if self.gone:                                    # another check confirmed a delete while the markers were read
            platform_events.emit("paid_result.existence.checked", source="sticky", result="orphan")
            return True
        seen = markers if markers is not None else (self.wm_record, self.wm_org)
        changed = seen != (self.wm_record, self.wm_org)
        if self.probe is None:
            # The parsing service: no graph. A changed marker is a reason to refuse; the consumer confirms it.
            if changed:
                return self._mark_gone("marker")
            platform_events.emit("paid_result.existence.checked", source="marker", result="live")
            return False
        now = self.clock()
        due = (
            (changed and now >= self._retry_at)
            or (self.confirm_first and now >= self._retry_at)
            or (now - self._last_read >= RECHECK_S and now >= self._retry_at)
        )
        if not due:
            platform_events.emit("paid_result.existence.checked", source="marker", result="live")
            return False
        generation = self._generation
        async with self._read_lock():
            # Another caller read while this one waited, or its read failed (the retry window is open): share that outcome.
            if self._generation != generation or self.clock() < self._retry_at:
                return self.gone
            return await self._read(seen, changed=changed)

    def _newer_incarnation(self, result: ProbeResult) -> bool:
        """Whether the live record is a different incarnation than the one this run started with.

        Both ids held: different means a delete and a re-create, whatever the origin and even when the source reuses its ids.
        This run started with an id and the live record has none: a writer that does not know the field re-created it
        (a process not yet on the release that writes it), so the dates decide: the run's start date against the live one.
        No id at the start (a record stored before the field): the older rule, the stamp of an uploaded record only (a
        connector that builds ``created_at`` from the clock rewrote it on every re-sync before the stamp was kept; the live record is
        never called new for lacking an id the run did not see).
        """
        if self.incarnation is not None:
            if result.incarnation is not None:
                return result.incarnation != self.incarnation
            return (self.created_at_start is not None and result.created is not None
                    and result.created != self.created_at_start)
        return self.created is not None and result.created is not None and result.created != self.created

    async def _read(self, seen: tuple[str | None, str | None], *, changed: bool) -> bool:
        assert self.probe is not None
        try:
            result = await asyncio.wait_for(self.probe(), self.probe_timeout_s)
        except Exception as exc:
            self._retry_at = self.clock() + RETRY_S
            platform_events.emit("paid_result.existence.checked", source="graph", result="error")
            log.debug("Existence read failed (%s); the watermark is kept and the read is retried later", type(exc).__name__)
            return False
        self._generation += 1
        self.graph_reads += 1
        self._last_read = self.clock()
        if not result.found or not result.live:
            return self._mark_gone("graph")
        if self._newer_incarnation(result):
            return self._mark_gone("graph", "incarnation")
        self.wm_record, self.wm_org = seen
        self.confirm_first = False
        platform_events.emit("paid_result.existence.checked", source="graph", result="live" if not changed else "live_after_change")
        return False

    async def confirm_gone(self, backend: LockBackend | None) -> str:
        """The parsing service refused with 410: read the record. ``"gone"`` (drain), ``"live"`` (parse again with the
        watermarks just read; counted, and the third time with the stop disabled) or ``"unknown"`` (the read failed: the
        delivery is handed back, never drained and never parsed again at once)."""
        if self.probe is None:
            return "gone" if self.gone else "unknown"
        markers = await self._markers(backend)
        seen = markers if markers is not None else (self.wm_record, self.wm_org)
        async with self._read_lock():
            try:
                result = await asyncio.wait_for(self.probe(), self.probe_timeout_s)
            except Exception as exc:
                platform_events.emit("paid_result.existence.checked", source="graph", result="error")
                log.debug("Existence read after a 410 failed (%s); the delivery is handed back", type(exc).__name__)
                self.hand_back = True
                return "unknown"
            self._generation += 1
            self.graph_reads += 1
            self._last_read = self.clock()
            if not result.found or not result.live:
                self._mark_gone("graph")
                return "gone"
            if self._newer_incarnation(result):
                self._mark_gone("graph", "incarnation")
                return "gone"
            self.wm_record, self.wm_org = seen
            self.confirm_first = False
            self.reparses += 1
            platform_events.emit("paid_result.parse.repeated", kind="stop_disabled" if self.reparses > MAX_REPARSES else "reparse")
            if self.reparses > MAX_REPARSES:
                self.service_stop_disabled = True
            platform_events.emit("paid_result.existence.checked", source="graph", result="live_after_410")
            return "live"

    def finish(self) -> None:
        if not self.closed:
            self.closed = True
            platform_events.emit(
                "paid_result.delivery.checked",
                kind="delivery" if self.probe is not None else "service",
                graph_reads=float(self.graph_reads),
            )

    def request_fields(self) -> dict[str, str]:
        """The form fields the parsing client sends: the strings this run started under, and whether to stop."""
        fields = {"orphan_guard": "1", "record_marker": self.wm_record or "", "org_marker": self.wm_org or ""}
        if self.stop_disabled or self.service_stop_disabled:
            fields["stop_disabled"] = "true"
        return fields


_current: contextvars.ContextVar[DeliveryGuard | None] = contextvars.ContextVar("paid_orphan_guard", default=None)


def bind_guard(guard: DeliveryGuard) -> None:
    """Makes ``guard`` the one of this delivery and the tasks it starts. Not reset: ``finish`` closes it, and a closed guard
    is never returned (a delivery's generator may be closed from another context)."""
    _current.set(guard)


def current_guard(org_id: str | None = None, record_id: str | None = None) -> DeliveryGuard | None:
    guard = _current.get()
    if guard is None or guard.closed:
        return None
    if org_id is not None and record_id is not None and not guard.matches(org_id, record_id):
        return None
    return guard


class OrphanFields(NamedTuple):
    """The orphan-stop form fields of a parse request (``DeliveryGuard.request_fields``)."""

    present: bool = False
    record_marker: str = ""
    org_marker: str = ""
    stop_disabled: bool = False


def guard_from_request(
    org_id: str, record_id: str, *, present: bool, record_marker: str, org_marker: str, stop_disabled: bool,
) -> DeliveryGuard | None:
    """The parsing service's guard from a parse request: None for an older caller that sent no markers (fail open)."""
    if not present:
        return None
    return DeliveryGuard(
        org_id=org_id, record_id=record_id, wm_record=record_marker or None, wm_org=org_marker or None,
        stop_disabled=stop_disabled,
    )


def is_record_gone(exc: BaseException | None, guard: DeliveryGuard | None) -> bool:
    """True when a stop is why ``exc`` happened: the guard says so, or ``RecordGone`` is in its cause chain (a parser that
    wrapped it, ``from e``, keeps the chain)."""
    if guard is not None and guard.gone:
        return True
    seen = 0
    while exc is not None and seen < 8:
        if isinstance(exc, RecordGone):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


async def ensure_record_alive(org_id: str, record_id: str, site: str) -> None:
    """The check of a stage boundary (before the embeddings, before the vector and blob writes). Raises ``RecordGone``.
    No guard (a delivery the check does not cover, or the check is off) means no check."""
    guard = current_guard(org_id, record_id)
    if guard is None:
        return
    backend = None
    if guard.backend_of is not None:
        try:
            backend = await guard.backend_of()
        except Exception as exc:
            log.debug("Paid-result lock backend unavailable (%s); the existence check goes on", type(exc).__name__)
    if await guard.check(backend):
        platform_events.emit("paid_result.work.stopped", site=site)
        raise RecordGone(record_id)


def orphan_check_enabled() -> bool:
    """``PAID_ORPHAN_CHECK=0`` is the kill switch; on by default."""
    import os

    return (os.getenv("PAID_ORPHAN_CHECK") or "1").strip().lower() not in {"0", "false", "no", "off"}
