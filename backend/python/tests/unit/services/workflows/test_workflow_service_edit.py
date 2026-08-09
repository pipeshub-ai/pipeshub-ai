"""Unit tests for the review-first edit path on `WorkflowService`.

The invariant these pin down: `edit_workflow` PROPOSES and persists nothing,
`commit_version` is the only thing that makes source live, and
`activate_version` re-pins an existing version. Anything that lets `/edit`
write makes the UI's "Discard" button a lie, since the discarded code would
already be what the next scheduled run executes.

Fully mocked stores/builder -- no ArangoDB, no LLM.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tasks.domain.errors import TaskNotFoundError
from app.services.workflows.application.workflow_service import WorkflowService
from app.services.workflows.domain.errors import (
    PinFailedError,
    WorkflowCodegenError,
    WorkflowNotFoundError,
    WorkflowVersionConflictError,
    WorkflowVersionNotFoundError,
)
from app.services.workflows.domain.models import (
    ArtifactRef,
    WorkflowIR,
    WorkflowVersion,
)

_GENERATED = "@workflow\ndef my_workflow(ctx): pass"


def _make_task(workflow_version_id: str | None = None) -> MagicMock:
    task = MagicMock()
    task.task_id = "wf-1"
    task.org_id = "org-1"
    task.workflow_version_id = workflow_version_id
    task.execution_kind = "agent_task"
    task.status = MagicMock(value="active")
    task.title = "My Workflow"
    task.created_by_user_id = "u-1"
    task.created_from_conversation_id = None
    task.tool_names = []
    return task


def _make_version(version_id: str | None = None, *, version_number: int = 1) -> WorkflowVersion:
    return WorkflowVersion(
        version_id=version_id or str(uuid.uuid4()),
        version_number=version_number,
        workflow_id="wf-1",
        org_id="org-1",
        bundle_ref=ArtifactRef(artifact_id="art-old"),
        content_hash=hashlib.sha256(b"old_source_bytes").hexdigest(),
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by_user_id="u-1",
    )


class _Deps:
    """The mocked collaborators, kept together so tests can assert on any."""

    def __init__(self, task: MagicMock, old_source: bytes, gen_result: dict[str, Any]) -> None:
        self.task = task
        self.engine = MagicMock()
        self.engine.get = AsyncMock(return_value=task)
        self.engine.update_fields = AsyncMock(return_value=task)

        self.saved_version = _make_version()
        self.version_store = MagicMock()
        self.version_store.get = AsyncMock(
            return_value=_make_version(task.workflow_version_id)
            if task.workflow_version_id
            else None
        )
        self.version_store.list_for_workflow = AsyncMock(return_value=[self.saved_version])
        self.version_store.save = AsyncMock(return_value=self.saved_version)
        self.version_store.delete = AsyncMock(return_value=True)

        self.code_store = MagicMock()
        self.code_store.get = AsyncMock(return_value=old_source)
        self.code_store.put = AsyncMock(return_value=ArtifactRef(artifact_id="art-new"))
        self.code_store.delete = AsyncMock(return_value=True)

        self.builder = MagicMock()
        self.builder.generate = AsyncMock(return_value=gen_result)


def _make_service(
    task: MagicMock,
    *,
    old_source: bytes = b"def old(): pass",
    gen_result: dict[str, Any] | None = None,
    with_builder: bool = True,
) -> tuple[WorkflowService, _Deps]:
    deps = _Deps(
        task,
        old_source,
        gen_result
        or {
            "ok": True,
            "source": _GENERATED,
            "ir": {"nodes": [], "edges": [], "entry_node_id": None},
            "errors": [],
        },
    )
    service = WorkflowService(
        task_engine=deps.engine,
        version_store=deps.version_store,
        code_store=deps.code_store,
        builder_agent=deps.builder if with_builder else None,
    )
    return service, deps


def _patched_codegen() -> Any:
    """Verifier + IR extractor stubbed so tests don't depend on real codegen."""
    verify = patch(
        "app.services.workflows.codegen.verifier.verify_workflow_source",
        return_value=MagicMock(ok=True, to_dict=lambda: {"errors": []}),
    )
    extract = patch(
        "app.services.workflows.ir.extractor.extract_ir",
        return_value=WorkflowIR(nodes=[], edges=[], entry_node_id=None),
    )
    return verify, extract


class TestEditWorkflow:
    @pytest.mark.asyncio
    async def test_raises_when_no_stores_configured(self) -> None:
        service = WorkflowService(task_engine=MagicMock())
        with pytest.raises(WorkflowCodegenError):
            await service.edit_workflow(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", instructions="Add step"
            )

    @pytest.mark.asyncio
    async def test_raises_workflow_not_found_when_engine_has_no_such_task(self) -> None:
        service, deps = _make_service(_make_task())
        deps.engine.get.side_effect = TaskNotFoundError("wf-1")
        with pytest.raises(WorkflowNotFoundError):
            await service.edit_workflow(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", instructions="Add step"
            )

    @pytest.mark.asyncio
    async def test_raises_when_no_builder_agent(self) -> None:
        service, _ = _make_service(_make_task(), with_builder=False)
        with pytest.raises(WorkflowCodegenError, match="builder agent"):
            await service.edit_workflow(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", instructions="Add step"
            )

    @pytest.mark.asyncio
    async def test_raises_when_codegen_fails(self) -> None:
        service, _ = _make_service(
            _make_task(),
            gen_result={"ok": False, "source": "", "errors": [{"code": "E001"}]},
        )
        with pytest.raises(WorkflowCodegenError, match="Code generation failed"):
            await service.edit_workflow(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", instructions="Add step"
            )

    @pytest.mark.asyncio
    async def test_returns_proposal_and_persists_nothing(self) -> None:
        """The review-first contract: a proposal, with no version written and
        no pin moved -- otherwise Discard cannot undo the edit."""
        task = _make_task(workflow_version_id="ver-1")
        service, deps = _make_service(task, old_source=b"def old_workflow(): pass")

        verify, extract = _patched_codegen()
        with verify, extract:
            result = await service.edit_workflow(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", instructions="Add Slack step"
            )

        assert result["source"] == _GENERATED
        assert result["previousSource"] == "def old_workflow(): pass"
        assert result["baseVersionId"] == "ver-1"
        assert "ir" in result
        assert "versionId" not in result

        deps.builder.generate.assert_awaited_once()
        deps.version_store.save.assert_not_awaited()
        deps.code_store.put.assert_not_awaited()
        deps.engine.update_fields.assert_not_awaited()


class TestCommitVersion:
    @pytest.mark.asyncio
    async def test_persists_source_and_pins_it(self) -> None:
        task = _make_task(workflow_version_id="ver-1")
        service, deps = _make_service(task)

        verify, extract = _patched_codegen()
        with verify, extract:
            saved = await service.commit_version(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source=_GENERATED, base_version_id="ver-1",
            )

        assert saved is deps.saved_version
        deps.code_store.put.assert_awaited_once()
        deps.version_store.save.assert_awaited_once()
        deps.engine.update_fields.assert_awaited_once_with(
            "wf-1", "org-1", execution_kind="code", workflow_version_id=saved.version_id,
        )

    @pytest.mark.asyncio
    async def test_rejects_commit_when_pin_moved_under_it(self) -> None:
        """Concurrent edit: the task now points somewhere else, so this commit
        is refused rather than clobbering the winner. The generated code was
        still successfully saved, though, so it must remain listable/
        activatable rather than being discarded as an orphan (BUG-2 fix)."""
        task = _make_task(workflow_version_id="ver-SOMEONE-ELSE")
        service, deps = _make_service(task)

        verify, extract = _patched_codegen()
        with verify, extract, pytest.raises(PinFailedError) as exc_info:
            await service.commit_version(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source=_GENERATED, base_version_id="ver-1",
            )

        assert isinstance(exc_info.value.__cause__, WorkflowVersionConflictError)
        assert exc_info.value.version is deps.saved_version
        deps.engine.update_fields.assert_not_awaited()
        # The version that could not be pinned is a real, successfully
        # generated artifact -- it must survive so it can be listed and
        # activated later, not be deleted as if generation itself failed.
        deps.version_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_source_that_fails_verification(self) -> None:
        service, deps = _make_service(_make_task())
        with patch(
            "app.services.workflows.codegen.verifier.verify_workflow_source",
            return_value=MagicMock(ok=False, to_dict=lambda: {"errors": [{"code": "E9"}]}),
        ), pytest.raises(WorkflowCodegenError, match="verification"):
            await service.commit_version(
                workflow_id="wf-1", org_id="org-1", user_id="u-1", source="import os",
            )
        deps.version_store.save.assert_not_awaited()


class TestActivateVersion:
    @pytest.mark.asyncio
    async def test_repins_an_existing_version(self) -> None:
        task = _make_task(workflow_version_id="ver-current")
        service, deps = _make_service(task)
        target = _make_version("ver-old", version_number=3)
        deps.version_store.get = AsyncMock(return_value=target)

        result = await service.activate_version(
            workflow_id="wf-1", version_id="ver-old", org_id="org-1", user_id="u-1",
        )

        assert result is target
        deps.engine.update_fields.assert_awaited_once_with(
            "wf-1", "org-1", execution_kind="code", workflow_version_id="ver-old",
        )
        # Rollback re-points at existing bytes; it must not write a new version.
        deps.version_store.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rejects_a_version_belonging_to_another_workflow(self) -> None:
        service, deps = _make_service(_make_task())
        foreign = _make_version("ver-x")
        foreign = foreign.model_copy(update={"workflow_id": "wf-OTHER"})
        deps.version_store.get = AsyncMock(return_value=foreign)

        with pytest.raises(WorkflowVersionNotFoundError):
            await service.activate_version(
                workflow_id="wf-1", version_id="ver-x", org_id="org-1", user_id="u-1",
            )
        deps.engine.update_fields.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_version_is_not_found(self) -> None:
        service, deps = _make_service(_make_task())
        deps.version_store.get = AsyncMock(return_value=None)
        with pytest.raises(WorkflowVersionNotFoundError):
            await service.activate_version(
                workflow_id="wf-1", version_id="nope", org_id="org-1", user_id="u-1",
            )
