from __future__ import annotations


class AgentLoopError(Exception):
    """Base for all agent-loop errors."""


class AgentError(AgentLoopError):
    """Unrecoverable agent error."""


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
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
