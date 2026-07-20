"""`PipesHubPromptBuilder` (`app/agents/agent_loop/prompt_builder.py`) —
validates the builder's assembly/gating logic (which sections appear,
in what circumstances) by stubbing out the heavy content-bearing helpers
it reuses (knowledge context, workflow patterns, user context, time
context). The "Available Tools" section and all composed-aware steering
are driven by a real `AgentRuntime`/`ToolRegistry` (see `_runtime_for`).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from tests.unit.agents.adapter.conftest import make_context


class FakeTool(Tool):
    """Minimal registrable tool with a real parameter schema, so the
    "Available Tools" section has something concrete to render."""

    def __init__(
        self, name: str, *, description: str = "", parameters: list[ToolParameter] | None = None,
    ) -> None:
        self._name = name
        self._description = description or f"fake {name}"
        self._parameters = parameters or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description.splitlines()[0]

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/fake/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


def _runtime_for(tools: list[Tool]) -> AgentRuntime:
    registry = ToolRegistry()
    for tool in tools:
        registry.register_tool(tool)
    return AgentRuntime(tool_registry=registry)


def _build(
    context,
    *,
    goal: Goal | None = None,
    base_system_prompt: str = "BASE_REACT_PROMPT",
    tool_names: list[str] | None = None,
    sandbox_has_network: bool = False,
    runtime: AgentRuntime | None = None,
) -> str:
    spec = AgentSpec(
        name="pipeshub-agent",
        system_prompt=base_system_prompt,
        tool_names=tool_names or [],
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )
    builder = PipesHubPromptBuilder(context)
    # Empty by default: existing tests reference tool NAMES (`run_code`,
    # `web_search`, ...) purely to drive the composed-aware steering
    # branches below, not to assert "Available Tools" content — an empty
    # registry makes every name unresolvable, so that section renders "".
    runtime = runtime if runtime is not None else _runtime_for([])
    with patch.multiple(
        "app.agents.agent_loop.prompt_builder",
        _build_workflow_patterns=MagicMock(return_value=""),
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=""),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
        sandbox_network_enabled=MagicMock(return_value=sandbox_has_network),
    ):
        return builder.build(spec, runtime, goal or Goal(description="hello"), [], {})


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


class TestCodeExecutionSteering:
    """`run_code` being available must trigger the mandatory
    file-generation/API-call steering rule — without it, the model has no
    strong signal to prefer code execution over answering in text for
    file-generation and external-API-calling tasks."""

    def test_steering_included_when_run_code_available(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code", "web_search"])
        assert "## Code Execution (MANDATORY" in result

    def test_steering_excluded_when_run_code_unavailable(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["web_search"])
        assert "## Code Execution (MANDATORY" not in result

    def test_steering_excluded_with_no_tools(self) -> None:
        context = make_context()
        result = _build(context, tool_names=[])
        assert "## Code Execution (MANDATORY" not in result

    def test_steering_clarifies_no_network_access(self) -> None:
        """When the sandbox has no network access (see `docker.py`'s
        `network_mode="none"`), the steering rule must say so explicitly, or
        the model keeps trying (and failing) to call external APIs from
        inside `run_code` instead of fetching first via `web_search`/
        `fetch_url`."""
        context = make_context()
        result = _build(context, tool_names=["run_code"], sandbox_has_network=False)
        assert "NO network access" in result
        assert "web_search" in result
        assert "fetch_url" in result

    def test_steering_advertises_network_access_when_sandbox_networked(self) -> None:
        """Once the sandbox CAN reach the network (see
        `sandbox_bridge.sandbox_network_enabled()`), the steering must flip
        to actively recommend calling a live public API from `run_code`
        over `web_search` — the whole point of turning network on."""
        context = make_context()
        result = _build(context, tool_names=["run_code"], sandbox_has_network=True)
        assert "network access available" in result
        assert "NO network access" not in result
        assert "CAN reach the network" in result


class TestToolSelectionStrategy:
    """Generic tool-arbitration guidance: pick the tool matching the data
    source, prefer live data over stale snapshots, and call overlapping
    tools in parallel when it's unclear which one is the better fit."""

    def test_section_included_when_web_search_configured(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_search"])
        assert "## Tool Selection Strategy" in result

    def test_section_excluded_without_web_search(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code"], sandbox_has_network=True)
        assert "## Tool Selection Strategy" not in result

    def test_live_api_guidance_included_when_sandbox_networked(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["run_code", "web_search"], sandbox_has_network=True)
        section = result.split("## Tool Selection Strategy", 1)[1]
        assert "run_code" in section
        assert "an API call IS the data" in section

    def test_live_api_guidance_excluded_when_sandbox_has_no_network(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["run_code", "web_search"], sandbox_has_network=False)
        section = result.split("## Tool Selection Strategy", 1)[1]
        assert "an API call IS the data" not in section

    def test_arbitration_guidance_always_present_when_section_shown(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_search"])
        section = result.split("## Tool Selection Strategy", 1)[1]
        assert "call BOTH in parallel" in section


class TestIdentityAndBehavior:
    def test_identity_block_present_when_no_custom_prompt(self) -> None:
        context = make_context()
        result = _build(context)
        assert "PipesHub workplace agent" in result

    def test_identity_block_absent_when_custom_prompt_set(self) -> None:
        context = make_context(system_prompt="You are Aria, a witty legal assistant.")
        result = _build(context)
        assert "PipesHub workplace agent" not in result

    def test_behavior_rules_always_present(self) -> None:
        context = make_context()
        result = _build(context)
        assert "## Behavior" in result
        assert "## Response Format" in result

    def test_behavior_rules_present_even_with_custom_prompt(self) -> None:
        context = make_context(system_prompt="You are Aria, a witty legal assistant.")
        result = _build(context)
        assert "## Behavior" in result

    def test_no_connector_guidance_in_output(self) -> None:
        from app.modules.agents.prompts.connector_guidance import GUIDANCE_MAP

        context = make_context(agent_toolsets=[{"name": "jira-cloud"}])
        result = _build(context)
        assert GUIDANCE_MAP["jira"] not in result

    def test_no_forbidden_bullets_in_service_only_strategy(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = False
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(context)
        assert "❌" not in result


class TestToolReferenceSection:
    """"Available Tools" must reflect what `spec.tool_names` actually
    grants (resolved against the real `AgentRuntime.tool_registry`), never
    a flat dump of every tool the legacy ChatState tool list carried —
    the regression this fix addresses (see the Opik trace in the plan)."""

    def test_no_section_without_any_resolvable_tools(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code"])  # empty registry by default
        assert "## Available Tools" not in result

    def test_flat_tool_rendered_with_short_description(self) -> None:
        context = make_context()
        tool = FakeTool(
            "jira_search_issues",
            description="Search Jira issues by JQL.",
            parameters=[ToolParameter(name="jql", type=ParameterType.STRING, description="JQL query", required=True)],
        )
        result = _build(context, tool_names=["jira_search_issues"], runtime=_runtime_for([tool]))
        assert "## Available Tools" in result
        assert "**jira_search_issues**" in result
        assert "Search Jira issues by JQL." in result

    def test_composed_delegate_rendered_instead_of_claimed_flat_tools(self) -> None:
        """Only what `spec.tool_names` grants is rendered — a domain that
        composition claimed (e.g. `run_code`, absent from `tool_names`
        even though still registered on the shared registry) must NOT
        appear, even though `coding_agent` (the delegate that claimed it)
        does."""
        context = make_context()
        coding_agent = FakeTool(
            "coding_agent",
            description="Delegate a coding task to a focused sub-agent.",
            parameters=[ToolParameter(name="goal", type=ParameterType.STRING, description="The goal.", required=True)],
        )
        run_code = FakeTool("run_code", description="Execute Python code in a sandbox.")
        runtime = _runtime_for([coding_agent, run_code])

        result = _build(context, tool_names=["coding_agent"], runtime=runtime)

        assert "**coding_agent**" in result
        assert "run_code" not in result.split("## Available Tools", 1)[1]

    def test_unresolvable_tool_name_skipped_without_error(self) -> None:
        """A name in `spec.tool_names` with nothing registered under it
        (e.g. a stale reference) must be skipped, not raise."""
        context = make_context()
        tool = FakeTool("run_code", description="Execute Python code.")
        result = _build(context, tool_names=["run_code", "ghost_tool"], runtime=_runtime_for([tool]))
        assert "**run_code**" in result
        assert "ghost_tool" not in result

    def test_tool_short_description_used_in_compact_format(self) -> None:
        context = make_context()
        tool = FakeTool(
            "calculator_evaluate",
            description="Evaluate a math expression.\nExtended details here.",
            parameters=[
                ToolParameter(name="expression", type=ParameterType.STRING, description="Expr", required=True),
                ToolParameter(name="precision", type=ParameterType.INTEGER, description="Digits", required=False),
            ],
        )
        result = _build(context, tool_names=["calculator_evaluate"], runtime=_runtime_for([tool]))
        section = result.split("## Available Tools", 1)[1]
        assert "**calculator_evaluate**" in section
        assert "Evaluate a math expression." in section


class TestComposedCodeExecutionSteering:
    """When domain-agent composition (`domain_agents.py`) claims `run_code`
    into `coding_agent`, the mandatory code-execution steering must
    reference the delegate the agent can actually call, not the tool it
    no longer has direct access to."""

    def test_coding_agent_referenced_instead_of_run_code(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["coding_agent", "web_agent"])
        section = result.split("## Code Execution", 1)[1]
        assert "delegate to `coding_agent`" in section
        assert "`run_code`" not in section

    def test_composed_no_network_still_warns_and_names_web_agent(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["coding_agent", "web_agent"], sandbox_has_network=False)
        assert "coding_agent`'s sandbox has NO network access" in result
        assert "delegate to `web_agent` FIRST" in result

    def test_composed_network_variant_names_coding_agent(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["coding_agent", "web_agent"], sandbox_has_network=True)
        assert "coding_agent`'s sandbox CAN reach the network" in result
        assert "prefer delegating to it over `web_agent`" in result

    def test_flat_run_code_unaffected_when_not_composed(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code", "web_search"])
        section = result.split("## Code Execution", 1)[1]
        assert "use `run_code`" in section
        assert "coding_agent" not in section


class TestComposedWebAndToolSelection:
    def test_web_search_rules_reference_web_agent_when_composed(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_agent"])
        section = result.split("## Web Search Rules", 1)[1]
        assert "delegate" in section
        assert "web_agent" in section
        assert "`web_search`" not in section

    def test_tool_selection_references_web_agent_when_composed(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_agent"])
        section = result.split("## Tool Selection Strategy", 1)[1]
        assert "web_agent" in section
        assert "`web_search`" not in section

    def test_tool_selection_composed_live_api_names_both_delegates(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["coding_agent", "web_agent"], sandbox_has_network=True)
        section = result.split("## Tool Selection Strategy", 1)[1]
        assert "coding_agent" in section
        assert "web_agent" in section
        assert "an API call IS the data" in section
        assert "call BOTH in parallel" in section


class TestComposedHybridStrategy:
    def test_hybrid_strategy_references_internal_exploration_agent_when_composed(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = True
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(context, tool_names=["internal_exploration_agent"])
        section = result.split("## Hybrid Search Strategy", 1)[1]
        assert "internal_exploration_agent" in section
        assert "retrieval.search_internal_knowledge" not in section

    def test_hybrid_strategy_keeps_flat_retrieval_name_when_not_composed(self) -> None:
        context = make_context()
        context.tool_state["has_knowledge"] = True
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(context, tool_names=["retrieval_search_internal_knowledge"])
        section = result.split("## Hybrid Search Strategy", 1)[1]
        assert "retrieval.search_internal_knowledge" in section


class TestInternalKnowledgeFirstSection:
    """`internal_exploration_agent` being granted must steer the model to
    prefer it over training data by default (see `prompt_builder.py`'s
    `_build_internal_knowledge_first_section`); the web-arbitration
    bullets — and the `ask_user_question` escape hatch specifically — must
    only appear when the corresponding tool is actually granted this turn."""

    def test_section_included_when_internal_exploration_agent_granted(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["internal_exploration_agent"])
        assert "## Internal Knowledge First" in result
        assert "internal_exploration_agent" in result.split("## Internal Knowledge First", 1)[1]

    def test_no_section_without_internal_exploration_agent(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["web_agent"])
        assert "## Internal Knowledge First" not in result

    def test_no_web_arbitration_when_web_agent_unavailable(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["internal_exploration_agent"])
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "web_agent" not in section

    def test_web_arbitration_included_when_both_agents_granted(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["internal_exploration_agent", "web_agent"])
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "web_agent" in section
        assert "genuinely unclear" in section

    def test_ask_user_question_offered_only_when_that_tool_is_granted(self) -> None:
        context = make_context()
        result = _build(
            context,
            tool_names=["internal_exploration_agent", "web_agent", "internaltools_ask_user_question"],
        )
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "ask_user_question" in section

    def test_no_ask_user_question_mention_when_that_tool_is_unavailable(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["internal_exploration_agent", "web_agent"])
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "ask_user_question" not in section
        assert "use BOTH and present both results" in section

    def test_no_full_record_follow_up_note_before_the_dynamic_tool_is_granted(self) -> None:
        """`dynamic_fetch_full_record` doesn't exist on turn 1 — mentioning
        it before `citation_tracking` (`hooks/citations.py`) has actually
        granted it would invite a tool-not-found call."""
        context = make_context()
        result = _build(context, tool_names=["internal_exploration_agent"])
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "dynamic_fetch_full_record" not in section

    def test_full_record_follow_up_note_included_once_dynamic_tool_is_granted(self) -> None:
        context = make_context()
        result = _build(
            context,
            tool_names=["internal_exploration_agent", "dynamic_fetch_full_record"],
        )
        section = result.split("## Internal Knowledge First", 1)[1]
        assert "dynamic_fetch_full_record" in section
        assert "re-delegating" in section
