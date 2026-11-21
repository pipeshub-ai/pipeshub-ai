"""The durable consumer inbox (``platform.consumer_inbox``): the events each consumer group has finished, per org.

A consumer skips an event its group already finished (``seen``) and records one only after its handler succeeded (``record``). So a
redelivery (a lost acknowledgement, a rebalance, a restart, an expired claim) or a replayed envelope runs once, and a failed handler
records nothing and is retried. A row is (org, ``<len>:<group>:<event id>``): every read and write runs in a unit of work bound to the
message's org, so row security keeps one org from learning which ids another processed, and two groups on one topic keep their own rows.

What it guarantees: at-least-once delivery with redeliveries skipped. No consumer handler commits its effect in one PostgreSQL
transaction (a graph write is many units, Neo4j and ArangoDB are not PostgreSQL), so the record follows the handler: a handler that
finished but whose record was lost (a crash in between, a failed write) runs again when the message comes back. On the PostgreSQL
broker the record and the acknowledgement are one transaction, so a message is acknowledged exactly when it is recorded.

Rows older than :func:`app.core.envelope.replay_window_seconds` (the longest any copy of an envelope can still be accepted) are deleted
as the jobs role, at most once a minute per process. Without PostgreSQL as a store, or without the jobs login that prunes the table,
:func:`shared_inbox` is None and says so once: consumers keep the broker's at-least-once delivery, and a signed replay is still bounded
by the envelope's age. A Redis seen-id set is not used instead: eviction would drop ids (a replay runs) and ``noeviction`` lets them fill
the server.
"""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import TYPE_CHECKING, Any

from app.core import org_context
from app.core.envelope import INBOX_ERRORS, replay_window_seconds
from app.services.pg_runtime import RuntimeDb
from app.services.redis.connection_provider_factory import uses_pg_state

if TYPE_CHECKING:
    from app.core.org_context import OrgContext
    from app.services.messaging.postgres.queue import Delivery, PgQueue

logger = logging.getLogger(__name__)

_SEEN = """/* pipeshub:inbox.seen */
SELECT EXISTS (SELECT 1 FROM platform.consumer_inbox WHERE org_id = $1 AND message_id = $2)"""
_RECORD = """/* pipeshub:inbox.record */
INSERT INTO platform.consumer_inbox (org_id, message_id) VALUES ($1, $2) ON CONFLICT DO NOTHING"""
_SWEEP = """/* pipeshub:inbox.sweep */
DELETE FROM platform.consumer_inbox WHERE ctid IN (
  SELECT ctid FROM platform.consumer_inbox WHERE received_at < now() - make_interval(secs => $1::double precision) LIMIT $2)"""

SWEEP_EVERY_S = 60.0
SWEEP_BATCH = 5000
SWEEP_MAX_BATCHES = 40  # per sweep: bounds one pass; the next one carries on


def inbox_message_id(consumer: str, event_id: str) -> str:
    """The row's id: the group's name carries its length, so no other (group, event id) pair can spell the same id."""
    return f"{len(consumer)}:{consumer}:{event_id}"


class PgInbox:
    def __init__(self, runtime: RuntimeDb | None = None) -> None:
        self.rt = runtime or RuntimeDb()
        self._next_sweep = 0.0

    async def seen(self, consumer: str, ctx: OrgContext) -> bool:
        with org_context.bind(ctx):
            async with (await self.rt.db()).uow() as tx:
                return bool(await tx.fetchval(_SEEN, ctx.org_id, inbox_message_id(consumer, ctx.event_id or "")))

    async def record(self, consumer: str, ctx: OrgContext) -> None:
        with org_context.bind(ctx):
            async with (await self.rt.db()).uow(write=True) as tx:
                await self._record_in(tx, consumer, ctx)

    async def record_and_ack(self, consumer: str, ctx: OrgContext, queue: PgQueue, group_id: str, d: Delivery) -> bool:
        """The record and the queue's acknowledgement in one transaction (PostgreSQL broker); True when the ack took effect."""
        with org_context.bind(ctx):
            async with (await self.rt.db()).uow(write=True) as tx:
                await self._record_in(tx, consumer, ctx)
                return await queue.ack_in(tx, group_id, d)

    async def _record_in(self, tx: Any, consumer: str, ctx: OrgContext) -> None:  # noqa: ANN401 - the unit of work
        await tx.execute(_RECORD, ctx.org_id, inbox_message_id(consumer, ctx.event_id or ""))
        now = time.monotonic()
        if now >= self._next_sweep:
            self._next_sweep = now + SWEEP_EVERY_S
            org_context.spawn_detached(self._sweep_logged(), name="consumer-inbox-sweep")

    async def sweep(self) -> int:
        """Delete rows past the replay window, as the jobs role across every org; returns how many went."""
        removed = 0
        window = replay_window_seconds()
        with org_context.system("consumer-inbox-sweep"):
            db = await self.rt.db()
            for _ in range(SWEEP_MAX_BATCHES):
                async with db.jobs_uow(write=True) as tx:
                    n = int((await tx.execute(_SWEEP, window, SWEEP_BATCH)).rsplit(" ", 1)[-1])
                removed += n
                if n < SWEEP_BATCH:
                    break
        return removed

    async def _sweep_logged(self) -> None:
        try:
            removed = await self.sweep()
        except Exception as e:
            INBOX_ERRORS.inc("sweep")
            logger.warning("consumer inbox sweep failed: %s", type(e).__name__)
            return
        if removed:
            logger.info("consumer inbox sweep removed %d row(s)", removed)


_PG_STORES = (("MESSAGE_BROKER", "postgres"), ("DATA_STORE", "postgres"), ("KV_STORE_TYPE", "postgres"), ("VECTOR_DB_TYPE", "pgvector"))


@functools.cache
def shared_inbox() -> PgInbox | None:
    """This process's inbox, or None when PostgreSQL is not one of its stores or the jobs login that prunes it is not configured."""
    if not (uses_pg_state() or any(os.getenv(k, "").strip().lower() == v for k, v in _PG_STORES)):
        logger.info("No consumer inbox (PostgreSQL is not a store here): a redelivered message may be processed again")
        return None
    if not (os.getenv("PG_CORE_URL") and os.getenv("PG_JOBS_URL")):
        logger.warning("No consumer inbox: PG_CORE_URL and PG_JOBS_URL (the login that prunes it) are both needed; a redelivered message may be processed again")
        return None
    return PgInbox()
