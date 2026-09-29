"""The `internal_search` chat mode offers a focused enterprise-search surface:
tools and prompt sections that read, search and navigate the organization's
knowledge stay; file generation, code execution, skills, the public web,
MCP, and connector write actions are withheld from BOTH the grant and the
prompt. Every other mode is pinned byte-for-byte in `test_surface_golden.py`.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.tools.base import Tag
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.surface import SurfacePolicy, is_read_only_tool
from app.agents.agent_loop.tool_loader import _build_dynamic_tools
from app.agents.chat_modes.bridge import _apply_policy_to_chat_state
from app.agents.chat_modes.policy import (
    AGENT_POLICY,
    ENTERPRISE_SEARCH_SURFACE,
    INTERNAL_SEARCH_POLICY,
    WEB_SEARCH_POLICY,
    resolve_agent_policy,
    resolve_chat_mode_policy,
)
from tests.unit.agents.adapter.test_surface_golden import (
    INTERNAL_SEARCH_CASE,
    build_case,
    chat_state_for_policy,
    pinned_environment,
)

WITHHELD_TOOLS = frozenset({
    "artifacts__save_artifact", "artifacts__update_artifact", "artifacts__list_artifacts",
    "artifacts__get_artifact_content", "artifacts__get_artifact_download_url",
    "artifacts__get_record_download_url", "artifacts__promote_artifact",
    "image_generator__generate_image",
    "run_code", "install_packages", "read_sandbox_file",
    "skills_list", "load_skill", "load_skill_resource", "skill_search", "skill_manage",
    "dynamic__web_search", "dynamic__fetch_url",
    "tickets__create_ticket",
    "list_toolsets", "fetch_tools", "search_tools",
})
KEPT_TOOLS = frozenset({
    "knowledgegraph__search", "knowledgegraph__navigate", "knowledgegraph__lookup_record",
    "knowledgegraph__list_files", "knowledgegraph__search_entities",
    "calculator__evaluate_expression", "calculator__date_difference",
    "date_calculator__list_weekend_dates",
    "internaltools__ask_user_question",
    "tickets__search_tickets",
    "retrieve_artifact_content",
})
# Granted mid-run by hooks (`hooks/citations.py`, `hooks/progressive_tools.py`),
# never on turn 0, and named in the prompt with that caveat.
DYNAMICALLY_GRANTED = frozenset({"knowledgegraph__fetch_record", "knowledgegraph__find_records_by_entity"})

WITHHELD_PROMPT_TEXT = (
    "## Code Execution", "## Skills", "Tools you must load before calling",
    "artifacts__", "image_generator", "run_code", "load_skill", "web_search",
    "Write actions require explicit user intent", "Before calling any WRITE tool",
    "Web search results use a different marker", "write actions where every required parameter",
    "jira_", "Example 4", "You are a PipesHub workplace agent",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    pinned_environment(monkeypatch)
    return monkeypatch


async def _internal_search() -> dict[str, Any]:
    return await build_case(*INTERNAL_SEARCH_CASE)


def _available_tools_listing(prompt: str) -> set[str]:
    section = prompt.split("## Available Tools", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^- \*\*([A-Za-z0-9_.]+)\*\*", section, re.MULTILINE))


def _backticked_tool_names(prompt: str) -> set[str]:
    names = set(re.findall(r"`([a-z][a-z0-9_]*__[a-z0-9_]+)", prompt))
    return names | set(re.findall(r"\b([a-z_]+__[a-z0-9_]+)\b", prompt))


class TestPolicyResolution:
    @pytest.mark.parametrize("wire", ["internal_search", None, "", "quick", "standard", "unknown"])
    def test_internal_search_and_its_fallbacks_carry_the_focused_surface(self, wire: str | None) -> None:
        assert resolve_chat_mode_policy(wire).surface is ENTERPRISE_SEARCH_SURFACE

    @pytest.mark.parametrize("policy", [WEB_SEARCH_POLICY, AGENT_POLICY, resolve_agent_policy()])
    def test_other_modes_keep_the_full_surface(self, policy) -> None:
        assert policy.surface is None

    def test_bridge_hands_the_surface_to_the_agent_context(self) -> None:
        state: dict[str, Any] = {}
        _apply_policy_to_chat_state(state, INTERNAL_SEARCH_POLICY, None)
        assert AgentContext.from_chat_state(state).surface_policy is ENTERPRISE_SEARCH_SURFACE

        _apply_policy_to_chat_state(state, AGENT_POLICY, None)
        assert AgentContext.from_chat_state(state).surface_policy is None


class TestFocusedGrant:
    async def test_withheld_tools_absent_and_kept_tools_present(self, env) -> None:
        case = await _internal_search()
        granted = set(case["tool_names"])
        assert not granted & WITHHELD_TOOLS
        assert granted >= KEPT_TOOLS

    async def test_withheld_toolsets_are_never_registered(self, env) -> None:
        case = await _internal_search()
        assert not {"artifacts", "image_generator", "skills", "skill_authoring"} & set(case["registry_toolsets"])

    async def test_lazy_catalog_lists_no_withheld_toolset(self, env) -> None:
        env.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        case = await _internal_search()
        assert case["tool_disclosure"] == "lazy"
        catalog = case["prompt"].split("Tools you must load before calling", 1)[-1]
        for name in ("artifacts", "image_generator", "skills", "coding", "tickets__create_ticket"):
            assert name not in catalog
        assert not set(case["tool_names"]) & (WITHHELD_TOOLS - {"list_toolsets", "fetch_tools", "search_tools"})

    async def test_web_tools_withheld_even_with_web_config(self, env) -> None:
        state = chat_state_for_policy(INTERNAL_SEARCH_POLICY)
        state["web_search_config"] = {"provider": "duckduckgo", "configuration": {}}
        tools = _build_dynamic_tools(AgentContext.from_chat_state(state))
        assert not [t for t in tools if "web_search" in t.name or "fetch_url" in t.name]

    async def test_mcp_servers_are_not_loaded(self, env) -> None:
        load_into = AsyncMock()
        env.setattr("app.agents.agent_loop.factory.MCPToolProvider.load_into", load_into)
        state = chat_state_for_policy(INTERNAL_SEARCH_POLICY)
        state["mcp_servers"] = [{"instanceId": "mcp-1", "name": "tracker"}]
        context = AgentContext.from_chat_state(state)
        await PipesHubAgentFactory().create(context, context.llm, "quick", query="q")
        load_into.assert_not_awaited()

    async def test_artifact_reminder_hook_not_registered(self, env) -> None:
        context = AgentContext.from_chat_state(chat_state_for_policy(INTERNAL_SEARCH_POLICY))
        _agent, runtime, _goal, _ = await PipesHubAgentFactory().create(context, context.llm, "quick", query="q")
        names = [mw.__qualname__ for _m, mw in runtime.hooks.on(HookEvent.PRE_TURN)._stack]
        assert not any("artifact_context_reminder" in n for n in names)


class TestFocusedPrompt:
    async def test_withheld_sections_absent(self, env) -> None:
        prompt = (await _internal_search())["prompt"]
        present = [text for text in WITHHELD_PROMPT_TEXT if text in prompt]
        assert not present

    async def test_kept_sections_and_grounding_instruction_present(self, env) -> None:
        prompt = (await _internal_search())["prompt"]
        assert prompt.startswith("You are PipesHub's enterprise search assistant.")
        assert "Answer ONLY from the organization's internal knowledge base" in prompt
        for heading in (
            "## Operating Rules", "## Response Format", "## Citation Rules", "## Finding Information",
            "## Available Tools", "## Knowledge Sources", "## Current User Information", "## Time context",
        ):
            assert heading in prompt, heading
        assert "**Trust boundary**" in prompt

    async def test_tools_listed_are_exactly_the_granted_ones(self, env) -> None:
        case = await _internal_search()
        assert _available_tools_listing(case["prompt"]) == set(case["tool_names"])

    async def test_every_tool_named_in_the_prompt_is_granted(self, env) -> None:
        case = await _internal_search()
        named = _backticked_tool_names(case["prompt"])
        assert named <= set(case["tool_names"]) | DYNAMICALLY_GRANTED

    async def test_small_models_get_only_knowledge_worked_examples(self, env) -> None:
        # No model info resolves to the MID tier, which gets worked traces.
        prompt = (await _internal_search())["prompt"]
        assert "## Worked Examples" in prompt
        assert "### Example 2 — Empty result then query reformulation" in prompt
        examples = prompt.split("## Worked Examples", 1)[1].split("\n## ", 1)[0]
        assert "jira" not in examples.lower()
        assert "### Example 3" not in examples

    async def test_focused_prompt_is_much_smaller_than_the_full_surface(self, env) -> None:
        focused = (await _internal_search())["prompt"]

        def full_surface_state() -> dict[str, Any]:
            state = chat_state_for_policy(INTERNAL_SEARCH_POLICY)
            state["surface_policy"] = None
            return state

        full = (await build_case(full_surface_state, "quick"))["prompt"]
        assert len(focused) < 0.75 * len(full)


class TestWithheldLogging:
    async def test_one_info_line_names_what_was_withheld_without_prompt_content(self, env, caplog) -> None:
        caplog.set_level(logging.INFO, logger="app.agents.agent_loop.factory")
        await _internal_search()
        lines = [r.getMessage() for r in caplog.records if "surface=enterprise_search" in r.getMessage()]
        assert len(lines) == 1
        line = lines[0]
        for fragment in ("code_execution", "artifacts", "image_generator", "write_action_rule",
                         "write_tools=1", "conversation_id=conv-1", "run_id="):
            assert fragment in line
        assert "parental leave" not in line
        assert "Answer ONLY" not in line

    async def test_full_surface_logs_nothing_about_withholding(self, env, caplog) -> None:
        caplog.set_level(logging.INFO, logger="app.agents.agent_loop.factory")
        await build_case(lambda: chat_state_for_policy(AGENT_POLICY), "quick")
        assert not [r for r in caplog.records if "withheld capabilities" in r.getMessage()]


def _tool(name: str, *tags: Tag) -> Any:
    tool = MagicMock()
    tool.name = name
    tool.tags = list(tags)
    return tool


class TestReadOnlyClassification:
    @pytest.mark.parametrize("name", [
        "jira__search_issues", "confluence__get_page", "slack__list_channels", "drive__fetch_file",
        "github__read_file", "tickets__lookup_record", "outlook__query_messages",
    ])
    def test_read_verbs_are_read_only(self, name: str) -> None:
        assert is_read_only_tool(_tool(name))

    @pytest.mark.parametrize("name", [
        "jira__create_issue", "confluence__update_page", "slack__send_message", "drive__delete_file",
        "jira__transition_issue", "gmail__reply_to_thread", "calendar__schedule_meeting",
    ])
    def test_everything_else_is_withheld(self, name: str) -> None:
        assert not is_read_only_tool(_tool(name))

    def test_tags_override_the_verb(self) -> None:
        assert not is_read_only_tool(_tool("jira__get_or_create", Tag("category", "write")))
        assert not is_read_only_tool(_tool("tickets__search", Tag("risk", "high")))
        assert is_read_only_tool(_tool("tickets__summarize", Tag("category", "read")))

    def test_full_surface_permits_every_tool(self) -> None:
        assert SurfacePolicy(name="full").permits_tool(_tool("jira__create_issue"))
        assert not ENTERPRISE_SEARCH_SURFACE.permits_tool(_tool("jira__create_issue"))
