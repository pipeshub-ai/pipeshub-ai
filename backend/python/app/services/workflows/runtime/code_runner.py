"""CodeWorkflowRunner — executes a WorkflowVersion's Python code with
journaled-effect replay (D1).

Plugs into TaskExecutor._execute_claimed_run via an injected optional
dispatch branch (D4). The executor's existing claim/heartbeat/DLQ logic
is reused unchanged.

Execution model:
1. Download source bundle from ICodeStore.
2. Build a RunPrincipal from the task.
3. If a provisioner is wired (production path):
   a. Provision a sandbox session (temp dir + source written there).
   b. Launch a WorkflowToolBridge subprocess.
   c. Serve JSON-Lines broker/journal RPC calls from the subprocess.
   d. Collect the terminal "done" result.
   e. Teardown the sandbox.
4. On _WaitForEventSuspension or _ApprovalSuspension: park the run.
5. On any other exception: re-raise (TaskExecutor's crash path handles it).
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING, Any

from app.services.workflows.application.journal_spill import SpillingExecutionJournal

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.tasks.domain.models import TaskDefinition, TaskRun
    from app.services.workflows.interface.broker import IPlatformBroker, RunPrincipal
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.interface.payload_store import IJournalPayloadStore
    from app.services.workflows.interface.provisioner import ISandboxSessionProvisioner
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

    PayloadStoreFactory = Callable[[RunPrincipal], IJournalPayloadStore | None]

__all__ = ["CodeWorkflowRunner"]

logger = logging.getLogger(__name__)

_APPROVAL_TRUE_VALUES = frozenset({"true", "yes", "y", "approve", "approved", "ok", "1"})


def _decode_event_answer(answer: str) -> Any:
    """`ctx.wait_for_event` is typed as returning the event payload dict, so a
    JSON answer is handed back as a dict; anything else is wrapped rather than
    dropped."""
    import json

    try:
        decoded = json.loads(answer)
    except (TypeError, ValueError):
        return {"answer": answer}
    return decoded if isinstance(decoded, dict) else {"answer": decoded}


class CodeWorkflowRunner:
    """Runs one code workflow run to completion (or suspension)."""

    def __init__(
        self,
        *,
        journal: "IExecutionJournal",
        broker: "IPlatformBroker",
        version_store: "IWorkflowVersionStore",
        code_store: "ICodeStore",
        provisioner: "ISandboxSessionProvisioner | None" = None,
        payload_store_factory: "PayloadStoreFactory | None" = None,
    ) -> None:
        self._journal = journal
        self._broker = broker
        self._version_store = version_store
        self._code_store = code_store
        self._provisioner = provisioner
        # Spilling needs a tenant to write artifacts as, which is only known
        # per run -- hence a factory rather than a store.
        self._payload_store_factory = payload_store_factory

    def _journal_for(self, principal: "RunPrincipal") -> "IExecutionJournal":
        """The journal this run should use: spilling when a payload store can
        be built for its principal, the bare journal otherwise."""
        if self._payload_store_factory is None:
            return self._journal
        try:
            payload_store = self._payload_store_factory(principal)
        except Exception:
            logger.exception(
                "run %s: could not build a journal payload store, large results "
                "will stay inline in Redis",
                principal.run_id,
            )
            return self._journal
        if payload_store is None:
            return self._journal
        return SpillingExecutionJournal(self._journal, payload_store)

    async def run(
        self,
        *,
        task: "TaskDefinition",
        run: "TaskRun",
        trigger_payload: dict[str, Any] | None = None,
        broker: "IPlatformBroker | None" = None,
    ) -> dict[str, Any]:
        """Execute the code workflow. Returns a result dict.

        Raises on crash (TaskExecutor handles it).
        Returns {"status": "awaiting_input", ...} on suspension.
        Returns {"status": "succeeded", "output": ...} on success.

        The run's authority is computed here rather than accepted from the
        caller: the grant depends on the pinned `WorkflowVersion.tool_pins`,
        and this is the only component that resolves the version.

        Args:
            broker: Optional per-run broker override.  Pass this when the caller
                has already built a ToolRegistry for this run (e.g. the
                TaskExecutor after spec_assembler resolves toolset credentials).
                Falls back to ``self._broker`` when None.
        """
        from app.services.workflows.domain.grants import compute_run_grant
        from app.services.workflows.interface.broker import RunPrincipal
        effective_broker = broker if broker is not None else self._broker

        workflow_version_id = getattr(task, "workflow_version_id", None)
        if not workflow_version_id:
            raise ValueError(
                f"task {task.task_id} is execution_kind=code but has no workflow_version_id"
            )

        version = await self._version_store.get(
            version_id=workflow_version_id, org_id=task.org_id
        )
        if version is None:
            raise ValueError(f"WorkflowVersion {workflow_version_id} not found")

        if not version.bundle_ref:
            raise ValueError(f"WorkflowVersion {workflow_version_id} has no bundle_ref")

        source_bytes = await self._code_store.get(version.bundle_ref)

        if version.content_hash:
            actual_hash = hashlib.sha256(source_bytes).hexdigest()
            if actual_hash != version.content_hash:
                # The version row and the source blob disagree, so we cannot say
                # which code this run's journal belongs to; replay would be
                # against different code than the original attempt.
                raise ValueError(
                    f"WorkflowVersion {workflow_version_id} content hash mismatch "
                    f"(expected {version.content_hash[:12]}, got {actual_hash[:12]})"
                )

        is_dry_run = getattr(run, "is_dry_run", False)
        grant = compute_run_grant(task, is_dry_run=is_dry_run, version=version)
        # Every mid-run "tool not granted" rejection traces back to this set,
        # and which of the two sources produced it decides where the fix goes:
        # the task's declared tools, or the pinned version's `tool_pins`.
        logger.info(
            "Run %s grant: tools=%s (source=%s), collections=%d, "
            "can_create_agents=%s, max_calls=%d, dry_run=%s, version=%s",
            run.run_id,
            sorted(grant.tool_names) or "<unrestricted>",
            "task.tool_names" if task.tool_names else "version.tool_pins",
            len(grant.collection_ids),
            grant.can_create_agents,
            grant.max_calls,
            is_dry_run,
            workflow_version_id,
        )
        principal = RunPrincipal(
            org_id=task.org_id,
            user_id=task.principal.user_id,
            run_id=run.run_id,
            workflow_id=task.task_id,
            is_dry_run=is_dry_run,
            conversation_id=task.created_from_conversation_id or None,
            grant=grant,
        )
        # After the principal exists: the journal is per-run because spilling
        # writes artifacts as the run's tenant.
        journal = self._journal_for(principal)
        resumed = await self._record_resumption(run, journal)

        if self._provisioner is not None:
            result = await self._exec_via_provisioner(
                source_bytes=source_bytes,
                principal=principal,
                run=run,
                workflow_version_id=workflow_version_id,
                trigger_payload=trigger_payload or {},
                broker=effective_broker,
                in_replay=resumed,
                journal=journal,
            )
        else:
            # Provisioner-less path retained for test harnesses only.
            # Production container_wiring always injects a provisioner.
            logger.warning(
                "CodeWorkflowRunner: no provisioner — using in-process exec (test/dev path). "
                "Do NOT use in production: this path has no OS isolation."
            )
            result = await self._exec_in_process(
                source_bytes, principal, trigger_payload or {},
                broker=effective_broker, is_dry_run=is_dry_run, in_replay=resumed,
                journal=journal,
            )

        if result.get("status") == "awaiting_input":
            # The run stops writing here, so without restarting the clock its
            # journal ages out from the last completed step and a late answer
            # would replay the whole workflow against nothing.
            result["resume_deadline_at"] = await journal.touch(run.run_id)
        return result

    async def _record_resumption(self, run: "TaskRun", journal: "IExecutionJournal") -> bool:
        """Turn a parked run's answer into a journal entry before re-execution.

        A suspended code workflow resumes by re-running from the top and
        replaying its journal. Without an entry at the step it parked on, the
        replay reaches `ctx.wait_for_event`/`ctx.request_approval` again, finds
        nothing, and suspends forever. Writing the answer here -- once, keyed by
        the step it suspended at -- is what actually unblocks it.

        Returns True when this execution is a replay of a suspended run.
        """
        step_key = getattr(run, "suspended_step_key", None)
        if not step_key:
            return False

        answer = run.pending_answer
        if answer is None:
            # Re-dispatched after suspension without an answer (e.g. lease
            # reaped). Still a replay; the parked step just isn't resolved yet.
            return True

        existing = await journal.lookup(run.run_id, step_key)
        if existing is not None:
            return True

        from app.services.workflows.domain.models import JournalEntry, ResultRef, StepOutcome

        kind = getattr(run, "suspension_kind", None)
        if kind == "approval":
            value: Any = answer.strip().lower() in _APPROVAL_TRUE_VALUES
            entry_kind = "approval"
        else:
            value = _decode_event_answer(answer)
            entry_kind = "wait"

        await journal.append(
            JournalEntry(
                run_id=run.run_id,
                seq=0,  # the journal adapter assigns the monotonic seq
                step_key=step_key,
                entry_kind=entry_kind,
                idempotency_key=step_key,
                outcome=StepOutcome.SUCCEEDED,
                result_ref=ResultRef(inline=value),
            )
        )
        logger.info(
            "CodeWorkflowRunner: resumed run %s at step %s (%s)", run.run_id, step_key, entry_kind,
        )
        return True

    # -------------------------------------------------------------------------
    # Subprocess path (production)
    # -------------------------------------------------------------------------

    async def _exec_via_provisioner(
        self,
        *,
        source_bytes: bytes,
        principal: "RunPrincipal",
        run: "TaskRun",
        workflow_version_id: str,
        trigger_payload: dict[str, Any],
        broker: "IPlatformBroker | None" = None,
        in_replay: bool = False,
        journal: "IExecutionJournal | None" = None,
    ) -> dict[str, Any]:
        from app.services.workflows.interface.provisioner import SessionSpec
        from app.services.workflows.runtime.sandbox import WorkflowToolBridge

        spec = SessionSpec(
            run_id=run.run_id,
            org_id=principal.org_id,
            workflow_version_id=workflow_version_id,
            source_bundle=source_bytes,
            trigger_payload=trigger_payload,
            is_dry_run=getattr(run, "is_dry_run", False),
        )
        session = await self._provisioner.provision(spec)
        src_path = os.path.join(session.sandbox_root, "workflow.py")
        try:
            effective = broker if broker is not None else self._broker
            bridge = WorkflowToolBridge(
                broker=effective,
                principal=principal,
                journal=journal if journal is not None else self._journal,
                working_dir=session.sandbox_root,
                is_dry_run=spec.is_dry_run,
                in_replay=in_replay,
            )
            result = await bridge.run(src_path)
            return result
        finally:
            await self._provisioner.teardown(session)

    # -------------------------------------------------------------------------
    # In-process path (test/dev only)
    # -------------------------------------------------------------------------

    async def _exec_in_process(
        self,
        source_bytes: bytes,
        principal: "RunPrincipal",
        trigger_payload: dict[str, Any],
        broker: "IPlatformBroker | None" = None,
        is_dry_run: bool = False,
        in_replay: bool = False,
        journal: "IExecutionJournal | None" = None,
    ) -> dict[str, Any]:
        from app.services.workflows.sdk.context import (
            Ctx,
            _ApprovalSuspension,
            _WaitForEventSuspension,
        )

        effective = broker if broker is not None else self._broker
        ctx = Ctx(
            run_id=principal.run_id,
            journal=journal if journal is not None else self._journal,
            broker=effective,
            principal=principal,
            is_dry_run=is_dry_run,
            in_replay=in_replay,
        )
        source = source_bytes.decode("utf-8")
        try:
            result = await self._exec_source(source, ctx, trigger_payload=trigger_payload)
            return {"status": "succeeded", "output": result}
        except _WaitForEventSuspension as suspension:
            return {
                "status": "awaiting_input",
                "suspension_kind": "wait_for_event",
                "event_type": suspension.event_type,
                "step_key": suspension.step_key,
            }
        except _ApprovalSuspension as suspension:
            return {
                "status": "awaiting_input",
                "suspension_kind": "approval",
                "label": suspension.label,
                "step_key": suspension.step_key,
                "payload": suspension.payload,
            }

    @staticmethod
    async def _exec_source(source: str, ctx: "Any", *, trigger_payload: dict[str, Any]) -> Any:
        """Exec the workflow source in a restricted namespace and call the entry point."""
        import inspect

        from app.services.workflows.security.sandbox_policy import build_safe_builtins

        import app.services.workflows.sdk as _sdk

        ns: dict[str, Any] = {
            "__builtins__": build_safe_builtins(),
            "ctx": ctx,
            "trigger_payload": trigger_payload,
            "sdk": _sdk,
        }
        compiled = compile(source, "<workflow>", "exec")
        exec(compiled, ns)  # noqa: S102

        entry = None
        for obj in ns.values():
            if callable(obj) and hasattr(obj, "__workflow_meta__"):
                entry = obj
                break
        if entry is None:
            raise ValueError("No @workflow-decorated function found in source")

        sig = inspect.signature(entry)
        params = list(sig.parameters.keys())
        if len(params) >= 2:
            return await entry(ctx, trigger_payload)
        return await entry(ctx)
