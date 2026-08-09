"""IWorkflowVersionStore port."""
from __future__ import annotations

from typing import Protocol

from app.services.workflows.domain.models import WorkflowVersion


class IWorkflowVersionStore(Protocol):
    async def save(self, version: WorkflowVersion) -> WorkflowVersion:
        """Persist a new immutable version, assigning its `version_number`.

        Raises `WorkflowVersionConflictError` if `version_id` already exists:
        versions are the audit trail for what code actually ran, so a silent
        overwrite would rewrite history for any run already pinned to it.
        Returns the stored version (with `version_number` populated).
        """
        ...

    async def get(self, version_id: str, org_id: str) -> WorkflowVersion | None: ...

    async def list_for_workflow(
        self, workflow_id: str, org_id: str, *, limit: int = 20, offset: int = 0
    ) -> list[WorkflowVersion]:
        """Newest first, so page 1 is always the most recent versions."""
        ...

    async def get_latest(self, workflow_id: str, org_id: str) -> WorkflowVersion | None: ...

    async def delete(self, version_id: str, org_id: str) -> bool:
        """Remove a version. Only used to clean up an orphan whose task pin
        failed — never to retire a version a run may reference."""
        ...
