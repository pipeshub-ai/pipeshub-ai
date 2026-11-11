from __future__ import annotations


class AgentLoopError(Exception):
    """Base for all agent-loop errors."""


class AgentError(AgentLoopError):
    """Unrecoverable agent error."""


class SpawnDepthError(AgentError):
    """A child was refused: the spawn tree is already at its maximum depth."""


def spawn_refusal(exc: BaseException) -> dict[str, str] | None:
    """The `tool_call.end` fields of a spawn the library refused (the model asked for something it
    can't do: not our fault, so WARNING with no stack), or None for any other failure."""
    # Imported here: spawn_scheduler imports the coordination tools, which import this module.
    from app.agent_loop_lib.agent.spawn_scheduler import SpawnDependencyError

    if isinstance(exc, SpawnDepthError):
        return {"reason": "max_spawn_depth", "error_class": "bad_request"}
    if isinstance(exc, SpawnDependencyError):
        # A failed prerequisite, or one of the batch's validation errors (duplicate task_id, cycle, ...).
        return {"reason": "dependency_failed", "error_class": "bad_request"}
    return None


class ToolError(AgentLoopError):
    """Tool execution failed."""


class BudgetExceeded(AgentLoopError):
    """Token/cost budget exceeded."""


class HookBlocked(AgentLoopError):
    """A hook blocked a tool call."""


class RunCancelled(AgentLoopError):
    """A PRE_TURN guard observed a cancelled `CancellationToken`.

    Distinct from `HookBlocked` so `Agent.run()` can preserve the
    cancellation-specific outcome shape (status="cancelled", EventType.
    CANCELLATION) even though the underlying check now runs through the
    same PRE_TURN middleware pipeline as any other turn guard — see
    `hooks.builtin.turn_guards.check_not_cancelled`.
    """


class FeasibilityError(AgentLoopError):
    """Required tools unavailable for goal."""


class RegistryError(AgentLoopError):
    """Registry key not found."""


class PlanningError(AgentLoopError):
    """Planning failed."""


class TransportError(AgentLoopError):
    """LLM transport request failed.

    status_code: HTTP-ish status code if the provider exposed one (429, 500, ...).
    retryable: whether the RetryHook should retry this call. Transports should
    set this explicitly based on the underlying SDK exception type — network
    timeouts and 429/5xx are typically retryable, 4xx client errors are not.
    partial_output: part of the response had already been streamed to the
    caller when the call failed. Re-running the call would show that part a
    second time, so `retryable` is False until the caller retracts what it
    passed on and says so with `discard_partial_output()`.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        partial_output: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.partial_output = partial_output
        self._transient = retryable
        self.retryable = retryable and not partial_output

    def discard_partial_output(self) -> None:
        """The caller has retracted everything this call streamed, so the
        failure is as retryable as it would have been before any output."""
        self.partial_output = False
        self.retryable = self._transient
