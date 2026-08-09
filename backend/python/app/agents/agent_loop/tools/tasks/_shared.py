"""Shared helpers for the task/workflow tool pairs (`task_manage`/
`workflow_manage`, `task_find`/`workflow_find`). Both tools in each pair
delegate to the exact same `TaskEngine` and previously duplicated this
logic verbatim -- kept here once so the two vocabularies can't drift.
"""
from __future__ import annotations

import difflib
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.services.tasks.domain.models import TaskStep

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.services.tasks.application.engine import TaskEngine
    from app.services.tasks.domain.models import TaskTrigger
    from app.services.workflows.codegen.agent import WorkflowBuilderAgent
    from app.services.workflows.interface.code_store import ICodeStore
    from app.services.workflows.interface.version_store import IWorkflowVersionStore

__all__ = [
    "UPDATABLE_FIELDS",
    "parse_steps",
    "run_codegen",
    "tool_name_error",
    "tool_name_issues",
    "trigger_overview",
    "upsert_declarative_triggers",
]

logger = logging.getLogger(__name__)

UPDATABLE_FIELDS = (
    "title", "description", "instructions", "tool_names", "collection_ids",
    "connector_ids", "model_ref", "max_turns", "timeout_seconds",
)


def parse_steps(raw_steps: "list[dict[str, Any]] | None") -> "list[TaskStep] | None":
    if raw_steps is None:
        return None
    steps: list[TaskStep] = []
    errors: list[str] = []
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            errors.append(f"Step {i}: expected an object, got {type(raw).__name__}")
            continue
        try:
            steps.append(TaskStep(**raw))
        except ValidationError as e:
            errors.append(f"Step {i}: {e}")
    if errors:
        raise ValueError("Invalid steps:\n" + "\n".join(errors))
    return steps


def tool_name_issues(
    requested: "list[str] | None",
    available_tool_names: "Callable[[], Sequence[str]] | None",
) -> list[dict[str, Any]]:
    """Names in `requested` that the live session registry cannot resolve.

    The chat turn that creates a task holds a fully loaded `ToolRegistry`,
    so this check is exact and free -- and it is the only place the mistake
    is cheap to fix. `TaskSpecAssembler` now raises rather than substituting
    other tools, so an unvalidated typo turns into a failed run hours later
    with nobody watching.

    `available_tool_names` is a callable, not a snapshot: `register_task_tools`
    runs before the last few tools are registered, so reading names eagerly
    would report tools that do exist by the time the model calls us.
    """
    if not requested or available_tool_names is None:
        return []
    try:
        available = list(available_tool_names())
    except Exception:
        return []
    if not available:
        return []

    known = set(available)
    issues: list[dict[str, Any]] = []
    for name in requested:
        if name in known:
            continue
        close = difflib.get_close_matches(name, known, n=3, cutoff=0.6)
        issues.append({
            "kind": "tool",
            "id": name,
            "reason": (
                f"no such tool in this session; closest matches: {', '.join(close)}"
                if close
                else "no such tool in this session"
            ),
            "blocking": True,
        })
    return issues


def tool_name_error(
    requested: "list[str] | None",
    available_tool_names: "Callable[[], Sequence[str]] | None",
) -> str | None:
    issues = tool_name_issues(requested, available_tool_names)
    if not issues:
        return None
    return (
        "Unknown tool name(s) -- use the exact names from this session's tool list: "
        + "; ".join(f'{i["id"]!r}: {i["reason"]}' for i in issues)
    )


def trigger_overview(trigger: "TaskTrigger") -> dict[str, Any]:
    return {
        "trigger_id": trigger.trigger_id,
        "kind": trigger.kind.value,
        "cron_expression": trigger.cron_expression,
        "interval_seconds": trigger.interval_seconds,
        "fire_at": trigger.fire_at,
        "timezone": trigger.timezone,
        "next_run_at": trigger.next_run_at,
        "last_fire_at": trigger.last_fire_at,
        "enabled": trigger.enabled,
        "run_count": trigger.run_count,
        "max_runs": trigger.max_runs,
    }


# ---------------------------------------------------------------------------
# Codegen helpers — used by both task_manage and workflow_manage so that
# the model's tool choice does not determine whether codegen runs.
# ---------------------------------------------------------------------------

def _dict_to_ir(raw: dict[str, Any]) -> Any:
    from app.services.workflows.domain.models import IREdge, IRNode, WorkflowIR
    return WorkflowIR(
        schema_version=raw.get("schema_version", 1),
        nodes=[IRNode(**n) for n in raw.get("nodes", [])],
        edges=[IREdge(**e) for e in raw.get("edges", [])],
        entry_node_id=raw.get("entry_node_id"),
    )


async def run_codegen(
    *,
    workflow_builder: "WorkflowBuilderAgent",
    code_store: "ICodeStore",
    version_store: "IWorkflowVersionStore",
    engine: "TaskEngine",
    org_id: str,
    user_id: str,
    workflow_id: str,
    spec: str | None,
    tool_names: list[str] | None = None,
    existing_source: str | None = None,
) -> dict[str, Any]:
    """Generate workflow code and persist it as an immutable version.

    Returns a dict callers inspect to distinguish three outcomes:
      - ``ok=True, pinned=True``  -- generated, saved, and activated.
      - ``ok=True, pinned=False`` -- generated and saved, but the pin
        failed; ``failure_reason`` explains why.
      - ``ok=False``              -- nothing was saved.
    """
    from app.services.workflows.application.version_writer import WorkflowVersionWriter
    from app.services.workflows.domain.errors import PinFailedError

    if not spec:
        logger.warning("Codegen skipped for workflow %s: empty spec", workflow_id)
        return {"ok": False, "pinned": False, "failure_reason": "No instructions provided"}

    try:
        gen = await workflow_builder.generate(
            spec=spec,
            org_id=org_id,
            user_id=user_id,
            workflow_id=workflow_id,
            existing_source=existing_source,
            tool_names=tool_names,
        )
    except Exception as exc:
        logger.exception("WorkflowBuilderAgent.generate failed for workflow %s", workflow_id)
        return {"ok": False, "pinned": False, "failure_reason": f"Code generation raised an exception: {exc}"}

    if not gen.get("ok"):
        errors = gen.get("errors", [])
        logger.warning("Codegen failed for workflow %s: %s", workflow_id, errors)
        return {
            "ok": False, "pinned": False,
            "failure_reason": "; ".join(str(e) for e in errors) or "Code generator returned no source",
        }

    source_str: str = gen["source"]
    writer = WorkflowVersionWriter(
        version_store=version_store,
        code_store=code_store,
        task_engine=engine,
        logger=logger,
    )
    try:
        saved_version = await writer.persist(
            workflow_id=workflow_id,
            org_id=org_id,
            user_id=user_id,
            source=source_str,
            ir=_dict_to_ir(gen.get("ir") or {}),
            generation_spec=spec,
        )
    except PinFailedError as exc:
        logger.warning(
            "Codegen: workflow %s saved version %s but pin failed: %s",
            workflow_id, exc.version.version_id, exc,
        )
        await upsert_declarative_triggers(engine=engine, org_id=org_id, workflow_id=workflow_id, source=source_str)
        return {
            "ok": True,
            "pinned": False,
            "version_id": exc.version.version_id,
            "version_number": exc.version.version_number,
            "content_hash": exc.version.content_hash,
            "failure_reason": str(exc),
        }
    except Exception as exc:
        logger.exception("Failed to persist code/version for workflow %s", workflow_id)
        return {"ok": False, "pinned": False, "failure_reason": f"Failed to store generated code: {exc}"}

    await upsert_declarative_triggers(engine=engine, org_id=org_id, workflow_id=workflow_id, source=source_str)

    logger.info(
        "Codegen: workflow %s pinned to version %s (v%d, hash %s, bundle=%s)",
        workflow_id, saved_version.version_id, saved_version.version_number,
        saved_version.content_hash[:12],
        saved_version.bundle_ref.artifact_id if saved_version.bundle_ref else "<none>",
    )
    return {
        "ok": True,
        "pinned": True,
        "version_id": saved_version.version_id,
        "version_number": saved_version.version_number,
        "content_hash": saved_version.content_hash,
    }


async def upsert_declarative_triggers(
    *, engine: "TaskEngine", org_id: str, workflow_id: str, source: str,
) -> None:
    """Reconcile ``@workflow(triggers=[...])`` declarations with stored triggers."""
    from app.services.tasks.domain.models import (
        DECLARATIVE_TRIGGER_PREFIX,
        compute_declarative_trigger_id,
    )
    from app.services.workflows.ir.extractor import extract_trigger_specs

    try:
        specs = extract_trigger_specs(source)
    except Exception:
        logger.exception("Failed to extract trigger specs from workflow %s source", workflow_id)
        return

    desired: dict[str, dict[str, Any]] = {
        compute_declarative_trigger_id(workflow_id, spec): spec for spec in specs
    }

    from app.services.tasks.domain.errors import InvalidTriggerError

    skipped: set[str] = set()
    for trigger_id, spec in desired.items():
        try:
            trigger, _ = await engine.add_trigger(workflow_id, org_id, spec, trigger_id=trigger_id)
        except InvalidTriggerError as exc:
            skipped.add(trigger_id)
            if spec.get("kind") == "one_time":
                logger.debug(
                    "Skipping past one-time trigger for workflow %s (fire_at=%s): %s",
                    workflow_id, spec.get("fire_at"), exc,
                )
            else:
                logger.warning(
                    "Invalid declarative trigger for workflow %s spec=%r: %s",
                    workflow_id, spec, exc,
                )
            continue
        except Exception:
            logger.warning(
                "Failed to upsert declarative trigger for workflow %s spec=%r",
                workflow_id, spec, exc_info=True,
            )
        else:
            logger.info(
                "Declarative trigger upserted: workflow=%s kind=%s trigger_id=%s next_run_at=%s",
                workflow_id, trigger.kind.value, trigger.trigger_id, trigger.next_run_at,
            )

    live_desired = set(desired) - skipped
    try:
        existing = await engine.list_triggers(workflow_id)
    except Exception:
        logger.warning("Could not list triggers to prune workflow %s", workflow_id, exc_info=True)
        return
    for trigger in existing:
        if trigger.trigger_id.startswith(DECLARATIVE_TRIGGER_PREFIX) and trigger.trigger_id not in live_desired:
            try:
                await engine.delete_trigger(trigger.trigger_id, org_id)
            except Exception:
                logger.warning(
                    "Could not remove stale declarative trigger %s from workflow %s",
                    trigger.trigger_id, workflow_id, exc_info=True,
                )
