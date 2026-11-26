"""QAFINAL-FU-PW113 (ADR-RUN-04): a deep sub-agent is the tree's bound, so it is offered no coordination tools.

ST-1 row PW-113: the coordinator's programmatic dispatch hands a plan step that names no tools to a sub-agent, pinned
``subAgent: {"toolNames": []}`` and ``maxChildren: 0``. An empty list is the whole registry, and the sub-agent's
registry held the coordinator's ``create_plan``, ``critique_plan``, ``verify_result`` and ``spawn_agent``: its model was
offered them, and its ``spawn_agent`` was refused as "removed after this chat started", which is not what happened.

The real ``PipesHubRuntimeAssembler`` and ``RunDriver`` over the in-memory journal; the scripted model (the e2e
lane's ``fake_llm``) plans and delegates only for the coordinator.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.deep import (
    COORDINATION_TOOLS,
    not_for_sub_agent,
    sub_agent_pool,
)
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.session_tools import changed_since_pinned
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

REMOVED = "removed after this chat started"
SERVER = Path(__file__).resolve().parents[6] / "deployment" / "e2e" / "fake_llm" / "server.py"


@pytest.fixture(autouse=True)
def _deployment() -> Any:  # noqa: ANN401
    with w.deployment():
        yield


def _spec(*, sub_agent: bool) -> RunSpec:
    """What Node pins: a deep root granted children, or its sub-agent as PW-113's was (no tools named, no children)."""
    raw = w.pinned_spec(tool_ids=[]).model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["agent"]["mode"] = "deep"
    if sub_agent:
        raw["extensions"]["x.pipeshub"]["subAgent"] = {"role": "general", "toolNames": []}
    else:
        raw["budgets"]["maxChildren"] = 16
    return validate_contract(RunSpec, raw)


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="pw113", turns=[Turn(reply=r) for r in replies])))


async def _build(spec: RunSpec) -> Any:  # noqa: ANN401 - an AssembledRuntime
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(_port(Reply(text="ok"))))
    return await assembler.build(w.claim(spec, chat_mode="deep"))


class _DeepClaims:
    """The world's claims for a deep chat (``w.ClaimAssembler`` admits a quick one)."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner

    async def build(self, attempt: Any) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="deep")
        values = {f: getattr(attempt, f) for f in attempt.__dataclass_fields__}
        return await self._inner.build(type(node)(**values, run=node.run))


async def _drive_sub_agent(*replies: Reply) -> tuple[Any, ScriptedModelPort]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=_spec(sub_agent=True),
                  input_text="## Your delegated task\nfind the open bugs")
    port = _port(*replies)
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    driver = RunDriver(journal=journal, assembler=_DeepClaims(assembler), clock=clock)
    return await driver.run_claimed(await w.claim_next(journal)), port


def test_pw113_1_only_a_deep_root_coordinates() -> None:
    assert RunContext.from_claim(w.claim(_spec(sub_agent=False), chat_mode="deep")).coordinates is True
    assert RunContext.from_claim(w.claim(_spec(sub_agent=True), chat_mode="deep")).coordinates is False
    assert RunContext.from_claim(w.claim(_spec(sub_agent=False), chat_mode="react")).coordinates is False


async def test_pw113_2_the_root_holds_the_coordination_tools_its_sub_agent_none() -> None:
    root = await _build(_spec(sub_agent=False))
    child = await _build(_spec(sub_agent=True))
    try:
        assert root.delegation is not None and {s.name for s in root.delegation.schemas()} >= {"spawn_agent"}
        assert all(root.tools.describe(n) is not None for n in COORDINATION_TOOLS - {"spawn_agent"})
        assert child.delegation is None or "spawn_agent" not in {s.name for s in child.delegation.schemas()}
        assert [n for n in sorted(COORDINATION_TOOLS) if child.tools.describe(n) is not None] == []
    finally:
        await root.close()
        await child.close()


async def test_pw113_3_a_sub_agent_with_no_tools_named_is_offered_no_coordination_tool() -> None:
    done, port = await _drive_sub_agent(Reply(text="Two bugs are open."))
    assert done.status == "completed"
    offered = {t.name for t in port.calls[0].tools or []}
    assert offered, "the whole registry, less the coordination tools"
    assert not offered & COORDINATION_TOOLS, sorted(offered & COORDINATION_TOOLS)


async def test_pw113_4_a_sub_agents_spawn_is_told_the_true_reason() -> None:
    done, port = await _drive_sub_agent(
        Reply(tool_calls=[ToolCallSpec(id="c1", name="spawn_agent", args={"role": "researcher", "goal": "dig"})]),
        Reply(text="I looked myself."),
    )
    assert done.status == "completed"
    told = [str(m.content) for m in port.calls[1].messages if m.role == "tool"]
    assert told and "cannot start sub-agents of its own" in told[-1], told
    assert REMOVED not in told[-1]


def test_pw113_5_a_session_list_that_showed_a_withheld_tool_is_answered_with_why() -> None:
    """A sub-agent's list journaled before the fix still shows ``spawn_agent``: its call is told why it has none."""
    shown = {"spawn_agent": ToolSchema(name="spawn_agent", description="d", input_schema={"type": "object"})}
    reason = not_for_sub_agent("spawn_agent")
    assert changed_since_pinned("spawn_agent", shown, None, available=False, withheld=reason) == (reason, "not_found")
    removed = changed_since_pinned("spawn_agent", shown, None, available=False)
    assert removed is not None and REMOVED in removed[0], "a tool really removed is still said so"


def test_pw113_6_the_pool_and_the_withheld_set_are_one_list() -> None:
    assert not set(sub_agent_pool(sorted(COORDINATION_TOOLS)))
    assert COORDINATION_TOOLS == {"create_plan", "critique_plan", "verify_result", "spawn_agent"}


def _fake() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ.setdefault("FAKE_LLM_API_KEY", "sk-pw113")
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = importlib.util.spec_from_file_location("pw113_fake_llm_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pw113_7_the_scripted_coordinator_rule_is_the_roots_alone() -> None:
    server = _fake()
    tools = ["create_plan", "critique_plan", "spawn_agent", "verify_result"]
    asked = 'tool lookup_note {"topic": "x"}\nsay done'
    coordinator = server._delegate([server.ChatMessage("user", asked)], tools, "c1")
    assert coordinator is not None and coordinator.tool_calls[0].name == "create_plan"
    task = f"The user's request:\n{asked}\n\n{server.DELEGATED_TASK_HEADER}\n{asked}"
    assert server.is_sub_agent([server.ChatMessage("user", task)])
    assert server._delegate([server.ChatMessage("user", task)], tools, "c2") is None
    # PW-113's own child: its task is the request itself (``with_user_request`` adds no header), its prompt a sub-agent's.
    from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt

    child = [server.ChatMessage("system", build_sub_agent_prompt("general", None)), server.ChatMessage("user", asked)]
    assert server.is_sub_agent(child)
    assert server._delegate(child, tools, "c3") is None
    assert not server.is_sub_agent([server.ChatMessage("system", "You are PipesHub's assistant."),
                                    server.ChatMessage("user", asked)])


def test_pw113_8_every_st1_row_checks_no_sub_agent_was_offered_them() -> None:
    import sys

    sys.path.insert(0, str(SERVER.parents[3] / "integration-tests" / "harness" / "e2e"))
    import checks
    import observe

    check = next(c for c in checks.CHECKS if "sub-agent is offered no coordination tools" in c.name)
    seen = observe.Observation(route="durable", terminal="finished", first_frame="conversation_created")
    seen.model_calls = [
        {"kind": "delegate", "tools": ["create_plan", "spawn_agent"], "subAgent": False},
        {"kind": "loop", "tools": ["lookup_note", "fetch_tools"], "subAgent": True},
    ]
    assert check.verify(None, seen) == []  # type: ignore[arg-type]
    seen.model_calls.append({"kind": "loop", "tools": ["spawn_agent", "create_plan", "lookup_note"], "subAgent": True})
    assert check.verify(None, seen) == ["a sub-agent was offered ['create_plan', 'spawn_agent']"]  # type: ignore[arg-type]
