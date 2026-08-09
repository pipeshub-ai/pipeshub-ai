"""`ITriggerStore` -- persistence port for `TaskTrigger`.

The reference adapter is Redis-backed (`adapters/redis/trigger_store.py`):
`claim_due` needs atomic test-and-set with lease expiry, which Redis sorted
sets + a Lua script provide for free (the exact `ZADD`/`ZREMRANGEBYSCORE`
pattern already proven by `DistributedConcurrencyManager` for indexing
concurrency). Nothing in this module or its callers may assume a Redis
backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from app.services.tasks.domain.models import TaskTrigger


class ITriggerStore(ABC):
    @abstractmethod
    async def upsert(self, trigger: TaskTrigger) -> TaskTrigger:
        """Create or replace a trigger by `trigger_id`. Used both at task
        creation and whenever the scheduler reschedules `next_run_at`."""
        ...

    @abstractmethod
    async def get(self, trigger_id: str) -> TaskTrigger | None:
        ...

    @abstractmethod
    async def list_for_task(self, task_id: str) -> list[TaskTrigger]:
        ...

    @abstractmethod
    async def list_for_tasks(self, task_ids: "Sequence[str]") -> dict[str, list[TaskTrigger]]:
        """Triggers for several tasks at once, keyed by task id.

        A workflow list page needs every row's triggers, and calling
        `list_for_task` per row makes that a round trip per workflow. Tasks
        with no triggers are absent from the result rather than mapped to an
        empty list, so callers must default."""
        ...

    @abstractmethod
    async def delete(self, trigger_id: str) -> bool:
        ...

    @abstractmethod
    async def delete_for_task(self, task_id: str) -> int:
        """Removes every trigger belonging to `task_id`. Returns count
        deleted. Called on task deletion/cancellation."""
        ...

    @abstractmethod
    async def claim_due(
        self,
        *,
        now: datetime,
        owner: str,
        limit: int,
        lease_seconds: float,
    ) -> list[TaskTrigger]:
        """Atomically claim up to `limit` triggers whose `next_run_at` <=
        `now` and whose lease is absent or expired, setting `lease_owner`
        and `lease_expires_at` on each in the same operation. The claim
        lease IS the distributed lock -- no separate locking primitive is
        needed by callers. Must be safe under N concurrent callers: each
        trigger is claimed by exactly one caller.
        """
        ...

    @abstractmethod
    async def complete_claim(
        self, *, trigger_id: str, owner: str, next_run_at: str | None
    ) -> None:
        """Release the lease held by `owner` on `trigger_id` and set its
        `next_run_at` to the given value (None means the trigger has no
        more scheduled fires). Must be a no-op (not an error) if `owner`
        no longer holds the lease (it expired and was reaped) -- the
        caller's work is still valid, it just lost the race to update
        state, and the reaper's re-publish takes over.
        """
        ...

    @abstractmethod
    async def reap_expired_leases(self, *, now: datetime) -> int:
        """Clear `lease_owner`/`lease_expires_at` on every trigger whose
        lease has expired without being completed (the claiming scheduler
        crashed mid-tick), making them claimable again. Returns count
        reaped."""
        ...

    @abstractmethod
    async def get_by_webhook_id(self, webhook_id: str) -> TaskTrigger | None:
        """O(1) lookup for inbound webhook dispatch (Phase 8) -- the public
        `webhook_id` in the URL path is never the same value as
        `trigger_id`, so this can't be satisfied by `get()`."""
        ...

    @abstractmethod
    async def claim_fire(
        self, trigger_id: str, *, fire_at: str, dedupe_token: str,
    ) -> int | None:
        """Atomically consume one of `trigger_id`'s remaining `max_runs`,
        returning its new `run_count`, or None if it is already exhausted
        (or gone).

        Event and webhook triggers have no lease to serialize them the way
        `claim_due` serializes scheduled ones: two deliveries can arrive
        concurrently on different workers. Read-check-then-increment would
        let both observe `run_count == max_runs - 1` and both fire, so the
        check and the increment have to happen in one operation.

        A repeat claim for a `dedupe_token` already claimed returns the
        count recorded the first time without consuming another slot, so a
        redelivered provider webhook does not eat a trigger's budget.
        Implementations may forget tokens after a retention window; the
        window only has to outlive a provider's redelivery attempts.

        Also sets `last_fire_at` to `fire_at`, since a caller that claimed a
        fire has by definition fired.
        """
        ...

    @abstractmethod
    async def list_by_event_type(self, org_id: str, event_type: str) -> list[TaskTrigger]:
        """Candidates for `TaskEngine.fire_event` -- every enabled EVENT
        trigger in `org_id` whose `event_filter.event_type` equals
        `event_type`. Callers must still match any additional
        `event_filter` keys against the event payload themselves; this only
        narrows by the one indexed field."""
        ...
