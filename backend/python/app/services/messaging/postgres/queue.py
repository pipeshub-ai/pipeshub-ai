"""The PostgreSQL work queue behind ``MESSAGE_BROKER=postgres``: Redis Streams semantics on three tables.

* **Topics and groups.** A message is stored once (``queue_messages``); every consumer group on its topic gets a delivery row
  (``queue_deliveries``), so groups ack, retry and dead-letter independently, as consumer groups do on a stream. A group created with
  ``from_start`` replays what is retained; an ephemeral group starts at the tail and is dropped when its process stops refreshing it.
* **Claim.** ``FOR UPDATE SKIP LOCKED`` over the open deliveries of a group in message order, so concurrent claimers never block each
  other or take the same row. A claim counts one attempt and hides the row until ``locked_until`` (the pending list's idle time);
  an expired claim is handed out again, which is the XAUTOCLAIM redelivery.
* **Ack** deletes the delivery; the message goes when its last delivery does. **Nack** makes the row visible again after a backoff.
  A delivery past ``max_attempts`` is marked ``dead`` (kept for inspection).
* **Wake-up** is NOTIFY on ``pipeshub_queue`` with the topic as payload; a consumer with a LISTEN connection sleeps until
  a notification or the next retry/claim expiry (the connection is pinged, and a lost one is re-established and re-read), a consumer
  without one polls.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.pg_runtime import QUEUE_CHANNEL, RuntimeDb

_GROUPS = """/* pipeshub:queue.groups */
SELECT group_id FROM platform.queue_groups WHERE topic = $1 FOR SHARE"""
_INSERT = """/* pipeshub:queue.publish */
WITH m AS (
  INSERT INTO platform.queue_messages (topic, msg_key, value)
  SELECT $1, u.k, u.v FROM unnest($2::text[], $3::text[]) WITH ORDINALITY AS u(k, v, ord) ORDER BY u.ord
  RETURNING id, msg_key)
INSERT INTO platform.queue_deliveries (group_id, topic, message_id, msg_key)
SELECT g, $1, m.id, m.msg_key FROM m CROSS JOIN unnest($4::text[]) AS g"""
_NOTIFY = "/* pipeshub:queue.notify */ SELECT pg_notify('pipeshub_queue', $1)"  # QUEUE_CHANNEL
_LOCK_GROUPS = """/* pipeshub:queue.lockgroups */
LOCK TABLE platform.queue_groups IN EXCLUSIVE MODE"""
_ADD_GROUP = """/* pipeshub:queue.addgroup */
INSERT INTO platform.queue_groups (topic, group_id, ephemeral) VALUES ($1, $2, $3)
ON CONFLICT (topic, group_id) DO UPDATE SET seen_at = now() RETURNING (xmax = 0) AS created"""
_BACKFILL = """/* pipeshub:queue.backfill */
INSERT INTO platform.queue_deliveries (group_id, topic, message_id, msg_key)
SELECT $2, topic, id, msg_key FROM platform.queue_messages WHERE topic = $1 ORDER BY id"""
_DROP_GROUP_DELIVERIES = """/* pipeshub:queue.dropdeliveries */
DELETE FROM platform.queue_deliveries WHERE group_id = $1 AND topic = $2"""
_DROP_GROUP = """/* pipeshub:queue.dropgroup */
DELETE FROM platform.queue_groups WHERE group_id = $1 AND topic = $2"""
_TOUCH = """/* pipeshub:queue.touch */
UPDATE platform.queue_groups SET seen_at = now() WHERE group_id = $1 AND topic = ANY($2::text[]) AND ephemeral"""
_CLAIM = """/* pipeshub:queue.claim */
WITH picked AS (
  SELECT group_id, topic, message_id FROM platform.queue_deliveries
  WHERE group_id = $1 AND topic = ($2::text[])[1]
    AND ((status = 'ready' AND visible_at <= now()) OR (status = 'claimed' AND locked_until <= now()))
  ORDER BY message_id LIMIT $3 FOR UPDATE SKIP LOCKED),
upd AS (
  UPDATE platform.queue_deliveries d
  SET status = 'claimed', attempts = d.attempts + 1, locked_until = now() + make_interval(secs => $4::double precision), consumer = $5
  FROM picked p WHERE d.group_id = p.group_id AND d.topic = p.topic AND d.message_id = p.message_id
  RETURNING d.topic, d.message_id, d.attempts)
SELECT u.topic, u.message_id, u.attempts, m.value, m.msg_key
FROM upd u JOIN platform.queue_messages m ON m.id = u.message_id ORDER BY u.message_id"""
# Per-key order. Serialised per group by a transaction-scoped advisory lock, so a claimer sees what the previous one claimed and
# committed. A delivery is handed out only when no other delivery of its key (same group and topic) is open AND earlier, and none is
# held by a live claim whatever its id (ids follow the sequence, not commit order: a late-committing earlier message must not start
# beside a later one already running). Both are probes of queue_deliveries_key, so the claim costs O(batch), not O(backlog).
_LOCK_ORDERED = """/* pipeshub:queue.lockordered */
SELECT pg_advisory_xact_lock(7401, hashtext($1 || '/' || $2))"""
_CLAIM_ORDERED = """/* pipeshub:queue.claimordered */
WITH picked AS (
  SELECT d.group_id, d.topic, d.message_id FROM platform.queue_deliveries d
  WHERE d.group_id = $1 AND d.topic = ($2::text[])[1]
    AND ((d.status = 'ready' AND d.visible_at <= now()) OR (d.status = 'claimed' AND d.locked_until <= now()))
    AND (d.msg_key IS NULL OR NOT EXISTS (
      SELECT 1 FROM platform.queue_deliveries e
      WHERE e.msg_key = d.msg_key AND (e.group_id || '') = $1 AND e.topic = ($2::text[])[1] AND e.message_id <> d.message_id
        AND e.status <> 'dead'   -- open: ready or claimed (an acked row is gone); not IN (...), see queue_deliveries_key
        AND (e.message_id < d.message_id OR (e.status = 'claimed' AND e.locked_until > now()))))
  ORDER BY d.message_id LIMIT $3 FOR UPDATE OF d SKIP LOCKED),
upd AS (
  UPDATE platform.queue_deliveries d
  SET status = 'claimed', attempts = d.attempts + 1, locked_until = now() + make_interval(secs => $4::double precision), consumer = $5
  FROM picked p WHERE d.group_id = p.group_id AND d.topic = p.topic AND d.message_id = p.message_id
  RETURNING d.topic, d.message_id, d.attempts)
SELECT u.topic, u.message_id, u.attempts, m.value, m.msg_key
FROM upd u JOIN platform.queue_messages m ON m.id = u.message_id ORDER BY u.message_id"""
# Seconds until the next delivery of this group becomes claimable (a retry backoff ending, or a claim expiring); NULL when none is open.
_NEXT_DUE = """/* pipeshub:queue.nextdue */
SELECT extract(epoch FROM min(CASE WHEN status = 'ready' THEN visible_at ELSE locked_until END) - now())::double precision AS secs
FROM platform.queue_deliveries WHERE group_id = $1 AND topic = ANY($2::text[]) AND status IN ('ready', 'claimed')"""
_ACK = """/* pipeshub:queue.ack */
DELETE FROM platform.queue_deliveries WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'"""
_NACK = """/* pipeshub:queue.nack */
UPDATE platform.queue_deliveries SET status = 'ready', locked_until = NULL, consumer = NULL, last_error = $5,
  attempts = attempts - $7::int,
  visible_at = now() + make_interval(secs => $6::double precision)
WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'"""
_DEAD = """/* pipeshub:queue.dead */
UPDATE platform.queue_deliveries SET status = 'dead', locked_until = NULL, visible_at = now(), last_error = $5
WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'"""
_HAS_DEAD = """/* pipeshub:queue.hasdead */
SELECT EXISTS (SELECT 1 FROM platform.queue_deliveries WHERE topic = $1 AND msg_key = $2 AND status = 'dead')"""
_EXTEND = """/* pipeshub:queue.extend */
UPDATE platform.queue_deliveries d SET locked_until = now() + make_interval(secs => $5::double precision)
FROM unnest($2::text[], $3::bigint[], $4::int[]) AS x(topic, message_id, attempts)
WHERE d.group_id = $1 AND d.topic = x.topic AND d.message_id = x.message_id AND d.attempts = x.attempts AND d.status = 'claimed'"""
_SWEEP_MESSAGES = """/* pipeshub:queue.sweepmessages */
DELETE FROM platform.queue_messages WHERE id IN (
  SELECT m.id FROM platform.queue_messages m
  WHERE NOT EXISTS (SELECT 1 FROM platform.queue_deliveries d WHERE d.message_id = m.id)
    AND (EXISTS (SELECT 1 FROM platform.queue_groups g WHERE g.topic = m.topic) OR m.created_at < now() - make_interval(secs => $1::double precision))
  LIMIT $2)"""
_SWEEP_DEAD = """/* pipeshub:queue.sweepdead */
DELETE FROM platform.queue_deliveries WHERE (group_id, topic, message_id) IN (
  SELECT group_id, topic, message_id FROM platform.queue_deliveries
  WHERE status = 'dead' AND visible_at < now() - make_interval(secs => $1::double precision) LIMIT $2)"""
_SWEEP_GROUPS = """/* pipeshub:queue.sweepgroups */
WITH gone AS (DELETE FROM platform.queue_groups WHERE ephemeral AND seen_at < now() - make_interval(secs => $1::double precision)
              RETURNING group_id, topic)
DELETE FROM platform.queue_deliveries d USING gone WHERE d.group_id = gone.group_id AND d.topic = gone.topic"""
_BACKLOG = """/* pipeshub:queue.backlog */
SELECT status, count(*) AS n FROM platform.queue_deliveries WHERE group_id = $1 AND topic = ANY($2::text[]) GROUP BY status"""
_TOPICS = """/* pipeshub:queue.topics */
SELECT DISTINCT topic FROM platform.queue_groups UNION SELECT DISTINCT topic FROM platform.queue_messages ORDER BY 1"""


@dataclass(frozen=True, slots=True)
class Delivery:
    topic: str
    message_id: int
    attempts: int
    value: str
    key: str | None


class PgQueue:
    def __init__(self, runtime: RuntimeDb | None = None) -> None:
        self.rt = runtime or RuntimeDb()

    async def _write(self):  # noqa: ANN202
        return (await self.rt.db()).runtime_uow(write=True)

    async def publish(self, topic: str, items: list[tuple[str | None, str]]) -> None:
        """Append messages in order; one transaction, one NOTIFY."""
        if not items:
            return
        async with await self._write() as tx:
            # FOR SHARE on the group rows conflicts with add_group's table lock: a group is either seen here or backfilled from this
            # message, never neither.
            groups = [r["group_id"] for r in await tx.fetch(_GROUPS, topic)]
            await tx.execute(
                _INSERT, topic, [k for k, _ in items], [v for _, v in items], groups
            )
            await tx.execute(_NOTIFY, topic)

    async def ensure_group(
        self, topic: str, group_id: str, *, from_start: bool, ephemeral: bool = False
    ) -> bool:
        """Create the group if absent (and, for a group that replays, give it the retained messages). True when created."""
        async with await self._write() as tx:
            await tx.execute(_LOCK_GROUPS)
            created = await tx.fetchval(_ADD_GROUP, topic, group_id, ephemeral)
            if created and from_start and not ephemeral:
                await tx.execute(_BACKFILL, topic, group_id)
            return bool(created)

    async def drop_group(self, topic: str, group_id: str) -> None:
        async with await self._write() as tx:
            await tx.execute(_DROP_GROUP_DELIVERIES, group_id, topic)
            await tx.execute(_DROP_GROUP, group_id, topic)

    async def touch(self, group_id: str, topics: list[str]) -> None:
        async with await self._write() as tx:
            await tx.execute(_TOUCH, group_id, topics)

    async def claim(
        self,
        group_id: str,
        topics: list[str],
        consumer: str,
        limit: int,
        visibility_s: float,
        *,
        ordered: bool = False,
    ) -> list[Delivery]:
        """``ordered``: nothing is handed out while an earlier delivery with the same message key is open (per-record or per-connector order).
        One topic at a time (a single-topic predicate lets the planner walk the group's open deliveries in message order and stop at the
        limit; an ``ANY`` over topics makes it sort the whole backlog); with several topics the starting one rotates."""
        out: list[Delivery] = []
        self._rr = (getattr(self, "_rr", -1) + 1) % max(len(topics), 1)
        for topic in topics[self._rr :] + topics[: self._rr]:
            if len(out) >= limit:
                break
            out += await self._claim_one(
                group_id,
                topic,
                consumer,
                limit - len(out),
                visibility_s,
                ordered=ordered,
            )
        return sorted(out, key=lambda d: d.message_id)

    async def _claim_one(
        self,
        group_id: str,
        topic: str,
        consumer: str,
        limit: int,
        visibility_s: float,
        *,
        ordered: bool,
    ) -> list[Delivery]:
        async with await self._write() as tx:
            if ordered:
                await tx.fetchval(_LOCK_ORDERED, group_id, topic)
            rows = await tx.fetch(
                _CLAIM_ORDERED if ordered else _CLAIM,
                group_id,
                [topic],
                limit,
                visibility_s,
                consumer,
            )
        return [
            Delivery(
                r["topic"], r["message_id"], r["attempts"], r["value"], r["msg_key"]
            )
            for r in rows
        ]

    async def next_due(self, group_id: str, topics: list[str]) -> float | None:
        async with (await self.rt.db()).runtime_uow() as tx:
            return await tx.fetchval(_NEXT_DUE, group_id, topics)

    async def ack(self, group_id: str, d: Delivery) -> bool:
        async with await self._write() as tx:
            return (
                await tx.execute(_ACK, group_id, d.topic, d.message_id, d.attempts)
            ).endswith(" 1")

    async def nack(
        self, group_id: str, d: Delivery, delay_s: float, error: str = "", *, refund: bool = False
    ) -> bool:
        """Make the delivery visible again after ``delay_s``. ``refund`` gives back the attempt the claim counted (queue time, not failure)."""
        async with await self._write() as tx:
            return (
                await tx.execute(
                    _NACK,
                    group_id,
                    d.topic,
                    d.message_id,
                    d.attempts,
                    error[:500],
                    delay_s,
                    1 if refund else 0,
                )
            ).endswith(" 1")

    async def dead(self, group_id: str, d: Delivery, error: str = "") -> bool:
        async with await self._write() as tx:
            return (
                await tx.execute(
                    _DEAD, group_id, d.topic, d.message_id, d.attempts, error[:500]
                )
            ).endswith(" 1")

    async def extend(
        self, group_id: str, held: list[Delivery], visibility_s: float
    ) -> None:
        if not held:
            return
        async with await self._write() as tx:
            await tx.execute(
                _EXTEND,
                group_id,
                [d.topic for d in held],
                [d.message_id for d in held],
                [d.attempts for d in held],
                visibility_s,
            )

    SWEEP_BATCH = 5000
    SWEEP_MAX_BATCHES = 40  # per call: bounds one tick; the next tick carries on

    async def sweep(
        self,
        *,
        orphan_retention_s: float = 86400.0,
        dead_retention_s: float = 7 * 86400.0,
        ephemeral_stale_s: float = 600.0,
    ) -> None:
        """Drop stale ephemeral groups, old dead letters (counted from when they died) and finished messages. Each
        kind loops, one short transaction per batch, while batches come back full, so the sweep keeps up with a burst."""
        async with await self._write() as tx:
            await tx.execute(_SWEEP_GROUPS, ephemeral_stale_s)
        for sql, retention in (
            (_SWEEP_DEAD, dead_retention_s),
            (_SWEEP_MESSAGES, orphan_retention_s),
        ):
            for _ in range(self.SWEEP_MAX_BATCHES):
                async with await self._write() as tx:
                    done = await tx.execute(sql, retention, self.SWEEP_BATCH)
                if int(done.rsplit(" ", 1)[-1]) < self.SWEEP_BATCH:
                    break

    async def backlog(self, group_id: str, topics: list[str]) -> dict[str, int]:
        async with (await self.rt.db()).runtime_uow() as tx:
            return {
                r["status"]: r["n"] for r in await tx.fetch(_BACKLOG, group_id, topics)
            }

    async def has_dead(self, topic: str, key: str) -> bool:
        """Whether a delivery with this key ran out of attempts and is still held as dead (until the dead-letter sweep)."""
        async with (await self.rt.db()).runtime_uow() as tx:
            return bool(await tx.fetchval(_HAS_DEAD, topic, key))

    async def ping(self) -> None:
        async with (await self.rt.db()).runtime_uow() as tx:
            await tx.fetchval("/* pipeshub:queue.ping */ SELECT 1")

    async def topics(self) -> list[str]:
        async with (await self.rt.db()).runtime_uow() as tx:
            return [r["topic"] for r in await tx.fetch(_TOPICS)]


__all__ = ["Delivery", "PgQueue", "QUEUE_CHANNEL"]
