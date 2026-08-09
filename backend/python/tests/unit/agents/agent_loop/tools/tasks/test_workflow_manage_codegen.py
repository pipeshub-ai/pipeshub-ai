"""Unit tests for `WorkflowManageTool._run_codegen` and its callers.

BUG-2 / Phase 2 & 5: a version whose pin failed must be reported distinctly
from one that never generated at all -- the code was still saved and is
listable, so collapsing both into a single "Code generation failed" note
would tell the user their work was lost when it was not.

Uses the same fakes as `tests/integration/test_code_workflow_e2e.py` but
in isolation, without the rest of the engine/executor stack.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.tools.tasks.workflow_manage import WorkflowManageTool
from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal, TaskStatus
from app.services.workflows.domain.models import ArtifactRef, WorkflowVersion


class FakeCodeStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, workflow_id: str, org_id: str, source: bytes, *, content_type: str = "text/x-python") -> ArtifactRef:
        artifact_id = f"art-{len(self.blobs) + 1}"
        self.blobs[artifact_id] = source
        return ArtifactRef(artifact_id=artifact_id)

    async def get(self, ref: ArtifactRef) -> bytes:
        return self.blobs[ref.artifact_id]

    async def delete(self, ref: ArtifactRef) -> bool:
        return self.blobs.pop(ref.artifact_id, None) is not None


class FakeVersionStore:
    def __init__(self) -> None:
        self.versions: dict[str, WorkflowVersion] = {}

    async def save(self, version: WorkflowVersion) -> WorkflowVersion:
        stored = version.model_copy(update={"version_number": len(self.versions) + 1})
        self.versions[stored.version_id] = stored
        return stored

    async def get(self, version_id: str, org_id: str) -> WorkflowVersion | None:
        return self.versions.get(version_id)

    async def list_for_workflow(self, workflow_id: str, org_id: str, *, limit: int = 20, offset: int = 0) -> list[WorkflowVersion]:
        return [v for v in self.versions.values() if v.workflow_id == workflow_id]

    async def get_latest(self, workflow_id: str, org_id: str) -> WorkflowVersion | None:
        rows = await self.list_for_workflow(workflow_id, org_id)
        return rows[0] if rows else None

    async def delete(self, version_id: str, org_id: str) -> bool:
        return self.versions.pop(version_id, None) is not None


class FakeWorkflowBuilder:
    def __init__(self, *, ok: bool = True, source: str = "@workflow\ndef f(ctx): pass", errors: list | None = None) -> None:
        self._ok = ok
        self._source = source
        self._errors = errors or []

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        if not self._ok:
            return {"ok": False, "source": "", "errors": self._errors}
        return {"ok": True, "source": self._source, "ir": {"nodes": [], "edges": [], "entry_node_id": None}}


def _make_task() -> TaskDefinition:
    return TaskDefinition(
        org_id="org-1", created_by_user_id="user-1",
        principal=TaskPrincipal(org_id="org-1", user_id="user-1", user_email="u@example.com"),
        title="t", description="d", instructions="do the thing", status=TaskStatus.ACTIVE,
    )


def _tool(*, engine: Any, builder: Any, code_store: Any, version_store: Any) -> WorkflowManageTool:
    return WorkflowManageTool(
        engine, org_id="org-1", user_id="user-1", user_email="u@example.com",
        graph_provider=None, config_service=None,
        workflow_builder=builder, code_store=code_store, version_store=version_store,
    )


class TestRunCodegenOutcomes:
    @pytest.mark.asyncio
    async def test_reports_ok_and_pinned_on_success(self) -> None:
        engine = MagicMock()
        engine.update_fields = AsyncMock()
        tool = _tool(
            engine=engine, builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(), version_store=FakeVersionStore(),
        )

        result = await tool._run_codegen(workflow_id="wf-1", spec="do the thing")

        assert result["ok"] is True
        assert result["pinned"] is True
        assert result["version_number"] == 1
        engine.update_fields.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_distinguishes_pin_failure_from_generation_failure(self) -> None:
        engine = MagicMock()
        engine.update_fields = AsyncMock(side_effect=RuntimeError("engine unreachable"))
        version_store = FakeVersionStore()
        tool = _tool(
            engine=engine, builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(), version_store=version_store,
        )

        result = await tool._run_codegen(workflow_id="wf-1", spec="do the thing")

        assert result["ok"] is True
        assert result["pinned"] is False
        assert result["version_id"] in version_store.versions
        assert "engine unreachable" in result["failure_reason"]

    @pytest.mark.asyncio
    async def test_reports_not_ok_when_generation_itself_fails(self) -> None:
        engine = MagicMock()
        builder = FakeWorkflowBuilder(ok=False, errors=[{"code": "E001", "message": "bad spec"}])
        tool = _tool(
            engine=engine, builder=builder,
            code_store=FakeCodeStore(), version_store=FakeVersionStore(),
        )

        result = await tool._run_codegen(workflow_id="wf-1", spec="do the thing")

        assert result["ok"] is False
        assert result["pinned"] is False
        assert "version_id" not in result
        assert result["failure_reason"]


class TestCreateSurfacesCodegenOutcome:
    @pytest.mark.asyncio
    async def test_pin_failure_leaves_execution_kind_untouched_with_a_note(self) -> None:
        """The response must not claim `execution_kind: code` for a version
        that never got pinned -- that would tell the caller the workflow is
        running generated code when it is still on the agent-task path."""
        engine = MagicMock()
        engine.update_fields = AsyncMock(side_effect=RuntimeError("engine unreachable"))
        engine.create = AsyncMock(return_value=(_make_task(), [], None, {}))
        version_store = FakeVersionStore()
        tool = _tool(
            engine=engine, builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(), version_store=version_store,
        )

        result = await tool.execute(
            action="create", title="t", description="d", instructions="do the thing",
        )

        assert result.success is True
        assert result.data["execution_kind"] != "code"
        assert result.data["workflow_version_id"] in version_store.versions
        assert "pin" in result.data["codegen_note"].lower()

    @pytest.mark.asyncio
    async def test_full_success_sets_execution_kind_code(self) -> None:
        engine = MagicMock()
        engine.update_fields = AsyncMock()
        engine.create = AsyncMock(return_value=(_make_task(), [], None, {}))
        tool = _tool(
            engine=engine, builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(), version_store=FakeVersionStore(),
        )

        result = await tool.execute(
            action="create", title="t", description="d", instructions="do the thing",
        )

        assert result.success is True
        assert result.data["execution_kind"] == "code"
        assert "codegen_note" not in result.data

    @pytest.mark.asyncio
    async def test_generation_failure_leaves_agent_task_with_a_note(self) -> None:
        engine = MagicMock()
        engine.create = AsyncMock(return_value=(_make_task(), [], None, {}))
        builder = FakeWorkflowBuilder(ok=False, errors=[{"code": "E001"}])
        tool = _tool(
            engine=engine, builder=builder,
            code_store=FakeCodeStore(), version_store=FakeVersionStore(),
        )

        result = await tool.execute(
            action="create", title="t", description="d", instructions="do the thing",
        )

        assert result.success is True
        assert result.data["execution_kind"] == "agent_task"
        assert "workflow_version_id" not in result.data
        assert "codegen_note" in result.data
