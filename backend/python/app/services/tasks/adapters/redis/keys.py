"""Redis key namespace constants for the task engine's trigger/run adapters.

Centralized here so the key shape is defined exactly once and both adapters
(and their tests) stay consistent -- same rationale as
`DistributedConcurrencyManager.KEY_PREFIX`.
"""
from __future__ import annotations

import hashlib

PREFIX = "pipeshub:tasks"


def _digest(value: str) -> str:
    """Keeps provider-supplied ids out of the key literal: they carry colons
    and unbounded length, either of which would corrupt the key namespace."""
    return hashlib.sha256(value.encode()).hexdigest()


def trigger_hash_key(trigger_id: str) -> str:
    return f"{PREFIX}:trigger:{trigger_id}"


def trigger_fire_token_key(trigger_id: str, dedupe_token: str) -> str:
    """STRING: the run_count a delivery token was granted, TTL'd. Lets a
    redelivered webhook replay its original claim instead of consuming a
    second one of the trigger's `max_runs`."""
    return f"{PREFIX}:trigger:{trigger_id}:fired:{_digest(dedupe_token)}"


def triggers_due_zset_key() -> str:
    """ZSET: member=trigger_id, score=next_run_at (epoch ms). Only contains
    triggers that are currently claimable (enabled, has a next_run_at, not
    currently leased)."""
    return f"{PREFIX}:triggers:due"


def triggers_lease_zset_key() -> str:
    """ZSET: member=trigger_id, score=lease_expires_at (epoch ms). Contains
    every trigger currently claimed by a scheduler; `reap_expired_leases`
    scans this."""
    return f"{PREFIX}:triggers:leases"


def triggers_by_task_set_key(task_id: str) -> str:
    """SET of trigger_ids belonging to one task -- lets `list_for_task`/
    `delete_for_task` avoid a full scan."""
    return f"{PREFIX}:task_triggers:{task_id}"


def webhook_trigger_key(webhook_id: str) -> str:
    """Value: trigger_id. O(1) index from the public `webhook_id` (URL
    path segment) to the trigger it belongs to -- `get_by_webhook_id`."""
    return f"{PREFIX}:webhook_trigger:{webhook_id}"


def event_triggers_set_key(org_id: str, event_type: str) -> str:
    """SET of trigger_ids: every enabled EVENT trigger in `org_id` whose
    `event_filter.event_type` equals `event_type` -- `list_by_event_type`."""
    return f"{PREFIX}:event_triggers:{org_id}:{event_type}"


def run_hash_key(run_id: str) -> str:
    return f"{PREFIX}:run:{run_id}"


def run_idempotency_key(idempotency_key: str) -> str:
    """Value: run_id. `SETNX`-style create-if-absent target."""
    return f"{PREFIX}:run_idem:{idempotency_key}"


def runs_by_task_zset_key(task_id: str) -> str:
    """ZSET: member=run_id, score=created_at (epoch ms) -- `list_for_task`
    is naturally a recency-ordered listing."""
    return f"{PREFIX}:task_runs:{task_id}"


def runs_lease_zset_key() -> str:
    """ZSET: member=run_id, score=lease_expires_at (epoch ms). Contains
    every RUNNING run currently held by a worker; `reap_abandoned` scans
    this."""
    return f"{PREFIX}:runs:leases"


def runs_pending_zset_key() -> str:
    """ZSET: member=run_id, score=created_at (epoch ms). Contains every run
    still in PENDING status -- the outbox-lite re-publish path scans this
    for entries older than a threshold."""
    return f"{PREFIX}:runs:pending"


def runs_suspension_deadline_zset_key() -> str:
    """ZSET: member=run_id, score=resume_deadline_at (epoch ms). Every
    AWAITING_INPUT run that will outlive its execution journal if nobody
    answers it. Scanned by the reaper so those runs are failed on purpose
    instead of resuming later against a journal that no longer exists."""
    return f"{PREFIX}:runs:suspension_deadlines"


def runs_awaiting_event_set_key(org_id: str, event_type: str) -> str:
    """SET: member=run_id. Every run parked on `ctx.wait_for_event` for this
    event type in this org.

    Without it `fire_event` has no way to find a suspended run, so
    `ctx.wait_for_event` parked a run that nothing could ever resume. Keyed by
    org as well as type so one tenant's event cannot resume another's run."""
    return f"{PREFIX}:runs:awaiting_event:{org_id}:{event_type}"
