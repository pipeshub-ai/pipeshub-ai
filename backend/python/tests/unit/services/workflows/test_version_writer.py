"""Unit tests for `WorkflowVersionWriter.persist`.

BUG-2: a version whose pin failed used to be deleted along with its source,
even though the code had been generated and stored successfully. The result
was a workflow stuck showing "Generate Code" despite a real, if inactive,
version existing moments before. `persist` must now retain the version and
raise `PinFailedError` (carrying the saved version) instead.

Fully mocked collaborators -- no ArangoDB, no Neo4j, no TaskEngine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.workflows.application.version_writer import WorkflowVersionWriter
from app.services.workflows.codegen.verifier import CURRENT_VERIFIER_VERSION
from app.services.workflows.domain.errors import PinFailedError, WorkflowVersionConflictError
from app.services.workflows.domain.models import ArtifactRef, WorkflowIR, WorkflowVersion


def _saved_version() -> WorkflowVersion:
    return WorkflowVersion(
        version_id="ver-1",
        version_number=1,
        workflow_id="wf-1",
        org_id="org-1",
        bundle_ref=ArtifactRef(artifact_id="art-1"),
        content_hash="h",
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by_user_id="u-1",
    )


class _Deps:
    def __init__(self) -> None:
        self.version_store = MagicMock()
        self.version_store.save = AsyncMock(return_value=_saved_version())
        self.version_store.delete = AsyncMock(return_value=True)

        self.code_store = MagicMock()
        self.code_store.put = AsyncMock(return_value=ArtifactRef(artifact_id="art-1"))
        self.code_store.delete = AsyncMock(return_value=True)

        self.engine = MagicMock()
        self.engine.update_fields = AsyncMock(side_effect=RuntimeError("engine unreachable"))


def _writer(deps: _Deps) -> WorkflowVersionWriter:
    return WorkflowVersionWriter(
        version_store=deps.version_store, code_store=deps.code_store, task_engine=deps.engine,
    )


class TestPersistPinFailure:
    @pytest.mark.asyncio
    async def test_retains_the_version_when_pin_fails(self) -> None:
        deps = _Deps()
        writer = _writer(deps)

        with pytest.raises(PinFailedError):
            await writer.persist(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(),
            )

        deps.version_store.delete.assert_not_awaited()
        deps.code_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pin_failed_error_carries_the_saved_version_and_cause(self) -> None:
        deps = _Deps()
        writer = _writer(deps)

        with pytest.raises(PinFailedError) as exc_info:
            await writer.persist(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(),
            )

        assert exc_info.value.version.version_id == "ver-1"
        assert isinstance(exc_info.value.__cause__, RuntimeError)

    @pytest.mark.asyncio
    async def test_a_concurrency_conflict_is_also_a_pin_failure_not_a_discard(self) -> None:
        """A conflicting pin is not a transient outage, but the same
        retention rule applies: the code was generated, so it stays listed
        rather than vanishing as if generation itself had failed."""
        deps = _Deps()
        deps.engine.update_fields = AsyncMock()
        deps.engine.get = AsyncMock(
            return_value=MagicMock(workflow_version_id="ver-SOMEONE-ELSE")
        )
        writer = _writer(deps)

        with pytest.raises(PinFailedError) as exc_info:
            await writer.persist(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(),
                expected_current_version_id="ver-1",
            )

        assert isinstance(exc_info.value.__cause__, WorkflowVersionConflictError)
        deps.version_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_successful_pin_returns_the_version_normally(self) -> None:
        deps = _Deps()
        deps.engine.update_fields = AsyncMock()
        writer = _writer(deps)

        result = await writer.persist(
            workflow_id="wf-1", org_id="org-1", user_id="u-1",
            source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(),
        )

        assert result.version_id == "ver-1"
        deps.version_store.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_save_still_discards_the_orphaned_source(self) -> None:
        """Unlike a pin failure, a save failure means there is no version at
        all to keep -- the source blob it wrote first must not leak."""
        deps = _Deps()
        deps.version_store.save = AsyncMock(side_effect=RuntimeError("save failed"))
        writer = _writer(deps)

        with pytest.raises(RuntimeError, match="save failed"):
            await writer.persist(
                workflow_id="wf-1", org_id="org-1", user_id="u-1",
                source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(),
            )

        deps.code_store.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pin_false_skips_pinning_and_returns_unpinned(self) -> None:
        deps = _Deps()
        writer = _writer(deps)

        result = await writer.persist(
            workflow_id="wf-1", org_id="org-1", user_id="u-1",
            source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(), pin=False,
        )

        assert result.version_id == "ver-1"
        deps.engine.update_fields.assert_not_awaited()


class TestPersistStampsVerifierVersion:
    """Phase 4: every persisted version records the verifier version in
    effect at write time, so a later rule change can tell "verified under an
    older rule set" apart from "never verified" without re-running the
    verifier against the whole fleet's history on every deploy."""

    @pytest.mark.asyncio
    async def test_persist_stamps_the_current_verifier_version(self) -> None:
        deps = _Deps()
        writer = _writer(deps)

        await writer.persist(
            workflow_id="wf-1", org_id="org-1", user_id="u-1",
            source="@workflow\ndef f(ctx): pass", ir=WorkflowIR(), pin=False,
        )

        saved_arg: WorkflowVersion = deps.version_store.save.call_args.args[0]
        assert saved_arg.verifier_version == CURRENT_VERIFIER_VERSION
        assert saved_arg.verified_at is not None
