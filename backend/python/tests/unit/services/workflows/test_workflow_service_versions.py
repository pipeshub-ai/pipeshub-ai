"""Unit tests for `WorkflowService.list_versions`.

BUG-1: a version-store failure used to come back as an empty list,
indistinguishable from "no versions exist yet" -- the exact ambiguity that
made the "Generate Code" button show up for workflows whose code failed to
load rather than never having been generated. `list_versions` must now raise
`VersionStoreUnavailableError` so the REST layer can answer 503 instead of a
misleading 200.

Fully mocked -- no ArangoDB, no Neo4j.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.workflows.application.workflow_service import WorkflowService
from app.services.workflows.domain.errors import VersionStoreUnavailableError


def _make_task() -> MagicMock:
    task = MagicMock()
    task.task_id = "wf-1"
    task.org_id = "org-1"
    return task


class TestListVersions:
    @pytest.mark.asyncio
    async def test_raises_version_store_unavailable_on_store_failure(self) -> None:
        engine = MagicMock()
        engine.get = AsyncMock(return_value=_make_task())
        version_store = MagicMock()
        version_store.list_for_workflow = AsyncMock(side_effect=RuntimeError("graph down"))

        service = WorkflowService(task_engine=engine, version_store=version_store)

        with pytest.raises(VersionStoreUnavailableError, match="wf-1"):
            await service.list_versions(workflow_id="wf-1", org_id="org-1")

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_store_genuinely_has_none(self) -> None:
        """Distinct from the failure case: a working store with zero rows is
        a normal, successful outcome and must not raise."""
        engine = MagicMock()
        engine.get = AsyncMock(return_value=_make_task())
        version_store = MagicMock()
        version_store.list_for_workflow = AsyncMock(return_value=[])

        service = WorkflowService(task_engine=engine, version_store=version_store)

        result = await service.list_versions(workflow_id="wf-1", org_id="org-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_version_store_configured(self) -> None:
        engine = MagicMock()
        service = WorkflowService(task_engine=engine)

        result = await service.list_versions(workflow_id="wf-1", org_id="org-1")
        assert result == []
        engine.get.assert_not_called()
