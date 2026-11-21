# pyright: basic
from __future__ import annotations


class AgentLoopError(Exception):
    """Base for all agent-loop errors."""


class AgentError(AgentLoopError):
    """Unrecoverable agent error."""


class SpawnDepthError(AgentError):
    """A child was refused: the spawn tree is already at its maximum depth."""

    spawn_refusal_reason = "max_spawn_depth"


def spawn_refusal(exc: BaseException) -> dict[str, str] | None:
    """The `tool_call.end` fields of a spawn the library refused (the model asked for something it
    can't do: not our fault, so WARNING with no stack), or None for any other failure. A refusal is
    an exception class with a ``spawn_refusal_reason``: `SpawnDepthError` here, the host's spawn
    scheduler's own (``dependency_failed``), which this package cannot import."""
    reason = getattr(type(exc), "spawn_refusal_reason", None)
    if isinstance(reason, str):
        return {"reason": reason, "error_class": "bad_request"}
    return None


class ToolError(AgentLoopError):
    """Tool execution failed."""


class BudgetExceeded(AgentLoopError):
    """Token/cost budget exceeded."""


class HookBlocked(AgentLoopError):
    """A hook blocked a tool call."""


class BudgetStop(HookBlocked):
    """A PRE_TURN budget guard refused the next turn (BUDGETEND): the run answers from what it gathered on one
    answer-only turn instead of failing."""


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
    provider_code: the provider's own error code or type (``server_error``,
    ``rate_limit_exceeded``), a short identifier and never its message, so a
    log can say what the provider answered (WP-77).
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        partial_output: bool = False,
        provider_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code
        self.partial_output = partial_output
        self._transient = retryable
        self.retryable = retryable and not partial_output

    def discard_partial_output(self) -> None:
        """The caller has retracted everything this call streamed, so the
        failure is as retryable as it would have been before any output."""
        self.partial_output = False
        self.retryable = self._transient
