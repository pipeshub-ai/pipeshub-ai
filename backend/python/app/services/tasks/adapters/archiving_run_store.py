"""`ArchivingRunStore` -- makes run history outlive Redis.

`RedisRunStore` is the right store for a run while it is executing, and the
wrong one for a run that has finished. Left alone it keeps every run, forever,
in memory: a task on a five-minute schedule contributes a hash and two sorted
set members roughly a hundred thousand times a year, and none of it is ever
read again after the run appears in the history list once.

Adding a TTL alone would fix the growth and lose the history. So this wraps
the live store and does both: a terminal run is mirrored to an `IRunArchive`
that does not evict, and only then does its Redis copy get an expiry. Reads
fall back to the archive, so a run aging out is invisible to callers.

A decorator rather than changes inside `RedisRunStore` because archiving is a
policy, not a Redis concern, and because the live store's Lua scripts (claim,
resume, heartbeat) must keep operating on exactly the keys they own.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.tasks.domain.models import Page, TaskRun
from app.services.tasks.interface.run_store import ITaskRunStore

if TYPE_CHECKING:
    from datetime import datetime

    from app.services.tasks.interface.run_archive import IRunArchive

__all__ = ["ArchivingRunStore"]

logger = logging.getLogger(__name__)


class ArchivingRunStore(ITaskRunStore):
    def __init__(self, inner: ITaskRunStore, archive: "IRunArchive") -> None:
        self._inner = inner
        self._archive = archive

    async def create_if_absent(self, run: TaskRun) -> TaskRun | None:
        return await self._inner.create_if_absent(run)

    async def get(self, run_id: str) -> TaskRun | None:
        run = await self._inner.get(run_id)
        if run is not None:
            return run
        # Not a miss so much as an aged-out hit: the run finished long enough
        # ago that Redis let it go.
        try:
            return await self._archive.get(run_id)
        except Exception:
            logger.exception("run archive: lookup failed for run %s", run_id)
            return None

    async def get_by_idempotency_key(self, idempotency_key: str) -> TaskRun | None:
        return await self._inner.get_by_idempotency_key(idempotency_key)

    async def update(self, run: TaskRun, *, expected_owner: str | None = None) -> TaskRun | None:
        updated = await self._inner.update(run, expected_owner=expected_owner)
        if updated is not None and updated.is_terminal():
            await self._archive_terminal(updated)
        return updated

    async def claim_for_execution(
        self, run_id: str, *, owner: str, lease_seconds: float,
    ) -> TaskRun | None:
        return await self._inner.claim_for_execution(
            run_id, owner=owner, lease_seconds=lease_seconds,
        )

    async def resume_with_answer(self, run_id: str, *, answer: str) -> TaskRun | None:
        return await self._inner.resume_with_answer(run_id, answer=answer)

    async def heartbeat(self, run_id: str, owner: str, lease_seconds: float) -> bool:
        return await self._inner.heartbeat(run_id, owner, lease_seconds)

    async def list_expired_suspensions(self, *, now: "datetime", limit: int = 100) -> list[TaskRun]:
        return await self._inner.list_expired_suspensions(now=now, limit=limit)

    async def reap_abandoned(self, *, now: "datetime") -> list[TaskRun]:
        reaped = await self._inner.reap_abandoned(now=now)
        for run in reaped:
            # ABANDONED is terminal and is reached without any `update` call,
            # so it would otherwise be the one outcome never archived -- and
            # it is the one an operator most wants to find later.
            await self._archive_terminal(run)
        return reaped

    async def list_for_task(
        self, task_id: str, *, limit: int = 50, offset: int = 0,
    ) -> Page[TaskRun]:
        """The archive is authoritative for history; Redis contributes the
        runs that have not reached it yet.

        Reading Redis alone would silently shorten a task's history to its
        TTL. Reading the archive alone would omit the in-flight run the user
        is most likely looking at. So: archive page first, then any live run
        the archive does not have, newest first.
        """
        try:
            archived = await self._archive.list_for_task(task_id, limit=limit, offset=offset)
        except Exception:
            logger.exception("run archive: list failed for task %s, serving live runs only", task_id)
            return await self._inner.list_for_task(task_id, limit=limit, offset=offset)

        live = await self._inner.list_for_task(task_id, limit=limit + offset, offset=0)
        archived_ids = {run.run_id for run in archived.items}
        unarchived = [run for run in live.items if run.run_id not in archived_ids]
        if not unarchived:
            return archived

        merged = sorted(
            unarchived + archived.items, key=lambda run: run.created_at, reverse=True,
        )
        # `total` counts each run once: live runs already in the archive are
        # not extra, only the ones the archive has never seen.
        total = archived.total + len(
            [run for run in live.items if run.run_id not in archived_ids],
        )
        return Page(items=merged[:limit], total=total, limit=limit, offset=offset)

    async def list_pending(
        self, *, now: "datetime", older_than_seconds: float, limit: int = 100,
    ) -> list[TaskRun]:
        return await self._inner.list_pending(
            now=now, older_than_seconds=older_than_seconds, limit=limit,
        )

    async def list_awaiting_event(self, org_id: str, event_type: str) -> list[TaskRun]:
        return await self._inner.list_awaiting_event(org_id, event_type)

    async def _archive_terminal(self, run: TaskRun) -> None:
        """Mirror, then hand the live store permission to expire it.

        Order matters and is not just tidiness: expiring first would leave a
        window in which the run exists nowhere, and a crash inside it would
        lose the record permanently.
        """
        try:
            await self._archive.archive(run)
        except Exception:
            # Keeping the run in Redis un-expired is the safe failure: it
            # costs memory, but the history is still readable and the next
            # terminal write for this run will retry the mirror.
            logger.exception(
                "run archive: could not archive terminal run %s (task=%s); leaving it in Redis",
                run.run_id, run.task_id,
            )
            return

        expire = getattr(self._inner, "expire_terminal", None)
        if expire is None:
            return
        try:
            await expire(run)
        except Exception:
            logger.exception("run archive: could not set expiry on run %s", run.run_id)
