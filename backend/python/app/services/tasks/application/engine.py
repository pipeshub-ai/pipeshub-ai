"""`TaskEngine`: the use-case layer for task CRUD + lifecycle, shared by
every caller (chat agent tools -- Part A3/A4, the REST routes -- Phase 9,
and any future caller) so none of them re-implement optimistic-concurrency
retries, trigger scheduling, or the create-time prerequisite gate.

Depends only on ports (`ITaskStore`/`ITriggerStore`/`ITaskRunStore`/
`IClock`) plus the pure `domain` layer and, for prerequisite checks and
`promote_to_agent`, an injected `IGraphDBProvider` handle passed per-call
(never stored on `self`) -- this class itself has no hard dependency on a
concrete database, matching every other file in this package.
"""
from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.services.events.domain.matching import evaluate_filter, predicates_from_filter
from app.services.tasks.domain.dag_validation import validate_steps
from app.services.tasks.domain.errors import (
    ExpiredSuspensionError,
    InvalidScheduleError,
    InvalidTriggerError,
    OptimisticConcurrencyError,
    PrerequisiteError,
    RunNotFoundError,
    StaleAnswerError,
    TaskNotFoundError,
    TriggerNotFoundError,
)
from app.services.tasks.domain.models import (
    RunStatus,
    TaskDefinition,
    TaskPrincipal,
    TaskRun,
    TaskStatus,
    TaskTrigger,
    TriggerKind,
    compute_idempotency_key,
)
from app.services.tasks.domain.policies import BudgetPolicy, RetryPolicy
from app.services.tasks.domain.schedule_calculator import ScheduleCalculator
from app.services.tasks.interface.clock import SystemClock

if TYPE_CHECKING:
    from collections.abc import Sequence
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.interface.producer import IMessagingProducer
    from app.services.tasks.application.prerequisites import (
        PrerequisiteCheckResult,
        PrerequisiteValidator,
    )
    from app.services.tasks.domain.models import Page, TaskQuery, TaskStep
    from app.services.tasks.interface.clock import IClock
    from app.services.tasks.interface.run_store import ITaskRunStore
    from app.services.tasks.interface.task_store import ITaskStore
    from app.services.tasks.interface.trigger_store import ITriggerStore
    from app.services.tasks.interface.webhook_secret_store import IWebhookSecretStore

__all__ = ["TaskEngine"]

_TASK_RUN_DISPATCH_EVENT = "task_run_dispatch"
_SCHEDULED_TRIGGER_KINDS = frozenset(
    {TriggerKind.CRON, TriggerKind.INTERVAL, TriggerKind.ONE_TIME}
)
_TASK_STORE_UPDATE_RETRIES = 5
_WEBHOOK_SECRET_BYTES = 32


def _now_iso(clock: "IClock") -> str:
    return clock.now().astimezone(timezone.utc).isoformat()


def _validate_event_filter(event_filter: "dict[str, Any] | None") -> None:
    """Reject an event subscription the catalog says can never match.

    A typo'd `event_type`, or a field no verifier ever normalizes, stores fine
    and then sits inert -- the workflow looks subscribed and simply never
    fires. Checking at creation turns that into an error the author can act on.

    Only enforced for apps the catalog actually describes. The catalog covers
    the built-in providers, not every event type the platform can carry, so an
    uncatalogued type is allowed through rather than blocking a subscription
    the catalog was never meant to arbitrate.
    """
    event_type = str((event_filter or {}).get("event_type") or "")
    if not event_type:
        raise InvalidTriggerError(
            "An event trigger requires 'event_type' in its event_filter."
        )

    from app.services.events.catalog.registry import get_catalog

    catalog = get_catalog()
    source_app = event_type.split(".", 1)[0]
    if catalog.get(event_type) is None and source_app not in catalog.source_apps():
        return

    errors = catalog.validate_filter(event_type, predicates_from_filter(event_filter))
    if errors:
        raise InvalidTriggerError("; ".join(error.fix_hint for error in errors))


class TaskEngine:
    def __init__(
        self,
        *,
        task_store: "ITaskStore",
        trigger_store: "ITriggerStore",
        run_store: "ITaskRunStore",
        producer: "IMessagingProducer",
        prerequisite_validator: "PrerequisiteValidator | None" = None,
        webhook_secret_store: "IWebhookSecretStore | None" = None,
        calculator: ScheduleCalculator | None = None,
        clock: "IClock | None" = None,
        logger: "Logger | None" = None,
    ) -> None:
        self._task_store = task_store
        self._trigger_store = trigger_store
        self._run_store = run_store
        self._producer = producer
        self._prerequisite_validator = prerequisite_validator
        self._webhook_secret_store = webhook_secret_store
        self._calculator = calculator or ScheduleCalculator()
        self._clock: IClock = clock or SystemClock()
        self._logger = logger or logging.getLogger(__name__)

    # -- Create -----------------------------------------------------------

    async def create(
        self,
        *,
        org_id: str,
        user_id: str,
        user_email: str,
        title: str,
        description: str,
        instructions: str,
        is_service_account: bool = False,
        clarifications: list[dict[str, Any]] | None = None,
        reasoning: str | None = None,
        steps: "list[TaskStep] | None" = None,
        tool_names: list[str] | None = None,
        toolset_ids: list[str] | None = None,
        mcp_server_ids: list[str] | None = None,
        skill_names: list[str] | None = None,
        collection_ids: list[str] | None = None,
        connector_ids: list[str] | None = None,
        model_ref: str | None = None,
        loop_strategy_name: str = "react",
        max_turns: int = 15,
        timeout_seconds: int = 900,
        retry_policy: RetryPolicy | None = None,
        budget: BudgetPolicy | None = None,
        triggers: list[dict[str, Any]] | None = None,
        created_from_conversation_id: str | None = None,
        graph_provider: "IGraphDBProvider | None" = None,
        config_service: "ConfigurationService | None" = None,
        skip_prerequisite_check: bool = False,
    ) -> tuple[TaskDefinition, list[TaskTrigger], "PrerequisiteCheckResult | None", dict[str, str]]:
        """Validates the step DAG (if any) and prerequisites (unless
        skipped or no validator/graph_provider is available), then
        persists the task DRAFT->ACTIVE and its triggers together.

        Raises `TaskDAGError` for an invalid step graph, `PrerequisiteError`
        for a BLOCKING missing prerequisite (see `PrerequisiteCheckResult
        .ok`) -- a non-blocking issue (today: only `mcp_server_ids`, which
        has nothing to verify against) is returned in the third tuple
        element instead of raising, so the caller can still surface it as
        an FYI without failing creation over it. Raises `InvalidTriggerError`
        for a malformed trigger spec, or a `webhook`-kind trigger requested
        with no `webhook_secret_store` configured on this engine.

        The fourth tuple element, `webhook_secrets`, maps each newly created
        webhook trigger's `trigger_id` to its plaintext HMAC secret --
        available ONLY on this call. The secret itself is never persisted
        in plaintext (see `IWebhookSecretStore`) and this return value is
        the caller's only chance to show it to the user (Part E: "Webhook
        secrets in EncryptedKeyValueStore" -- reveal-once, same convention
        as e.g. a cloud provider's access-key creation flow).
        """
        validate_steps(steps)

        check_result: "PrerequisiteCheckResult | None" = None
        if not skip_prerequisite_check and self._prerequisite_validator is not None and graph_provider is not None:
            check_result = await self._prerequisite_validator.validate(
                org_id=org_id, user_id=user_id,
                connector_ids=connector_ids or [], collection_ids=collection_ids or [],
                mcp_server_ids=mcp_server_ids or [],
                # Without these two the toolset-auth branch never fires, so a
                # workflow could be scheduled against a toolset the user has
                # not authenticated and only fail at 2am on its first run.
                toolset_ids=toolset_ids or [],
                config_service=config_service,
                is_service_account=is_service_account,
                graph_provider=graph_provider,
            )
            if not check_result.ok:
                raise PrerequisiteError(
                    f"Cannot create task {title!r}: {check_result.summary()}",
                    missing=[f"{i.kind}:{i.id}" for i in check_result.blocking_issues],
                )

        now = _now_iso(self._clock)
        task = TaskDefinition(
            org_id=org_id,
            created_by_user_id=user_id,
            principal=TaskPrincipal(
                org_id=org_id, user_id=user_id, user_email=user_email, is_service_account=is_service_account,
            ),
            title=title,
            description=description,
            instructions=instructions,
            clarifications=clarifications or [],
            reasoning=reasoning,
            steps=steps,
            tool_names=tool_names or [],
            toolset_ids=toolset_ids or [],
            mcp_server_ids=mcp_server_ids or [],
            skill_names=skill_names or [],
            collection_ids=collection_ids or [],
            connector_ids=connector_ids or [],
            model_ref=model_ref,
            loop_strategy_name=loop_strategy_name,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy or RetryPolicy(),
            budget=budget or BudgetPolicy(),
            status=TaskStatus.ACTIVE,
            created_from_conversation_id=created_from_conversation_id,
            created_at=now,
            updated_at=now,
        )
        created = await self._task_store.create(task)

        created_triggers: list[TaskTrigger] = []
        webhook_secrets: dict[str, str] = {}
        for spec in triggers or []:
            trigger, secret = await self._persist_trigger(created.task_id, org_id, spec)
            if secret is not None:
                webhook_secrets[trigger.trigger_id] = secret
            created_triggers.append(trigger)

        return created, created_triggers, check_result, webhook_secrets

    # -- Triggers -------------------------------------------------------------

    async def add_trigger(
        self,
        task_id: str,
        org_id: str,
        spec: dict[str, Any],
        *,
        trigger_id: str | None = None,
    ) -> tuple[TaskTrigger, str | None]:
        """Attach one trigger to an existing task, going through the same
        validation + `next_run_at` computation `create` uses.

        Callers outside this class must use this instead of reaching into
        `ITriggerStore.upsert` directly: a trigger persisted without
        `next_run_at` is never indexed by the due-set and silently never
        fires. Returns `(trigger, webhook_secret_or_None)`.

        `trigger_id` lets a caller supply a deterministic id so that
        re-deriving the same declarative trigger updates the existing row
        instead of inserting a duplicate on every code regeneration.
        """
        await self.get(task_id, org_id)
        return await self._persist_trigger(task_id, org_id, spec, trigger_id=trigger_id)

    async def get_trigger(self, trigger_id: str, org_id: str) -> TaskTrigger:
        """Ownership-checked single-trigger read. Raises
        `TriggerNotFoundError` for an unknown trigger or one belonging to a
        different org, so a caller cannot probe ids across tenants."""
        trigger = await self._trigger_store.get(trigger_id)
        if trigger is None or trigger.org_id != org_id:
            raise TriggerNotFoundError(trigger_id)
        return trigger

    async def set_trigger_enabled(
        self, trigger_id: str, org_id: str, *, enabled: bool, task_id: str | None = None,
    ) -> TaskTrigger:
        """Pause or resume one trigger without deleting it.

        Re-enabling recomputes `next_run_at`: a cron trigger disabled for a
        week would otherwise come back with a fire time in the past and
        immediately fire (or be skipped, depending on its misfire policy)
        rather than resuming on its normal schedule.
        """
        trigger = await self.get_trigger(trigger_id, org_id)
        if task_id is not None and trigger.task_id != task_id:
            raise TriggerNotFoundError(trigger_id)
        if trigger.enabled == enabled:
            return trigger

        updated = trigger.model_copy(update={"enabled": enabled})
        if enabled:
            updated = updated.model_copy(update={
                "next_run_at": self._calculator.next_run_at(updated, after=self._clock.now()),
            })
        stored = await self._trigger_store.upsert(updated)
        self._logger.info(
            "Trigger %s (task=%s org=%s) %s; next_run_at=%s",
            trigger_id, trigger.task_id, org_id,
            "enabled" if enabled else "disabled", stored.next_run_at,
        )
        return stored

    async def delete_trigger(self, trigger_id: str, org_id: str) -> bool:
        """Ownership-checked single-trigger removal, revoking the webhook
        secret first for the same reason `_delete_triggers_and_secrets`
        does. Raises `TriggerNotFoundError` for an unknown trigger or one
        belonging to a different org."""
        trigger = await self._trigger_store.get(trigger_id)
        if trigger is None or trigger.org_id != org_id:
            raise TriggerNotFoundError(trigger_id)
        if (
            trigger.kind == TriggerKind.WEBHOOK
            and trigger.webhook_id
            and self._webhook_secret_store is not None
        ):
            await self._webhook_secret_store.delete(trigger.webhook_id)
        return await self._trigger_store.delete(trigger_id)

    async def _persist_trigger(
        self,
        task_id: str,
        org_id: str,
        spec: Any,
        *,
        trigger_id: str | None = None,
    ) -> tuple[TaskTrigger, str | None]:
        if not isinstance(spec, dict):
            raise InvalidTriggerError(f"Invalid trigger spec {spec!r}: expected an object")
        kind = spec.get("kind")
        if kind is None:
            valid_kinds = ", ".join(k.value for k in TriggerKind)
            raise InvalidTriggerError(
                f"Invalid trigger spec {spec!r}: missing required field 'kind' (one of: {valid_kinds})"
            )
        # Server-generated only -- never trust a caller-supplied
        # webhook_id, which would let a chat user (or a REST caller)
        # point a trigger at a webhook_id they don't control the
        # secret for, or collide with another org's webhook.
        webhook_id = str(uuid.uuid4()) if kind == TriggerKind.WEBHOOK else None
        fields: dict[str, Any] = {
            "task_id": task_id,
            "org_id": org_id,
            "kind": kind,
            "cron_expression": spec.get("cron_expression"),
            "interval_seconds": spec.get("interval_seconds"),
            "fire_at": spec.get("fire_at"),
            "timezone": spec.get("timezone", "UTC"),
            "event_filter": spec.get("event_filter"),
            "webhook_id": webhook_id,
            "misfire_policy": spec.get("misfire_policy", "skip"),
            "max_runs": spec.get("max_runs"),
        }
        if trigger_id:
            fields["trigger_id"] = trigger_id
        try:
            trigger = TaskTrigger(**fields)
        except ValidationError as exc:
            raise InvalidTriggerError(f"Invalid trigger spec {spec!r}: {exc}") from exc

        if trigger.kind == TriggerKind.EVENT:
            _validate_event_filter(trigger.event_filter)

        try:
            next_run_at = self._calculator.next_run_at(trigger, after=self._clock.now())
        except InvalidScheduleError as exc:
            raise InvalidTriggerError(f"Invalid trigger spec {spec!r}: {exc}") from exc
        if next_run_at is None and trigger.kind in _SCHEDULED_TRIGGER_KINDS:
            # A scheduled trigger with no computable next fire (past
            # `fire_at`, unparseable cron) would be stored as a row that can
            # never fire; surface it at creation instead of silently.
            raise InvalidTriggerError(
                f"Trigger spec {spec!r} resolves to no future fire time; "
                "use a future fire_at / a valid cron expression."
            )

        secret: str | None = None
        if trigger.kind == TriggerKind.WEBHOOK:
            if self._webhook_secret_store is None:
                raise InvalidTriggerError(
                    "Cannot create a webhook trigger: this engine has no webhook_secret_store configured"
                )
            secret = secrets.token_urlsafe(_WEBHOOK_SECRET_BYTES)
            await self._webhook_secret_store.store(webhook_id, secret)  # type: ignore[arg-type]

        stored = await self._trigger_store.upsert(
            trigger.model_copy(update={"next_run_at": next_run_at})
        )
        return stored, secret

    # -- Read ---------------------------------------------------------------

    async def get(self, task_id: str, org_id: str) -> TaskDefinition:
        task = await self._task_store.get(task_id, org_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    async def find(self, query: "TaskQuery") -> "Page[TaskDefinition]":
        return await self._task_store.list(query)

    async def list_triggers(self, task_id: str) -> list[TaskTrigger]:
        return await self._trigger_store.list_for_task(task_id)

    async def list_triggers_for_tasks(
        self, task_ids: "Sequence[str]",
    ) -> dict[str, list[TaskTrigger]]:
        """Triggers for a page of tasks, keyed by task id. Every task id asked
        for is present, so a caller can index without guarding."""
        found = await self._trigger_store.list_for_tasks(task_ids)
        return {task_id: found.get(task_id, []) for task_id in task_ids}

    async def list_runs(self, task_id: str, *, limit: int = 50, offset: int = 0) -> "Page[TaskRun]":
        return await self._run_store.list_for_task(task_id, limit=limit, offset=offset)

    async def get_run(self, run_id: str, task_id: str, org_id: str) -> TaskRun:
        """`task_id`/`org_id` are ownership checks, not lookup keys --
        `ITaskRunStore.get` is keyed by `run_id` alone (see that method's
        own docstring), so without this check a caller who merely knows
        another org's `run_id` could read its contents. Raises
        `RunNotFoundError` for a missing run OR one belonging to a
        different task/org, deliberately not distinguishing the two in the
        error (same reasoning as `WebhookVerificationError` never leaking
        which check failed)."""
        run = await self._run_store.get(run_id)
        if run is None or run.task_id != task_id or run.org_id != org_id:
            raise RunNotFoundError(run_id)
        return run

    # -- Lifecycle ------------------------------------------------------------

    async def pause(self, task_id: str, org_id: str) -> TaskDefinition:
        return await self._update_status(task_id, org_id, status=TaskStatus.PAUSED, enabled=False)

    async def unpause(self, task_id: str, org_id: str) -> TaskDefinition:
        return await self._update_status(task_id, org_id, status=TaskStatus.ACTIVE, enabled=True)

    async def cancel(self, task_id: str, org_id: str) -> TaskDefinition:
        """Soft delete (Part L: "cancel is soft-delete") -- the task row is
        kept for audit/run-history purposes, but its triggers are removed
        so it never fires again."""
        updated = await self._update_status(task_id, org_id, status=TaskStatus.CANCELLED, enabled=False)
        await self._delete_triggers_and_secrets(task_id)
        return updated

    async def _delete_triggers_and_secrets(self, task_id: str) -> int:
        """Removes every trigger for `task_id`, also revoking any webhook
        secret first -- a leaked/rotated secret for a trigger whose task
        was just cancelled must stop verifying immediately, not linger in
        `IWebhookSecretStore` as an orphan."""
        if self._webhook_secret_store is not None:
            for trigger in await self._trigger_store.list_for_task(task_id):
                if trigger.kind == TriggerKind.WEBHOOK and trigger.webhook_id:
                    await self._webhook_secret_store.delete(trigger.webhook_id)
        return await self._trigger_store.delete_for_task(task_id)

    async def _update_status(self, task_id: str, org_id: str, *, status: TaskStatus, enabled: bool) -> TaskDefinition:
        for _ in range(_TASK_STORE_UPDATE_RETRIES):
            current = await self.get(task_id, org_id)
            updated = current.model_copy(update={
                "status": status, "enabled": enabled, "updated_at": _now_iso(self._clock),
            })
            try:
                return await self._task_store.update(updated, expected_revision=current.revision)
            except OptimisticConcurrencyError:
                continue
        raise OptimisticConcurrencyError(task_id, -1, -1)

    async def update_fields(self, task_id: str, org_id: str, **fields: object) -> TaskDefinition:
        """Partial update of any `TaskDefinition` field (title, instructions,
        tool_names, budget, ...). Optimistic-concurrency-safe: re-reads and
        retries on a revision conflict rather than surfacing it to a chat
        turn that has no way to resolve a merge itself."""
        for _ in range(_TASK_STORE_UPDATE_RETRIES):
            current = await self.get(task_id, org_id)
            updated = current.model_copy(update={**fields, "updated_at": _now_iso(self._clock)})
            try:
                return await self._task_store.update(updated, expected_revision=current.revision)
            except OptimisticConcurrencyError:
                continue
        raise OptimisticConcurrencyError(task_id, -1, -1)

    async def delete(self, task_id: str, org_id: str) -> bool:
        """Hard delete -- see `ITaskStore.delete`'s own docstring on when
        this (vs. `cancel`) is appropriate."""
        await self._delete_triggers_and_secrets(task_id)
        return await self._task_store.delete(task_id, org_id)

    # -- Run now / trigger dispatch --------------------------------------------

    async def run_now(self, task_id: str, org_id: str) -> TaskRun:
        """User-triggered immediate execution, independent of any trigger's
        schedule -- e.g. `task_manage(action="run_now")`. Keyed by
        `fire_time=now` so two rapid `run_now` calls in the same instant
        collapse to one run rather than double-executing."""
        task = await self.get(task_id, org_id)
        return await self._dispatch_run(task, trigger_id=None, fire_time=_now_iso(self._clock))

    async def dry_run(self, task_id: str, org_id: str) -> TaskRun:
        """Like `run_now` but sets `is_dry_run=True` on the run so the
        executor skips WRITE-side-effected steps and skips notifications.
        Each call always creates a fresh run (not idempotency-deduplicated)
        because dry runs are inherently one-shot debug tools."""
        task = await self.get(task_id, org_id)
        fire_time = _now_iso(self._clock)
        import uuid as _uuid
        run = TaskRun(
            task_id=task.task_id,
            trigger_id=None,
            org_id=task.org_id,
            idempotency_key=f"dryrun:{task.task_id}:{_uuid.uuid4()}",
            scheduled_for=fire_time,
            created_at=fire_time,
            is_dry_run=True,
        )
        created = await self._run_store.create_if_absent(run)
        if created is None:
            created = run
        await self._publish_dispatch(created)
        return created

    async def fire_trigger(
        self,
        trigger_id: str,
        *,
        payload: dict[str, Any] | None = None,
        dedupe_token: str | None = None,
    ) -> TaskRun:
        """Dispatch a run for an EVENT or WEBHOOK trigger firing right now
        (as opposed to a CRON/INTERVAL/ONE_TIME trigger, which
        `SchedulerLoop` dispatches via `claim_due`). Shared by
        `WebhookDispatchService` and `fire_event` so both go through the
        same enabled/active checks and the same idempotent-dispatch path
        `run_now`/`SchedulerLoop` use.

        `dedupe_token` identifies the delivery this fire is for -- a
        provider delivery id, typically. Passing it makes the whole call
        idempotent: a redelivered message re-uses the existing run and
        does not consume a second `max_runs` slot. Callers with no stable
        delivery identity omit it and get one run per call.

        Raises `TriggerNotFoundError` for an unknown trigger,
        `TaskNotFoundError` if its parent task is gone, and
        `InvalidTriggerError` if the trigger or its task is disabled, or
        if it has exhausted `max_runs` -- callers (e.g. the webhook HTTP
        layer) should treat those as client-visible rejections, not 5xx.
        """
        trigger = await self._trigger_store.get(trigger_id)
        if trigger is None:
            raise TriggerNotFoundError(trigger_id)
        if not trigger.enabled:
            raise InvalidTriggerError(f"Trigger {trigger_id} is disabled")
        task = await self._task_store.get(trigger.task_id, trigger.org_id)
        if task is None:
            raise TaskNotFoundError(trigger.task_id)
        if not task.enabled or task.status != TaskStatus.ACTIVE:
            raise InvalidTriggerError(f"Task {task.task_id} is not active (status={task.status.value})")

        fire_time = _now_iso(self._clock)
        occasion = dedupe_token or fire_time
        # `ScheduleCalculator` enforces `max_runs` for the clock-driven kinds by
        # refusing to compute a `next_run_at`. Event and webhook triggers never
        # go through it, so without this claim their `max_runs` is decorative
        # and a "run this at most once" webhook fires forever. Claimed before
        # dispatch, not after: the reverse would let a concurrent delivery
        # dispatch a second run in the window before the count lands.
        claimed = await self._trigger_store.claim_fire(
            trigger.trigger_id, fire_at=fire_time, dedupe_token=occasion,
        )
        if claimed is None:
            raise InvalidTriggerError(
                f"Trigger {trigger_id} has reached its max_runs limit "
                f"({trigger.run_count}/{trigger.max_runs})",
            )
        return await self._dispatch_run(
            task,
            trigger_id=trigger.trigger_id,
            fire_time=fire_time,
            payload=payload,
            occasion=occasion,
        )

    async def answer_run(self, run_id: str, task_id: str, org_id: str, answer: str) -> TaskRun:
        """Answers an AWAITING_INPUT run's pending question, resuming
        execution from its checkpoint. Ownership-checked via `get_run`
        first (raises `RunNotFoundError` for a missing run or one
        belonging to a different task/org), then atomically transitioned
        via `ITaskRunStore.resume_with_answer` -- raises `StaleAnswerError`
        if that transition fails because the run is no longer
        AWAITING_INPUT (already answered concurrently, cancelled, or
        otherwise moved on since the question was asked).

        Publishes the same `_TASK_RUN_DISPATCH_EVENT` `TaskExecutor`
        already consumes, so answering reuses the ordinary claim/lease/
        heartbeat machinery rather than a bespoke resume path."""
        run = await self.get_run(run_id, task_id, org_id)
        await self._reject_if_replay_expired(run)
        resumed = await self._run_store.resume_with_answer(run.run_id, answer=answer)
        if resumed is None:
            raise StaleAnswerError(run_id)
        await self._publish_dispatch(resumed)
        return resumed

    async def _reject_if_replay_expired(self, run: TaskRun) -> None:
        """Refuse to resume a run that outlived its journal, and retire it.

        A code workflow resumes by re-running from the top and skipping steps
        the journal says already happened. Once the journal expires that skip
        list is empty, so an answer arriving late does not continue the run --
        it silently repeats every side effect the first attempt performed.
        Failing the run here is what keeps that from happening, and it also
        stops the stale question from sitting in the UI forever.
        """
        deadline = getattr(run, "resume_deadline_at", None)
        if not deadline:
            return
        try:
            expires_at = datetime.fromisoformat(deadline)
        except ValueError:
            self._logger.warning(
                "Run %s has an unparseable resume_deadline_at %r; allowing resume",
                run.run_id, deadline,
            )
            return
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if self._clock.now().astimezone(timezone.utc) <= expires_at:
            return

        self._logger.warning(
            "Run %s cannot be resumed: suspended past its replay deadline %s",
            run.run_id, deadline,
        )
        await self._run_store.update(run.model_copy(update={
            "status": RunStatus.FAILED,
            "error": (
                "Suspended longer than the execution journal is retained; "
                "resuming would have re-run completed steps."
            ),
            "completed_at": self._clock.now().astimezone(timezone.utc).isoformat(),
        }))
        raise ExpiredSuspensionError(run.run_id, deadline)

    async def fire_event(self, org_id: str, event_type: str, payload: dict[str, Any]) -> list[TaskRun]:
        """Dispatches every enabled EVENT trigger in `org_id` whose
        `event_filter` matches `event_type`/`payload`. Best-effort per
        trigger -- one broken/disabled trigger must not stop the others
        from firing (same isolation principle as `SchedulerLoop`'s per-
        trigger tick handling).

        Matching goes through `domain/matching.evaluate_filter`, the same
        evaluator the catalog validates filters against, so dot paths and the
        non-equality operators work and a filter written as
        `on_event(..., channel="C123")` still matches the `channel: {id, name}`
        object the verifiers normalize to. The previous flat top-level equality
        could match neither.

        A trigger that refuses the event (disabled, paused task, `max_runs`
        spent) is skipped and logged. Anything else -- a store or broker
        failure -- propagates, so the caller can retry the whole event
        rather than silently losing it; `payload["_dedupe_key"]`, when
        present, makes that retry land on the same runs instead of
        duplicating the ones that already succeeded.
        """
        candidates = await self._trigger_store.list_by_event_type(org_id, event_type)
        dedupe_key = payload.get("_dedupe_key") or None
        runs: list[TaskRun] = []
        matched = 0
        for trigger in candidates:
            predicates = predicates_from_filter(trigger.event_filter)
            if not evaluate_filter(predicates, payload):
                continue
            matched += 1
            try:
                runs.append(await self.fire_trigger(
                    trigger.trigger_id,
                    payload=payload,
                    dedupe_token=f"{dedupe_key}:{trigger.trigger_id}" if dedupe_key else None,
                ))
            except (InvalidTriggerError, TriggerNotFoundError, TaskNotFoundError) as exc:
                self._logger.info(
                    "fire_event: trigger %s declined event %s (org=%s): %s",
                    trigger.trigger_id, event_type, org_id, exc,
                )

        resumed = await self._resume_runs_awaiting(org_id, event_type, payload)
        runs.extend(resumed)
        self._logger.info(
            "fire_event: event_type=%s org=%s dedupe_key=%s candidates=%d matched=%d "
            "dispatched=%d resumed=%d",
            event_type, org_id, dedupe_key, len(candidates), matched,
            len(runs) - len(resumed), len(resumed),
        )
        return runs

    async def _resume_runs_awaiting(
        self, org_id: str, event_type: str, payload: dict[str, Any],
    ) -> list[TaskRun]:
        """Wake runs parked on `ctx.wait_for_event(event_type)`.

        Distinct from firing a trigger: a trigger starts a new run, this
        continues one that already exists. Without it `ctx.wait_for_event`
        suspends a run that nothing can ever resume.

        The event payload is delivered through the same `resume_with_answer`
        path a human approval uses, so resumption reuses the existing
        claim/lease/replay machinery rather than a second resume path.

        Every parked run is attempted even if an earlier one fails -- one
        wedged run must not strand the rest -- but the first failure is
        re-raised afterwards so the caller retries the event. Retrying is
        safe: `resume_with_answer` no-ops on a run that already left
        AWAITING_INPUT.
        """
        resumed: list[TaskRun] = []
        parked = await self._run_store.list_awaiting_event(org_id, event_type)

        failure: Exception | None = None
        for run in parked:
            try:
                answer = json.dumps(payload)
            except (TypeError, ValueError):
                answer = str(payload)
            try:
                await self._reject_if_replay_expired(run)
                updated = await self._run_store.resume_with_answer(run.run_id, answer=answer)
                if updated is None:
                    # Answered or cancelled between the listing and here.
                    continue
                await self._publish_dispatch(updated)
                resumed.append(updated)
            except ExpiredSuspensionError:
                # Already failed and logged by the guard. Not a delivery
                # problem, so it must not make the caller retry the event.
                continue
            except Exception as exc:
                self._logger.exception(
                    "fire_event: failed to resume run %s awaiting %s (org=%s)",
                    run.run_id, event_type, org_id,
                )
                failure = failure or exc
        if failure is not None:
            raise failure
        return resumed

    async def _dispatch_run(
        self,
        task: TaskDefinition,
        *,
        trigger_id: str | None,
        fire_time: str,
        payload: dict[str, Any] | None = None,
        occasion: str | None = None,
    ) -> TaskRun:
        """Idempotent-create-then-publish, shared by `run_now`/`fire_trigger`
        and `SchedulerLoop._dispatch_run`'s own claim-driven dispatch path
        -- keeping exactly one place that knows how a `TaskRun` gets born
        avoids the two paths drifting (e.g. one publishing a payload shape
        the other doesn't).

        `occasion` defaults to `fire_time`; see `compute_idempotency_key`.
        """
        run = TaskRun(
            task_id=task.task_id,
            trigger_id=trigger_id,
            org_id=task.org_id,
            idempotency_key=compute_idempotency_key(task.task_id, occasion or fire_time),
            scheduled_for=fire_time,
            created_at=fire_time,
            trigger_payload=payload or {},
        )
        created = await self._run_store.create_if_absent(run)
        if created is None:
            existing = await self._run_store.get_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return existing
            raise RunNotFoundError(run.run_id)
        await self._publish_dispatch(created)
        return created

    async def _publish_dispatch(self, run: TaskRun) -> None:
        """Shared by `_dispatch_run` (fresh run) and `answer_run` (resumed
        run) -- both a brand-new PENDING run and one just returned to
        PENDING by an answer are picked up by `TaskExecutor.handle_dispatch`
        the same way, so they publish the identical event shape."""
        await self._producer.send_event(
            topic=self._task_events_topic(),
            event_type=_TASK_RUN_DISPATCH_EVENT,
            payload={
                "run_id": run.run_id,
                "task_id": run.task_id,
                "trigger_id": run.trigger_id,
                "org_id": run.org_id,
                "scheduled_for": run.scheduled_for,
                "idempotency_key": run.idempotency_key,
            },
            key=run.task_id,
        )

    @staticmethod
    def _task_events_topic() -> str:
        from app.services.messaging.config import Topic

        return Topic.TASK_EVENTS.value

    # -- Promote to agent -----------------------------------------------------

    async def promote_to_agent(
            self, task_id: str, org_id: str, *, graph_provider: "IGraphDBProvider", config_service: "ConfigurationService",
        ) -> str:
        """One-way copy (Part A4: "not a live link") into a standalone
        Agent Builder agent. Delegates the graph-write shape to
        `promote_to_agent.create_agent_from_task` -- kept in its own module
        since it duplicates (deliberately, not by import -- see that
        module's docstring on why) `api/routes/agent.py`'s graph-write
        sequence."""
        from app.services.tasks.application.promote_to_agent import (
            create_agent_from_task,
        )

        task = await self.get(task_id, org_id)
        agent_id = await create_agent_from_task(task, graph_provider=graph_provider, config_service=config_service)
        await self.update_fields(task_id, org_id, promoted_agent_id=agent_id)
        return agent_id
