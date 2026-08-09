"""`ITaskStore` -- persistence port for `TaskDefinition`.

The reference adapter is graph-backed (`adapters/graph/task_store.py`, over
`IGraphDBProvider`), chosen because task definitions have real relationship
edges (task-to-toolset, task-to-connector, task-to-KB, task-to-agent,
task-to-conversation) and low write frequency. Nothing in this module or its
callers may assume a graph backend -- see `app/services/tasks/domain/`
boundary rule.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.tasks.domain.models import Page, TaskDefinition, TaskQuery


class ITaskStore(ABC):
    @abstractmethod
    async def create(self, task: TaskDefinition) -> TaskDefinition:
        """Persist a new task. `task.revision` must be 0. Raises
        `TaskEngineError` subtypes on duplicate `task_id` (should not
        happen given uuid4 defaults, but adapters must guard it)."""
        ...

    @abstractmethod
    async def get(self, task_id: str, org_id: str) -> TaskDefinition | None:
        """Returns None if not found OR not owned by `org_id` -- callers
        must never be able to distinguish "wrong org" from "doesn't exist"
        through this port; that distinction is a cross-tenant leak."""
        ...

    @abstractmethod
    async def update(self, task: TaskDefinition, *, expected_revision: int) -> TaskDefinition:
        """Optimistic-concurrency update. Raises
        `OptimisticConcurrencyError` if the stored revision does not match
        `expected_revision`. On success, returns the task with
        `revision = expected_revision + 1`."""
        ...

    @abstractmethod
    async def delete(self, task_id: str, org_id: str) -> bool:
        """Hard delete. Callers wanting "cancel" semantics should instead
        `update()` the task to `TaskStatus.CANCELLED` -- this method exists
        for GDPR-style purges and test cleanup, not everyday task
        management."""
        ...

    @abstractmethod
    async def list(self, query: TaskQuery) -> Page[TaskDefinition]:
        ...
