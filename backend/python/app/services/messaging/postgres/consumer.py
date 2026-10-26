"""Consumer for ``MESSAGE_BROKER=postgres``: simple (entity, sync, config events) and indexing (record events) in one class.

A claim counts a delivery attempt and hides the row for ``claim_min_idle_ms``; a heartbeat keeps the rows this consumer is still working
on hidden, so a long record is not handed to a second consumer, while a crashed consumer's rows reappear after the idle time. A
transient failure makes the row visible again after a backoff; a delivery that reaches ``MAX_DELIVERY_ATTEMPTS`` (or fails
terminally) is marked dead, after telling the disposition sink so the record it names is left in a terminal status.

Left out on purpose, because they exist for a fleet on a shared broker: lanes, the fair (DRR) scheduler, the resource governor's
parse gates and the capacity leases. The indexing variant runs up to ``MAX_CONCURRENT_INDEXING`` handlers at once.
"""

import asyncio
import contextlib
import json
import os
import time
import uuid
from logging import Logger
from typing import TYPE_CHECKING, Any, override

from app.core.envelope import (
    DuplicateEnvelope,
    EnvelopeRejected,
    ctx_of,
    open_configured,
    open_stream_message,
)
from app.core.org_context import bind_optional, spawn_detached
from app.core.org_context import pop as pop_org
from app.core.org_context import push as push_org
from app.services.messaging.config import (
    IndexingEvent,
    MessageHandler,
    RedisStreamsConfig,
    StreamMessage,
    compute_retry_backoff_seconds,
    messaging_env,
)
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt, work_key
from app.services.messaging.disposition import describe_message, notify_abandoned
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.interface.consumer import IMessagingConsumer
from app.services.messaging.postgres.queue import Delivery, PgQueue
from app.services.pg_runtime import QUEUE_CHANNEL, PgListener, listen_dsn
from app.utils.request_context import context_from_envelope, reset_context, set_context

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from app.services.distributed.interface import IDistributedLeaseManager
    from app.services.messaging.consumer_concurrency import StageAdmission
    from app.services.messaging.disposition import AbandonedMessageSink
    from app.services.messaging.worker_loop import WorkerLoop

_SWEEP_EVERY_S = 60.0
_INDEX_POOL = "pg-queue-indexing"
_SAFETY_NET_S = 300.0  # longest an idle consumer with a LISTEN connection sleeps
_HANDBACK_CAP_S = 60.0   # longest the per-message back-off of hand-backs grows to


def _handback_base_s() -> float:
    return float(os.getenv("PG_QUEUE_HANDBACK_BASE_S", "1"))


def _handback_limit() -> int:
    """Hand-backs of one message (in this process) after which one that counts toward the backstop is charged as a failed attempt."""
    return int(os.getenv("PG_QUEUE_HANDBACK_LIMIT", "200"))


class RecordHeld(RequeueWithoutAttempt):
    """The record's lease is held by another delivery (a stage rewriting the record, a duplicate): not a reason to pause a consumer."""


def _retry_base_s() -> float:
    return float(os.getenv("PG_QUEUE_RETRY_BASE_S", "5"))


def _loads(value: str) -> Any:  # noqa: ANN401
    raw = json.loads(value)
    return (
        json.loads(raw) if isinstance(raw, str) else raw
    )  # a double-encoded body, as the Redis consumers accept


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
        worker: "WorkerLoop | None" = None,
    ) -> None:
        self._worker = worker  # the loop the indexing handler runs on; claims, heartbeats and settling stay on this one
        self.stage_admission = stage_admission  # a pipeline-stage consumer: its own permits and lease pool, no record lease
        self.concurrency_manager = concurrency_manager  # cluster-wide cap on records indexing at once, across worker processes
        self._leases: set[tuple[str, str]] = set()
        self.logger = logger
        self.config = config
        self.indexing = indexing
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

    @property
    def _visibility_s(self) -> float:
        return max(self.config.claim_min_idle_ms / 1000.0, 5.0)

    async def initialize(self) -> None:
        for topic in self.config.topics:
            created = await self.queue.ensure_group(
                topic,
                self.config.group_id,
                from_start=not self.config.ephemeral_group,
                ephemeral=self.config.ephemeral_group,
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
        if self.config.ephemeral_group:
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
                batch = await self.queue.claim(
                    self.config.group_id,
                    self.config.topics,
                    self.config.client_id,
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
            busy = bool(self._held) or self.config.ephemeral_group
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
                if self._leases and self.concurrency_manager is not None:
                    await self.concurrency_manager.renew_many(
                        list(self._leases), self._visibility_s
                    )
                if self.config.ephemeral_group:
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

    async def _settle(self, d: Delivery) -> None:
        group, max_attempts = self.config.group_id, messaging_env.max_delivery_attempts
        if (
            d.attempts > max_attempts
        ):  # handed out past the limit (a consumer that keeps dying mid-handler)
            # The sink needs the message to find its record: without it the record stays IN_PROGRESS and recovery hands it back for ever.
            await self._abandon(d, await self._parse_for_sink(d), "delivery attempts exhausted")
            return
        final = d.attempts >= max_attempts
        try:
            success, terminal, parsed, err = await self._process(d, final=final)
        except RequeueWithoutAttempt as e:
            # Queue time, not a failure (a stage whose dependency is paused, a record whose lease stayed held): visible again after the
            # delay with the attempt this claim counted given back, so waiting never dead-letters it.
            key = (d.topic, d.message_id)
            n = self._handbacks[key] = self._handbacks.get(key, 0) + 1
            if len(self._handbacks) > 10_000:      # a bounded table: the oldest entries go first
                self._handbacks.pop(next(iter(self._handbacks)))
            delay = max(
                e.delay_s if e.delay_s is not None else compute_retry_backoff_seconds(1),
                min(_handback_base_s() * 2 ** min(n - 1, 10), _HANDBACK_CAP_S),     # grows with every hand-back of this message
            )
            reason = type(e).__name__
            self._handback_log[reason] = self._handback_log.get(reason, 0) + 1
            self.logger.debug("%s handed back without counting an attempt (%d so far, retry in %.1fs): %s", d.message_id, n, delay, e)
            if self.stage_admission is not None and not isinstance(e, RecordHeld):
                self._paused_until = max(self._paused_until, time.monotonic() + delay)     # stop claiming until the stage can run
            if e.counts_toward_backstop and n > _handback_limit():
                # handed back for ever (a lease that is never freed): charged as a failure, so the delivery limit ends it
                await self.queue.nack(group, d, delay, f"handed back {n} times: {e}"[:500])
                return
            await self.queue.nack(group, d, delay, str(e), refund=True)
            return
        self._handbacks.pop((d.topic, d.message_id), None)
        if success:
            await self.queue.ack(group, d)
        elif terminal:
            await self._abandon(d, parsed, err or "terminal error")
        elif final:
            await self._abandon(d, parsed, err or f"failed {d.attempts} times")
        else:
            delay = (
                compute_retry_backoff_seconds(d.attempts)
                if self.indexing
                else min(_retry_base_s() * d.attempts, 30.0)
            )
            await self.queue.nack(group, d, delay, err)
            self.logger.warning(
                "Transient failure on %s (%d/%d), retrying in %.0fs",
                d.message_id,
                d.attempts,
                max_attempts,
                delay,
            )

    async def _parse_for_sink(self, d: Delivery) -> StreamMessage | None:
        """The message of a delivery that will not be processed, for the disposition sink; None when it cannot be read."""
        if not self.indexing:
            return None
        try:
            raw = _loads(d.value)
            return await open_stream_message(d.topic, raw, StreamMessage) if isinstance(raw, dict) else None
        except Exception as e:
            self.logger.warning("Could not read the exhausted message %s for its record: %s", d.message_id, e)
            return None

    async def _abandon(
        self, d: Delivery, parsed: StreamMessage | None, reason: str
    ) -> None:
        await notify_abandoned(
            self.disposition_sink,
            self.logger,
            parsed,
            reason=reason,
            attempts=d.attempts,
        )
        self.logger.warning(
            "Dead-lettered %s (%s): %s", d.message_id, describe_message(parsed), reason
        )
        await self.queue.dead(self.config.group_id, d, reason)

    async def _process(
        self, d: Delivery, *, final: bool
    ) -> tuple[bool, bool, StreamMessage | None, str]:
        """(success, terminal, parsed message, error text)."""
        if self.message_handler is None:
            return False, True, None, "no message handler"
        try:
            raw = _loads(d.value)
        except json.JSONDecodeError as e:
            return False, True, None, f"JSON parsing failed: {e}"
        if not isinstance(raw, dict):
            return False, True, None, "message is not an object"
        parsed: StreamMessage | None = None
        token = set_context(context_from_envelope(raw).root_id)
        try:
            if self.indexing:
                try:
                    parsed = await open_stream_message(d.topic, raw, StreamMessage)
                except DuplicateEnvelope:
                    return True, False, None, ""
                except EnvelopeRejected as e:
                    return False, True, None, f"envelope rejected: {e.reason}"
                if parsed is None:
                    return False, True, None, "unparseable message"
                parsed.is_final_failure = final
                await self._drive_indexing(parsed, f"message:{d.message_id}")
                return True, False, parsed, ""
            try:
                opened = await open_configured(d.topic, raw)
            except DuplicateEnvelope:
                return True, False, None, ""
            except EnvelopeRejected as e:
                return False, True, None, f"envelope rejected: {e.reason}"
            parsed = StreamMessage(**opened.message)
            parsed.is_final_failure = final
            with bind_optional(opened.ctx):
                ok = await self.message_handler(parsed)
            return bool(ok), False, parsed, "" if ok else "handler returned false"
        except RequeueWithoutAttempt:
            raise
        except Exception as e:
            self.logger.error(
                "Error in message handler for %s: %s", d.message_id, e, exc_info=True
            )
            terminal = (
                MessageErrorClassifier.classify_by_exception(e)
                == MessageErrorType.TERMINAL
            )
            if terminal and parsed is not None:
                parsed.is_final_failure = True
            return False, terminal, parsed, f"{type(e).__name__}: {e}"
        finally:
            reset_context(token)

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

            async with asyncio.timeout(timeout):
                complete = await self._on_worker(drain)
            if not complete:
                raise RuntimeError("handler ended without INDEXING_COMPLETE")
        finally:
            pop_org(org_token)
            for held in (record_slot, slot):
                if held is not None:
                    self._leases.discard(held)
                    with contextlib.suppress(Exception):
                        await self.concurrency_manager.release(*held)  # type: ignore[union-attr]

    async def _take_slot(self, parsed: StreamMessage) -> tuple[str, str] | None:
        """Wait for one of the ``MAX_CONCURRENT_INDEXING`` cluster-wide indexing slots (a lease row in PostgreSQL). The slot is renewed
        with the claim and expires with it, so a worker that dies frees its slot."""
        if self.concurrency_manager is None:
            return None
        pool, limit = _INDEX_POOL, max(1, messaging_env.max_concurrent_indexing)
        if self.stage_admission is not None:    # a stage's own pool, sized for every worker process
            pool, limit = self.stage_admission.lease_pool, self.stage_admission.lease_limit
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
            return pool, holder
        raise RecordHeld(f"{pool} is held by another delivery")
