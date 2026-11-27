"""SMOKEFIX: a tool found after a chat's list was fixed (R4) is called and runs as its own call.

The production assembler and ``RunDriver`` over the in-memory journal (``world``), lazy disclosure on, the model the
e2e stacks run (``ScriptedBrain``, ``deployment/e2e/fake_llm``) reading the human-test smoke's own script: find the MCP tool
with ``search_tools``, then call it. The smoke went 56 -> 49 when R4 fixed the list: the scripted model only named
a tool the list showed, so every script that found its tool first ended without calling it. A model calling the
found tool by name, not through ``call_tool``, still runs it, as before R4.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app import edition_config
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.tool_environment import toolset_tool_id
from harness_core.driver import RunDriver
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage
from harness_core.kernel.durable.transcript import text_of
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.testing.engines.script import ChatMessage, ModelReply, ScriptedBrain
from harness_core.testing.engines.script import ToolCall as BrainCall

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from harness_core.kernel.durable.ports import DeltaKind, ModelCall, SampledTurn

MCP_TOOL = "mcp_github_list_prs"
JIRA_WRITE = "jira__create_issue"
# The human-test smoke's discovery (deployment/e2e/smoke_human.py `discover`), then the call by the tool's short name.
SMOKE = 'tool search_tools {"query": "list_prs", "make_visible": true}\ntool list_prs {"repo": "a"}\nsay looked'


class BrainPort(ScriptedModelPort):
    """The e2e stacks' scripted model as the durable model port: the request's messages and tools, as a wire sends
    them, decide each reply."""

    def __init__(self, brain: Callable[[list[ChatMessage], list[str], str], ModelReply]) -> None:
        super().__init__(ScriptedModel(ModelScript(id="brain", turns=[Turn(reply=Reply(text=""))])))
        self._brain = brain
        self.offered: list[list[str]] = []

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        messages: list[ChatMessage] = []
        for message in call.messages:
            if isinstance(message, UserMessage):
                messages.append(ChatMessage("user", text_of(message)))
            elif isinstance(message, AssistantMessage):
                calls = tuple(BrainCall(c.id, c.name, json.dumps(c.arguments)) for c in message.tool_calls or [])
                messages.append(ChatMessage("assistant", text_of(message), tool_calls=calls))
            elif isinstance(message, ToolMessage):
                messages.append(ChatMessage("tool", text_of(message), tool_call_id=message.tool_call_id))
        tools = [t.name for t in call.tools or []]
        self.offered.append(tools)
        reply = self._brain(messages, tools, f"call-{len(self.offered)}")
        self.model = ScriptedModel(ModelScript(id="brain", turns=[Turn(reply=Reply(
            text=reply.content,
            tool_calls=[ToolCallSpec(id=c.id, name=c.name, args=json.loads(c.arguments)) for c in reply.tool_calls],
        ))]))
        sampled = await super().sample(call, on_delta)
        _ = self.calls.pop()
        return sampled


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "1")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def _run(port: BrainPort, query: str, services: w.Services | None = None) -> tuple[str, list[dict[str, Any]]]:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock)
    agent = w.agent_doc()
    tool_ids = [toolset_tool_id(t, tool["name"]) for t in agent["toolsets"] for tool in t["tools"]]
    spec = w.pinned_spec(tool_ids=[*tool_ids, f"mcp@{w.MCP}/list_prs"], mcp=[w.MCP])
    assembler = PipesHubRuntimeAssembler(services or w.Services(agent=agent), model_factory=w.model_factory(port))
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=spec, input_text=query)
    outcome = await driver.run_claimed(await w.claim_next(journal))
    return outcome.status, [{"type": e.type, **(e.data or {})} for e in journal.events("ses-1")]


def _invoked(events: list[dict[str, Any]]) -> list[str]:
    return [str(e.get("toolName")) for e in events if e["type"] == "x.pipeshub.tool.call_presented"]


async def test_smokefix_the_smokes_script_calls_the_mcp_tool_it_found_on_a_fixed_list() -> None:
    port = BrainPort(ScriptedBrain().reply)
    _status, events = await _run(port, SMOKE)
    assert MCP_TOOL not in port.offered[0], "the MCP tool is found, not listed: this test is about a fixed list"
    assert port.offered[1] == port.offered[0], "R4: the list never changes mid-chat"
    assert _invoked(events)[:2] == ["search_tools", MCP_TOOL], [e["type"] for e in events]


async def test_smokefix_a_model_naming_a_found_tool_outright_still_runs_it() -> None:
    """Before R4 a found tool was listed and the model named it; a model that still does gets the tool run."""
    replies = iter([
        ModelReply(tool_calls=(BrainCall("c1", "search_tools", json.dumps({"query": "list_prs"})),)),
        ModelReply(tool_calls=(BrainCall("c2", MCP_TOOL, json.dumps({"repo": "a"})),)),
        ModelReply(content="looked"),
    ])

    def by_name(_messages: list[ChatMessage], _tools: list[str], _call_id: str) -> ModelReply:
        return next(replies)

    port = BrainPort(by_name)
    status, events = await _run(port, "find the open PRs")
    assert MCP_TOOL not in port.offered[0]
    assert _invoked(events)[:2] == ["search_tools", MCP_TOOL]
    assert status == "completed"


async def test_smokefix_a_found_write_tool_called_through_call_tool_still_asks() -> None:
    script = ('tool search_tools {"query": "create_issue"}\n'
              'tool create_issue {"project": "P", "summary": "s"}\nsay filed')
    port = BrainPort(ScriptedBrain().reply)
    # Main #4313: a toolset write starts at Allow, so the ask here is an admin's rule (organization rules on).
    services = w.Services(agent=w.agent_doc())
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "ask"}})
    with patch.object(edition_config, "organization_toolset_tool_rules", True):
        status, events = await _run(port, script, services)
    assert JIRA_WRITE not in port.offered[0]
    asked = [e for e in events if e["type"] == "interaction.requested"]
    assert asked, [e["type"] for e in events]
    assert status != "completed"
