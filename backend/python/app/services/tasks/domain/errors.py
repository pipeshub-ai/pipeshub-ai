"""Domain-level errors for the task engine. Pure, no I/O."""
from __future__ import annotations


class TaskEngineError(Exception):
    """Base class for every error raised by `app.services.tasks`."""


class TaskNotFoundError(TaskEngineError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id}")
        self.task_id = task_id


class TriggerNotFoundError(TaskEngineError):
    def __init__(self, trigger_id: str) -> None:
        super().__init__(f"Trigger not found: {trigger_id}")
        self.trigger_id = trigger_id


class RunNotFoundError(TaskEngineError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run not found: {run_id}")
        self.run_id = run_id


class OptimisticConcurrencyError(TaskEngineError):
    """Raised by `ITaskStore.update()` when `expected_revision` does not
    match the currently stored revision."""

    def __init__(self, task_id: str, expected_revision: int, actual_revision: int) -> None:
        super().__init__(
            f"Task {task_id} revision mismatch: expected {expected_revision}, "
            f"got {actual_revision} -- reload and retry"
        )
        self.task_id = task_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision


class InvalidScheduleError(TaskEngineError):
    """Raised by `ScheduleCalculator` for a malformed cron expression,
    an impossible interval, or a `fire_at` in the past for a ONE_TIME
    trigger with no misfire tolerance."""


class PrerequisiteError(TaskEngineError):
    """Raised when a task cannot be created or run because a required
    connector/toolset/collection is missing or unauthenticated."""

    def __init__(self, message: str, missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.missing = missing or []


class ToolResolutionError(PrerequisiteError):
    """Raised by `TaskSpecAssembler.assemble()` when a task declares
    `tool_names` the loaded `ToolRegistry` cannot resolve.

    A `PrerequisiteError` because retrying cannot change the outcome: the
    registry is a deterministic function of the org's toolsets and the
    task's declaration, so the executor must finalize this as an orderly
    FAILED rather than a crash-retry. Granting the run some other set of
    tools instead (the old behaviour) is a privilege escalation, not a
    graceful degradation.
    """

    def __init__(self, missing: list[str], suggestions: dict[str, list[str]] | None = None) -> None:
        self.suggestions = suggestions or {}
        parts = []
        for name in missing:
            close = self.suggestions.get(name) or []
            parts.append(f"{name!r} (did you mean {', '.join(repr(c) for c in close)}?)" if close else repr(name))
        super().__init__(
            f"Tool(s) not available for this task: {'; '.join(parts)}",
            missing=list(missing),
        )


class BudgetExceededError(TaskEngineError):
    """Raised when a task run would exceed its `BudgetPolicy`."""


class TaskDAGError(TaskEngineError):
    """Raised for an invalid `TaskStep` graph (cycle, unknown dependency,
    duplicate id)."""


class InvalidTriggerError(TaskEngineError):
    """Raised for a `TaskTrigger` spec that is malformed for its `kind`
    (e.g. an `event` trigger with no `event_filter.event_type`), or that
    requires a capability the engine wasn't constructed with (e.g. a
    `webhook` trigger requested with no `IWebhookSecretStore` configured)."""


class WebhookVerificationError(TaskEngineError):
    """Raised by `WebhookDispatchService` for any reason an inbound webhook
    request must be rejected. `reason` is a short machine-readable code the
    HTTP layer uses to pick a status code -- never included verbatim in the
    response body, so a prober can't distinguish "unknown webhook_id" from
    "bad signature" by response text (only by status code, which is already
    necessary for HTTP semantics)."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        super().__init__(message or reason)
        self.reason = reason


class RateLimitExceededError(TaskEngineError):
    """Raised by `IRateLimiter`-gated call sites (webhook ingress) when the
    caller has exceeded its allotted request rate."""


class StaleAnswerError(TaskEngineError):
    """Raised by `TaskEngine.answer_run` when the run is no longer
    AWAITING_INPUT by the time the answer arrives -- e.g. it was already
    answered by a concurrent request, cancelled, or otherwise moved on.
    Never a race: `ITaskRunStore.resume_with_answer`'s AWAITING_INPUT ->
    PENDING transition is atomic, so at most one caller's answer is ever
    accepted for a given question."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"Run {run_id} is not awaiting input (already answered, or moved on)")
        self.run_id = run_id


class ExpiredSuspensionError(TaskEngineError):
    """Raised when an answer arrives for a run whose `resume_deadline_at` has
    passed. Resuming would replay the workflow against a journal that has
    since expired, so every step the first attempt completed would run a
    second time -- refusing is the only outcome that cannot duplicate work."""

    def __init__(self, run_id: str, deadline: str) -> None:
        super().__init__(
            f"Run {run_id} can no longer be resumed: it was suspended past its "
            f"replay deadline ({deadline}). Start a new run instead."
        )
        self.run_id = run_id
        self.deadline = deadline
