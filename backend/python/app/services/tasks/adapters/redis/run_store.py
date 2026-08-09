"""`RedisRunStore`: `ITaskRunStore` backed by Redis hashes + sorted sets.

Index sorted sets:
  - `task_runs:{task_id}` -- member=run_id, score=created_at (epoch ms).
                              Backs `list_for_task` (naturally recency-ordered).
  - `runs:leases`         -- member=run_id, score=lease_expires_at (epoch ms).
                              Only RUNNING runs with an active lease.
  - `runs:pending`        -- member=run_id, score=created_at (epoch ms).
                              Only PENDING runs (outbox-lite re-publish scan).

Idempotency: `run_idem:{idempotency_key}` is a plain string key holding the
`run_id`, written with `SET ... NX` -- the atomic create-if-absent primitive
the port requires.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from redis.exceptions import WatchError

from app.services.tasks.adapters.redis import keys as k
from app.services.tasks.domain.models import Page, RunStatus, TaskRun
from app.services.tasks.interface.run_store import ITaskRunStore
from app.utils.time_conversion import datetime_to_epoch_ms, epoch_ms_to_iso

if TYPE_CHECKING:
    from redis.asyncio import Redis

_JSON_FIELDS = ("completed_steps", "skipped_steps", "usage", "trigger_payload")
_JSON_OBJECT_FIELDS = frozenset({"usage", "trigger_payload"})
_TIMESTAMP_FIELDS = ("started_at", "completed_at", "created_at", "scheduled_for")

_TERMINAL_RUN_TTL_S = 14 * 24 * 3600
"""How long a finished run stays readable from Redis before falling back to the
archive. Long enough to cover the window in which anyone actually opens a run
(and to absorb an archive outage), short enough that Redis holds a bounded
working set rather than every run the platform has ever executed."""

_MAX_RUNS_PER_TASK = 500
"""Cap on a single task's Redis run index. A minutely task produces more runs
than this inside the TTL window, and nothing pages that deep -- the archive
serves anything older."""

# Atomically claim a PENDING run into RUNNING under `owner`'s lease.
# Refuses anything not currently PENDING (including ABANDONED -- reviving
# those is the caller's explicit decision, not this primitive's).
_CLAIM_FOR_EXECUTION_SCRIPT = """
local hkey = KEYS[1]
local leases_key = KEYS[2]
local pending_key = KEYS[3]
local run_id = ARGV[1]
local owner = ARGV[2]
local lease_ms = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])
local started_at_iso = ARGV[5]

local status = redis.call("HGET", hkey, "status")
if status == false or status ~= "pending" then
    return 0
end

local expires_at = now_ms + lease_ms
redis.call("HSET", hkey, "status", "running", "lease_owner", owner, "lease_expires_at", tostring(expires_at))
local started_at = redis.call("HGET", hkey, "started_at")
if not started_at or started_at == "" then
    redis.call("HSET", hkey, "started_at", started_at_iso)
end
redis.call("ZREM", pending_key, run_id)
redis.call("ZADD", leases_key, expires_at, run_id)
return 1
"""

# Atomically transition an AWAITING_INPUT run back to PENDING with the
# caller's answer stashed for the executor's next claim. Refuses anything
# not currently AWAITING_INPUT -- the stale-answer guard.
_RESUME_WITH_ANSWER_SCRIPT = """
local hkey = KEYS[1]
local pending_key = KEYS[2]
local run_id = ARGV[1]
local answer = ARGV[2]
local now_ms = tonumber(ARGV[3])

local status = redis.call("HGET", hkey, "status")
if status == false or status ~= "awaiting_input" then
    return 0
end

redis.call("HSET", hkey, "status", "pending", "pending_answer", answer)
redis.call("ZADD", pending_key, now_ms, run_id)
return 1
"""

# Extend a run's lease iff `owner` still holds it, mirroring
# `DistributedConcurrencyManager`'s renew script shape.
_HEARTBEAT_SCRIPT = """
local hkey = KEYS[1]
local leases_key = KEYS[2]
local run_id = ARGV[1]
local owner = ARGV[2]
local lease_ms = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])

local current_owner = redis.call("HGET", hkey, "lease_owner")
if current_owner ~= owner then
    return 0
end
local expires_at = now_ms + lease_ms
redis.call("HSET", hkey, "lease_expires_at", tostring(expires_at))
redis.call("ZADD", leases_key, expires_at, run_id)
return 1
"""


def _run_to_hash(run: TaskRun) -> dict[str, str]:
    payload = run.model_dump(mode="json")
    hash_fields: dict[str, str] = {}
    for field_name, value in payload.items():
        if value is None:
            continue
        if field_name in _JSON_FIELDS:
            hash_fields[field_name] = json.dumps(value)
        elif field_name == "lease_expires_at" and value:
            hash_fields[field_name] = str(datetime_to_epoch_ms(value))
        elif isinstance(value, bool):
            hash_fields[field_name] = "1" if value else "0"
        else:
            hash_fields[field_name] = str(value)
    return hash_fields


def _hash_to_run(hash_fields: dict[str, str]) -> TaskRun:
    payload: dict[str, Any] = dict(hash_fields)
    raw_lease = payload.get("lease_expires_at")
    payload["lease_expires_at"] = epoch_ms_to_iso(int(raw_lease)) if raw_lease else None
    for field_name in _JSON_FIELDS:
        raw = payload.get(field_name)
        empty: Any = {} if field_name in _JSON_OBJECT_FIELDS else []
        payload[field_name] = json.loads(raw) if raw else empty
    if "had_write_side_effect" in payload:
        payload["had_write_side_effect"] = payload["had_write_side_effect"] in ("1", "True", "true")
    if "is_dry_run" in payload:
        payload["is_dry_run"] = payload["is_dry_run"] in ("1", "True", "true")
    if "attempt" in payload and payload["attempt"] not in (None, ""):
        payload["attempt"] = int(payload["attempt"])
    if "schema_version" in payload and payload["schema_version"] not in (None, ""):
        payload["schema_version"] = int(payload["schema_version"])
    return TaskRun.model_validate(payload)


class RedisRunStore(ITaskRunStore):
    def __init__(
        self,
        redis_client: Redis,
        *,
        terminal_ttl_seconds: int = _TERMINAL_RUN_TTL_S,
        max_runs_per_task: int = _MAX_RUNS_PER_TASK,
    ) -> None:
        self._redis = redis_client
        self._claim_script = redis_client.register_script(_CLAIM_FOR_EXECUTION_SCRIPT)
        self._resume_with_answer_script = redis_client.register_script(_RESUME_WITH_ANSWER_SCRIPT)
        self._heartbeat_script = redis_client.register_script(_HEARTBEAT_SCRIPT)
        self._terminal_ttl = terminal_ttl_seconds
        self._max_runs_per_task = max_runs_per_task

    async def expire_terminal(self, run: TaskRun) -> None:
        """Hand a finished run a TTL, and trim its task's run index.

        Only ever called once the run is safely archived (see
        `ArchivingRunStore`), because everything here is destructive.

        The idempotency key gets the same TTL rather than outliving the run:
        it exists to collapse duplicate dispatches of one fire, and a fire
        cannot still be arriving days later. Keeping it forever would leak one
        key per run indefinitely -- the largest of these three leaks, since
        unlike the hash it is never read after the run starts.
        """
        run_key = k.run_hash_key(run.run_id)
        idem_key = k.run_idempotency_key(run.idempotency_key)
        task_index = k.runs_by_task_zset_key(run.task_id)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.expire(run_key, self._terminal_ttl)
            pipe.expire(idem_key, self._terminal_ttl)
            # Bound the index independently of the TTL: a hot task can produce
            # more runs inside one TTL window than anyone will ever page
            # through, and the zset itself has no expiry.
            pipe.zremrangebyrank(task_index, 0, -(self._max_runs_per_task + 1))
            pipe.expire(task_index, self._terminal_ttl)
            await pipe.execute()

    async def _write(self, run: TaskRun, *, expected_owner: str | None = None) -> bool:
        """Persist `run` and reconcile its index memberships.

        When `expected_owner` is given, the write is applied only if the stored
        `lease_owner` still matches, under a WATCH on the run hash. That guard
        is what stops a worker whose lease was reaped mid-run from later
        overwriting the state of the worker that legitimately reclaimed it.
        Returns False when the guard rejected the write.
        """
        hkey = k.run_hash_key(run.run_id)
        hash_fields = _run_to_hash(run)
        created_at_ms = datetime_to_epoch_ms(run.created_at) or 0

        def _queue(pipe: Any) -> None:
            pipe.delete(hkey)
            if hash_fields:
                pipe.hset(hkey, mapping=hash_fields)
            pipe.zadd(k.runs_by_task_zset_key(run.task_id), {run.run_id: created_at_ms})

            if run.status == RunStatus.PENDING:
                pipe.zadd(k.runs_pending_zset_key(), {run.run_id: created_at_ms})
            else:
                pipe.zrem(k.runs_pending_zset_key(), run.run_id)

            if run.status == RunStatus.RUNNING and run.lease_expires_at:
                pipe.zadd(k.runs_lease_zset_key(), {run.run_id: datetime_to_epoch_ms(run.lease_expires_at)})
            else:
                pipe.zrem(k.runs_lease_zset_key(), run.run_id)

            if run.status == RunStatus.AWAITING_INPUT and run.resume_deadline_at:
                pipe.zadd(
                    k.runs_suspension_deadline_zset_key(),
                    {run.run_id: datetime_to_epoch_ms(run.resume_deadline_at) or 0},
                )
            else:
                pipe.zrem(k.runs_suspension_deadline_zset_key(), run.run_id)

            if run.awaiting_event_type:
                awaiting_key = k.runs_awaiting_event_set_key(run.org_id, run.awaiting_event_type)
                if run.status == RunStatus.AWAITING_INPUT:
                    pipe.sadd(awaiting_key, run.run_id)
                else:
                    # Resumed, cancelled or failed: leaving the id behind would
                    # have the next event resume a run that is no longer parked.
                    pipe.srem(awaiting_key, run.run_id)

        if expected_owner is None:
            async with self._redis.pipeline(transaction=True) as pipe:
                _queue(pipe)
                await pipe.execute()
            return True

        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.watch(hkey)
            current_owner = await pipe.hget(hkey, "lease_owner")
            if isinstance(current_owner, bytes):
                current_owner = current_owner.decode()
            if current_owner != expected_owner:
                await pipe.unwatch()
                return False
            pipe.multi()
            _queue(pipe)
            try:
                await pipe.execute()
            except WatchError:
                return False
        return True

    async def create_if_absent(self, run: TaskRun) -> TaskRun | None:
        idem_key = k.run_idempotency_key(run.idempotency_key)
        created = await self._redis.set(idem_key, run.run_id, nx=True)
        if not created:
            return None
        await self._write(run)
        return run

    async def get(self, run_id: str) -> TaskRun | None:
        hash_fields = await self._redis.hgetall(k.run_hash_key(run_id))
        if not hash_fields:
            return None
        return _hash_to_run(hash_fields)

    async def get_by_idempotency_key(self, idempotency_key: str) -> TaskRun | None:
        run_id = await self._redis.get(k.run_idempotency_key(idempotency_key))
        if not run_id:
            return None
        return await self.get(run_id)

    async def update(self, run: TaskRun, *, expected_owner: str | None = None) -> TaskRun | None:
        written = await self._write(run, expected_owner=expected_owner)
        return run if written else None

    async def claim_for_execution(self, run_id: str, *, owner: str, lease_seconds: float) -> TaskRun | None:
        now = datetime.now(timezone.utc)
        lease_ms = max(1, int(lease_seconds * 1000))
        claimed: int = await self._claim_script(
            keys=[k.run_hash_key(run_id), k.runs_lease_zset_key(), k.runs_pending_zset_key()],
            args=[run_id, owner, lease_ms, int(now.timestamp() * 1000), now.isoformat()],
        )
        if not claimed:
            return None
        return await self.get(run_id)

    async def resume_with_answer(self, run_id: str, *, answer: str) -> TaskRun | None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        resumed: int = await self._resume_with_answer_script(
            keys=[k.run_hash_key(run_id), k.runs_pending_zset_key()],
            args=[run_id, answer, now_ms],
        )
        if not resumed:
            return None
        return await self.get(run_id)

    async def heartbeat(self, run_id: str, owner: str, lease_seconds: float) -> bool:
        lease_ms = max(1, int(lease_seconds * 1000))
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        result = await self._heartbeat_script(
            keys=[k.run_hash_key(run_id), k.runs_lease_zset_key()],
            args=[run_id, owner, lease_ms, now_ms],
        )
        return bool(result)

    async def reap_abandoned(self, *, now: datetime) -> list[TaskRun]:
        now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
        expired_ids = await self._redis.zrangebyscore(k.runs_lease_zset_key(), "-inf", now_ms)
        reaped: list[TaskRun] = []
        for run_id in expired_ids:
            run = await self.get(run_id)
            if run is None:
                await self._redis.zrem(k.runs_lease_zset_key(), run_id)
                continue
            if run.status != RunStatus.RUNNING:
                # Already transitioned by its worker between the scan and
                # this read -- not abandoned, just a stale lease-zset entry.
                await self._redis.zrem(k.runs_lease_zset_key(), run_id)
                continue
            updated = run.model_copy(update={
                "status": RunStatus.ABANDONED,
                "lease_owner": None,
                "lease_expires_at": None,
                "completed_at": now.isoformat(),
            })
            await self._write(updated)
            reaped.append(updated)
        return reaped

    async def list_for_task(self, task_id: str, *, limit: int = 50, offset: int = 0) -> Page[TaskRun]:
        zset_key = k.runs_by_task_zset_key(task_id)
        total = await self._redis.zcard(zset_key)
        run_ids = await self._redis.zrevrange(zset_key, offset, offset + limit - 1)
        runs: list[TaskRun] = []
        for run_id in run_ids:
            run = await self.get(run_id)
            if run is not None:
                runs.append(run)
        return Page(items=runs, total=total, limit=limit, offset=offset)

    async def list_pending(self, *, now: datetime, older_than_seconds: float, limit: int = 100) -> list[TaskRun]:
        cutoff_ms = int(now.astimezone(timezone.utc).timestamp() * 1000) - int(older_than_seconds * 1000)
        run_ids = await self._redis.zrangebyscore(k.runs_pending_zset_key(), "-inf", cutoff_ms, start=0, num=limit)
        runs: list[TaskRun] = []
        for run_id in run_ids:
            run = await self.get(run_id)
            if run is not None:
                runs.append(run)
        return runs

    async def list_expired_suspensions(self, *, now: datetime, limit: int = 100) -> list[TaskRun]:
        zset_key = k.runs_suspension_deadline_zset_key()
        now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
        run_ids = await self._redis.zrangebyscore(zset_key, "-inf", now_ms, start=0, num=limit)
        runs: list[TaskRun] = []
        for run_id in run_ids:
            run = await self.get(run_id)
            if run is None or run.status != RunStatus.AWAITING_INPUT:
                # Answered between the scan and this read, or already gone.
                await self._redis.zrem(zset_key, run_id)
                continue
            runs.append(run)
        return runs

    async def list_awaiting_event(self, org_id: str, event_type: str) -> list[TaskRun]:
        set_key = k.runs_awaiting_event_set_key(org_id, event_type)
        runs: list[TaskRun] = []
        for run_id in await self._redis.smembers(set_key):
            run = await self.get(run_id)
            # A run whose hash expired, or that moved on without the index
            # being reconciled, must not be resumed -- and the stale member is
            # dropped so the set does not grow without bound.
            if run is None or run.status != RunStatus.AWAITING_INPUT:
                await self._redis.srem(set_key, run_id)
                continue
            runs.append(run)
        return runs
