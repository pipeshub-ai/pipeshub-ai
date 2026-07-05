"""`PipesHubPromptBuilder` (`app/agents/agent_loop/prompt_builder.py`) —
Phase 4: validates the builder's OWN assembly/gating logic (which sections
appear, in what circumstances) by stubbing out the heavy content-bearing
helpers it reuses unchanged from Phase 0's extracted modules (knowledge
context, workflow patterns, capability summary, user context, tool
descriptions, time context) — those have their own unit coverage; a
byte-for-byte diff against `nodes.py`'s legacy prompt output is impractical
to drive from a unit test without constructing a full `ChatState`, so this
suite instead pins down every conditional branch `build()` itself owns."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.tool_guidance import ToolGuidanceProvider
from tests.unit.agents.adapter.conftest import make_context


def _build(context, *, goal: Goal | None = None, base_system_prompt: str = "BASE_REACT_PROMPT") -> str:
    spec = AgentSpec(
        name="pipeshub-agent",
        system_prompt=base_system_prompt,
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )
    builder = PipesHubPromptBuilder(context, ToolGuidanceProvider())
    with patch.multiple(
        "app.agents.agent_loop.prompt_builder",
        _get_cached_tool_descriptions=MagicMock(return_value=""),
        _build_workflow_patterns=MagicMock(return_value=""),
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=""),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
    ):
        return builder.build(spec, MagicMock(), goal or Goal(description="hello"), [], {})


class TestLayering:
    def test_base_system_prompt_always_present(self) -> None:
        context = make_context()
        result = _build(context)
        assert "BASE_REACT_PROMPT" in result

    def test_agent_instructions_included_when_set(self) -> None:
        context = make_context(instructions="Always respond in French.")
        result = _build(context)
        assert "## Agent Instructions" in result
        assert "Always respond in French." in result

    def test_no_instructions_section_when_unset(self) -> None:
        context = make_context()
        result = _build(context)
        assert "## Agent Instructions" not in result

    def test_custom_system_prompt_included_when_distinct_from_default(self) -> None:
        context = make_context(system_prompt="You are Aria, a witty legal assistant.")
        result = _build(context)
        assert "You are Aria, a witty legal assistant." in result

    def test_default_placeholder_system_prompt_not_duplicated(self) -> None:
        from app.modules.agents.qna.chat_state import DEFAULT_AGENT_SYSTEM_PROMPT

        context = make_context(system_prompt=DEFAULT_AGENT_SYSTEM_PROMPT)
        result = _build(context)
        assert result.count(DEFAULT_AGENT_SYSTEM_PROMPT) == 0

    def test_goal_constraints_rendered_as_additional_context(self) -> None:
        context = make_context()
        goal = Goal(description="hi", constraints=["Reuse the previous search results."])
        result = _build(context, goal=goal)
        assert "## Additional Context" in result
        assert "Reuse the previous search results." in result

    def test_no_additional_context_section_without_constraints(self) -> None:
        context = make_context()
        result = _build(context, goal=Goal(description="hi"))
        assert "## Additional Context" not in result


class TestCitationAndWebSearchRules:
    def test_citation_rules_included_when_retrieval_results_present(self) -> None:
        context = make_context()
        context.tool_state["final_results"] = [{"id": "r1"}]
        result = _build(context)
        assert "## Citation Rules" in result

    def test_citation_rules_included_when_attachments_present(self) -> None:
        context = make_context()
        context.tool_state["attachments"] = [{"name": "doc.pdf"}]
        result = _build(context)
        assert "## Citation Rules" in result

    def test_no_citation_rules_without_retrieval_or_attachments(self) -> None:
        context = make_context()
        result = _build(context)
        assert "## Citation Rules" not in result

    def test_web_search_rules_included_when_web_search_configured(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context)
        assert "## Web Search Rules" in result

    def test_no_web_search_rules_without_config(self) -> None:
        context = make_context()
        result = _build(context)
        assert "## Web Search Rules" not in result


class TestHybridStrategySection:
    def test_hybrid_strategy_when_knowledge_and_service_tools_both_present(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = True
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(context)
        assert "## Hybrid Search Strategy" in result

    def test_service_only_strategy_when_no_knowledge_base(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = False
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(context)
        assert "## Service-Tool Search Strategy" in result
        assert "## Hybrid Search Strategy" not in result

    def test_no_strategy_section_when_no_service_tools(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = True
        context.tool_state["agent_toolsets"] = []
        result = _build(context)
        assert "## Hybrid Search Strategy" not in result
        assert "## Service-Tool Search Strategy" not in result

    def test_hybrid_strategy_mentions_web_search_when_configured(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = True
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context)
        assert "web_search" in result.split("## Hybrid Search Strategy", 1)[1]


class TestConnectorGuidance:
    def test_active_connector_guidance_included(self) -> None:
        """`ToolGuidanceProvider.get_active_guidance()` reads `context.
        agent_toolsets` (the typed `AgentContext` field), not
        `tool_state["agent_toolsets"]` — the two start in sync via
        `model_post_init`'s seeding but are otherwise independent."""
        from app.modules.agents.prompts.connector_guidance import GUIDANCE_MAP

        context = make_context(agent_toolsets=[{"name": "jira-cloud"}])
        result = _build(context)
        assert GUIDANCE_MAP["jira"] in result

    def test_inactive_connector_guidance_excluded(self) -> None:
        from app.modules.agents.prompts.connector_guidance import GUIDANCE_MAP

        context = make_context(agent_toolsets=[])
        result = _build(context)
        assert GUIDANCE_MAP["jira"] not in result
