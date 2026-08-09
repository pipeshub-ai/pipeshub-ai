"""Unit tests for the shared ``run_codegen`` and ``upsert_declarative_triggers``
helpers in ``_shared.py``.

These are the canonical codegen helpers used by both ``workflow_manage`` (and
formerly ``task_manage``). Tests use the same fakes as
``test_workflow_manage_codegen.py`` to keep the doubles consistent.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.agent_loop.tools.tasks._shared import (
    run_codegen,
    upsert_declarative_triggers,
)
from app.services.workflows.domain.models import ArtifactRef, WorkflowVersion


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

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
    def __init__(self, *, ok: bool = True, source: str = "@workflow\ndef f(ctx): pass", errors: list | None = None, raise_exc: Exception | None = None) -> None:
        self._ok = ok
        self._source = source
        self._errors = errors or []
        self._raise_exc = raise_exc

    async def generate(self, **kwargs: Any) -> dict[str, Any]:
        if self._raise_exc:
            raise self._raise_exc
        if not self._ok:
            return {"ok": False, "source": "", "errors": self._errors}
        return {"ok": True, "source": self._source, "ir": {"nodes": [], "edges": [], "entry_node_id": None}}


# ---------------------------------------------------------------------------
# run_codegen
# ---------------------------------------------------------------------------

class TestRunCodegen:
    @pytest.mark.asyncio
    async def test_returns_ok_and_pinned_on_success(self) -> None:
        engine = MagicMock()
        engine.update_fields = AsyncMock()
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(),
            version_store=FakeVersionStore(),
            engine=engine,
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="do the thing",
        )
        assert result["ok"] is True
        assert result["pinned"] is True
        assert result["version_number"] == 1

    @pytest.mark.asyncio
    async def test_returns_not_ok_when_spec_is_empty(self) -> None:
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(),
            version_store=FakeVersionStore(),
            engine=MagicMock(),
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="",
        )
        assert result["ok"] is False
        assert "No instructions" in result["failure_reason"]

    @pytest.mark.asyncio
    async def test_returns_not_ok_when_spec_is_none(self) -> None:
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(),
            version_store=FakeVersionStore(),
            engine=MagicMock(),
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec=None,
        )
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_returns_not_ok_when_generation_fails(self) -> None:
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(ok=False, errors=[{"code": "E001", "message": "bad spec"}]),
            code_store=FakeCodeStore(),
            version_store=FakeVersionStore(),
            engine=MagicMock(),
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="do the thing",
        )
        assert result["ok"] is False
        assert result["pinned"] is False
        assert result["failure_reason"]

    @pytest.mark.asyncio
    async def test_returns_not_ok_when_builder_raises(self) -> None:
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(raise_exc=RuntimeError("LLM timeout")),
            code_store=FakeCodeStore(),
            version_store=FakeVersionStore(),
            engine=MagicMock(),
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="do the thing",
        )
        assert result["ok"] is False
        assert "LLM timeout" in result["failure_reason"]

    @pytest.mark.asyncio
    async def test_pin_failure_returns_ok_but_not_pinned(self) -> None:
        engine = MagicMock()
        engine.update_fields = AsyncMock(side_effect=RuntimeError("engine unreachable"))
        version_store = FakeVersionStore()
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(),
            code_store=FakeCodeStore(),
            version_store=version_store,
            engine=engine,
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="do the thing",
        )
        assert result["ok"] is True
        assert result["pinned"] is False
        assert result["version_id"] in version_store.versions
        assert "engine unreachable" in result["failure_reason"]

    @pytest.mark.asyncio
    async def test_persist_failure_returns_not_ok(self) -> None:
        bad_code_store = MagicMock()
        bad_code_store.put = AsyncMock(side_effect=RuntimeError("disk full"))
        result = await run_codegen(
            workflow_builder=FakeWorkflowBuilder(),
            code_store=bad_code_store,
            version_store=FakeVersionStore(),
            engine=MagicMock(),
            org_id="org-1",
            user_id="user-1",
            workflow_id="wf-1",
            spec="do the thing",
        )
        assert result["ok"] is False
        assert "disk full" in result["failure_reason"]


# ---------------------------------------------------------------------------
# upsert_declarative_triggers
# ---------------------------------------------------------------------------

class TestUpsertDeclarativeTriggers:
    @pytest.mark.asyncio
    async def test_no_op_when_source_has_no_triggers(self) -> None:
        engine = MagicMock()
        engine.list_triggers = AsyncMock(return_value=[])
        await upsert_declarative_triggers(
            engine=engine,
            org_id="org-1",
            workflow_id="wf-1",
            source="@workflow\ndef f(ctx): pass",
        )
        engine.add_trigger.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_extraction_errors(self) -> None:
        """If the IR extractor can't parse the source it should not propagate."""
        engine = MagicMock()
        await upsert_declarative_triggers(
            engine=engine,
            org_id="org-1",
            workflow_id="wf-1",
            source="this is not valid python",
        )
