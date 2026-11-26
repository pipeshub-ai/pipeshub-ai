"""ASSIST1 AS1-10 and AS1-11: a run that pins the system assistant definition (the assistant's Agent mode).

The worker builds what Universal Agent Mode gives the actor now (``get_assistant_agent``), narrowed to the pin:
the loop mode is the pinned one, no saved agent is read or checked, the org's Agent custom instructions and the
user-context switch apply as in legacy, and MCP tools come from the grant.
"""

from __future__ import annotations

import copy
import dataclasses
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.run_definition import ASSISTANT_AGENT_KEY
from app.harness.assembly.context import RunContext
from app.harness.assembly.tool_environment import (
    build_tool_environment,
    is_system_assistant,
)
from app.harness.credentials.tool_credentials import unavailable_reference
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

ASSISTANT: dict[str, Any] = {
    "systemPrompt": "You are a helpful AI assistant with access to various tools and knowledge sources.",
    "models": [],
    "toolsets": copy.deepcopy(w.agent_doc()["toolsets"]),
    # The assistant's servers name no tools: Universal Agent Mode discovers them live.
    "mcpServers": [{"instanceId": w.MCP, "name": "GitHubMCP", "typeId": "github", "displayName": "GitHub"}],
    "knowledge": [{"connectorId": "kb-1", "type": "KB"}, {"connectorId": "kb-2", "type": "KB"}],
}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    with w.deployment():
        yield


def _system_spec(tool_ids: list[str], *, mode: str = "quick", kb: tuple[str, ...] = ("kb-1",)) -> RunSpec:
    spec = dump_contract(w.pinned_spec(tool_ids=tool_ids, mcp=[w.MCP], kb=list(kb)))
    spec["extensions"]["x.pipeshub"]["agent"] = {"agentKey": ASSISTANT_AGENT_KEY, "definitionVersion": 1, "mode": mode}
    return validate_contract(RunSpec, spec)


def _run(tool_ids: list[str], **kwargs: Any) -> RunContext:  # noqa: ANN401
    return RunContext.from_claim(w.claim(_system_spec(tool_ids, **kwargs), chat_mode="agent"))


async def _environment(run: RunContext, services: w.Services, prompts: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    assistant = AsyncMock(return_value=(copy.deepcopy(ASSISTANT), {}))
    with patch("app.api.routes.agent.get_assistant_agent", assistant), \
         patch("app.api.routes.chatbot.load_system_prompts", AsyncMock(return_value=prompts or {})):
        env = await build_tool_environment(run, services, await w.model_factory(port)(run, services))
    return env, assistant


GRANT = ["jira@inst-42/search_issues", f"mcp@{w.MCP}/list_prs", "web@duckduckgo/web_search"]


class TestContext:
    def test_the_loop_mode_is_the_pinned_one_while_the_input_keeps_agent(self) -> None:
        run = _run(GRANT)
        assert is_system_assistant(run)
        assert run.chat_mode == "quick"
        assert run.loop_mode == "quick"
        assert run.chat is None

    def test_a_saved_agent_keeps_the_input_mode(self) -> None:
        run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[]), chat_mode="react"))
        assert not is_system_assistant(run)
        assert run.chat_mode == "react"


class TestEnvironment:
    async def test_universal_agent_modes_grant_narrowed_to_the_pin_without_a_saved_agent(self) -> None:
        services = w.Services()
        env, assistant = await _environment(_run(GRANT), services)

        assert assistant.await_args.args[:2] == (w.ACTOR, w.ORG)
        services.graph.get_agent.assert_not_called()
        services.graph.check_agent_permission.assert_not_called()
        state = env.context.tool_state
        assert [t["name"] for t in state["agent_toolsets"][0]["tools"]] == ["search_issues"]
        assert state["mcp_servers"][0]["tools"] == [{"name": "list_prs", "fullName": "mcp_github_list_prs"}]
        assert env.granted_mcp == {w.MCP: frozenset({"list_prs"})}
        assert env.web_search_provider == "duckduckgo"
        assert [k["connectorId"] for k in state["agent_knowledge"]] == ["kb-1"]

    async def test_the_orgs_agent_custom_instructions_apply_as_in_legacy(self) -> None:
        env, _ = await _environment(
            _run(GRANT),
            w.Services(),
            prompts={"customSystemPromptAgent": "  Cite the handbook.  ", "customSystemPrompt": "not this"},
        )
        assert env.context.tool_state["custom_instructions"] == "Cite the handbook."

    async def test_a_grant_without_mcp_loads_no_server(self) -> None:
        env, _ = await _environment(_run(["jira@inst-42/search_issues"]), w.Services())
        assert env.context.tool_state["mcp_servers"] == []

    async def test_a_toolset_disconnected_since_admission_is_unavailable_never_widened(self) -> None:
        services = w.Services()
        services.config.answer("/services/toolsets/", lambda _key: {"isAuthenticated": False})
        env, _ = await _environment(_run(GRANT), services)
        assert [(item.kind, item.instance) for item in env.unavailable] == [("toolset", w.JIRA)]
        assert env.context.toolset_configs[w.JIRA] == unavailable_reference("Jira")


class TestReplay:
    async def test_as1_11_a_resumed_attempt_reads_the_same_grants(self) -> None:
        first, _ = await _environment(_run(GRANT), w.Services())
        again, _ = await _environment(_run(GRANT), w.Services())
        assert again.granted_toolsets == first.granted_toolsets
        assert again.granted_mcp == first.granted_mcp
        assert again.context.tool_state["agent_knowledge"] == first.context.tool_state["agent_knowledge"]


def _deep(tool_ids: list[str]) -> RunContext:
    claim = w.claim(_system_spec(tool_ids), chat_mode="agent")
    snapshot = claim.run.input.model_copy(update={"agent_capabilities": {"webSearch": True, "deepSearch": True}})
    return RunContext.from_claim(dataclasses.replace(claim, run=claim.run.model_copy(update={"input": snapshot})))


class TestDeepResearch:
    """#4300: a turn that asks for deep research gets it as on the legacy loop, and only with web search."""

    async def test_the_turns_deep_research_reaches_the_loop(self) -> None:
        env, _ = await _environment(_deep(GRANT), w.Services())
        assert env.context.deep_research is True

    async def test_without_a_web_grant_there_is_no_deep_research(self) -> None:
        env, _ = await _environment(_deep(["jira@inst-42/search_issues"]), w.Services())
        assert env.context.deep_research is False

    async def test_a_turn_that_did_not_ask_gets_none(self) -> None:
        env, _ = await _environment(_run(GRANT), w.Services())
        assert env.context.deep_research is False
