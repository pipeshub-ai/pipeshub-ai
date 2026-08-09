"""`RedisTriggerStore`: `ITriggerStore` backed by Redis sorted sets + Lua
scripts. Same atomic sliding-window-lease pattern already proven by
`DistributedConcurrencyManager` (`app/services/messaging/distributed_concurrency.py`)
for indexing concurrency -- `ZADD`/`ZREMRANGEBYSCORE`/Lua, different key
namespace.

Two sorted sets index the hash-per-trigger records:
  - `triggers:due`    -- member=trigger_id, score=next_run_at (epoch ms).
                          Only claimable triggers are present.
  - `triggers:leases` -- member=trigger_id, score=lease_expires_at (epoch ms).
                          Only currently-claimed triggers are present.

`claim_due` atomically moves candidates from `due` to `leases`; `reap_expired_leases`
moves expired entries back from `leases` to `due` so a crashed scheduler's
claims become reclaimable.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.services.tasks.adapters.redis import keys as k
from app.services.tasks.domain.models import TaskTrigger, TriggerKind
from app.services.tasks.interface.trigger_store import ITriggerStore
from app.utils.time_conversion import datetime_to_epoch_ms, epoch_ms_to_iso

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_JSON_FIELDS = ("event_filter",)

# Atomically claim up to `limit` due-and-unleased triggers, moving each from
# the `due` ZSET to the `leases` ZSET in the same script invocation so no
# other caller can observe a trigger as simultaneously due and unclaimed.
_CLAIM_DUE_SCRIPT = """
local due_key = KEYS[1]
local leases_key = KEYS[2]
local hash_prefix = ARGV[1]
local now_ms = tonumber(ARGV[2])
local owner = ARGV[3]
local lease_ms = tonumber(ARGV[4])
local limit = tonumber(ARGV[5])

local scan_limit = math.max(limit * 3, 50)
local candidates = redis.call("ZRANGEBYSCORE", due_key, "-inf", now_ms, "LIMIT", 0, scan_limit)
local claimed = {}

for _, trigger_id in ipairs(candidates) do
    if #claimed >= limit then
        break
    end
    local hkey = hash_prefix .. trigger_id
    local exists = redis.call("EXISTS", hkey)
    if exists == 1 then
        local expires_at = now_ms + lease_ms
        redis.call("HSET", hkey, "lease_owner", owner, "lease_expires_at", tostring(expires_at))
        redis.call("ZREM", due_key, trigger_id)
        redis.call("ZADD", leases_key, expires_at, trigger_id)
        table.insert(claimed, trigger_id)
    else
        -- Stale index entry (hash was deleted without cleaning the zset) --
        -- drop it so future ticks don't keep re-scanning a dead id.
        redis.call("ZREM", due_key, trigger_id)
    end
end
return claimed
"""

# Release a lease and (re)schedule `next_run_at`. No-op if `owner` no longer
# holds the lease -- see `ITriggerStore.complete_claim` contract.
_COMPLETE_CLAIM_SCRIPT = """
local due_key = KEYS[1]
local leases_key = KEYS[2]
local hkey = ARGV[1]
local trigger_id = ARGV[2]
local owner = ARGV[3]
local next_run_at_ms = ARGV[4]

local current_owner = redis.call("HGET", hkey, "lease_owner")
if current_owner ~= owner then
    return 0
end

redis.call("HDEL", hkey, "lease_owner", "lease_expires_at")
redis.call("ZREM", leases_key, trigger_id)

if next_run_at_ms == "" then
    redis.call("HSET", hkey, "next_run_at", "")
    redis.call("ZREM", due_key, trigger_id)
else
    redis.call("HSET", hkey, "next_run_at", next_run_at_ms)
    redis.call("ZADD", due_key, tonumber(next_run_at_ms), trigger_id)
end
return 1
"""

# Consume one of a trigger's remaining max_runs. Returns -1 when the trigger is
# gone or exhausted, otherwise the post-increment run_count. The check and the
# HINCRBY share one script so two concurrent deliveries cannot both pass a
# check made against the same pre-increment count. A token already recorded
# replays its first answer instead of incrementing again.
_CLAIM_FIRE_SCRIPT = """
local hkey = KEYS[1]
local token_key = KEYS[2]
local fire_at_ms = ARGV[1]
local token_ttl = tonumber(ARGV[2])

if redis.call("EXISTS", hkey) == 0 then
    return -1
end

local prior = redis.call("GET", token_key)
if prior then
    return tonumber(prior)
end

local max_runs = redis.call("HGET", hkey, "max_runs")
local run_count = tonumber(redis.call("HGET", hkey, "run_count")) or 0
if max_runs and max_runs ~= "" and run_count >= tonumber(max_runs) then
    return -1
end

local new_count = redis.call("HINCRBY", hkey, "run_count", 1)
redis.call("HSET", hkey, "last_fire_at", fire_at_ms)
redis.call("SET", token_key, new_count, "EX", token_ttl)
return new_count
"""

_FIRE_TOKEN_TTL_S = 24 * 3600
"""How long a claimed delivery token is remembered. Comfortably longer than
any provider's redelivery schedule (GitHub and Slack give up within hours),
short enough that the keys do not accumulate indefinitely."""

# Move every trigger whose lease expired without a matching complete_claim
# back onto the due index, using its existing (unchanged) next_run_at.
_REAP_EXPIRED_LEASES_SCRIPT = """
local due_key = KEYS[1]
local leases_key = KEYS[2]
local hash_prefix = ARGV[1]
local now_ms = tonumber(ARGV[2])

local expired = redis.call("ZRANGEBYSCORE", leases_key, "-inf", now_ms)
local reaped = 0
for _, trigger_id in ipairs(expired) do
    local hkey = hash_prefix .. trigger_id
    redis.call("HDEL", hkey, "lease_owner", "lease_expires_at")
    redis.call("ZREM", leases_key, trigger_id)
    local next_run_at_ms = redis.call("HGET", hkey, "next_run_at")
    if next_run_at_ms and next_run_at_ms ~= "" then
        redis.call("ZADD", due_key, tonumber(next_run_at_ms), trigger_id)
    end
    reaped = reaped + 1
end
return reaped
"""


def _trigger_to_hash(trigger: TaskTrigger) -> dict[str, str]:
    payload = trigger.model_dump(mode="json")
    hash_fields: dict[str, str] = {}
    for field_name, value in payload.items():
        if value is None:
            continue
        if field_name in _JSON_FIELDS:
            hash_fields[field_name] = json.dumps(value)
        elif field_name in ("next_run_at", "last_fire_at", "fire_at", "lease_expires_at") and value:
            hash_fields[field_name] = str(datetime_to_epoch_ms(value))
        elif isinstance(value, bool):
            hash_fields[field_name] = "1" if value else "0"
        else:
            hash_fields[field_name] = str(value)
    return hash_fields


def _hash_to_trigger(hash_fields: dict[str, str]) -> TaskTrigger:
    payload: dict[str, Any] = dict(hash_fields)
    for field_name in ("next_run_at", "last_fire_at", "fire_at", "lease_expires_at"):
        raw = payload.get(field_name)
        payload[field_name] = epoch_ms_to_iso(int(raw)) if raw else None
    for field_name in _JSON_FIELDS:
        raw = payload.get(field_name)
        payload[field_name] = json.loads(raw) if raw else None
    for bool_field in ("enabled",):
        if bool_field in payload:
            payload[bool_field] = payload[bool_field] in ("1", "True", "true")
    for int_field in ("max_runs", "run_count", "interval_seconds"):
        if payload.get(int_field) not in (None, ""):
            payload[int_field] = int(payload[int_field])
        else:
            payload[int_field] = None if int_field == "max_runs" else 0
    return TaskTrigger.model_validate(payload)


class RedisTriggerStore(ITriggerStore):
    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._claim_script = redis_client.register_script(_CLAIM_DUE_SCRIPT)
        self._complete_script = redis_client.register_script(_COMPLETE_CLAIM_SCRIPT)
        self._reap_script = redis_client.register_script(_REAP_EXPIRED_LEASES_SCRIPT)
        self._claim_fire_script = redis_client.register_script(_CLAIM_FIRE_SCRIPT)

    async def upsert(self, trigger: TaskTrigger) -> TaskTrigger:
        hkey = k.trigger_hash_key(trigger.trigger_id)
        hash_fields = _trigger_to_hash(trigger)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(hkey)
            if hash_fields:
                pipe.hset(hkey, mapping=hash_fields)
            pipe.sadd(k.triggers_by_task_set_key(trigger.task_id), trigger.trigger_id)
            due_key = k.triggers_due_zset_key()
            if trigger.enabled and trigger.next_run_at and not trigger.lease_owner:
                pipe.zadd(due_key, {trigger.trigger_id: datetime_to_epoch_ms(trigger.next_run_at)})
            else:
                pipe.zrem(due_key, trigger.trigger_id)
            # `webhook_id`/`event_filter.event_type` are immutable for a
            # trigger's lifetime (nothing updates `kind` after creation), so
            # these index writes are idempotent -- no stale-entry cleanup
            # needed here, unlike the due/lease zsets above which change on
            # every reschedule.
            if trigger.kind == TriggerKind.WEBHOOK and trigger.webhook_id:
                pipe.set(k.webhook_trigger_key(trigger.webhook_id), trigger.trigger_id)
            if trigger.kind == TriggerKind.EVENT and trigger.event_filter:
                event_type = trigger.event_filter.get("event_type")
                if event_type:
                    pipe.sadd(k.event_triggers_set_key(trigger.org_id, str(event_type)), trigger.trigger_id)
            await pipe.execute()
        return trigger

    async def get(self, trigger_id: str) -> TaskTrigger | None:
        hash_fields = await self._redis.hgetall(k.trigger_hash_key(trigger_id))
        if not hash_fields:
            return None
        return _hash_to_trigger(hash_fields)

    async def list_for_task(self, task_id: str) -> list[TaskTrigger]:
        trigger_ids = await self._redis.smembers(k.triggers_by_task_set_key(task_id))
        triggers: list[TaskTrigger] = []
        for trigger_id in trigger_ids:
            trig = await self.get(trigger_id)
            if trig is not None:
                triggers.append(trig)
        return triggers

    async def list_for_tasks(self, task_ids: "Sequence[str]") -> dict[str, list[TaskTrigger]]:
        if not task_ids:
            return {}
        # Two pipelined round trips regardless of page size: the id sets, then
        # every trigger hash they name. Per-task calls would be two per row.
        pipe = self._redis.pipeline(transaction=False)
        for task_id in task_ids:
            pipe.smembers(k.triggers_by_task_set_key(task_id))
        id_sets = await pipe.execute()

        owner_by_trigger: dict[str, str] = {}
        for task_id, trigger_ids in zip(task_ids, id_sets, strict=True):
            for trigger_id in trigger_ids or ():
                tid = trigger_id.decode() if isinstance(trigger_id, bytes) else trigger_id
                owner_by_trigger[tid] = task_id
        if not owner_by_trigger:
            return {}

        pipe = self._redis.pipeline(transaction=False)
        for trigger_id in owner_by_trigger:
            pipe.hgetall(k.trigger_hash_key(trigger_id))
        hashes = await pipe.execute()

        by_task: dict[str, list[TaskTrigger]] = {}
        for (trigger_id, task_id), hash_fields in zip(owner_by_trigger.items(), hashes, strict=True):
            if not hash_fields:
                # Deleted between the two reads, or a stale set member.
                logger.debug("trigger %s indexed for task %s but missing", trigger_id, task_id)
                continue
            by_task.setdefault(task_id, []).append(_hash_to_trigger(hash_fields))
        return by_task

    async def delete(self, trigger_id: str) -> bool:
        trigger = await self.get(trigger_id)
        if trigger is None:
            return False
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(k.trigger_hash_key(trigger_id))
            pipe.zrem(k.triggers_due_zset_key(), trigger_id)
            pipe.zrem(k.triggers_lease_zset_key(), trigger_id)
            pipe.srem(k.triggers_by_task_set_key(trigger.task_id), trigger_id)
            if trigger.kind == TriggerKind.WEBHOOK and trigger.webhook_id:
                pipe.delete(k.webhook_trigger_key(trigger.webhook_id))
            if trigger.kind == TriggerKind.EVENT and trigger.event_filter:
                event_type = trigger.event_filter.get("event_type")
                if event_type:
                    pipe.srem(k.event_triggers_set_key(trigger.org_id, str(event_type)), trigger_id)
            await pipe.execute()
        return True

    async def delete_for_task(self, task_id: str) -> int:
        trigger_ids = await self._redis.smembers(k.triggers_by_task_set_key(task_id))
        count = 0
        for trigger_id in trigger_ids:
            if await self.delete(trigger_id):
                count += 1
        await self._redis.delete(k.triggers_by_task_set_key(task_id))
        return count

    async def claim_due(
        self, *, now: datetime, owner: str, limit: int, lease_seconds: float,
    ) -> list[TaskTrigger]:
        now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
        lease_ms = max(1, int(lease_seconds * 1000))
        claimed_ids: list[str] = await self._claim_script(
            keys=[k.triggers_due_zset_key(), k.triggers_lease_zset_key()],
            args=[f"{k.PREFIX}:trigger:", now_ms, owner, lease_ms, limit],
        )
        triggers: list[TaskTrigger] = []
        for trigger_id in claimed_ids:
            trig = await self.get(trigger_id)
            if trig is not None:
                triggers.append(trig)
        return triggers

    async def complete_claim(self, *, trigger_id: str, owner: str, next_run_at: str | None) -> None:
        next_run_at_ms = str(datetime_to_epoch_ms(next_run_at)) if next_run_at else ""
        await self._complete_script(
            keys=[k.triggers_due_zset_key(), k.triggers_lease_zset_key()],
            args=[k.trigger_hash_key(trigger_id), trigger_id, owner, next_run_at_ms],
        )

    async def reap_expired_leases(self, *, now: datetime) -> int:
        now_ms = int(now.astimezone(timezone.utc).timestamp() * 1000)
        reaped: int = await self._reap_script(
            keys=[k.triggers_due_zset_key(), k.triggers_lease_zset_key()],
            args=[f"{k.PREFIX}:trigger:", now_ms],
        )
        return int(reaped)

    async def claim_fire(
        self, trigger_id: str, *, fire_at: str, dedupe_token: str,
    ) -> int | None:
        new_count: int = await self._claim_fire_script(
            keys=[
                k.trigger_hash_key(trigger_id),
                k.trigger_fire_token_key(trigger_id, dedupe_token),
            ],
            args=[str(datetime_to_epoch_ms(fire_at)), _FIRE_TOKEN_TTL_S],
        )
        return None if int(new_count) < 0 else int(new_count)

    async def get_by_webhook_id(self, webhook_id: str) -> TaskTrigger | None:
        trigger_id = await self._redis.get(k.webhook_trigger_key(webhook_id))
        if not trigger_id:
            return None
        return await self.get(trigger_id)

    async def list_by_event_type(self, org_id: str, event_type: str) -> list[TaskTrigger]:
        trigger_ids = await self._redis.smembers(k.event_triggers_set_key(org_id, event_type))
        triggers: list[TaskTrigger] = []
        for trigger_id in trigger_ids:
            trig = await self.get(trigger_id)
            if trig is not None and trig.enabled:
                triggers.append(trig)
        return triggers
