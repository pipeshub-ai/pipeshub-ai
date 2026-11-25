"""Consumer for ``MESSAGE_BROKER=postgres``: simple (entity, sync, config events) and indexing (record events) in one class.

A claim counts a delivery attempt and hides the row for ``claim_min_idle_ms``; a heartbeat keeps the rows this consumer is still working
on hidden, so a long record is not handed to a second consumer, while a crashed consumer's rows reappear after the idle time. A
transient failure makes the row visible again after a backoff; a delivery that reaches ``MAX_DELIVERY_ATTEMPTS`` (or fails
terminally) is marked dead, after telling the disposition sink so the record it names is left in a terminal status.

Left out on purpose, because they exist for a fleet on a shared broker: lanes, the fair (DRR) scheduler, the resource governor's
parse gates and the capacity leases. The indexing variant runs up to ``MAX_CONCURRENT_INDEXING`` handlers at once; the consumer of
the priority topic runs up to its lane's cap, with no per-org share and no backpressure pause (neither exists for this broker).
"""

import asyncio
import contextlib
import json
import os
import time
import uuid
from collections import Counter
from logging import Logger
from typing import TYPE_CHECKING, Any, override

from app.core.envelope import (
    INBOX_ERRORS,
    DuplicateEnvelope,
    EnvelopeRejected,
    Inbox,
    configured_admission_valid,
    ctx_of,
    decode_message,
    max_age_seconds,
    new_admission,
    open_configured,
    open_stream_message,
    record_processed,
    refresh_envelope,
    within_lifetime,
)
from app.core.org_context import bind_optional, spawn_detached
from app.core.org_context import pop as pop_org
from app.core.org_context import push as push_org
from app.modules.indexing.embed_resume import stamp_delivery_id
from app.services.messaging import record_budget
from app.services.messaging.config import (
    IndexingEvent,
    MessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    compute_retry_backoff_seconds,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import (
    RequeueWithoutAttempt,
    backstop_give_up_reason,
    work_key,
)
from app.services.messaging.disposition import describe_message, notify_abandoned
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    is_expected_outcome,
)
from app.services.messaging.inbox import PgInbox
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.membership import register_group
from app.services.messaging.message_log import bound_message
from app.services.messaging.postgres.queue import Delivery, PgQueue
from app.services.messaging.storage_protocol import tagged
from app.services.pg_runtime import QUEUE_CHANNEL, PgListener, listen_dsn
from app.utils import error_class
from app.utils import indexing_timing as timing
from app.utils.logging_fields import log_fields, log_once
from app.utils.request_context import bind_context, context_from_envelope, reset_context

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator

    from app.core.org_context import OrgContext
    from app.services.distributed.interface import IDistributedLeaseManager
    from app.services.messaging.consumer_concurrency import PriorityLane, StageAdmission
    from app.services.messaging.disposition import AbandonedMessageSink
    from app.services.messaging.precision_check import PrecisionHandBack
    from app.services.messaging.scheduling.interface import IndexingFairness
    from app.services.messaging.worker_loop import WorkerLoop

_SWEEP_EVERY_S = 60.0
_INDEX_POOL = "pg-queue-indexing"
_SAFETY_NET_S = 300.0  # longest an idle consumer with a LISTEN connection sleeps
_HANDBACK_CAP_S = 60.0   # longest the per-message back-off of hand-backs grows to
_HANDBACK_TABLE_MAX = 10_000   # hand-back counts kept in memory


class ClaimLost(Exception):
    """This consumer no longer holds the delivery (another claimed it, or its claim lapsed): nothing is run, acknowledged, retried or reported for it."""


class AdmissionWriteError(Exception):
    """The record that a delivery's message was opened could not be written; the delivery fails transiently and is retried."""


def _within_lifetime(delay: float) -> float:
    """A refunded wait, capped at two thirds of the envelope lifetime.

    The message is signed again at the nack, so the wait is its age when it is next claimed; the last third of the
    lifetime is the margin for clock skew. The default lifetime (900 s) leaves the 600 s precision wait as it is.
    """
    return min(delay, max_age_seconds() * 2 / 3)


def _handback_base_s() -> float:
    return float(os.getenv("PG_QUEUE_HANDBACK_BASE_S", "1"))


def _handback_limit() -> int:
    """Hand-backs of one message (in this process) after which one that counts toward the backstop is charged as a failed attempt."""
    return int(os.getenv("PG_QUEUE_HANDBACK_LIMIT", "200"))


class RecordHeld(RequeueWithoutAttempt):
    """The record's lease is held by another delivery (a stage rewriting the record, a duplicate): not a reason to pause a consumer."""

    pauses_consumer = False


def _retry_base_s() -> float:
    return float(os.getenv("PG_QUEUE_RETRY_BASE_S", "5"))


@contextlib.contextmanager
def _queue_write(reason: str) -> "Iterator[None]":
    """A nack or dead update that raised settled nothing: index.end says so, and the error propagates (the claim expires and
    the row is handed out again)."""
    try:
        yield
    except Exception:
        timing.note(disposition="retry", reason=reason)
        raise


class _ExpectedOutcome(str):
    """The error text of an outcome nobody acts on (deleted while indexed, refused by
    the source), so settling it does not log a second, warning-level line."""

class PgMessagingConsumer(IMessagingConsumer):
    def __init__(
        self,
        logger: Logger,
        config: RedisStreamsConfig,
        *,
        indexing: bool = False,
        disposition_sink: "AbandonedMessageSink | None" = None,
        queue: PgQueue | None = None,
        concurrency_manager: "IDistributedLeaseManager | None" = None,
        stage_admission: "StageAdmission | None" = None,
        priority_lane: "PriorityLane | None" = None,
        worker: "WorkerLoop | None" = None,
        precision_filter: "PrecisionHandBack | None" = None,
        fairness: "IndexingFairness | None" = None,
        inbox: Inbox | None = None,
    ) -> None:
        self.inbox = inbox  # simple consumers: skips events this group already finished (see app.services.messaging.inbox)
        self.precision_filter = precision_filter  # hands back events that would write vectors of the wrong precision
        self._fairness = fairness  # claim steps: which topics to claim from, in what order and amounts
        self._worker = worker  # the loop the indexing handler runs on; claims, heartbeats and settling stay on this one
        self.stage_admission = stage_admission  # a pipeline-stage consumer: its own permits and lease pool, no record lease
        self.priority_lane = priority_lane  # the consumer of the priority topic: its own handler cap and lease pool
        self.concurrency_manager = concurrency_manager  # cluster-wide cap on records indexing at once, across worker processes
        self._leases: set[tuple[str, str]] = set()
        # A delivery's record lease -> the task driving it, and the record leases the manager reported lost: the driver is cancelled
        # when its lease is not renewed, so a delivery whose lease lapsed stops writing (as on the other indexing consumers).
        self._lease_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._lost_leases: set[tuple[str, str]] = set()
        self.logger = logger
        self.config = config
        self.indexing = indexing
        # The name its claims are taken under, which the indexing membership check reads (``membership.PgClaimCheck``).
        self.consumer_name = tagged(config.client_id) if indexing else config.client_id
        if indexing:
            register_group(config.group_id, list(config.topics))
        self.disposition_sink = disposition_sink
        self.queue = queue or PgQueue()
        self.running = False
        self.message_handler: MessageHandler | None = None
        self.capacity_gate: "Callable[[], bool] | None" = (
            None  # same hook as the Redis consumers
        )
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None
        self._beat_task: asyncio.Task | None = None
        self._held: dict[tuple[str, int], Delivery] = {}
        self._wake: asyncio.Event | None = None
        self._beat_wake = asyncio.Event()
        self._ordered = False
        self._listener: PgListener | None = None
        self._last_sweep = 0.0
        self._paused_until = 0.0         # a stage that asked for hand-backs is not claimed from until then (monotonic seconds)
        self._handbacks: dict[tuple[str, int], int] = {}      # hand-backs per message, for the back-off and the backstop
        self._handback_log: dict[str, int] = {}               # reason -> count since the last summary line
        # Deliveries this process opened, by (topic, message id, attempts): "aged" when the message passed the age check itself,
        # "stored" when only a stored admission let it past. Dropped when the delivery settles.
        self._opened: dict[tuple[str, int, int], str] = {}

    @property
    def _visibility_s(self) -> float:
        return max(self.config.claim_min_idle_ms / 1000.0, 5.0)

    async def initialize(self) -> None:
        for topic in self.config.topics:
            created = await self.queue.ensure_group(
                topic,
                self.config.group_id,
                from_start=not self.config.broadcast,
                ephemeral=self.config.broadcast,
            )
            if created:
                self.logger.info(
                    "Created consumer group %s for topic %s",
                    self.config.group_id,
                    topic,
                )

    async def cleanup(self) -> None:
        if self._listener is not None:
            await self._listener.stop()
            self._listener = None
        if self.config.broadcast:
            for topic in self.config.topics:
                try:
                    await self.queue.drop_group(topic, self.config.group_id)
                except Exception as e:
                    self.logger.warning(
                        "Could not drop consumer group %s on %s: %s",
                        self.config.group_id,
                        topic,
                        e,
                    )

    @override
    async def start(self, message_handler: MessageHandler) -> None:
        self.running = True
        self.message_handler = message_handler
        await self.initialize()
        self._wake = asyncio.Event()
        ordered = {
            t.strip()
            for t in os.getenv("PG_QUEUE_ORDERED_TOPICS", "record-events").split(",")
            if t.strip()
        }
        self._ordered = any(
            t in ordered or t.split(".")[0] in ordered for t in self.config.topics
        )
        dsn = listen_dsn()
        if dsn is not None:
            topics = set(self.config.topics)
            self._listener = PgListener(
                dsn,
                QUEUE_CHANNEL,
                lambda t: self._wake.set() if t in topics else None,
                on_reconnect=lambda: self._wake.set(),
                on_lost=lambda: self._wake.set(),  # wake to start polling: a notification can no longer be trusted to arrive
            )
            self._listener.start()
        self._loop_task = spawn_detached(self._consume_loop())
        self._beat_task = spawn_detached(self._heartbeat_loop())
        self.logger.info(
            "Started PostgreSQL queue consumer %s on %s",
            self.config.group_id,
            self.config.topics,
        )

    @override
    async def stop(self, message_handler: MessageHandler | None = None) -> None:
        self.running = False
        for t in (self._loop_task, self._beat_task, *self._tasks):
            if t is not None:
                t.cancel()
        await asyncio.gather(
            *[
                t
                for t in (self._loop_task, self._beat_task, *self._tasks)
                if t is not None
            ],
            return_exceptions=True,
        )
        self._tasks.clear()
        await self.cleanup()

    @override
    def is_running(self) -> bool:
        return self.running

    def _concurrency(self) -> int:
        if self.stage_admission is not None:
            return self.stage_admission.limit
        if self.priority_lane is not None:
            return self.priority_lane.limit
        return max(1, messaging_env.max_concurrent_indexing) if self.indexing else 1

    # -- loops ---------------------------------------------------------------------------------------------------
    async def _consume_loop(self) -> None:
        poll_s = (
            self.config.block_ms / 1000.0
            if self._listener is not None
            else min(self.config.block_ms / 1000.0, 1.0)
        )
        assert self._wake is not None
        while self.running:
            try:
                if self.capacity_gate is not None and not self.capacity_gate():
                    await asyncio.sleep(min(poll_s, 0.5))
                    continue
                paused_for = self._paused_until - time.monotonic()
                if paused_for > 0:
                    # a paused stage claims nothing (no claim and hand-back churn); a NOTIFY or the end of the pause ends the wait
                    self._wake.clear()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=min(paused_for, _SAFETY_NET_S))
                    continue
                free = self._concurrency() - len(
                    self._tasks
                )  # handlers that may start now
                if free <= 0:
                    await asyncio.sleep(0.05)
                    continue
                self._wake.clear()
                batch = await self._claim(
                    self.config.group_id,
                    self.config.topics,
                    self.consumer_name,
                    min(free, max(self.config.batch_size, 1))
                    if self.indexing
                    else max(self.config.batch_size, 1),
                    self._visibility_s,
                    ordered=self._ordered,
                )
                if not batch:
                    await self._idle(poll_s)
                    continue
                for d in batch:
                    self._held[(d.topic, d.message_id)] = d
                self._beat_wake.set()
                if self.indexing:
                    for d in batch:
                        t = spawn_detached(self._run_one(d))
                        self._tasks.add(t)
                        t.add_done_callback(self._tasks.discard)
                else:
                    for d in batch:
                        if not self.running:
                            break
                        await self._run_one(d)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(
                    "Error in queue consume loop: %s: %s", type(e).__name__, e
                )
                await asyncio.sleep(1)

    async def _claim(self, group: str, topics: list[str], consumer: str, limit: int, visibility_s: float, *, ordered: bool) -> list[Delivery]:
        """``queue.claim``; with claim steps, one single-topic claim per step, in the order and amounts they give, up to ``limit`` rows."""
        steps = self._fairness.claim_steps if self._fairness else None
        if steps is None:
            return await self.queue.claim(group, topics, consumer, limit, visibility_s, ordered=ordered)
        out: list[Delivery] = []
        for topic, rows in steps(limit, Counter(t for t, _ in self._held)):
            if len(out) >= limit:
                break
            if rows > 0:
                out += await self.queue.claim(group, [topic], consumer, min(rows, limit - len(out)), visibility_s, ordered=ordered)
        return sorted(out, key=lambda d: d.message_id)

    async def _idle(self, poll_s: float) -> None:
        """Wait for a NOTIFY. With a LISTEN connection nothing polls: the only timer is the moment a retry backoff ends or a claim
        expires (neither sends a notification), capped at ``_SAFETY_NET_S``. Without one, or while the connection is down or silent, poll every ``poll_s``."""
        assert self._wake is not None
        wait = poll_s
        if self._listener is not None and self._listener.healthy():
            wait = _SAFETY_NET_S
            try:
                due = await self.queue.next_due(
                    self.config.group_id, self.config.topics
                )
                if due is not None:
                    # On an ordered group a delivery that is already due but was not handed out is waiting behind a live claim of its
                    # key; that ends with an ack, which sends no notification. Poll at the normal rate, not every 0.1 s.
                    floor = poll_s if (due <= 0 and self._ordered) else 0.05
                    wait = min(max(due, floor) + 0.05, _SAFETY_NET_S)
            except Exception as e:
                self.logger.warning("Could not read the next due time: %s", e)
                wait = poll_s
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=wait)

    async def _heartbeat_loop(self) -> None:
        """Keep the claims being worked on hidden, keep an ephemeral group alive, and sweep (whether or not the consumer is idle)."""
        while self.running:
            # an idle consumer with nothing held wakes only for the sweep; claiming something wakes it at once
            busy = bool(self._held) or self.config.broadcast
            self._beat_wake.clear()
            woken = True
            try:
                await asyncio.wait_for(
                    self._beat_wake.wait(),
                    timeout=self._visibility_s / 3 if busy else _SWEEP_EVERY_S,
                )
            except asyncio.TimeoutError:
                woken = False
            if woken and self._held:
                await asyncio.sleep(self._visibility_s / 3)
            held = list(self._held.values())
            try:
                if held:
                    await self.queue.extend(
                        self.config.group_id, held, self._visibility_s
                    )
                await self._renew_leases()
                if self.config.broadcast:
                    await self.queue.touch(self.config.group_id, self.config.topics)
                if time.monotonic() - self._last_sweep > _SWEEP_EVERY_S:
                    self._last_sweep = time.monotonic()
                    await self.queue.sweep()
                    if self._handback_log:
                        self.logger.info(
                            "Handed back without an attempt in the last minute on %s: %s",
                            self.config.topics, dict(self._handback_log),
                        )
                        self._handback_log.clear()
            except Exception as e:
                self.logger.warning(
                    "Queue heartbeat failed (%d claims held): %s", len(held), e
                )

    # -- one delivery --------------------------------------------------------------------------------------------
    async def _run_one(self, d: Delivery) -> None:
        try:
            await self._settle(d)
        except asyncio.CancelledError:
            raise  # left claimed: it is handed out again when the claim expires
        except Exception as e:
            self.logger.error(
                "Unexpected error settling message %s: %s",
                d.message_id,
                e,
                exc_info=True,
            )
        finally:
            self._held.pop((d.topic, d.message_id), None)
            # A hand-back or a retry sets a due time and sends no notification: an idle loop must re-read the next due time, or it
            # sleeps until the claim it was waiting on would have expired.
            if self._wake is not None:
                self._wake.set()

    async def _settle(self, d: Delivery) -> None:
        try:
            await self._settle_timed(d)
        finally:
            self._opened.pop((d.topic, d.message_id, d.attempts), None)

    async def _settle_timed(self, d: Delivery) -> None:
        if not self.indexing:
            await self._decide(d, {})
            return
        # One record delivery, one index.end (as the Redis and Kafka indexing consumers): the decision below notes on it,
        # and the line is written in the message's root, org and ids, as P8's bound_message gives the other two.
        seen: dict[str, Any] = {}
        timeline, token = timing.begin_record(None)  # ADMITTED is marked once the indexing slot is taken
        timing.note(attempt=d.attempts)
        outcome = "failed"
        try:
            outcome = await self._decide(d, seen)
        except ClaimLost:
            # Another consumer holds the row: nothing was run, acknowledged, retried or reported here. The end line says so.
            timing.note(reason="claim_lost")
            outcome = "requeued"
        except asyncio.CancelledError:
            timing.note(reason="cancelled")  # shutdown: left claimed and handed out again, as Redis and Kafka say it
            raise
        finally:
            timeline.mark(timing.RELEASED)
            # The row's topic and id even when the body could not be parsed, so an operator can find it.
            with log_fields(topic=d.topic, message_id=str(d.message_id)), bound_message(seen.get("parsed"), seen.get("root")):
                timing.end_record(timeline, token, outcome=outcome, logger=self.logger)

    async def _decide(self, d: Delivery, seen: "dict[str, Any] | None" = None) -> str:
        """Process ``d`` and ack, nack or dead-letter it; returns the indexing timeline's outcome."""
        max_attempts = messaging_env.max_delivery_attempts
        if (
            d.attempts > max_attempts
        ):  # handed out past the limit (a consumer that keeps dying mid-handler)
            # The sink needs the message to find its record: without it the record stays IN_PROGRESS and recovery hands it back for ever.
            timing.note(reason="unfinished_attempts")
            parsed = await self._parse_for_sink(d)
            if parsed is not None:
                self._remember(parsed, seen)
            await self._abandon(d, parsed, "delivery attempts exhausted")
            return "failed"
        if await self._hand_back_for_precision(d):
            timing.note(disposition="retry", reason="precision_wait")
            return "requeued"
        final = d.attempts >= max_attempts
        try:
            success, terminal, parsed, err = await self._process(d, final=final, seen=seen)
        except RequeueWithoutAttempt as e:
            # Queue time, not a failure (a stage whose dependency is paused, a record whose lease stayed held): visible again after the
            # delay with the attempt this claim counted given back, so waiting never dead-letters it.
            key = (d.topic, d.message_id)
            n = self._count_handback(key)
            delay = max(
                e.delay_s if e.delay_s is not None else compute_retry_backoff_seconds(1),
                min(_handback_base_s() * 2 ** min(n - 1, 10), _HANDBACK_CAP_S),     # grows with every hand-back of this message
            )
            delay = _within_lifetime(delay)   # the pause too: a message due at the cap is claimed by then
            reason = type(e).__name__
            timing.note(code=reason, pool=getattr(e, "pool", None), end_level=e.log_level)
            self._handback_log[reason] = self._handback_log.get(reason, 0) + 1
            self.logger.debug("%s handed back without counting an attempt (%d so far, retry in %.1fs): %s", d.message_id, n, delay, e)
            if self.stage_admission is not None and e.pauses_consumer:
                self._paused_until = max(self._paused_until, time.monotonic() + delay)     # stop claiming until the stage can run
            if e.counts_toward_backstop and n > _handback_limit():
                if e.give_up_label:
                    # A hand-back that names itself (a same-bytes update whose skip cannot finish) is given up on here,
                    # with the reason the other brokers use, so what handles an abandoned message can tell it apart.
                    self._handbacks.pop(key, None)
                    timing.note(reason="backstop")
                    await self._abandon(d, await self._parse_for_sink(d), backstop_give_up_reason(e, n, _handback_limit()))
                    return "failed"
                # handed back for ever (a lease that is never freed): charged as a failure, so the delivery limit ends it
                with _queue_write("nack_failed"):
                    nacked = await self._nack(d, delay, f"handed back {n} times: {e}"[:500])
                if nacked:
                    timing.note(disposition="retry", reason="backstop")
                else:
                    timing.note(disposition="retry", reason="claim_lost")
                return "failed"
            with _queue_write("nack_failed"):
                nacked = await self._nack(d, delay, str(e), refund=True, reissue=True)
            if nacked:
                timing.note(disposition="retry")
            else:
                timing.note(disposition="retry", reason="claim_lost")
            return "requeued"
        self._handbacks.pop((d.topic, d.message_id), None)
        if success:
            try:
                acked = await self._ack(d, (seen or {}).get("ctx"))
            except Exception as e:
                # Indexed: the expired claim redelivers it and the redelivery is deduped. In a record's run its index.end says it.
                if not timing.note(disposition="retry", reason="ack_failed", exc=e):
                    raise
                return "ok"
            # A newer claim of the row (this one expired) is not this delivery's to settle.
            if acked:
                timing.note(disposition="ack")
            else:
                timing.note(disposition="retry", reason="claim_lost")
            return "ok"
        if terminal:
            if parsed is not None:  # otherwise _process noted the specific reason (envelope_rejected, unparseable, …)
                timing.note(reason="terminal")
            # A stale rejection is judged before the claim is written, so the claim is checked by the dead-letter write itself, first:
            # a consumer that no longer holds the row does not tell the sink about a message the new claimant is about to run.
            await self._abandon(d, parsed, err or "terminal error", claim_first=str(err).startswith("envelope rejected: stale"))
        elif final:
            timing.note(reason="attempts_exhausted")
            await self._abandon(d, parsed, err or f"failed {d.attempts} times")
        else:
            delay = (
                compute_retry_backoff_seconds(d.attempts)
                if self.indexing
                else min(_retry_base_s() * d.attempts, 30.0)
            )
            with _queue_write("nack_failed"):
                nacked = await self._nack(d, delay, err)
            in_run = timing.note(disposition="retry", reason=None if nacked else "claim_lost")
            if not in_run:  # in a record's run, its index.end says it
                self.logger.warning(
                    "Transient failure on %s (%d/%d), retrying in %.0fs",
                    d.message_id,
                    d.attempts,
                    max_attempts,
                    delay,
                )
        return "failed"

    async def _ack(self, d: Delivery, ctx: "OrgContext | None") -> bool:
        """Ack a delivery whose handler succeeded, recording its event in the inbox in the same transaction. A record that cannot be
        written does not hold the ack back: the message is acknowledged on its own, and a redelivery of it would run again."""
        group = self.config.group_id
        if not isinstance(self.inbox, PgInbox) or ctx is None or not ctx.event_id:
            await record_processed(self.inbox, group, ctx)
            return await self.queue.ack(group, d)
        try:
            return await self.inbox.record_and_ack(group, ctx, self.queue, group, d)
        except Exception as e:
            INBOX_ERRORS.inc("record")
            if log_once(f"inbox-ack:{group}", 30):    # the counter carries the exact count
                self.logger.warning("Could not record %s in the consumer inbox; acknowledging it alone: %s", d.message_id, type(e).__name__)
            return await self.queue.ack(group, d)

    @staticmethod
    def _remember(parsed: StreamMessage, seen: "dict[str, Any] | None") -> None:
        """The record's end line is written in this message's context and names its record."""
        if seen is not None:
            seen["parsed"] = parsed
        timing.note(record_id=parsed.payload.get("recordId"), event_type=parsed.eventType)

    def _count_handback(self, key: tuple[str, int]) -> int:
        """One more hand-back of this message, and how many it has had. The table is bounded: the oldest entries go first."""
        n = self._handbacks[key] = self._handbacks.get(key, 0) + 1
        if len(self._handbacks) > _HANDBACK_TABLE_MAX:
            self._handbacks.pop(next(iter(self._handbacks)))
        return n

    async def _hand_back_for_precision(self, d: Delivery) -> bool:
        """Before anything runs: an event that would write vectors of the wrong precision goes back.

        ``nack`` with a refund returns the attempt the claim counted, so nothing is spent and nothing is
        downloaded. The first hand-back of a message waits about a minute, later ones ten.
        """
        if not self.indexing or self.precision_filter is None:
            return False
        try:
            message = await self._parse_for_sink(d, record=True)
            delays = self.precision_filter.hand_back([message]) if message is not None else {}
        except ClaimLost:
            raise
        except Exception:
            self.logger.warning("Could not check %s against the embedding precision; it takes the normal path", d.message_id, exc_info=True)
            return False
        if 0 not in delays:
            return False
        key = (d.topic, d.message_id)
        n = self._count_handback(key)
        delay = delays[0] if n == 1 else delays[0] * 10
        delay = _within_lifetime(delay)
        self._handback_log["precision"] = self._handback_log.get("precision", 0) + 1
        await self._nack(d, delay, "waiting for the embedding precision", refund=True, reissue=True)
        return True

    async def _nack(self, d: Delivery, delay: float, error: str, *, refund: bool = False, reissue: bool = False) -> bool:
        """Send a delivery back. A delivery this process opened renews its admission in the same statement.

        ``reissue``: store the message signed again, for a delivery handed back without an attempt, so an older consumer that
        claims it later still finds a fresh signature. Only a message that passed the age check itself is signed again; one that
        was admitted by a stored admission alone keeps the value it has (an admission must never turn into a fresh signature).
        """
        mode = self._opened.get((d.topic, d.message_id, d.attempts))
        kwargs: dict[str, Any] = {}
        raw: Any = None
        if mode is not None or reissue:
            try:
                raw = decode_message(d.value)
            except Exception:
                raw = None
        message: Any = raw
        if reissue and isinstance(raw, dict) and mode != "stored":
            try:
                fresh = await refresh_envelope(d.topic, raw, admitted=mode == "aged")
            except Exception as e:
                self.logger.warning("Could not renew the envelope of %s before its hand-back: %s", d.message_id, type(e).__name__)
                fresh = None
            if fresh is not None:
                kwargs["value"] = json.dumps(fresh)
                message = fresh
        if mode is not None and isinstance(message, dict):
            stamp = await new_admission(d.topic, self.config.group_id, d.message_id, message)
            if stamp is not None:
                kwargs["admission"] = stamp
        return await self.queue.nack(self.config.group_id, d, delay, error, refund=refund, **kwargs)

    async def _open[T](self, d: Delivery, raw: dict, opener: "Callable[[bool], Coroutine[Any, Any, T]]", *, record: bool = True) -> T:
        """Open a delivery's message, judging its age only if no admission covers it, and record the admission before anything runs.

        A delivery this process already opened is admitted for every later open (the precision check, then the handler). Otherwise
        a stored admission (a MAC made with the envelope key over this message, group and row) admits it. The admission is written
        at once, so a handler that dies, a lapsed lease or a failed nack redelivers a message already known to have verified.
        A write that fails raises ``AdmissionWriteError``: the handler must not run without it. A claim is checked when a message is first
        opened (the precision check, or the handler if there is none); a claim lost after that open, before the handler starts or while it
        runs, is not noticed (a second check would cost a write on every delivery), so the delivery stays at-least-once from there.
        """
        key = (d.topic, d.message_id, d.attempts)
        mode = self._opened.get(key)
        admitted = mode is not None or await configured_admission_valid(
            d.topic, self.config.group_id, d.message_id, raw, d.admission_ms, d.admission_mac
        )
        result = await opener(admitted)
        if mode is not None or not record:
            return result
        self._opened[key] = "aged" if within_lifetime(raw) else "stored"
        try:
            stamp = await new_admission(d.topic, self.config.group_id, d.message_id, raw)
            held = True if stamp is None else await self.queue.admit(self.config.group_id, d, *stamp)
        except Exception as e:
            self._opened.pop(key, None)
            raise AdmissionWriteError(type(e).__name__) from e
        if not held:
            # The write is conditional on this claim: False says another consumer holds the row now, and it is theirs to run. A claim
            # lost after a handler has started is still at-least-once; this stops one that was lost before it.
            self._opened.pop(key, None)
            self._handback_log["claim_lost"] = self._handback_log.get("claim_lost", 0) + 1
            raise ClaimLost(f"{d.topic}:{d.message_id}")
        return result

    async def _parse_for_sink(self, d: Delivery, *, record: bool = False) -> StreamMessage | None:
        """The message of a delivery that will not be processed, for the disposition sink; None when it cannot be read."""
        if not self.indexing:
            return None
        try:
            raw = decode_message(d.value)
            if not isinstance(raw, dict):
                return None
            return await self._open(
                d, raw, lambda admitted: open_stream_message(d.topic, raw, StreamMessage, admitted=admitted), record=record
            )
        except ClaimLost:
            raise
        except Exception as e:
            self.logger.warning("Could not read the exhausted message %s for its record: %s", d.message_id, e)
            return None

    async def _read_stale_for_sink(self, d: Delivery, raw: dict) -> StreamMessage | None:
        """The message of a delivery rejected only for its age, so the sink can leave the record it names in a visible terminal state.

        The age is waived for this one read and for nothing else: the signature, shape, organisation and future-time checks still
        run, nothing is processed, and no admission is written.
        """
        try:
            return await open_stream_message(d.topic, raw, StreamMessage, admitted=True)
        except Exception as e:
            self.logger.warning("Could not read the stale message %s for its record: %s", d.message_id, type(e).__name__)
            return None

    async def _abandon(
        self, d: Delivery, parsed: StreamMessage | None, reason: str, *, claim_first: bool = False
    ) -> None:
        if claim_first:
            if not await self.queue.dead(self.config.group_id, d, reason):
                self._handback_log["claim_lost"] = self._handback_log.get("claim_lost", 0) + 1
                self.logger.info("Claim on %s was lost before it was dead-lettered; it belongs to another consumer", d.message_id)
                return
            await notify_abandoned(self.disposition_sink, self.logger, parsed, reason=reason, attempts=d.attempts)
            self.logger.warning("Dead-lettered %s (%s): %s", d.message_id, describe_message(parsed), reason)
            return
        await notify_abandoned(
            self.disposition_sink,
            self.logger,
            parsed,
            reason=reason,
            attempts=d.attempts,
        )
        with _queue_write("dead_letter_failed"):
            dead = await self.queue.dead(self.config.group_id, d, reason)
        # A newer claim of the row (this one expired) is not this delivery's to settle; in a record's run its index.end says it.
        if not timing.note(**({"disposition": "dead_letter"} if dead else {"disposition": "retry", "reason": "claim_lost"})):
            # An expected outcome was logged once at INFO when the handler raised it.
            log = self.logger.info if isinstance(reason, _ExpectedOutcome) else self.logger.warning
            log("Dead-lettered %s (%s): %s", d.message_id, describe_message(parsed), reason)

    async def _process(
        self, d: Delivery, *, final: bool, seen: "dict[str, Any] | None" = None
    ) -> tuple[bool, bool, StreamMessage | None, str]:
        """(success, terminal, parsed message, error text)."""
        if self.message_handler is None:
            timing.note(reason="no_handler")
            return False, True, None, "no message handler"
        try:
            raw = decode_message(d.value)
        except json.JSONDecodeError as e:
            timing.note(reason="unparseable")
            return False, True, None, f"JSON parsing failed: {e}"
        if not isinstance(raw, dict):
            timing.note(reason="unparseable")
            return False, True, None, "message is not an object"
        parsed: StreamMessage | None = None
        root = context_from_envelope(raw)
        token = bind_context(root)
        if seen is not None:
            seen["root"] = root  # the record's end line is written in the root its lines ran in
        try:
            if self.indexing:
                try:
                    parsed = await self._open(
                        d, raw, lambda admitted: open_stream_message(d.topic, raw, StreamMessage, admitted=admitted)
                    )
                except AdmissionWriteError as e:
                    return False, False, None, f"admission could not be recorded: {e}"
                except DuplicateEnvelope:
                    timing.note(outcome="skipped", reason="duplicate_envelope")
                    return True, False, None, ""
                except EnvelopeRejected as e:
                    timing.note(reason="envelope_rejected", code=e.reason, **e.facts())
                    stale = await self._read_stale_for_sink(d, raw) if e.reason == "stale" else None
                    return False, True, stale, f"envelope rejected: {e.reason}"
                if parsed is None:
                    timing.note(reason="unparseable")
                    return False, True, None, "unparseable message"
                self._remember(parsed, seen)
                parsed.is_final_failure = final
                # A hand-back is a nack of the same row, so the row names the event across its retries.
                stamp_delivery_id(parsed.payload, f"{d.topic}:{d.message_id}", parsed.org_ctx)
                await self._drive_indexing(parsed, f"message:{d.message_id}")
                return True, False, parsed, ""
            try:
                opened = await self._open(
                    d, raw, lambda admitted: open_configured(d.topic, raw, inbox=self.inbox, consumer=self.config.group_id, admitted=admitted)
                )
            except AdmissionWriteError as e:
                return False, False, None, f"admission could not be recorded: {e}"
            except DuplicateEnvelope:
                return True, False, None, ""
            except EnvelopeRejected as e:
                return False, True, None, f"envelope rejected: {e.reason}"
            parsed = StreamMessage(**opened.message)
            parsed.is_final_failure = final
            with bind_optional(opened.ctx):
                ok = await self.message_handler(parsed)
            if ok and seen is not None:
                seen["ctx"] = opened.ctx  # recorded in the inbox with the ack
            return bool(ok), False, parsed, "" if ok else "handler returned false"
        except (RequeueWithoutAttempt, ClaimLost):
            raise
        except Exception as e:
            expected = is_expected_outcome(e)
            # In a record's run (the indexing consumer) its index.end writes the failure once.
            if not timing.note(exc=e):
                if expected:
                    # Expected (deleted while indexed, refused by the source): the handler
                    # already logged it once, so no traceback at error level here.
                    self.logger.info(
                        "Message %s ended as expected: %s: %s", d.message_id, type(e).__name__, e
                    )
                else:
                    # No error text: a pydantic ValidationError quotes the payload, and the handler's own line names the failure.
                    error_class.log_failure(
                        self.logger, "message.handler_failed", e, include_error_text=False, message_id=d.message_id
                    )
            terminal = (
                MessageErrorClassifier.classify_by_exception(e)
                == MessageErrorType.TERMINAL
            )
            if terminal and parsed is not None:
                parsed.is_final_failure = True
            reason = f"{error_class.classify_error(e)}: {type(e).__name__}"  # kept in last_error: no text, as above
            return False, terminal, parsed, _ExpectedOutcome(reason) if expected else reason
        finally:
            reset_context(token)

    async def _renew_leases(self) -> None:
        """Renew every held lease; a record lease the manager says is no longer ours cancels the delivery that holds it."""
        if not self._leases or self.concurrency_manager is None:
            return
        renewed = await self.concurrency_manager.renew_many(list(self._leases), self._visibility_s)
        for lease, ok in (renewed or {}).items():
            task = self._lease_tasks.get(lease)
            if not ok and task is not None:
                self._lost_leases.add(lease)
                task.cancel()

    async def _on_worker[T](self, make: "Callable[[], Coroutine[Any, Any, T]]") -> T:
        """Run a handler on the shared worker loop, as the other indexing consumers do: parsing that blocks that loop cannot then stall
        the claim heartbeat, which stays on this one. Cancelling the wait cancels the handler."""
        if self._worker is None:
            return await make()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(make(), self._worker.start()))

    async def _drive_indexing(self, parsed: StreamMessage, fallback_key: str) -> None:
        """Run the record handler's event stream to the end; it must reach INDEXING_COMPLETE."""
        timeout = messaging_env.record_processing_timeout_for(
            parsed.payload.get("extension"), parsed.payload.get("mimeType")
        )
        org_token = push_org(ctx_of(parsed))
        # The record's lease first, tried once: a record held by a stage must not occupy a cluster slot while it waits.
        record_slot = slot = None
        try:
            record_slot = await self._take_record_lease(parsed, fallback_key)
            slot = await self._take_slot(parsed)
            if (timeline := timing.current_timeline()) is not None:
                timeline.mark(timing.ADMITTED)  # the wait for the slot is wait_slot_ms, not held_ms
            async def drain() -> bool:
                gen = self.message_handler(parsed)  # type: ignore[misc]
                complete = False
                try:
                    async for event in gen:
                        if event.event == IndexingEvent.INDEXING_COMPLETE:
                            complete = True
                finally:
                    await gen.aclose()
                return complete

            try:
                # On the claim loop, beside the lease renewal: the handler on the worker loop grows it by posting the move here.
                async with asyncio.timeout(timeout) as budget, record_budget.armed(budget, timeout):
                    complete = await self._on_worker(drain)
            except asyncio.CancelledError:
                if record_slot is not None and record_slot in self._lost_leases:
                    # Our own cancel, sent because the record lease was not renewed: another delivery may own the record now.
                    self._lost_leases.discard(record_slot)
                    task = asyncio.current_task()
                    if task is not None:
                        _ = task.uncancel()
                    raise RecordHeld(f"{record_slot[0]} was lost while the delivery ran; it is redone") from None
                raise
            if not complete:
                raise RuntimeError("handler ended without INDEXING_COMPLETE")
        finally:
            pop_org(org_token)
            for held in (record_slot, slot):
                if held is not None:
                    self._leases.discard(held)
                    self._lease_tasks.pop(held, None)
                    with contextlib.suppress(Exception):
                        await self.concurrency_manager.release(*held)  # type: ignore[union-attr]
            if (timeline := timing.current_timeline()) is not None:
                timeline.mark(timing.RELEASED)  # held_ms ends with the leases, not after the queue write that settles the row

    async def _take_slot(self, parsed: StreamMessage) -> tuple[str, str] | None:
        """Wait for one of the ``MAX_CONCURRENT_INDEXING`` cluster-wide indexing slots (a lease row in PostgreSQL). The slot is renewed
        with the claim and expires with it, so a worker that dies frees its slot."""
        if self.concurrency_manager is None:
            return None
        pool, limit = _INDEX_POOL, max(1, messaging_env.max_concurrent_indexing)
        if self.stage_admission is not None:    # a stage's own pool, sized for every worker process
            pool, limit = self.stage_admission.lease_pool, self.stage_admission.lease_limit
        elif self.priority_lane is not None:    # the priority topic's own pool, so the backlog's claims cannot hold it up
            pool, limit = self.priority_lane.index_lease_pool, self.priority_lane.index_lease_limit
        slot = (pool, f"{self.config.client_id}:{uuid.uuid4().hex}")
        while self.running:
            if await self.concurrency_manager.try_acquire(slot[0], slot[1], limit, self._visibility_s):
                self._leases.add(slot)
                return slot
            await asyncio.sleep(0.5)
        raise asyncio.CancelledError  # stopping: leave the message claimed, it is handed out again

    async def _take_record_lease(self, parsed: StreamMessage, fallback_key: str) -> tuple[str, str] | None:
        """``record:<id>`` (a stage job: ``record:job:<id>``, which the stage sweeper reads as proof the job is running). A stage that rewrites
        the stored record takes ``record:<id>`` too (``RecordLeases``), so a re-index and a stage never write it together. Held for the
        whole delivery and renewed with the claim. Tried once, before any cluster slot: held by another, the delivery is handed back at
        once without costing an attempt (``RecordHeld``), with a back-off that grows per message."""
        if self.concurrency_manager is None:
            return None
        pool = f"record:{work_key(parsed.payload, fallback_key)}"
        holder = f"{self.config.client_id}:{uuid.uuid4().hex}"
        if await self.concurrency_manager.try_acquire(pool, holder, 1, messaging_env.concurrency_lease_seconds):
            self._leases.add((pool, holder))
            task = asyncio.current_task()
            if task is not None:
                self._lease_tasks[(pool, holder)] = task
            return pool, holder
        raise RecordHeld(f"{pool} is held by another delivery")
