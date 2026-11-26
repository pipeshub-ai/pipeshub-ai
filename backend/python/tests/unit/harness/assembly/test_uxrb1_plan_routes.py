"""UXR-B1 B1-8..B1-10: the loop's plan through ``write_todos``, on the assembled durable runtime."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import ModelCall, ToolContext
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.fold import RunState
from harness_core.kernel.task_ledger import PLAN_SLOT
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w
from .test_parity import _full_grant


def _tasks(done: Any) -> list[Any]:  # noqa: ANN401
    return [e for e in done.events if e.type.startswith("task.")]


CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")
TODOS = [
    {"content": "Find the open bugs", "status": "in_progress", "tools": ["jira__search_issues"]},
    {"content": "File the summary", "status": "pending", "tools": ["jira__create_issue"]},
]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def _runtime(chat_mode: str, *, accepts: frozenset[str] | None = None) -> tuple[Any, list[str]]:
    agent = w.agent_doc()
    services = w.Services(agent=agent)
    await services.config.set_config("/services/agentHarness/approvals", {"default": {"writes": "ask"}})
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(w.pinned_spec(**_full_grant(agent)), query="triage the bugs", chat_mode=chat_mode)
    claim = dataclasses.replace(claim, accepts=accepts)
    runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(claim)
    slots: dict[str, Any] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run("triage the bugs")
    shaped = await runtime.shaper.shape(ModelCall([], None, None, slots), 1, RunState(run_id="run-1", slots=slots))
    return runtime, [t.name for t in shaped.tools or []]


async def test_b1_8_write_todos_journals_the_plan_and_its_slot() -> None:
    runtime, offered = await _runtime("react")
    assert "write_todos" in offered
    done = await runtime.tools.execute(ToolCall(id="c1", name="write_todos", arguments={"todos": TODOS}), CTX)
    assert done.status == "ok"
    assert [(e.type, e.data["taskId"], e.data["status"], e.critical) for e in _tasks(done)] == [
        ("task.created", "task-1", "in_progress", False),
        ("task.created", "task-2", "pending", False),
    ]
    assert [t["taskId"] for t in done.slots[PLAN_SLOT]] == ["task-1", "task-2"]


async def test_b1_8_a_list_that_is_not_a_list_is_a_tool_error_with_no_events() -> None:
    runtime, _ = await _runtime("react")
    done = await runtime.tools.execute(ToolCall(id="c1", name="write_todos", arguments={"todos": "x"}), CTX)
    assert done.status == "error"
    assert _tasks(done) == []


async def test_b1_9_a_step_naming_a_tool_the_rules_ask_about_will_ask_first() -> None:
    runtime, _ = await _runtime("react")
    done = await runtime.tools.execute(ToolCall(id="c1", name="write_todos", arguments={"todos": TODOS}), CTX)
    assert [e.data.get("asksFirst") for e in _tasks(done)] == [False, True]


async def test_b1_9_an_older_node_never_gets_asks_first() -> None:
    runtime, _ = await _runtime("react", accepts=frozenset())
    done = await runtime.tools.execute(ToolCall(id="c1", name="write_todos", arguments={"todos": TODOS}), CTX)
    assert all("asksFirst" not in e.data for e in _tasks(done))


async def test_b1_5_a_resumed_attempt_journals_no_duplicate_tasks() -> None:
    first, _ = await _runtime("react")
    done = await first.tools.execute(ToolCall(id="c1", name="write_todos", arguments={"todos": TODOS}), CTX)
    resumed, _ = await _runtime("react")
    slots = {**done.slots}
    await resumed.shaper.enter(2, RunState(run_id="run-1", slots=slots, turn=2))
    again = await resumed.tools.execute(ToolCall(id="c2", name="write_todos", arguments={"todos": TODOS}), CTX)
    assert _tasks(again) == []


async def test_b1_10_deep_is_never_offered_it() -> None:
    # Quick is offered it since R49-PLAN (test_r49plan_default_chat_plan.py); deep plans with create_plan.
    _, offered = await _runtime("deep")
    assert "write_todos" not in offered


async def test_r49plan_a_saved_agents_quick_turn_is_offered_it() -> None:
    _, offered = await _runtime("quick")
    assert "write_todos" in offered
