"""`IRunArchive` -- durable home for runs that have finished.

`ITaskRunStore` is the live, operational store: claims, leases, pending scans.
Everything it does wants Redis semantics and none of it wants to keep a run
forever. This port is the other half -- write-once records of what happened,
on a store that does not evict.

Kept separate from `ITaskRunStore` rather than added to it so the Redis
adapter is not obliged to implement history and the archive is not obliged to
implement leases. The read methods deliberately mirror `ITaskRunStore`'s
signatures, including their lack of an `org_id`: ownership is checked once, in
`TaskEngine.get_run`, and a second convention here would be a second place for
it to be checked differently.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tasks.domain.models import Page, TaskRun


class IRunArchive(ABC):
    @abstractmethod
    async def archive(self, run: "TaskRun") -> None:
        """Record a terminal run. Must be idempotent: the same run is
        re-archived whenever a write is retried."""
        ...

    @abstractmethod
    async def get(self, run_id: str) -> "TaskRun | None":
        ...

    @abstractmethod
    async def list_for_task(
        self, task_id: str, *, limit: int = 50, offset: int = 0,
    ) -> "Page[TaskRun]":
        """Archived runs for a task, newest first."""
        ...
