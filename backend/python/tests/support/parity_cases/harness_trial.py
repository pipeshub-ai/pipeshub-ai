"""One trial of a harness case on either harness: the case's script plays the model, and the trial records task
success (graders), the safety invariants, whether an injection succeeded, usage and prompts.

``prepare`` may change the drive spec before the run and ``grade`` may replace the outcome checks, so a suite whose
cases carry fields of their own drives them through the same trial.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tests.harness_replay.legacy_driver import drive
from tests.support.parity_cases.harness_graders import (
    GraderOutcome,
    grade,
    injection_succeeded,
    safety_violations,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.core.types import ToolCall as CoreToolCall
    from harness_core.testing.replay import Recording
    from tests.harness_replay.legacy_driver import DriveSpec
    from tests.support.parity_cases.harness_cases import ParityCase


@dataclass
class Trial:
    success: bool
    task_success: bool
    safety_ok: bool
    injection_success: bool
    status: str
    grader_failures: list[str]
    violations: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_ms: float = 0.0
    model_calls: int = 0
    tool_calls: int = 0
    approval_prompts: int = 0
    denials: int = 0
    error: str | None = None

    def details(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "grader_failures": list(self.grader_failures),
            "violations": list(self.violations),
            "error": self.error,
        }


def requirement_missing(case: ParityCase) -> str | None:
    for need in case.requires:
        if need == "local_sandbox" and sys.platform == "win32":
            return "local sandbox backend is POSIX-only"
        if need == "docker":
            docker = shutil.which("docker")
            if docker is None:
                return "needs Docker"
            probe = subprocess.run(
                [docker, "info"], capture_output=True, check=False, timeout=20
            )
            if probe.returncode != 0:
                return "needs a reachable Docker daemon"
    return None


def _usage(recording: Recording) -> tuple[int, int, int, int, int]:
    steps = recording.model_steps()
    totals = [0, 0, 0, 0]
    for s in steps:
        if s.response is not None:
            u = s.response.usage
            for i, v in enumerate(
                (
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_read_tokens,
                    u.cache_write_tokens,
                )
            ):
                totals[i] += v
    return totals[0], totals[1], totals[2], totals[3], len(steps)


def _driver(harness: str) -> Callable[[Any], Awaitable[Any]]:
    if harness == "durable":
        from tests.harness_replay.durable_driver import drive as durable_drive

        return durable_drive
    return drive


async def _deterministic(case: ParityCase, recording: Recording) -> GraderOutcome:
    return grade(case, recording)


async def run_trial[C: ParityCase](
    case: C,
    *,
    harness: str = "legacy",
    prepare: Callable[[DriveSpec, C], None] | None = None,
    grade_with: Callable[[C, Recording], Awaitable[GraderOutcome]] = _deterministic,
) -> tuple[Trial, Recording | None]:
    spec = case.drive_spec()
    if prepare is not None:
        prepare(spec, case)
    approved: set[str] = set()

    async def approve(call: CoreToolCall, _reason: str) -> bool:
        if case.safety.approve_asks:
            approved.add(call.id)
        return case.safety.approve_asks

    spec.approve = approve
    try:
        result = await _driver(harness)(spec)
    except Exception as exc:
        return (
            Trial(
                success=False,
                task_success=False,
                safety_ok=True,
                injection_success=False,
                status="crashed",
                grader_failures=["run crashed"],
                violations=[],
                error=f"{type(exc).__name__}: {exc}"[:300],
            ),
            None,
        )
    recording = result.recording
    graded = await grade_with(case, recording)
    violations = safety_violations(case, recording, frozenset(approved))
    tokens_in, tokens_out, cache_read, cache_write, model_calls = _usage(recording)
    errors = [s.error.kind for s in recording.model_steps() if s.error is not None]
    trial = Trial(
        success=graded.ok and not violations,
        task_success=graded.ok,
        safety_ok=not violations,
        injection_success=injection_succeeded(case, recording),
        status=recording.final.status,
        grader_failures=graded.failed,
        violations=violations,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        latency_ms=round(result.latency_s * 1000, 1),
        model_calls=model_calls,
        tool_calls=len(recording.tool_steps()),
        approval_prompts=result.stats.approval_prompts,
        denials=result.stats.denied,
        error=f"model error: {', '.join(errors)}"
        if errors and case.category != "robustness"
        else None,
    )
    return trial, recording


__all__ = ["Trial", "requirement_missing", "run_trial"]
