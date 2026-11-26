"""PA-2: lazy disclosure, ``search_tools``, ``final_answer`` and attachments on the durable path.

Each case runs the legacy implementation on the same inputs as the oracle.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.agent_loop.mcp_lazy import PINS_SLOT
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.tool_content import tool_message_content
from harness_core.kernel.durable.ports import ModelCall, ToolContext
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.kernel.fold import RunState

from . import world as w
from .test_parity import SANDBOX, _durable_turn_one, _full_grant, _legacy

if TYPE_CHECKING:
    from collections.abc import Iterator

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")
# R4: the chat's tool list is fixed at its first request, so the tool legacy grants once a search returned records
# is in the durable run's list from the start.
# Offered up front beside what legacy lists: the record reader and the plan tool (R49-PLAN, ADR-OWD-07 D-PLAN-1).
UP_FRONT = {"knowledgegraph__fetch_record", "write_todos"}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's own switches: lazy over a threshold the fixture crosses."""
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "3")
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_SCOPE", "top_level")


async def test_td2_lazy_disclosure_starts_with_the_legacy_first_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn

    _lazy(monkeypatch)
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    _, _, shaped = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    legacy_agent, legacy_runtime, _goal = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    assert legacy_agent.spec.tool_disclosure == "lazy"
    legacy_first = tool_schemas_for_turn(SimpleNamespace(visible_tools=None), legacy_agent.spec, legacy_runtime)
    durable = [t.name for t in shaped.tools or []]
    assert sorted(durable) == sorted({t.name for t in legacy_first if t.name not in SANDBOX} | UP_FRONT)
    assert {"list_toolsets", "fetch_tools", "search_tools", "call_tool"} <= set(durable)
    assert "jira__search_issues" not in durable, "a connector tool is not listed"


async def test_td2_search_tools_answers_schemas_and_never_grows_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4: the match comes back with its input schema and how to call it; the run's list, and a fresh attempt's
    (another worker), stay what they were, so the chat's cached tools prefix holds."""
    _lazy(monkeypatch)
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, first = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    found = await runtime.tools.execute(
        ToolCall(id="c1", name="search_tools", arguments={"query": "jira issues"}), CTX
    )
    assert found.status == "ok"
    # Journaled as the model reads it (the legacy serialization, key order kept).
    assert isinstance(found.content, str)
    answered = json.loads(found.content)
    match = next(m for m in answered["matches"] if m["name"] == "jira__search_issues")
    assert match["inputSchema"]["required"] == ["jql"]
    assert "call_tool" in answered["how_to_call"]
    # The list does not change; a server the search first used is pinned with the answer (D-LAZY-5).
    assert set(found.slots) <= {PINS_SLOT}

    resumed, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    state = RunState(run_id="run-1", slots=dict(slots))
    shaped = await resumed.shaper.shape(ModelCall([], None, None, state.slots), 2, state)  # type: ignore[union-attr]
    assert [t.name for t in shaped.tools or []] == [t.name for t in first.tools or []]
    assert "jira__search_issues" not in {t.name for t in shaped.tools or []}


async def test_td1_search_tools_in_eager_mode_matches_the_legacy_route(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eager (the default under the threshold): the model gets search_tools and
    the result the legacy special route returns (grant-filtered matches)."""
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")  # legacy's eager list, MCP listed at the claim
    from app.agent_loop_lib.agent.tool_loop import (
        execute_tool_call,  # noqa: F401 - the legacy route's home
    )
    from app.agent_loop_lib.tools.builtin.lazy_toolsets import SearchToolsTool

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, _, shaped = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    assert "search_tools" in {t.name for t in shaped.tools or []}
    call = ToolCall(id="c1", name="search_tools", arguments={"query": "jira issues"})
    durable = await runtime.tools.execute(call, CTX)
    assert durable.slots == {}, "eager disclosure journals no visibility"

    legacy_agent, legacy_runtime, _ = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    tool = legacy_runtime.tool_registry.resolve_by_name("search_tools")
    assert isinstance(tool, SearchToolsTool)
    legacy, _unavailable = await tool.resolve(call, grant=set(legacy_agent.spec.tool_names))
    assert durable.content == tool_message_content(legacy.content)


async def test_td3_final_answer_is_registered_and_ends_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "true")
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")  # legacy's eager list, MCP listed at the claim
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, _, shaped = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    legacy_agent, _, _ = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    assert "final_answer" in set(legacy_agent.spec.tool_names)
    assert {t.name for t in shaped.tools or []} == (set(legacy_agent.spec.tool_names) - SANDBOX) | UP_FRONT
    assert runtime.tools.describe("final_answer").effect == "read"  # type: ignore[union-attr]

    answered = await runtime.tools.execute(
        ToolCall(id="c1", name="final_answer", arguments={"answer_markdown": "25 days."}), CTX
    )
    assert (answered.status, answered.answer) == ("ok", "25 days.")
    empty = await runtime.tools.execute(
        ToolCall(id="c2", name="final_answer", arguments={"answer_markdown": ""}), CTX
    )
    assert empty.status == "error" and empty.answer is None
    fallback = await runtime.tools.execute(
        ToolCall(id="c3", name="final_answer", arguments={"answer_markdown": ""}),
        ToolContext("run-1", w.ORG, "t1.0", "att-1", "key", turn_text="From the turn text."),
    )
    assert fallback.answer == "From the turn text."


async def test_td4_attachments_join_the_goal_and_images_reach_the_context() -> None:
    """Legacy's attachment resolution: its text joins the goal with the citation
    hint (journaled once), its images reach the context the L0 shaper reads."""
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    attachments = [{"recordId": "rec-1", "virtualRecordId": "vr-1", "recordName": "Leave.pdf", "mimeType": "application/pdf"}]
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    resolved = AsyncMock(return_value=("<record>Leave policy: 25 days</record>", [image]))
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    with (
        patch("app.agents.agent_loop.hooks.resolve_attachments_for_goal", resolved),
        patch("app.agents.agent_loop.factory.resolve_attachments_for_goal", resolved),
    ):
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
        claim = w.claim(spec, query="How much leave?", attachments=attachments)
        services = w.Services(agent=agent)
        services.readable_vrids["vr-1"] = "rec-1"
        runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(claim)
        slots: dict[str, Any] = {}
        for step in runtime.pre_run:
            slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run("How much leave?")
        legacy_goal = await _legacy_with_attachments(agent, spec, attachments)
    assert slots["pre_run.goal"]["description"] == legacy_goal.description
    assert "Leave policy: 25 days" in slots["pre_run.goal"]["description"]
    context = runtime.presenter._context  # type: ignore[union-attr]
    assert context.attachment_image_blocks == [image]


async def _legacy_with_attachments(
    agent: dict[str, Any], spec: Any, attachments: list[dict[str, Any]]  # noqa: ANN401
) -> Any:  # noqa: ANN401
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import build_tool_environment
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort
    from tests.unit.agents.adapter.conftest import FakeChatModel

    services = w.Services(agent=agent)
    services.readable_vrids["vr-1"] = "rec-1"
    run = RunContext.from_claim(w.claim(spec, query="How much leave?", attachments=attachments))
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    env = await build_tool_environment(run, services, await w.model_factory(port)(run, services))
    created = await PipesHubAgentFactory().create(
        env.context, FakeChatModel(), "quick", query=run.query, model_name="gpt-x", model_key="model-1"
    )
    assert env.context.attachment_image_blocks, "legacy keeps the images for L0"
    return created[2]
