"""Progress of one record's embedding inside one attempt, and what a cut of the attempt means.

A record's attempt has a fixed budget. A large table can need several budgets of embedding, and an
attempt that was making steady progress and then ran out of time did nothing wrong: the next attempt
resumes from the points that exist (see ``embed_resume``). Two things follow from that.

* A record that is *stuck* should be found sooner than the whole budget. The stall timer ends an
  attempt when no batch has become durable for ``stall_seconds()``.
* An attempt that was cut after making progress must not count toward dead-lettering, as long as the
  chain as a whole is still bounded.

The consumer owns the budget (an ``asyncio.Timeout``) and the decision; the vector store reports
progress. ``RecordProgress`` is the object between them, carried in a context variable like
``EmbedAttempt``. It never raises into the embedding loop: the only way it ends an attempt is by
moving the deadline of the consumer's own timeout.

What decides that a cut is not counted (``settle_cut``), all of:

* the consumer's own timeout expired (a ``TimeoutError`` from anything else does not set that);
* the lease is intact and the consumer is running (a shutdown or a lost lease is never a requeue);
* at least one batch became durable during this attempt, and the attempt was not a restart (it did
  not discard points of its own chain);
* the chain's embedding time, taken from the node, is still under the ceiling;
* the record was handed back with one conditional write that names this delivery.

Every other case is counted as before.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import threading
from collections import deque
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

logger = logging.getLogger(__name__)

#: Percentile of recent batch intervals the stall timer is a multiple of, and the multiple.
STALL_PERCENTILE = 0.9
STALL_MULTIPLE = 4.0
#: Intervals remembered, and how many are needed before they are believed over the floor.
HISTORY_WINDOW = 50
HISTORY_MIN_SAMPLES = 10
#: Most seconds per attempt the stall timer is paused for while the embedding service is failing.
PAUSE_LIMIT_SECONDS = 1800.0
#: Most seconds per record the budget is paused for while a batch waits its turn at the embedding
#: server's gate. Nine in-flight records times the slowest measured batch is under this; past it the wait
#: counts against the budget again, so a gate that never opens still ends the attempt.
GATE_PAUSE_LIMIT_SECONDS = 1800.0
#: Display progress is written at most this often (seconds, or batches), never read to decide anything.
REPORT_EVERY_SECONDS = 15.0
REPORT_EVERY_BATCHES = 5
LOG_EVERY_BATCHES = 10

#: Reasons a cut stays counted; the value ``None`` means the requeue is uncounted.
COUNTED_NOT_BUDGET = "the attempt did not end on its own deadline"
COUNTED_LEASE = "the record lease is not held"
COUNTED_STOPPING = "the consumer is stopping"
COUNTED_NO_PROGRESS = "no batch became durable in this attempt"
COUNTED_RESTART = "the attempt discarded the points of its own chain"
COUNTED_UNKNOWN_CLOCK = "the chain's embedding time is not known"
COUNTED_CEILING = "the chain used its embedding time ceiling"
COUNTED_NOT_WRITTEN = "the hand-back could not be written"
COUNTED_NOT_OWNER = "the record no longer belongs to this delivery"


class StallHistory:
    """Recent intervals between durable batches in this process, and the stall limit they imply.

    A high percentile of a window rather than its maximum: one slow interval in fifty cannot move it,
    while a sustained slowdown (a contended host, a slow provider) does. Too few samples fall back to
    the floor, which is above every interval measured on a contended four-core host.
    """

    def __init__(self, window: int = HISTORY_WINDOW) -> None:
        self._intervals: deque[float] = deque(maxlen=window)
        self._lock = threading.Lock()

    def observe(self, seconds: float) -> None:
        if math.isfinite(seconds) and seconds >= 0:
            with self._lock:
                self._intervals.append(seconds)

    def clear(self) -> None:
        with self._lock:
            self._intervals.clear()

    def stall_seconds(self, floor: float, ceiling: float) -> float:
        with self._lock:
            samples = sorted(self._intervals)
        if len(samples) < HISTORY_MIN_SAMPLES:
            return floor
        rank = max(0, math.ceil(STALL_PERCENTILE * len(samples)) - 1)
        return min(ceiling, max(floor, STALL_MULTIPLE * samples[rank]))


#: Shared by every record in the process: batch speed is a property of the host and the provider.
PROCESS_HISTORY = StallHistory()


def _loop_time() -> float:
    return asyncio.get_running_loop().time()


class RecordProgress:
    """Progress of the embedding of one record in one attempt."""

    def __init__(
        self,
        *,
        floor_seconds: float,
        ceiling_seconds: float,
        record_ceiling_seconds: float,
        history: StallHistory | None = None,
        clock: Callable[[], float] = _loop_time,
        pause_limit_seconds: float = PAUSE_LIMIT_SECONDS,
        gate_pause_limit_seconds: float = GATE_PAUSE_LIMIT_SECONDS,
    ) -> None:
        self.floor_seconds = floor_seconds
        self.ceiling_seconds = ceiling_seconds
        self.record_ceiling_seconds = record_ceiling_seconds
        self.history = PROCESS_HISTORY if history is None else history
        self._clock = clock
        self._pause_limit = pause_limit_seconds
        self._gate_pause_limit = gate_pause_limit_seconds

        # Set by the consumer.
        self.budget: asyncio.Timeout | None = None
        # Set by the event layer once it knows the record and what the node says about its chain.
        self.record_id: str | None = None
        self.delivery_id: str | None = None
        self.prior_seconds: float | None = 0.0
        self.prior_uncounted = 0
        self._commit: Callable[[float, int], Awaitable[bool]] | None = None
        self._display: Callable[[int], Awaitable[None]] | None = None

        # Set by the vector store.
        self.planned = False
        self.finished = False
        self.restarted = False
        self.total = 0
        self.done = 0
        self.new_units = 0
        self._planned_at = 0.0
        self._cap: float | None = None
        self._last_beat = 0.0
        self._pausers = 0
        self._calls = 0
        self._pause_started = 0.0
        self._paused_total = 0.0
        self._gate_paused = 0.0
        self._gate_deadline: float | None = None
        self._gate_waits = 0
        self._gate_started = 0.0
        self._gate_allowance = 0.0
        self._reported_at = 0.0
        self._reported_units = 0

    # ---- consumer ---------------------------------------------------------------------------------------------------------------
    def attach(self, budget: asyncio.Timeout) -> None:
        """The record's processing timeout, once the consumer has entered it."""
        self.budget = budget

    def stall_seconds(self) -> float:
        return self.history.stall_seconds(self.floor_seconds, self.ceiling_seconds)

    def attempt_seconds(self) -> float:
        """Wall seconds since the embedding was planned, paused time included."""
        return max(0.0, self._clock() - self._planned_at) if self.planned else 0.0

    async def settle_cut(self, *, lease_intact: bool, running: bool) -> str | None:
        """``None`` when the cut was handed back without counting an attempt, else why it stays counted.

        Called by the consumer after its own timeout expired and the handler has finished unwinding.
        """
        reason = await self._decide(lease_intact=lease_intact, running=running)
        if reason is None:
            logger.warning(
                "Record %s: embed cut (%s) uncounted at %d of %d points after %.0fs of this attempt",
                self.record_id, "stall" if self._stalled() else "cap", self.done, self.total, self.attempt_seconds(),
            )
        else:
            logger.info("Record %s: embed cut counted: %s", self.record_id, reason)
        return reason

    def _stalled(self) -> bool:
        return self._cap is None or self._clock() < self._cap - 1e-6

    async def _decide(self, *, lease_intact: bool, running: bool) -> str | None:
        if self.budget is None or not self.budget.expired():
            return COUNTED_NOT_BUDGET
        if not lease_intact:
            return COUNTED_LEASE
        if not running:
            return COUNTED_STOPPING
        if not self.planned or self.new_units < 1:
            return COUNTED_NO_PROGRESS
        if self.restarted:
            return COUNTED_RESTART
        if self.prior_seconds is None or self._commit is None:
            return COUNTED_UNKNOWN_CLOCK
        seconds = self.prior_seconds + self.attempt_seconds()
        if seconds >= self.record_ceiling_seconds:
            return COUNTED_CEILING
        try:
            applied = await self._commit(seconds, self.prior_uncounted + 1)
        except Exception as exc:
            logger.warning("Record %s: could not hand back the cut attempt: %s", self.record_id, exc)
            return COUNTED_NOT_WRITTEN
        return None if applied else COUNTED_NOT_OWNER

    # ---- event layer ------------------------------------------------------------------------------------------------------------
    def bind(
        self,
        *,
        record_id: str,
        delivery_id: str,
        prior_seconds: float | None,
        prior_uncounted: int,
        commit: Callable[[float, int], Awaitable[bool]],
        display: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """Say which record this is, what the node says its chain has used, and how to write about it."""
        self.record_id = record_id
        self.delivery_id = delivery_id
        self.prior_seconds = prior_seconds
        self.prior_uncounted = prior_uncounted
        self._commit = commit
        self._display = display

    # ---- vector store -----------------------------------------------------------------------------------------------------------
    def plan(self, total: int, done: int, *, restarted: bool = False) -> None:
        """The embed phase begins: ``done`` of ``total`` points exist. Starts the stall timer."""
        now = self._clock()
        self.planned = True
        self.restarted = restarted
        self.total = total
        self.done = done
        self._planned_at = now
        self._last_beat = now
        self._reported_at = now
        self._reported_units = 0
        when = self.budget.when() if self.budget is not None else None
        # The live budget, which already carries every admission wait.
        self._cap = when
        self._arm()
        logger.info(
            "Record %s: embed plan: %d of %d points exist, %d to embed (stall limit %.0fs)",
            self.record_id, done, total, max(0, total - done), self.stall_seconds(),
        )

    def beat(self, points: int = 0) -> None:
        """A batch became durable."""
        if not self.planned:
            return
        now = self._clock()
        self.history.observe(now - self._last_beat)
        self._last_beat = now
        self.new_units += 1
        self.done = min(self.total, self.done + max(0, points)) if self.total else self.done + max(0, points)
        self._arm()
        if self.new_units % LOG_EVERY_BATCHES == 0:
            logger.info(
                "Record %s: embedded %d of %d points (%d batches this attempt)",
                self.record_id, self.done, self.total, self.new_units,
            )

    def finish(self) -> None:
        """Every point is embedded. What follows (verification, the record's writes) has no batches to
        time, so only the attempt's cap bounds it, as before the stall timer existed."""
        if not self.planned:
            return
        self.finished = True
        self._arm()

    def unfinish(self) -> None:
        """Another embed section follows a ``finish()``: the stall timer counts again, from now.

        The cap is what ``finish()`` left it (every gate pause included), so nothing is re-read from the budget.
        """
        if not self.planned or not self.finished:
            return
        self.finished = False
        self._last_beat = self._clock()
        self._arm()

    def pause(self) -> None:
        """The embedding service is failing and the caller is waiting to retry: do not call that a stall."""
        if not self.planned:
            return
        if self._pausers == 0:
            self._pause_started = self._clock()
        self._pausers += 1
        self._arm()

    def resume(self) -> None:
        """A retry begins. That is progress of a kind, so the stall timer starts over."""
        if not self.planned or self._pausers == 0:
            return
        self._pausers -= 1
        if self._pausers == 0:
            now = self._clock()
            self._paused_total += max(0.0, now - self._pause_started)
            self._last_beat = now
            self._arm()

    def call_start(self) -> None:
        """An embedding call is in flight: its own timeout bounds it, so the stall timer is off."""
        if not self.planned:
            return
        self._calls += 1
        self._arm()

    def call_end(self) -> None:
        """The call returned or failed; the stall timer counts from here, not from the last batch."""
        if not self.planned or self._calls == 0:
            return
        self._calls -= 1
        if self._calls == 0:
            self._last_beat = self._clock()
            self._arm()

    @contextlib.asynccontextmanager
    async def gate_wait(self) -> AsyncIterator[None]:
        """A batch waits its turn at the embedding server: that is queueing, not embedding, so the budget stops.

        The cap and the plan's start move with the budget. ``_arm`` rebuilds the deadline from the cap on
        every beat, so a pause that moved only the timeout would be undone by the next batch and cut a
        record that had embedded every point. Pausing is bounded per record; once the allowance is used
        the wait counts against the budget as any other time does. With no budget to move, nothing is paused.
        """
        if not self.planned or self._cap is None or self.budget is None:
            yield
            return
        if self._gate_waits == 0:
            allowance = self._gate_pause_limit - self._gate_paused
            if allowance <= 0:
                yield
                return
            self._gate_started = self._clock()
            # Until the allowance runs out the budget is held where it is, then it counts down again.
            self._gate_deadline = self._cap + allowance
            self._gate_allowance = allowance
        # Waits that overlap are one pause: it starts with the first and ends with the last.
        self._gate_waits += 1
        self.call_start()
        try:
            yield
        finally:
            self._gate_waits -= 1
            if self._gate_waits == 0:
                waited = min(max(0.0, self._clock() - self._gate_started), self._gate_allowance)
                self._gate_paused += waited
                self._cap += waited
                self._planned_at += waited
                self._gate_deadline = None
            self.call_end()
            self._arm()

    async def report(self, *, force: bool = False) -> None:
        """Write the display count, at most every few batches or seconds. A failure is logged and ignored."""
        if not self.planned or self._display is None:
            return
        now = self._clock()
        if not (
            force
            or self.new_units - self._reported_units >= REPORT_EVERY_BATCHES
            or now - self._reported_at >= REPORT_EVERY_SECONDS
        ):
            return
        self._reported_at = now
        self._reported_units = self.new_units
        try:
            await self._display(self.done)
        except Exception as exc:
            logger.warning("Record %s: could not write the embedding progress: %s", self.record_id, exc)

    # ---- the deadline -----------------------------------------------------------------------------------------------------------
    def _deadline(self) -> float | None:
        if self._gate_deadline is not None:
            return self._gate_deadline
        if self._cap is None or self.finished or self._calls:
            return self._cap
        stall = self.stall_seconds()
        if self._pausers:
            allowance = max(0.0, self._pause_limit - self._paused_total)
            return min(self._cap, self._pause_started + allowance + stall)
        return min(self._cap, self._last_beat + stall)

    def _arm(self) -> None:
        deadline = self._deadline()
        if deadline is None or self.budget is None:
            return
        try:
            self.budget.reschedule(deadline)
        except RuntimeError:
            # The timeout already fired or was left: there is nothing to move.
            return


def carried_counters(prior: dict[str, Any], delivery_id: str | None) -> tuple[float | None, int | None]:
    """The chain's ``(embedSeconds, embedUncounted)`` as the node had them, for this delivery.

    A new event starts a chain (``0, 0``). The same event keeps its clock even when the attempt has to
    discard its points. A value that is missing or not a sane number is ``None``: unknown is counted.
    """
    from app.modules.indexing.embed_resume import (
        EMBED_DELIVERY_ID,
        EMBED_SECONDS,
        EMBED_UNCOUNTED,
    )

    if not prior or not delivery_id or prior.get(EMBED_DELIVERY_ID) != delivery_id:
        return 0.0, 0
    seconds, uncounted = prior.get(EMBED_SECONDS), prior.get(EMBED_UNCOUNTED)
    ok_seconds = isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and math.isfinite(seconds) and seconds >= 0
    ok_uncounted = isinstance(uncounted, (int, float)) and not isinstance(uncounted, bool) and math.isfinite(uncounted) and uncounted >= 0
    return (float(seconds) if ok_seconds else None, int(uncounted) if ok_uncounted else None)


_progress: ContextVar[RecordProgress | None] = ContextVar("record_progress", default=None)


def current_progress() -> RecordProgress | None:
    return _progress.get()


def begin_progress(progress: RecordProgress) -> None:
    _progress.set(progress)


def end_progress() -> None:
    # Set back rather than reset by token: an async generator closed from another context cannot reset.
    _progress.set(None)


def new_progress() -> RecordProgress:
    """A ``RecordProgress`` with the deployment's limits."""
    from app.services.messaging.config import messaging_env

    return RecordProgress(
        floor_seconds=messaging_env.embed_stall_floor_seconds,
        ceiling_seconds=messaging_env.embed_stall_ceiling_seconds,
        record_ceiling_seconds=messaging_env.embed_record_ceiling_seconds,
    )

