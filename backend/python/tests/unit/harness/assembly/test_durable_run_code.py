"""``run_code`` on the durable loop (P5.11): the legacy tool and hooks, the pin's egress, receipts, crashes.

The real ``PipesHubRuntimeAssembler`` and ``RunDriver`` over the in-memory journal; ``run_code`` runs in
the local backend on the test interpreter (the ``host`` test sandbox), and its outputs land in the
test artifact registry, as the product registry would hold them.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.hooks import NOT_PORTED
from app.harness.assembly.run_code import INTERRUPTED
from app.harness.sandbox.receipts import RegistryExecutionReceipts
from app.models.entities import ArtifactType
from app.services.artifact_registry.models import Actor
from harness_core.driver import RunDriver
from harness_core.effects.receipts import ExecutionReceipt
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.failpoints import registry as failpoints
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver import AssembledRuntime

RUN_CODE = "run_code"
CODE = (
    "import os, pathlib\n"
    "out = pathlib.Path(os.environ['OUTPUT_DIR']); out.mkdir(parents=True, exist_ok=True)\n"
    "(out / 'report.csv').write_text('region,total\\nwest,42\\n')\n"
    "print('rows', 1)\n"
)
ARGS: dict[str, Any] = {"code": CODE, "language": "python"}
KEY = f"{w.ORG}:run-1:t1.0"


class _Died(BaseException):
    """A worker killed mid-call: not an ``Exception``, so no handler runs."""


@pytest.fixture
def artifacts() -> ParityArtifactRegistry:
    return ParityArtifactRegistry(w.ORG, "ses-1")


@pytest.fixture
def executions() -> list[str]:
    return []


@pytest.fixture(autouse=True)
def _deployment(artifacts: ParityArtifactRegistry, executions: list[str]) -> Iterator[None]:
    original = LocalCodingSandbox.execute

    async def counted(self: LocalCodingSandbox, request: Any) -> Any:  # noqa: ANN401
        executions.append(request.code)
        return await original(self, request)

    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(patch("app.services.artifact_registry.ArtifactRegistryService", new=artifacts))
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        stack.enter_context(patch.object(LocalCodingSandbox, "execute", counted))
        yield
    failpoints.reset()


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=r) for r in replies])))


async def _build(spec: Any, port: ScriptedModelPort | None = None) -> AssembledRuntime:  # noqa: ANN401
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port or _port(Reply(text="ok"))))
    return await assembler.build(w.claim(spec))  # type: ignore[arg-type]


def _ctx(invocation: str = "t1.0") -> ToolContext:
    return ToolContext("run-1", w.ORG, invocation, "att-1", f"{w.ORG}:run-1:{invocation}")


def _receipts(artifacts: ParityArtifactRegistry) -> RegistryExecutionReceipts:
    return RegistryExecutionReceipts(artifacts, Actor(org_id=w.ORG, user_id=w.ACTOR), "ses-1")


def _data(execution: Any) -> dict[str, Any]:  # noqa: ANN401
    """What the model reads: the legacy loop serializes a dict result as JSON text."""
    content = execution.content
    return json.loads(content) if isinstance(content, str) else content


def _versions(artifacts: ParityArtifactRegistry, name: str = "report.csv") -> int:
    return len(artifacts.history().get(name, ()))


# --- wiring -------------------------------------------------------------------------------------------


def test_rc1_the_run_context_carries_the_attempt_and_the_pinned_egress() -> None:
    from app.harness.assembly.context import RunContext

    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[], sandbox="agent-code")))
    assert (run.attempt_id, run.egress, run.sandbox_profile) == ("att-1", "none", "agent-code")


async def test_rc2_egress_none_keeps_code_off_the_network_whatever_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_sources import code_network

    monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "true")
    pinned = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    assert code_network(RunContext.from_claim(w.claim(pinned))) is False
    granted = pinned.model_copy(update={"grants": pinned.grants.model_copy(update={"egress": "allowlist:web"})})
    assert code_network(RunContext.from_claim(w.claim(granted))) is True
    runtime = await _build(pinned)
    tool = runtime.tools._registry.resolve_by_name(RUN_CODE)  # type: ignore[attr-defined]
    assert tool._allow_network is False  # pyright: ignore[reportAttributeAccessIssue]
    await runtime.close()  # type: ignore[misc]


def _sandbox_middleware(hooks: Any) -> list[str]:  # noqa: ANN401
    from app.agent_loop_lib.hooks.events import HookEvent

    names: list[str] = []
    for event in (HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE):
        for _matcher, middleware in hooks.on(event)._stack:  # pyright: ignore[reportPrivateUsage]
            qualname = getattr(middleware, "__qualname__", "") or type(middleware).__name__
            if "coding_sandbox" in qualname:
                names.append(f"{event.name}:{qualname}")
    return names


async def test_rc3_an_agent_code_run_has_the_legacy_sandbox_hooks_in_the_factorys_order() -> None:
    from app.agents.agent_loop.factory import PipesHubAgentFactory

    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    durable = _sandbox_middleware(runtime.tools._executor._kernel)  # type: ignore[attr-defined]
    context = runtime.inputs._env.context  # type: ignore[attr-defined]
    legacy = _sandbox_middleware(
        PipesHubAgentFactory._build_hooks(context, context.sandbox_manager, allow_network=False)  # pyright: ignore[reportPrivateUsage]
    )
    assert legacy and durable == legacy
    await runtime.close()  # type: ignore[misc]
    plain = await _build(w.pinned_spec(tool_ids=[], sandbox="none"))
    assert _sandbox_middleware(plain.tools._executor._kernel) == []  # type: ignore[attr-defined]
    assert "register_coding_sandbox_hooks" not in NOT_PORTED
    assert any("POST_AGENT" in item for item in NOT_PORTED)


async def test_rc4_the_sandbox_tools_are_receipted_executes() -> None:
    from harness_core.kernel.durable.effects import classify_tool_effect

    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    registry = runtime.tools._registry  # type: ignore[attr-defined]
    for name in ("run_code", "install_packages"):
        effect = runtime.tools.describe(name)  # type: ignore[attr-defined]
        assert (effect.effect, effect.idempotent) == ("execute", True)
        # The tool's own tags still classify it as a plain execute (legacy unchanged).
        assert classify_tool_effect(name, registry.tags_for_name(name)).idempotent is False
    assert runtime.tools.describe("read_sandbox_file").effect == "read"  # type: ignore[attr-defined]
    await runtime.close()  # type: ignore[misc]


# --- receipts, one call --------------------------------------------------------------------------------


async def _call(runtime: AssembledRuntime, invocation: str = "t1.0") -> Any:  # noqa: ANN401
    return await runtime.tools.execute(ToolCall(id="c1", name=RUN_CODE, arguments=dict(ARGS)), _ctx(invocation))


async def test_rc7_a_receipted_call_runs_once_and_its_retry_answers_from_the_receipt(
    artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    first = await _call(runtime)
    assert first.status == "ok" and len(executions) == 1
    receipt = await _receipts(artifacts).get(KEY)
    assert receipt is not None and receipt.state == "done"
    presented = [e for e in first.events if e.type == "x.pipeshub.artifact.presented"]
    assert [e.data["fileName"] for e in presented] == ["report.csv"]
    await runtime.close()  # type: ignore[misc]

    retried = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    again = await _call(retried)
    assert len(executions) == 1
    assert again.status == "ok"
    assert _data(again)["stdout"] == _data(first)["stdout"] == "rows 1\n"
    assert [a["name"] for a in _data(again)["artifacts"]] == ["report.csv"]
    assert "sandbox_id" not in _data(again)
    assert [e.data["fileName"] for e in again.events if e.type == "x.pipeshub.artifact.presented"] == ["report.csv"]
    assert _versions(artifacts) == 1
    await retried.close()  # type: ignore[misc]


async def test_rc8_a_call_that_only_started_is_not_run_again(
    artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    from datetime import UTC, datetime

    await _receipts(artifacts).put(KEY, ExecutionReceipt("started", datetime.now(UTC)))
    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    result = await _call(runtime)
    assert executions == []
    assert result.status == "error" and str(result.content).startswith(INTERRUPTED)
    receipt = await _receipts(artifacts).get(KEY)
    assert receipt is not None and (receipt.state, receipt.is_error) == ("done", True)
    await runtime.close()  # type: ignore[misc]


async def test_fixp1_a_sandbox_broker_that_is_down_is_an_unavailable_error_not_a_failed_call(
    monkeypatch: pytest.MonkeyPatch, executions: list[str]
) -> None:
    """FIX-P1 S2 (broker restart): the broker backend's own error is the cause of the sandbox error, so the call
    fails ``unavailable`` (the service, not the call), which the tool-error tracker does not count against the
    model's use of the tool."""
    from app.agent_loop_lib.sandbox.coding.base import CodingSandboxError
    from harness_core.sandbox.errors import BrokerUnavailable

    async def down(self: LocalCodingSandbox, request: Any) -> Any:  # noqa: ANN401
        raise CodingSandboxError("sandbox broker unavailable: restarting") from BrokerUnavailable("restarting")

    monkeypatch.setattr(LocalCodingSandbox, "execute", down)
    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    result = await _call(runtime)
    assert result.status == "error" and "sandbox broker unavailable" in str(result.content)
    assert result.error_code == "unavailable", result.error_code
    await runtime.close()  # type: ignore[misc]


async def test_rc9_an_executed_call_gives_its_output_and_the_files_its_attempt_saved(
    artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    from datetime import UTC, datetime, timedelta

    started = datetime.now(UTC) - timedelta(seconds=5)
    raw = {
        "stdout": "rows 1\n",
        "stderr": "",
        "exit_code": 0,
        "artifacts": ["output/report.csv", "output/chart.png", "scratch.txt"],
        "sandbox_id": "dead-sandbox",
    }
    await _receipts(artifacts).put(KEY, ExecutionReceipt("started", started))
    await _receipts(artifacts).put(KEY, ExecutionReceipt("executed", started, raw))
    actor = Actor(org_id=w.ORG, user_id=w.ACTOR)
    # The dead attempt's bridge registered the CSV, and not the chart, before it died.
    await artifacts.register_output(
        actor=actor, name="report.csv", artifact_type=ArtifactType.DATA_FILE,
        mime_type="text/csv", content=b"region,total\nwest,42\n", conversation_id="ses-1",
        source_tool="/toolsets/coding_sandbox/run_code",
    )
    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    result = await _call(runtime)
    assert executions == []
    assert result.status == "ok"
    assert _data(result)["stdout"] == "rows 1\n"
    assert [a["name"] for a in _data(result)["artifacts"]] == ["report.csv"]
    assert _data(result)["artifact_errors"] == [{"file": "chart.png", "error": "not saved before the worker stopped"}]
    assert "sandbox_id" not in _data(result) and "recovered_note" in _data(result)
    assert [e.data["fileName"] for e in result.events if e.type == "x.pipeshub.artifact.presented"] == ["report.csv"]
    await runtime.close()  # type: ignore[misc]


async def test_rc10_outside_the_runs_own_port_the_tool_runs_as_today(
    artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    runtime = await _build(w.pinned_spec(tool_ids=[], sandbox="agent-code"))
    registry = runtime.tools._registry  # type: ignore[attr-defined]
    output = await registry.resolve_by_name(RUN_CODE).execute(**ARGS)
    assert output.success and len(executions) == 1
    assert [f.name for f in artifacts.files() if f.name.startswith("receipt-")] == []
    await runtime.close()  # type: ignore[misc]


# --- crash and re-drive at each journaled point ------------------------------------------------------


def _outcomes(journal: Any) -> list[Any]:  # noqa: ANN401
    return [e for e in journal.events("ses-1") if e.type == "tool.outcome"]


async def _drive(point: str, *, nth: int = 1) -> tuple[Any, Any, list[Any]]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[], sandbox="agent-code"), input_text="make the report")
    port = _port(
        Reply(tool_calls=[ToolCallSpec(name=RUN_CODE, args=dict(ARGS))]),
        Reply(text="Here is the report."),
        Reply(text="Here is the report."),
    )
    driver = RunDriver(
        journal=journal,
        assembler=w.ClaimAssembler(PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))),
        clock=clock,
    )
    failpoints.arm(point, Arm(Raise(_Died), Nth(nth)))
    with pytest.raises(_Died):
        _ = await driver.run_claimed(await w.claim_next(journal))
    failpoints.reset()
    _ = clock.advance(31)
    _ = journal.sweep()
    done = await driver.run_claimed(await w.claim_next(journal))
    return done, journal, _outcomes(journal)


def _presented(journal: Any) -> list[str]:  # noqa: ANN401
    return [e.data["fileName"] for e in journal.events("ses-1") if e.type == "x.pipeshub.artifact.presented"]


@pytest.mark.parametrize(
    ("point", "runs", "status", "files"),
    [
        ("worker.tool.after_intent", 1, "ok", ["report.csv"]),
        ("worker.run_code.after_started", 0, "error", []),
        ("worker.run_code.after_executed", 1, "ok", []),
        ("worker.run_code.before_done", 1, "ok", ["report.csv"]),
        ("worker.run_code.after_done", 1, "ok", ["report.csv"]),
    ],
    ids=["C1-after-intent", "C2-after-started", "C3a-after-executed", "C3b-artifact-saved", "C4-after-done"],
)
async def test_rc_c_a_crash_at_each_point_never_runs_the_code_twice(
    point: str, runs: int, status: str, files: list[str], artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    done, journal, outcomes = await _drive(point)
    assert done.status == "completed"
    assert len(executions) == runs
    assert [o.data["status"] for o in outcomes] == [status]
    assert _presented(journal) == files
    assert _versions(artifacts) == len(files)


async def test_rc_c5_a_crash_after_the_outcome_commit_never_calls_the_tool_again(
    artifacts: ParityArtifactRegistry, executions: list[str]
) -> None:
    done, journal, outcomes = await _drive("worker.model.before_response_commit", nth=2)
    assert done.status == "completed"
    assert len(executions) == 1
    assert [o.data["status"] for o in outcomes] == ["ok"]
    assert _presented(journal) == ["report.csv"]
    assert _versions(artifacts) == 1
