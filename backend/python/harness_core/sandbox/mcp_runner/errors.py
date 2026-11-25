"""Typed refusals of MCP runners; each ``reason_code`` names one cause (C-6)."""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class MCPRunnerRefused(HarnessRefusal):
    reason_code: str = "runner_refused"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason_code = reason


class RunnerUnpinned(MCPRunnerRefused):
    """The command fetches code when it starts (``npx -y``, ``uvx``): D23 runs only what the image holds."""

    reason_code = "runner_unpinned"


class RunnerSpecInvalid(MCPRunnerRefused):
    reason_code = "runner_spec_invalid"


class RunnerEnvUndeclared(MCPRunnerRefused):
    reason_code = "runner_env_undeclared"


class RunnerOwnerMismatch(MCPRunnerRefused):
    """A secret of one owner asked for another owner's runner."""

    reason_code = "runner_owner_mismatch"


class RunnerIsolationUnavailable(MCPRunnerRefused):
    reason_code = "runner_isolation_unavailable"


class RunnerEgressInvalid(MCPRunnerRefused):
    reason_code = "runner_egress_invalid"


class RunnerUnavailable(MCPRunnerRefused):
    """The broker refused or could not be reached; ``cause`` is its own reason code where it gave one."""

    reason_code = "runner_unavailable"

    def __init__(self, message: str, *, cause: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class RunnerStartFailed(MCPRunnerRefused):
    reason_code = "runner_start_failed"


__all__ = [
    "MCPRunnerRefused",
    "RunnerEgressInvalid",
    "RunnerEnvUndeclared",
    "RunnerIsolationUnavailable",
    "RunnerOwnerMismatch",
    "RunnerSpecInvalid",
    "RunnerStartFailed",
    "RunnerUnavailable",
    "RunnerUnpinned",
]
