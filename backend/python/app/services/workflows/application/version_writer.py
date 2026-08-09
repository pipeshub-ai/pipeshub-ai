"""One place that turns generated source into a persisted, pinned version.

Both entry points that produce workflow code (the chat tool
`workflow_manage._run_codegen` and the REST `WorkflowService.edit_workflow`
/ commit path) route through here so the three-write sequence
(put source -> save version -> pin on task) has one implementation of its
failure handling: a version whose pin failed is RETAINED (visible via
`/versions`, activatable later via `activate_version`) rather than deleted --
the code was successfully generated and stored, and discarding it just
because the pin lost a race or the engine was briefly unreachable would
throw away a real, listable artifact. The pin itself is conditional so two
concurrent generations cannot silently clobber each other.

Every caller of `persist()` has already run `verify_workflow_source()` on
`source` and only reaches here on success (`WorkflowBuilderAgent.generate()`
for the codegen path, `WorkflowService.commit_version()`/`edit_workflow()`
for the hand-edit path) -- `persist()` does not re-verify. It does stamp the
version with the verifier version in effect at write time, so a rule added
later can tell "verified, predates this rule" apart from "never verified"
without re-running the verifier against the whole fleet's history (see
`codegen.verifier.CURRENT_VERIFIER_VERSION` / `is_version_stale`).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.services.workflows.codegen.verifier import CURRENT_VERIFIER_VERSION
from app.services.workflows.domain.errors import (
    PinFailedError,
    WorkflowVersionConflictError,
)
from app.services.workflows.domain.models import WorkflowVersion
from app.services.workflows.ir.extractor import agent_pins_from_ir, tool_pins_from_ir

if TYPE_CHECKING:
    from logging import Logger

    from app.services.tasks.application.engine import TaskEngine
    from app.services.workflows.domain.models import ArtifactRef, WorkflowIR
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

__all__ = ["WorkflowVersionWriter", "content_hash_of"]

_logger = logging.getLogger(__name__)


def content_hash_of(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


class WorkflowVersionWriter:
    """Persists generated code as an immutable version and pins it."""

    def __init__(
        self,
        *,
        version_store: "IWorkflowVersionStore",
        code_store: "ICodeStore",
        task_engine: "TaskEngine",
        logger: "Logger | None" = None,
    ) -> None:
        self._version_store = version_store
        self._code_store = code_store
        self._engine = task_engine
        self._logger = logger or _logger

    async def persist(
        self,
        *,
        workflow_id: str,
        org_id: str,
        user_id: str,
        source: str,
        ir: "WorkflowIR",
        generation_spec: str | None = None,
        expected_current_version_id: str | None = None,
        pin: bool = True,
    ) -> WorkflowVersion:
        """Store `source` as a new version and (optionally) pin it on the task.

        `expected_current_version_id` makes the pin conditional: if the task
        now points at something else, another generation won the race and this
        one is rejected instead of overwriting it.
        """
        source_bytes = source.encode("utf-8")
        ref: ArtifactRef = await self._code_store.put(workflow_id, org_id, source_bytes)
        now = datetime.now(timezone.utc).isoformat()

        try:
            saved = await self._version_store.save(WorkflowVersion(
                workflow_id=workflow_id,
                org_id=org_id,
                bundle_ref=ref,
                ir=ir,
                tool_pins=tool_pins_from_ir(ir),
                agent_pins=agent_pins_from_ir(ir),
                generation_spec=generation_spec,
                content_hash=content_hash_of(source_bytes),
                created_at=now,
                created_by_user_id=user_id,
                verifier_version=CURRENT_VERIFIER_VERSION,
                verified_at=now,
            ))
        except Exception:
            await self._discard_source(ref)
            raise

        if not pin:
            return saved

        try:
            await self.pin(
                workflow_id=workflow_id,
                org_id=org_id,
                version=saved,
                expected_current_version_id=expected_current_version_id,
            )
        except Exception as exc:
            # The version is a real, successfully generated artifact -- keep
            # it listable/activatable rather than discarding it, and let the
            # caller decide how to surface "generated but not activated."
            self._logger.warning(
                "Version %s for workflow %s saved but pin failed: %s",
                saved.version_id, workflow_id, exc, exc_info=True,
            )
            raise PinFailedError(saved, exc) from exc

        return saved

    async def pin(
        self,
        *,
        workflow_id: str,
        org_id: str,
        version: WorkflowVersion,
        expected_current_version_id: str | None = None,
    ) -> None:
        if expected_current_version_id is not None:
            task = await self._engine.get(workflow_id, org_id)
            if (task.workflow_version_id or "") != expected_current_version_id:
                raise WorkflowVersionConflictError(
                    f"Workflow {workflow_id} moved to version "
                    f"{task.workflow_version_id!r} while this edit was in flight; "
                    "reload and re-apply."
                )
        await self._engine.update_fields(
            workflow_id, org_id,
            execution_kind="code",
            workflow_version_id=version.version_id,
        )

    async def _discard_source(self, ref: "ArtifactRef") -> None:
        try:
            await self._code_store.delete(ref)
        except Exception:
            self._logger.warning(
                "Leaked workflow source artifact %s after a failed version save",
                ref.artifact_id, exc_info=True,
            )
