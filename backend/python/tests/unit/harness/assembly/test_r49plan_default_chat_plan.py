"""R49-PLAN (R49PLAN-1, ADR-OWD-07 D-PLAN-1): the default chat's Agent mode is offered the plan tool.

The default chat's Agent mode pins the system assistant definition and runs ``quick``, the only strategy the chat
offers. Quick was never offered ``write_todos``, so a long multi-step request kept no plan and journaled no ``task.*``
events. The built-in assistant's search modes stay as they were.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.run_definition import ASSISTANT_AGENT_KEY
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.plan import (
    HAND_OFF_PLAN_NOTE,
    PLAN_DESCRIPTION,
    PLAN_TOOL,
    plan_tool,
)
from app.harness.presentation import HIDDEN_TOOLS
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import ModelCall, ToolContext
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.fold import RunState
from harness_core.kernel.task_ledger import PLAN_SLOT
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.tools.registry import ToolRegistry

from . import world as w
from .test_parity import _durable_turn_one

if TYPE_CHECKING:
    from collections.abc import Iterator

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")
WEB = "web@default/web_search"
TODOS = [
    {"content": "Find last quarter's invoices", "status": "in_progress"},
    {"content": "Match them to the budget", "status": "pending"},
    {"content": "Draft the summary", "status": "pending"},
]
# The default chat's Agent mode, web and knowledge on, lazy disclosure off: the tool surface of its first request.
# Before R49-PLAN it was this list without write_todos.
AGENT_MODE_TOOLS = [
    "artifacts__get_artifact_content",
    "artifacts__get_artifact_download_url",
    "artifacts__get_record_download_url",
    "artifacts__list_artifacts",
    "artifacts__promote_artifact",
    "artifacts__save_artifact",
    "artifacts__update_artifact",
    "dynamic__fetch_url",
    "dynamic__web_search",
    "internaltools__ask_user_question",
    "knowledgegraph__fetch_record",
    "knowledgegraph__search",
    "retrieve_artifact_content",
    "search_tools",
    "write_todos",
]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    logging.getLogger("assembly.tests").setLevel(logging.WARNING)
    with w.deployment():
        yield


def _tasks(done: Any) -> list[Any]:  # noqa: ANN401 - a tool execution
    return [e for e in done.events if e.type.startswith("task.")]


def _services() -> w.Services:
    services = w.Services(agent={})
    services.retrieval.search_with_filters = AsyncMock(
        return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    )
    return services


def _agent_mode_spec() -> RunSpec:
    """What Node pins for the default chat's Agent mode (ASSIST1): the system assistant definition, ``quick``."""
    spec = dump_contract(w.pinned_spec(tool_ids=[WEB], kb=["kb-1"]))
    spec["extensions"]["x.pipeshub"]["agent"] = {"agentKey": ASSISTANT_AGENT_KEY, "definitionVersion": 1, "mode": "quick"}
    return validate_contract(RunSpec, spec)


def _search_mode_spec(mode: str) -> Any:  # noqa: ANN401 - RunSpec
    return w.pinned_spec(
        tool_ids=[WEB] if mode == "web_search" else [],
        kb=[],
        agent=False,
        chat={"mode": mode, "internalSearch": mode == "internal_search"},
        strict_scope=False,
    )


async def _agent_mode() -> tuple[Any, list[str]]:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    assistant = {"systemPrompt": "You are a helpful AI assistant.", "models": [], "toolsets": [], "mcpServers": [],
                 "knowledge": [{"connectorId": "kb-1", "type": "KB"}]}
    query = "reconcile last quarter's invoices against the budget and draft a summary"
    with patch("app.api.routes.agent.get_assistant_agent", AsyncMock(return_value=(copy.deepcopy(assistant), {}))), \
         patch("app.api.routes.chatbot.load_system_prompts", AsyncMock(return_value={})):
        claim = w.claim(_agent_mode_spec(), query=query, chat_mode="agent")
        runtime = await PipesHubRuntimeAssembler(_services(), model_factory=w.model_factory(port)).build(claim)
        slots: dict[str, Any] = {}
        for step in runtime.pre_run:
            slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(query)
        shaped = await runtime.shaper.shape(ModelCall([], None, None, slots), 1, RunState(run_id="run-1", slots=slots))
    return runtime, sorted(schema.name for schema in shaped.tools or [])


async def test_r49plan_1_the_default_chats_agent_mode_offers_the_plan_tool() -> None:
    runtime, offered = await _agent_mode()
    assert offered == AGENT_MODE_TOOLS
    assert runtime.tools is not None


@pytest.mark.parametrize("mode", ["internal_search", "web_search"])
async def test_r49plan_2_the_built_in_assistants_search_modes_are_unchanged(mode: str) -> None:
    _runtime, _slots, shaped = await _durable_turn_one(_services(), _search_mode_spec(mode), mode, "refund policy")
    assert PLAN_TOOL not in {schema.name for schema in shaped.tools or []}


async def test_r49plan_3_a_plan_on_the_default_chat_journals_task_events() -> None:
    runtime, _ = await _agent_mode()
    done = await runtime.tools.execute(ToolCall(id="c1", name=PLAN_TOOL, arguments={"todos": TODOS}), CTX)
    assert done.status == "ok"
    assert [(e.type, e.data["taskId"], e.data["status"], e.data["title"]) for e in _tasks(done)] == [
        ("task.created", "task-1", "in_progress", "Find last quarter's invoices"),
        ("task.created", "task-2", "pending", "Match them to the budget"),
        ("task.created", "task-3", "pending", "Draft the summary"),
    ]
    assert [t["taskId"] for t in done.slots[PLAN_SLOT]] == ["task-1", "task-2", "task-3"]
    moved = [{**TODOS[0], "status": "completed"}, {**TODOS[1], "status": "in_progress"}, TODOS[2]]
    again = await runtime.tools.execute(ToolCall(id="c2", name=PLAN_TOOL, arguments={"todos": moved}), CTX)
    assert [(e.type, e.data["taskId"], e.data["status"]) for e in _tasks(again)] == [
        ("task.updated", "task-1", "completed"),
        ("task.updated", "task-2", "in_progress"),
    ]


def _description(run: RunContext) -> str:
    registry = ToolRegistry()
    assert plan_tool(run, registry) is not None
    return registry.resolve_by_name(PLAN_TOOL).description


def test_r49plan_4_a_run_that_may_hand_off_is_told_the_engine_plans_that_work() -> None:
    run = RunContext.from_claim(w.claim(_agent_mode_spec(), chat_mode="agent"))
    assert _description(run) == PLAN_DESCRIPTION
    handing_off = run.model_copy(update={"code_task": True})
    assert _description(handing_off) == PLAN_DESCRIPTION + HAND_OFF_PLAN_NOTE


def test_r49plan_5_the_plan_call_is_never_a_step_named_after_the_tool() -> None:
    # The chat shows the list as its plan checklist (task.*); the call itself is plumbing.
    assert PLAN_TOOL in HIDDEN_TOOLS
