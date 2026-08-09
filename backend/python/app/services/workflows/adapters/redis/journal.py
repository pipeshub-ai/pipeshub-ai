"""RedisExecutionJournal: IExecutionJournal over Redis hashes + sorted set.

Each journal entry is stored as a Redis hash keyed by run_id:step_key.
A sorted set (score = monotonic seq) maintains ordering for replay.

Append is idempotent: SET ... NX on the hash + only ZADD NX.
compact() truncates entries with seq <= upto_seq and resets the index.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.services.workflows.adapters.redis import keys as k
from app.services.workflows.domain.models import (
    ErrorRecord,
    JournalEntry,
    ResultRef,
    StepOutcome,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

__all__ = ["RedisExecutionJournal"]

logger = logging.getLogger(__name__)

# Atomically append a journal entry (idempotent via NX).
_APPEND_SCRIPT = """
local hkey = KEYS[1]
local idx_key = KEYS[2]
local seq_key = KEYS[3]
local step_key = ARGV[1]
local payload = ARGV[2]
local ttl = tonumber(ARGV[3])

-- Only write if this entry doesn't exist yet (idempotency)
local exists = redis.call("EXISTS", hkey)
if exists == 1 then
    return 0
end

-- Allocate a monotonic sequence number
local seq = redis.call("INCR", seq_key)
redis.call("EXPIRE", seq_key, ttl)

-- Write the entry hash
redis.call("HSET", hkey, "payload", payload, "seq", tostring(seq))
redis.call("EXPIRE", hkey, ttl)

-- Index: sorted set keyed by seq
redis.call("ZADD", idx_key, seq, step_key)
redis.call("EXPIRE", idx_key, ttl)

return seq
"""


def _entry_from_payload(step_key: str, payload_str: str) -> JournalEntry:
    data = json.loads(payload_str)
    result_ref = None
    if "result_ref" in data and data["result_ref"] is not None:
        result_ref = ResultRef(**data["result_ref"])
    error = None
    if "error" in data and data["error"] is not None:
        error = ErrorRecord(**data["error"])
    return JournalEntry(
        run_id=data["run_id"],
        seq=data.get("seq", 0),
        step_key=step_key,
        entry_kind=data["entry_kind"],
        idempotency_key=data.get("idempotency_key", step_key),
        outcome=StepOutcome(data["outcome"]),
        result_ref=result_ref,
        error=error,
        attempt=data.get("attempt", 1),
    )


class RedisExecutionJournal:
    """IExecutionJournal backed by Redis."""

    def __init__(self, redis_client: "Redis", *, ttl_seconds: int | None = None) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds or k.journal_ttl_seconds()
        self._append_sha: str | None = None

    async def _ensure_scripts(self) -> None:
        if self._append_sha is None:
            self._append_sha = await self._redis.script_load(_APPEND_SCRIPT)

    async def append(self, entry: JournalEntry) -> None:
        await self._ensure_scripts()
        hkey = k.journal_entry_key(entry.run_id, entry.step_key)
        idx_key = k.journal_index_key(entry.run_id)
        seq_key = k.journal_seq_key(entry.run_id)
        payload = json.dumps({
            "run_id": entry.run_id,
            "seq": entry.seq,
            "step_key": entry.step_key,
            "entry_kind": entry.entry_kind,
            "idempotency_key": entry.idempotency_key,
            "outcome": entry.outcome.value if hasattr(entry.outcome, "value") else entry.outcome,
            "result_ref": entry.result_ref.model_dump() if entry.result_ref else None,
            "error": entry.error.model_dump() if entry.error else None,
            "attempt": entry.attempt,
        })
        await self._redis.evalsha(
            self._append_sha,
            3,
            hkey, idx_key, seq_key,
            entry.step_key, payload, str(self._ttl),
        )

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:
        hkey = k.journal_entry_key(run_id, step_key)
        result = await self._redis.hmget(hkey, "payload", "seq")
        if not result or result[0] is None:
            return None
        payload_str = result[0].decode() if isinstance(result[0], bytes) else result[0]
        try:
            return _entry_from_payload(step_key, payload_str)
        except Exception:
            logger.exception("Failed to deserialize journal entry %s:%s", run_id, step_key)
            return None

    async def load(self, run_id: str) -> list[JournalEntry]:
        idx_key = k.journal_index_key(run_id)
        step_keys = await self._redis.zrange(idx_key, 0, -1)
        if not step_keys:
            return []

        step_key_strs = [
            raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            for raw_key in step_keys
        ]

        pipe = self._redis.pipeline(transaction=False)
        for sk in step_key_strs:
            pipe.hmget(k.journal_entry_key(run_id, sk), "payload", "seq")
        results = await pipe.execute()

        entries = []
        for sk, result in zip(step_key_strs, results):
            if not result or result[0] is None:
                continue
            payload_str = result[0].decode() if isinstance(result[0], bytes) else result[0]
            try:
                entries.append(_entry_from_payload(sk, payload_str))
            except Exception:
                logger.exception("Failed to deserialize journal entry %s:%s", run_id, sk)
        return sorted(entries, key=lambda e: e.seq)

    async def touch(self, run_id: str) -> str | None:
        idx_key = k.journal_index_key(run_id)
        step_keys = await self._redis.zrange(idx_key, 0, -1)
        if not step_keys:
            # No index means no journal: either this run never journaled
            # anything, or it already expired. The caller cannot tell those
            # apart from here, and must treat both as "cannot rely on it".
            return None

        pipe = self._redis.pipeline(transaction=False)
        pipe.expire(idx_key, self._ttl)
        pipe.expire(k.journal_seq_key(run_id), self._ttl)
        for raw_key in step_keys:
            sk = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            pipe.expire(k.journal_entry_key(run_id, sk), self._ttl)
        await pipe.execute()

        deadline = datetime.now(timezone.utc) + timedelta(seconds=self._ttl)
        logger.info(
            "journal: extended retention for run %s to %s (%d entries)",
            run_id, deadline.isoformat(), len(step_keys),
        )
        return deadline.isoformat()

    async def compact(self, run_id: str, upto_seq: int) -> None:
        """Delete all journal entries with seq <= upto_seq and remove from index."""
        idx_key = k.journal_index_key(run_id)
        to_remove = await self._redis.zrangebyscore(idx_key, "-inf", upto_seq)
        if not to_remove:
            return
        pipe = self._redis.pipeline()
        for raw_key in to_remove:
            sk = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            hkey = k.journal_entry_key(run_id, sk)
            pipe.delete(hkey)
        pipe.zremrangebyscore(idx_key, "-inf", upto_seq)
        await pipe.execute()
        logger.info(
            "Compacted journal for run %s up to seq %d (%d entries removed)",
            run_id, upto_seq, len(to_remove),
        )
