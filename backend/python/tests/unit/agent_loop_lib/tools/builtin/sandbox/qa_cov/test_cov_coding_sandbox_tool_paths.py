"""Paths of the coding sandbox tools the main suites leave unexercised: the
model's timeout argument, the run outcome logged for each failure kind, and
`install_packages` stopped or refused at each stage."""

from __future__ import annotations

import asyncio
import math

import pytest

from app.agent_loop_lib.sandbox.coding.base import (
    CodeResult,
    ErrorAnalysis,
    ErrorCategory,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxCallStopped,
    SandboxLimitExceeded,
)
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    InstallPackagesTool,
    _resolve_timeout,
    _run_outcome,
)


@pytest.mark.parametrize(("timeout", "cap", "expected"), [
    (None, 300.0, (60.0, 60.0, False)),
    ("abc", 300.0, (60.0, 60.0, False)),
    ([1, 2], 300.0, (60.0, 60.0, False)),
    (math.nan, 300.0, (60.0, 60.0, False)),
    ("nan", 300.0, (60.0, 60.0, False)),
    (0, 300.0, (1.0, 1.0, False)),
    (-5, 300.0, (1.0, 1.0, False)),
    ("12.5", 300.0, (12.5, 12.5, False)),
    (300, 300.0, (300.0, 300.0, False)),
    (301, 300.0, (301.0, 300.0, True)),
    (math.inf, 300.0, (math.inf, 300.0, True)),
    (10_000, None, (10_000.0, 10_000.0, False)),
])
def test_resolve_timeout(timeout: object, cap: float | None, expected: tuple) -> None:
    assert _resolve_timeout(timeout, 60.0, cap) == expected


def _result(success: bool, category: ErrorCategory | None) -> CodeResult:
    analysis = None if category is None else ErrorAnalysis(
        category=category, message="m", suggestion="s", is_retryable=False,
    )
    return CodeResult(stdout="", stderr="", exit_code=0 if success else 1, language="python",
                      duration_ms=1.0, error_analysis=analysis)


@pytest.mark.parametrize(("success", "category", "outcome"), [
    (True, None, "ok"),
    (False, ErrorCategory.TIMEOUT, "timeout"),
    (False, ErrorCategory.OUT_OF_MEMORY, "out_of_memory"),
    (False, ErrorCategory.RUNTIME, "error"),
    (False, None, "error"),
])
def test_run_outcome(success: bool, category: ErrorCategory | None, outcome: str) -> None:
    assert _run_outcome(_result(success, category)) == outcome


class _Manager:
    """`unless_stopped` raises what the test asks for at the first or the
    second call (get_or_create, then the install under a slot)."""

    def __init__(self, raise_at: int, exc: BaseException) -> None:
        self.raise_at = raise_at
        self.exc = exc
        self.calls = 0
        self.destroyed: list[str] = []

    async def unless_stopped(self, fn: object, *, tool: str) -> tuple[str, object]:
        self.calls += 1
        if self.calls == self.raise_at:
            raise self.exc
        return "sbx-1", object()

    async def destroy(self, sandbox_type: object, sandbox_id: str) -> None:
        self.destroyed.append(sandbox_id)


@pytest.fixture(autouse=True)
def _no_staged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox as mod

    async def none(_backend: object) -> list[str]:
        return []

    monkeypatch.setattr(mod, "_upload_staged_files", none)


class TestInstallPackagesRefusals:
    async def test_stopped_before_the_sandbox_exists(self) -> None:
        out = await InstallPackagesTool(_Manager(1, SandboxCallStopped("install_packages"))).execute(
            packages=["x"], language="python",
        )
        assert out.success is False
        assert out.error == "Stopped by the user before this call started. Nothing ran."
        assert out.end_fields == {"reason": "stopped", "error_class": "cancelled"}

    async def test_a_slot_refusal_does_not_suggest_reuse(self) -> None:
        manager = _Manager(2, SandboxLimitExceeded("all 2 code sandboxes on this server are busy (2/2)"))
        out = await InstallPackagesTool(manager).execute(
            packages=["x"], language="python",
        )
        assert out.success is False
        assert manager.destroyed == ["sbx-1"]  # created for this call, never run on
        assert out.error == (
            "all 2 code sandboxes on this server are busy (2/2). This is a temporary capacity "
            "limit, not a problem with your code. Every call needs a free slot while it runs, "
            "even on an existing sandbox, so retry this call shortly and run one call at a time."
        )

    async def test_a_new_sandbox_refusal_suggests_reuse(self) -> None:
        out = await InstallPackagesTool(_Manager(1, SandboxLimitExceeded("too many sandboxes"))).execute(
            packages=["x"], language="python",
        )
        assert out.error is not None and "Reuse an existing sandbox_id" in out.error

    async def test_stopped_while_waiting_for_the_slot_says_nothing_ran(self) -> None:
        out = await InstallPackagesTool(_Manager(2, SandboxCallStopped("install_packages"))).execute(
            packages=["x"], language="python",
        )
        assert out.error == "Stopped by the user before this call started. Nothing ran."

    async def test_a_caller_cancel_is_not_turned_into_a_result(self) -> None:
        with pytest.raises(asyncio.CancelledError):
            await InstallPackagesTool(_Manager(2, asyncio.CancelledError())).execute(
                packages=["x"], language="python",
            )
