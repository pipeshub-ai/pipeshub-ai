"""Public workflow REST API (Python side) -- the single user-facing
surface for the task engine. "Task" is an internal implementation detail
(see `services/tasks/`); this router, its Node proxy
(`modules/workflows/`), and the frontend `/workflows/` page are the only
places a user should ever see "Workflow."

The Node proxy forwards `/api/v1/workflows/*` here after auth + scope
checks (`modules/workflows/controller/workflows.controller.ts`).

Identity: resolved from `request.state.user`, populated by the existing
JWT-decoding auth middleware shared with `tasks.py`/`toolsets.py` -- NOT
from `X-Org-Id`/`X-User-Id` headers, which Node's thin proxy never sets
(it forwards the original `Authorization` header, not synthetic identity
headers).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes, Routes
from app.services.tasks.domain.errors import (
    ExpiredSuspensionError,
    OptimisticConcurrencyError,
    PrerequisiteError,
    RunNotFoundError,
    StaleAnswerError,
    TaskEngineError,
    TriggerNotFoundError,
)
from app.services.tasks.domain.models import TaskRun, TaskTrigger
from app.services.workflows.domain.errors import (
    PinFailedError,
    VersionStoreUnavailableError,
    WorkflowCodegenError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowVersionConflictError,
    WorkflowVersionNotFoundError,
)
from app.services.workflows.domain.models import (
    TraceEntry,
    Workflow,
    WorkflowStatus,
    WorkflowVersion,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


# ============================================================================
# Response shaping -- camelCase for the frontend, same convention as
# `tasks.py`'s `_task_to_dict`/`_run_to_dict`. "Task" vocabulary (`taskId`)
# is deliberately never surfaced here -- everything is renamed to `workflow*`.
# ============================================================================


def _workflow_to_dict(workflow: Workflow) -> dict[str, Any]:
    return {
        "workflowId": workflow.workflow_id,
        "orgId": workflow.org_id,
        "kind": workflow.kind.value,
        "name": workflow.name,
        "description": workflow.description,
        "currentVersionId": workflow.current_version_id,
        "triggers": [
            {
                "triggerId": t.trigger_id,
                "kind": t.kind,
                "nextRunAt": t.next_run_at,
                "lastFireAt": t.last_fire_at,
                "enabled": t.enabled,
            }
            for t in workflow.triggers
        ],
        "status": workflow.status.value,
        "requiredScopes": workflow.required_scopes,
        "executionKind": workflow.execution_kind,
        "createdByUserId": workflow.created_by_user_id,
        "createdAt": workflow.created_at,
        "updatedAt": workflow.updated_at,
        "conversationId": workflow.conversation_id,
        "toolNames": workflow.tool_names,
        "connectorIds": workflow.connector_ids,
        "collectionIds": workflow.collection_ids,
        "maxTurns": workflow.max_turns,
        "timeoutSeconds": workflow.timeout_seconds,
    }


def _trigger_to_dict(trigger: TaskTrigger) -> dict[str, Any]:
    return {
        "triggerId": trigger.trigger_id,
        "workflowId": trigger.task_id,
        "kind": trigger.kind.value,
        "cronExpression": trigger.cron_expression,
        "intervalSeconds": trigger.interval_seconds,
        "fireAt": trigger.fire_at,
        "timezone": trigger.timezone,
        "eventFilter": trigger.event_filter,
        # `webhook_id` only -- the HMAC secret is reveal-once at creation
        # time and is never readable again, by design.
        "webhookId": trigger.webhook_id,
        "nextRunAt": trigger.next_run_at,
        "lastFireAt": trigger.last_fire_at,
        "misfirePolicy": trigger.misfire_policy.value,
        "maxRuns": trigger.max_runs,
        "runCount": trigger.run_count,
        "enabled": trigger.enabled,
    }


def _run_to_dict(run: TaskRun) -> dict[str, Any]:
    return {
        "runId": run.run_id,
        "workflowId": run.task_id,
        "triggerId": run.trigger_id,
        "status": run.status.value,
        "attempt": run.attempt,
        "completedSteps": run.completed_steps,
        "failedStepId": run.failed_step_id,
        "skippedSteps": run.skipped_steps,
        "outputSummary": run.output_summary,
        "error": run.error,
        "usage": run.usage,
        "scheduledFor": run.scheduled_for,
        "startedAt": run.started_at,
        "completedAt": run.completed_at,
        "createdAt": run.created_at,
        "agentRunId": run.agent_run_id,
        "isDryRun": run.is_dry_run,
        "suspensionKind": run.suspension_kind,
    }


def _trace_entry_to_dict(e: TraceEntry) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "kind": e.kind,
        "label": e.label,
        "outcome": e.outcome,
        "target": e.target,
        "timestamp": e.timestamp,
        "error": e.error,
        "attempt": e.attempt,
        "detail": e.detail,
    }


def _version_to_dict(v: WorkflowVersion) -> dict[str, Any]:
    from app.services.workflows.codegen.verifier import is_version_stale

    return {
        "versionId": v.version_id,
        "workflowId": v.workflow_id,
        "versionNumber": v.version_number,
        "sdkVersion": v.sdk_version,
        "contentHash": v.content_hash,
        "hasBundleRef": v.bundle_ref is not None,
        "createdAt": v.created_at,
        "createdByUserId": v.created_by_user_id,
        "ir": v.ir.model_dump() if v.ir else None,
        "verifierVersion": v.verifier_version,
        "verifiedAt": v.verified_at,
        # True for versions generated/committed before the verifier gained a
        # rule that would have failed them (e.g. this plan's MISSING_AWAIT
        # coverage for ctx.agent, or the new dry-exec compile step). They are
        # not re-verified automatically -- see codegen/verifier.py -- so this
        # is surfaced for the frontend to offer a "Regenerate" action rather
        # than letting the version fail silently at its next scheduled run.
        "needsRegeneration": is_version_stale(v.verifier_version),
    }


# ============================================================================
# Error mapping -- WorkflowService raises a mix of workflow-domain errors
# (WorkflowNotFoundError) and the underlying TaskEngine's own errors
# (PrerequisiteError, OptimisticConcurrencyError, ...), since it's a thin
# facade rather than a full re-wrap. Both are mapped here so no bare
# `except Exception` ever swallows a structured 404/409 into a 500.
# ============================================================================


def _handle_engine_error(e: WorkflowError | TaskEngineError) -> HTTPException:
    if isinstance(
        e,
        (WorkflowNotFoundError, WorkflowVersionNotFoundError, RunNotFoundError, TriggerNotFoundError),
    ):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, VersionStoreUnavailableError):
        # Distinct from 404 so the frontend can show "could not load
        # versions, retry" instead of "no code generated yet."
        return HTTPException(status_code=503, detail=str(e))
    if isinstance(e, PinFailedError):
        # The version WAS saved -- this is a partial-success, not an outright
        # failure, so 207-ish semantics via 409 (conflict with current state)
        # rather than 500; the caller can still find the version by listing.
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, PrerequisiteError):
        return HTTPException(status_code=409, detail={"message": str(e), "missing": e.missing})
    if isinstance(
        e,
        (
            OptimisticConcurrencyError,
            StaleAnswerError,
            ExpiredSuspensionError,
            WorkflowVersionConflictError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, WorkflowCodegenError):
        # The generator ran but produced code that failed verification -- the
        # request was well-formed, so 422 rather than 400.
        return HTTPException(
            status_code=422, detail={"message": str(e), "errors": e.errors},
        )
    return HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Service/context helpers
# ============================================================================


def _get_workflow_service(request: Request):
    """Extract WorkflowService from app.state (set up by containers/query.py)."""
    ws = getattr(request.app.state, "workflow_service", None)
    if ws is None:
        raise HTTPException(status_code=503, detail="WorkflowService not available")
    return ws


def _get_user_context(request: Request) -> tuple[str, str]:
    """Extract (org_id, user_id) from `request.state.user` ONLY -- no
    header fallback. `request.state.user` is always populated by the
    global `authenticate_requests` middleware (see `query_main.py`)
    before any route handler runs, so a header fallback here would only
    ever serve a caller trying to bypass JWT-derived identity."""
    user = getattr(request.state, "user", None) or {}
    org_id = user.get("orgId")
    user_id = user.get("userId")
    if not org_id or not user_id:
        raise HTTPException(status_code=401, detail="Authentication required. Please provide valid credentials.")
    return org_id, user_id


class PromoteToAgentResponse(BaseModel):
    agentId: str


class AnswerRunRequest(BaseModel):
    answer: str


class EditWorkflowRequest(BaseModel):
    instructions: str


class CreateTriggerRequest(BaseModel):
    """One trigger to attach to an existing workflow.

    Deliberately not a passthrough of the engine's trigger spec: `webhook_id`
    is server-generated, and letting a caller set it would let them point a
    trigger at a webhook whose secret they do not control.
    """

    kind: str
    cron_expression: str | None = Field(default=None, alias="cronExpression")
    interval_seconds: int | None = Field(default=None, alias="intervalSeconds")
    fire_at: str | None = Field(default=None, alias="fireAt")
    timezone: str = "UTC"
    event_filter: dict[str, Any] | None = Field(default=None, alias="eventFilter")
    max_runs: int | None = Field(default=None, alias="maxRuns")
    misfire_policy: str | None = Field(default=None, alias="misfirePolicy")

    model_config = {"populate_by_name": True}

    def to_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "kind": self.kind,
            "cron_expression": self.cron_expression,
            "interval_seconds": self.interval_seconds,
            "fire_at": self.fire_at,
            "timezone": self.timezone,
            "event_filter": self.event_filter,
            "max_runs": self.max_runs,
        }
        if self.misfire_policy:
            spec["misfire_policy"] = self.misfire_policy
        return spec


class UpdateTriggerRequest(BaseModel):
    """Only `enabled` is mutable. A schedule change is a delete plus an add,
    so that `next_run_at`, the due index and `run_count` cannot end up
    describing a schedule that no longer exists."""

    enabled: bool


class CommitVersionRequest(BaseModel):
    source: str
    base_version_id: str | None = Field(default=None, alias="baseVersionId")

    model_config = {"populate_by_name": True}


# ============================================================================
# List / get
# ============================================================================


@router.get("/", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def list_workflows(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    all_users: bool = Query(False),  # noqa: FBT001 -- FastAPI query params are keyword-only at the HTTP layer
    q: str | None = Query(None, alias="q"),
    conversation_id: str | None = Query(None),
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("list_workflows: org=%s user=%s status=%s q=%s limit=%d offset=%d conversation_id=%s", org_id, user_id, status, q, limit, offset, conversation_id)
    parsed_status = None
    if status:
        try:
            parsed_status = WorkflowStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    try:
        page = await service.list_workflows(
            org_id=org_id,
            user_id=user_id,
            all_users=all_users,
            status=parsed_status,
            text_search=q,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("list_workflows failed: %s", e)
        raise _handle_engine_error(e) from e
    logger.info("list_workflows: returning %d items (total=%d)", len(page.items), page.total)
    return {
        "workflows": [_workflow_to_dict(w) for w in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "hasMore": page.has_more,
    }


@router.get("/{workflow_id}", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def get_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, _user_id = _get_user_context(request)
    logger.info("get_workflow: org=%s workflow_id=%s", org_id, workflow_id)
    try:
        workflow = await service.get_workflow(workflow_id=workflow_id, org_id=org_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("get_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("get_workflow: found %s (status=%s)", workflow.name, workflow.status.value)
    return _workflow_to_dict(workflow)


@router.get("/{workflow_id}/triggers", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def list_workflow_triggers(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, _user_id = _get_user_context(request)
    logger.info("list_workflow_triggers: org=%s workflow_id=%s", org_id, workflow_id)
    try:
        triggers = await service.list_triggers(workflow_id=workflow_id, org_id=org_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("list_workflow_triggers failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("list_workflow_triggers: returning %d triggers", len(triggers))
    return {"triggers": [_trigger_to_dict(t) for t in triggers]}


@router.post(
    "/{workflow_id}/triggers",
    status_code=201,
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def create_workflow_trigger(
    workflow_id: str,
    body: CreateTriggerRequest,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info(
        "create_workflow_trigger: org=%s user=%s workflow_id=%s kind=%s",
        org_id, user_id, workflow_id, body.kind,
    )
    try:
        trigger, secret = await service.add_trigger(
            workflow_id=workflow_id, org_id=org_id, spec=body.to_spec(),
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("create_workflow_trigger failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e

    payload = _trigger_to_dict(trigger)
    if trigger.webhook_id:
        # Reveal-once: the secret is stored hashed/encrypted and this is the
        # only response that will ever carry it.
        payload["webhookSecret"] = secret
        # Path, not an absolute URL: the connectors service that serves it is
        # reachable at a different host per deployment and there is no config
        # key that records the externally-visible one.
        payload["webhookPath"] = f"{Routes.TASK_WEBHOOK_PREFIX.value}{trigger.webhook_id}"
    logger.info(
        "create_workflow_trigger: created %s (kind=%s next_run_at=%s)",
        trigger.trigger_id, trigger.kind.value, trigger.next_run_at,
    )
    return payload


@router.patch(
    "/{workflow_id}/triggers/{trigger_id}",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def update_workflow_trigger(
    workflow_id: str,
    trigger_id: str,
    body: UpdateTriggerRequest,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info(
        "update_workflow_trigger: org=%s user=%s workflow_id=%s trigger_id=%s enabled=%s",
        org_id, user_id, workflow_id, trigger_id, body.enabled,
    )
    try:
        trigger = await service.update_trigger(
            workflow_id=workflow_id, org_id=org_id, trigger_id=trigger_id, enabled=body.enabled,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("update_workflow_trigger failed: trigger_id=%s error=%s", trigger_id, e)
        raise _handle_engine_error(e) from e
    return _trigger_to_dict(trigger)


@router.delete(
    "/{workflow_id}/triggers/{trigger_id}",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def delete_workflow_trigger(
    workflow_id: str,
    trigger_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info(
        "delete_workflow_trigger: org=%s user=%s workflow_id=%s trigger_id=%s",
        org_id, user_id, workflow_id, trigger_id,
    )
    try:
        deleted = await service.delete_trigger(
            workflow_id=workflow_id, org_id=org_id, trigger_id=trigger_id,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("delete_workflow_trigger failed: trigger_id=%s error=%s", trigger_id, e)
        raise _handle_engine_error(e) from e
    return {"deleted": deleted}


@router.get("/{workflow_id}/runs", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def list_workflow_runs(
    workflow_id: str,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, _user_id = _get_user_context(request)
    logger.info("list_workflow_runs: org=%s workflow_id=%s limit=%d offset=%d", org_id, workflow_id, limit, offset)
    try:
        page = await service.list_runs(workflow_id=workflow_id, org_id=org_id, limit=limit, offset=offset)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("list_workflow_runs failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("list_workflow_runs: returning %d runs (total=%d)", len(page.items), page.total)
    return {
        "runs": [_run_to_dict(r) for r in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
        "hasMore": page.has_more,
    }


@router.get("/{workflow_id}/runs/{run_id}", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def get_workflow_run(
    workflow_id: str,
    run_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, _user_id = _get_user_context(request)
    logger.info("get_workflow_run: org=%s workflow_id=%s run_id=%s", org_id, workflow_id, run_id)
    try:
        run = await service.get_run(workflow_id=workflow_id, run_id=run_id, org_id=org_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("get_workflow_run failed: run_id=%s error=%s", run_id, e)
        raise _handle_engine_error(e) from e
    return _run_to_dict(run)


@router.get("/{workflow_id}/runs/{run_id}/trace", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def get_run_trace(
    workflow_id: str,
    run_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Return the run plus its execution trace, oldest step first.

    The trace is the replay journal for code workflows and the agent timeline
    for agent tasks; both are normalised to the same entry shape so the run
    inspector does not branch on execution kind.
    """
    org_id, _user_id = _get_user_context(request)
    logger.info("get_run_trace: org=%s workflow_id=%s run_id=%s", org_id, workflow_id, run_id)
    try:
        run, entries = await service.get_run_trace(
            workflow_id=workflow_id, run_id=run_id, org_id=org_id,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("get_run_trace failed: run_id=%s error=%s", run_id, e)
        raise _handle_engine_error(e) from e
    return {
        "run": _run_to_dict(run),
        "traceEntries": [_trace_entry_to_dict(e) for e in entries],
    }


@router.post(
    "/{workflow_id}/runs/{run_id}/answer",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_APPROVE))],
)
async def answer_workflow_run(
    workflow_id: str,
    run_id: str,
    body: AnswerRunRequest,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Answers a run paused with `status=awaiting_input`, resuming it from
    its checkpoint. A 409 here (`StaleAnswerError`) means the question was
    already answered, or the run moved on/was cancelled since it was
    asked; the frontend should refetch the run rather than retry the same
    answer."""
    org_id, user_id = _get_user_context(request)
    logger.info(
        "answer_workflow_run: org=%s user=%s workflow_id=%s run_id=%s",
        org_id, user_id, workflow_id, run_id,
    )
    try:
        run = await service.answer_run(
            workflow_id=workflow_id, run_id=run_id, org_id=org_id,
            answer=body.answer, user_id=user_id,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("answer_workflow_run failed: run_id=%s error=%s", run_id, e)
        raise _handle_engine_error(e) from e
    logger.info("answer_workflow_run: resumed, status=%s", run.status.value)
    return _run_to_dict(run)


# ============================================================================
# Lifecycle actions
# ============================================================================


@router.post("/{workflow_id}/run-now", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_EXECUTE))])
async def run_workflow_now(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("run_workflow_now: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        run = await service.run_now(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("run_workflow_now failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("run_workflow_now: started run_id=%s status=%s", run.run_id, run.status.value)
    return _run_to_dict(run)


@router.post("/{workflow_id}/dry-run", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_EXECUTE))])
async def dry_run_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("dry_run_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        run = await service.dry_run(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("dry_run_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("dry_run_workflow: started run_id=%s status=%s", run.run_id, run.status.value)
    return _run_to_dict(run)


@router.post("/{workflow_id}/pause", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))])
async def pause_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("pause_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        workflow = await service.pause(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("pause_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("pause_workflow: done, status=%s", workflow.status.value)
    return _workflow_to_dict(workflow)


@router.post("/{workflow_id}/resume", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))])
async def resume_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("resume_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        workflow = await service.resume(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("resume_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("resume_workflow: done, status=%s", workflow.status.value)
    return _workflow_to_dict(workflow)


@router.post("/{workflow_id}/cancel", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))])
async def cancel_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    org_id, user_id = _get_user_context(request)
    logger.info("cancel_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        workflow = await service.cancel(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("cancel_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("cancel_workflow: done, status=%s", workflow.status.value)
    return _workflow_to_dict(workflow)


@router.delete("/{workflow_id}", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))])
async def delete_workflow(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Hard delete -- see `TaskEngine.delete`'s own docstring on when this
    (vs. `POST /{workflow_id}/cancel`, a soft delete) is appropriate. The
    frontend should default to offering Cancel and only expose this behind
    an explicit confirmation."""
    org_id, user_id = _get_user_context(request)
    logger.info("delete_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    try:
        deleted = await service.delete(workflow_id=workflow_id, org_id=org_id, user_id=user_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("delete_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    if not deleted:
        logger.warning("delete_workflow: not found workflow_id=%s", workflow_id)
        raise HTTPException(status_code=404, detail=f"Workflow {workflow_id!r} not found.")
    logger.info("delete_workflow: deleted workflow_id=%s", workflow_id)
    return {"status": "success"}


@router.get("/{workflow_id}/versions", dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))])
async def list_workflow_versions(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Return all immutable WorkflowVersion records for this workflow, newest first."""
    org_id, _user_id = _get_user_context(request)
    logger.info("list_workflow_versions: org=%s workflow_id=%s", org_id, workflow_id)
    try:
        versions = await service.list_versions(workflow_id=workflow_id, org_id=org_id)
    except (WorkflowError, TaskEngineError) as e:
        logger.error("list_workflow_versions failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    except Exception as e:
        logger.error("list_workflow_versions failed unexpectedly: workflow_id=%s error=%s", workflow_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list versions") from e
    return {"versions": [_version_to_dict(v) for v in versions]}


@router.get(
    "/{workflow_id}/versions/{version_id}/source",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_READ))],
)
async def get_workflow_version_source(
    workflow_id: str,
    version_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Return the Python source for a specific workflow version as JSON."""
    org_id, _user_id = _get_user_context(request)
    logger.info(
        "get_workflow_version_source: org=%s workflow_id=%s version_id=%s",
        org_id, workflow_id, version_id,
    )
    try:
        source_bytes = await service.get_version_source(
            workflow_id=workflow_id, version_id=version_id, org_id=org_id
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error(
            "get_workflow_version_source failed: version_id=%s error=%s", version_id, e
        )
        raise _handle_engine_error(e) from e
    return {"versionId": version_id, "workflowId": workflow_id, "source": source_bytes.decode("utf-8")}


@router.post(
    "/{workflow_id}/edit",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def edit_workflow(
    workflow_id: str,
    body: EditWorkflowRequest,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Propose edited workflow code from natural-language instructions.

    Saves nothing. Returns ``{ source, ir, previousSource, baseVersionId }``
    so the frontend can render a diff; the user makes it live by POSTing the
    reviewed source to ``/versions/commit``, and discards it by doing nothing.
    """
    org_id, user_id = _get_user_context(request)
    logger.info("edit_workflow: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    if not body.instructions or not body.instructions.strip():
        raise HTTPException(status_code=400, detail="'instructions' must not be empty")
    try:
        result = await service.edit_workflow(
            workflow_id=workflow_id,
            org_id=org_id,
            user_id=user_id,
            instructions=body.instructions.strip(),
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("edit_workflow failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("edit_workflow: proposed %d chars of source", len(result.get("source", "")))
    return result


@router.post(
    "/{workflow_id}/versions/commit",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def commit_workflow_version(
    workflow_id: str,
    body: CommitVersionRequest,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Persist reviewed source as a new immutable version and pin it.

    `baseVersionId` (from `/edit`) makes the pin conditional so a concurrent
    edit is reported as a conflict instead of being silently overwritten.
    """
    org_id, user_id = _get_user_context(request)
    logger.info("commit_workflow_version: org=%s user=%s workflow_id=%s", org_id, user_id, workflow_id)
    if not body.source or not body.source.strip():
        raise HTTPException(status_code=400, detail="'source' must not be empty")
    try:
        version = await service.commit_version(
            workflow_id=workflow_id,
            org_id=org_id,
            user_id=user_id,
            source=body.source,
            base_version_id=body.base_version_id,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("commit_workflow_version failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("commit_workflow_version: pinned version_id=%s", version.version_id)
    return _version_to_dict(version)


@router.post(
    "/{workflow_id}/versions/{version_id}/activate",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)
async def activate_workflow_version(
    workflow_id: str,
    version_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> dict[str, Any]:
    """Roll back (or forward) to an existing version by re-pinning it."""
    org_id, user_id = _get_user_context(request)
    logger.info(
        "activate_workflow_version: org=%s workflow_id=%s version_id=%s", org_id, workflow_id, version_id,
    )
    try:
        version = await service.activate_version(
            workflow_id=workflow_id, version_id=version_id, org_id=org_id, user_id=user_id,
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("activate_workflow_version failed: version_id=%s error=%s", version_id, e)
        raise _handle_engine_error(e) from e
    return _version_to_dict(version)


@router.post(
    "/{workflow_id}/promote-to-agent",
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
    response_model=PromoteToAgentResponse,
)
async def promote_workflow_to_agent(
    workflow_id: str,
    request: Request,
    service=Depends(_get_workflow_service),
) -> PromoteToAgentResponse:
    """One-way copy into a standalone Agent Builder agent -- see
    `TaskEngine.promote_to_agent`'s own docstring ("not a live link")."""
    org_id, _user_id = _get_user_context(request)
    logger.info("promote_workflow_to_agent: org=%s workflow_id=%s", org_id, workflow_id)
    container = request.app.container
    try:
        agent_id = await service.promote_to_agent(
            workflow_id=workflow_id,
            org_id=org_id,
            graph_provider=await container.graph_provider(),
            config_service=container.config_service(),
        )
    except (WorkflowError, TaskEngineError) as e:
        logger.error("promote_workflow_to_agent failed: workflow_id=%s error=%s", workflow_id, e)
        raise _handle_engine_error(e) from e
    logger.info("promote_workflow_to_agent: done, agent_id=%s", agent_id)
    return PromoteToAgentResponse(agentId=agent_id)
