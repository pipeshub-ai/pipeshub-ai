"""SLASH3 SB3a, the worker's half of ``/run``: a run of kind ``command`` takes both of its turns itself, so it makes no
model call; its one ``run_code`` call goes through the same effect path as the agent's (CODEAUTO's own-sandbox
allow, ASKCODE's ask, a Deny, the flow guard with network, receipts), Stop ends it midway, and its turn is never in a
later turn's context (SD9).

The kernel cases (CR-*) run the real ``DurableKernel`` with ``CommandDirector`` over a function ``run_code``; the
transcript case folds a real session. The assembler's half (workspace path parity) is
``tests/unit/harness/assembly/test_slash3_command_assembly.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from app.harness.assembly.command_run import (
    CALL_ID,
    WORKSPACE_DIR,
    CommandDirector,
    command_line,
    launcher,
    summary,
)
from harness_core.effects.flow import FLOW_RULE, FlowGuardedPolicy, FlowRule
from harness_core.effects.policy import (
    OWN_SANDBOX_RULE,
    LayeredApprovalPolicy,
    parse_rules,
)
from harness_core.effects.types import ToolExecution
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import TurnResult
from harness_core.kernel.durable.transcript import TranscriptRenderer
from harness_core.kernel.fold import RunState, fold_session
from harness_core.testing.contracts.durable_turns import LoopDirectorContract
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import RUN, SESSION, World, reply, scripted
from tests.unit.harness_core.effects.test_codeauto_own_sandbox import (
    ORG_ASKS,
    RUN_CODE,
    RUN_CODE_ID,
    effect,
)
from tests.unit.harness_core.effects.test_ux1_flow_ask import Ledger, where

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolContext

LINE = "echo hi > out.txt && cat out.txt"
COMMAND_SPEC = minimal_run_spec().model_copy(update={"run_kind": "command"})
ASKCODE = LayeredApprovalPolicy((parse_rules({"tools": {RUN_CODE_ID: "ask"}}),), ("org",))
DENIED = LayeredApprovalPolicy((parse_rules({"tools": {RUN_CODE_ID: "deny"}}),), ("org",))


def _world(executions: list[Mapping[str, JsonValue]], *, network: bool = False, run_id: str = RUN) -> World:
    world = World()

    async def run_code(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        executions.append(args)
        return ToolExecution(status="ok", content={"stdout": "hi\n", "stderr": "", "exit_code": 0})

    world.tools.add("run_code", run_code, effect=effect("external") if network else RUN_CODE)
    world.admit(run_id, text=f"/run {LINE}", spec=COMMAND_SPEC)
    return world


def _kernel(world: World, **overrides: object) -> object:
    _, port = scripted()
    world.port = port  # type: ignore[attr-defined]
    return world.kernel(port, director=CommandDirector(line=LINE, timeout_s=60), **overrides)


# CR-1: the journal shape, and no model call.
async def test_cr1_a_command_run_calls_run_code_once_under_the_own_sandbox_rule_and_never_the_model() -> None:
    executions: list[Mapping[str, JsonValue]] = []
    world = _world(executions)
    kernel = _kernel(world, approval=ORG_ASKS)
    disposition = await world.run(kernel)  # type: ignore[arg-type]
    assert disposition.status == "completed"
    assert world.port.calls == []  # type: ignore[attr-defined]
    (admitted,) = world.events("run.admitted")
    assert admitted.data["runKind"] == "command"
    (intent,) = world.events("tool.intent")
    assert (intent.data["toolId"], intent.data.get("allowedBy")) == (RUN_CODE_ID, OWN_SANDBOX_RULE)
    assert "interaction.requested" not in world.types()
    assert [e.data["status"] for e in world.events("tool.outcome")] == ["ok"]
    (call,) = executions
    assert (call["language"], call["timeout"]) == ("python", 60.0)
    assert LINE in str(call["code"])
    answers = [e for e in world.events("message.completed") if e.data["role"] == "assistant"]
    assert [(await world.blob(a.data["textRef"])).decode() for a in answers] == ["Exit code 0."]


# CR-4: ASKCODE asks; approve runs it, deny refuses it with no execution.
@pytest.mark.parametrize(("decision", "ran"), [("approved", 1), ("denied", 0)])
async def test_cr4_askcode_asks_every_command_and_the_answer_decides(decision: str, ran: int) -> None:
    executions: list[Mapping[str, JsonValue]] = []
    world = _world(executions)
    suspended = await world.run(_kernel(world, approval=ASKCODE))  # type: ignore[arg-type]
    assert suspended.status == "suspended" and suspended.reason is not None
    assert len(world.events("interaction.requested")) == 1
    assert executions == []
    world.journal.resolve_interaction(RUN, suspended.reason, decision)
    done = await world.run(_kernel(world, approval=ASKCODE))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert len(executions) == ran
    if ran == 0:
        assert len(world.events("tool.denied")) == 1
        assert world.events("tool.intent") == []
    assert world.port.calls == []  # type: ignore[attr-defined]


# CR-5: with network the call asks, and after a confidential read the flow guard names its rule.
async def test_cr5_a_network_sandbox_asks_with_the_flow_reason_after_a_confidential_read() -> None:
    executions: list[Mapping[str, JsonValue]] = []
    world = _world(executions, network=True)
    guarded = FlowGuardedPolicy(ORG_ASKS, FlowRule("ask"), Ledger(frozenset({"rec-1"})), RUN, destination=where)
    suspended = await world.run(_kernel(world, approval=guarded))  # type: ignore[arg-type]
    assert suspended.status == "suspended"
    assert len(world.events("interaction.requested")) == 1
    command = ToolCall(id=CALL_ID, name="run_code", arguments={})
    reason = guarded.ask_reason(command, effect("external"))
    assert reason is not None and reason["rule"] == FLOW_RULE
    assert executions == []


# CR-6: a Deny refuses before any sandbox starts.
async def test_cr6_a_deny_rule_refuses_and_nothing_runs() -> None:
    executions: list[Mapping[str, JsonValue]] = []
    world = _world(executions)
    done = await world.run(_kernel(world, approval=DENIED))  # type: ignore[arg-type]
    assert done.status == "completed"
    assert executions == []
    assert len(world.events("tool.denied")) == 1
    answers = [e for e in world.events("message.completed") if e.data["role"] == "assistant"]
    assert [(await world.blob(a.data["textRef"])).decode() for a in answers] == ["The command did not run."]


# CR-8: Stop mid-command ends the run now and the call's task (its sandbox) is cancelled.
async def test_cr8_stop_mid_command_cancels_the_call_and_ends_the_run() -> None:
    from harness_core.interactions.memory import InMemoryInteractionWakeup

    started = asyncio.Event()
    released: list[str] = []
    world = World()

    async def run_code(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            released.append("sandbox")
        return ToolExecution(status="ok", content={"exit_code": 0})

    world.tools.add("run_code", run_code, effect=RUN_CODE)
    world.admit(text=f"/run {LINE}", spec=COMMAND_SPEC)
    wakeup = InMemoryInteractionWakeup()
    kernel = _kernel(world, approval=ORG_ASKS, cancel_wakeup=wakeup, cancel_poll_s=0.05)
    task = asyncio.ensure_future(world.run(kernel))  # type: ignore[arg-type]
    await asyncio.wait_for(started.wait(), 5)
    world.journal.request_cancel(RUN)
    wakeup.wake(RUN)
    disposition = await asyncio.wait_for(task, 5)
    assert disposition.status == "cancelled"
    assert released == ["sandbox"]


def test_the_launcher_runs_one_line_in_the_workspace_folder_without_stdin() -> None:
    code = launcher("ls -la; echo 'x'")
    assert repr("ls -la; echo 'x'") in code
    assert "stdin=subprocess.DEVNULL" in code
    assert f"cwd={WORKSPACE_DIR!r}" in code
    assert command_line("/run pytest -q") == "pytest -q"
    compile(code, "launcher", "exec")


def test_the_summary_is_the_journaled_exit_status() -> None:
    ok = TurnResult("run_code", CALL_ID, "ok", {"exit_code": 3})
    assert summary([ok]) == "Exit code 3."
    assert summary([TurnResult("run_code", CALL_ID, "ok", json.dumps({"exit_code": 0}))]) == "Exit code 0."
    assert summary([TurnResult("run_code", CALL_ID, "denied", "no")]) == "The command did not run."
    assert summary([]) == "The command did not run."


# CR-3 (QAFIX3 bug 3): the reply is the answer the director ended the run with, and a command result has no
# confidence. Every turn of a /run is synthetic, and the ending once took its answer from model turns only, so the
# product's finalizer put "I wasn't able to generate a response" with a Low chip under every /run.
@pytest.mark.parametrize("exit_code", [0, 3])
async def test_cr3_the_reply_is_the_exit_code_with_no_confidence(exit_code: int) -> None:
    from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_WRITERS
    from app.harness.presentation import PipesHubPresenter
    from harness_core.tools.registry import ToolRegistry
    from tests.unit.agents.adapter.conftest import make_context

    world = World(extension_writers=EVENT_WRITERS)  # type: ignore[arg-type]

    async def run_code(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content={"stdout": "", "stderr": "", "exit_code": exit_code})

    world.tools.add("run_code", run_code, effect=RUN_CODE)
    world.admit(text=f"/run {LINE}", spec=COMMAND_SPEC)
    context = make_context(protocol="agui")
    context.run_id = RUN
    presenter = PipesHubPresenter(context, ToolRegistry())
    disposition = await world.run(_kernel(world, approval=ORG_ASKS, presenter=presenter))  # type: ignore[arg-type]
    assert disposition.status == "completed"
    (finalized,) = world.events("x.pipeshub.answer.finalized")
    assert finalized.data["answer"] == f"Exit code {exit_code}."
    assert finalized.data["partTexts"] == [f"Exit code {exit_code}."]
    assert finalized.data.get("confidence") is None
    assert "answerMatchType" not in finalized.data


# CR-2 (context): a later turn's transcript holds nothing of a command run (SD9); the command run itself still has its own.
async def test_cr2_a_later_turn_never_sees_the_command_or_its_output() -> None:
    executions: list[Mapping[str, JsonValue]] = []
    world = _world(executions, run_id="run-cmd")
    assert (await world.run(_kernel(world, approval=ORG_ASKS), "run-cmd")).status == "completed"  # type: ignore[arg-type]
    world.admit("run-next", text="what changed?")
    model, port = scripted(reply("nothing"))
    del model
    assert (await world.run(world.kernel(port), "run-next")).status == "completed"
    (call,) = port.calls
    text = json.dumps([m.model_dump(mode="json") for m in call.messages])
    assert LINE not in text and "Exit code" not in text and "hi\\n" not in text
    assert "what changed?" in text
    session = fold_session(world.journal.events(SESSION), stream_id=SESSION)
    assert session.command_runs == ["run-cmd"]
    renderer = TranscriptRenderer(world.content, port, run_id="run-cmd")
    own, _ = await renderer.render_for(session, "run-cmd")
    assert any(LINE in json.dumps(m.model_dump(mode="json")) for m in own)


class CommandDirectors:
    def fresh(self) -> CommandDirector:
        return CommandDirector(line=LINE, timeout_s=60)

    def state(self) -> RunState:
        return RunState(run_id=RUN)


class TestCommandDirector(LoopDirectorContract):
    """DP14: a re-claimed ``/run`` attempt plans the same ``run_code`` call and the same answer."""

    @pytest.fixture
    def directors(self) -> CommandDirectors:  # type: ignore[override]
        return CommandDirectors()
