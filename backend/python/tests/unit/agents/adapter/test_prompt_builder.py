"""`PipesHubPromptBuilder` (`app/agents/agent_loop/prompt_builder.py`) —
validates the builder's assembly/gating logic (which sections appear,
in what circumstances) by stubbing out the heavy content-bearing helpers
it reuses (knowledge context, workflow patterns, user context, time
context). The "Available Tools" section and all composed-aware steering
are driven by a real `AgentRuntime`/`ToolRegistry` (see `_runtime_for`).

Assertions target gated SECTIONS and their operative rules, not exact
wording — a prompt rewrite that keeps the same behavior should not have
to touch this file (see the "Prompt consolidation" plan, Part 4).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
        _build_knowledge_context=MagicMock(return_value=""),
        build_llm_time_context=MagicMock(return_value=""),
        build_capability_summary=MagicMock(return_value=""),
        _format_user_context=MagicMock(return_value=""),
    ), patch(
        "app.modules.agents.context.tool_surface.sandbox_network_enabled",
        MagicMock(return_value=sandbox_has_network),
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

    def test_custom_instructions_included_when_set(self) -> None:
        """Org-level "Custom Instructions" (workspace settings, mode-resolved
        by `chat_modes.bridge`) must render as its own section, distinct
        from the per-agent `## Agent Instructions`."""
        context = make_context(custom_instructions="Always respond in Spanish.")
        result = _build(context)
        assert "## Custom Instructions" in result
        assert "Always respond in Spanish." in result

    def test_no_custom_instructions_section_when_unset(self) -> None:
        """Agent Builder agents never populate `custom_instructions` — this
        is the default for that path, and must not render an empty header."""
        context = make_context()
        result = _build(context)
        assert "## Custom Instructions" not in result

    def test_no_custom_instructions_section_when_blank(self) -> None:
        context = make_context(custom_instructions="   ")
        result = _build(context)
        assert "## Custom Instructions" not in result

    def test_custom_instructions_and_agent_instructions_coexist(self) -> None:
        """Both sections must be able to render together — org-level custom
        instructions and per-agent instructions are independent knobs."""
        context = make_context(
            instructions="Escalate anything about pricing.",
            custom_instructions="Always respond in Spanish.",
        )
        result = _build(context)
        assert "## Agent Instructions" in result
        assert "Escalate anything about pricing." in result
        assert "## Custom Instructions" in result
        assert "Always respond in Spanish." in result

    def test_goal_constraints_rendered_as_request_context(self) -> None:
        context = make_context()
        goal = Goal(description="hi", constraints=["Reuse the previous search results."])
        result = _build(context, goal=goal)
        assert "## Request Context" in result
        assert "Reuse the previous search results." in result

    def test_no_request_context_section_without_constraints(self) -> None:
        context = make_context()
        result = _build(context, goal=Goal(description="hi"))
        assert "## Request Context" not in result


class TestCitationRules:
    """When ``final_answer`` is enabled (default), citation rules live in the
    tool's parameter description, not the system prompt.  These tests verify
    system-prompt gating, so they mock ``final_answer_enabled`` to False."""

    _FA_PATCH = "app.agent_loop_lib.tools.builtin.planning.final_answer.final_answer_enabled"

    def test_citation_rules_included_when_retrieval_results_present(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context()
            context.tool_state["final_results"] = [{"id": "r1"}]
            result = _build(context)
        assert "## Citation Rules" in result

    def test_citation_rules_included_when_attachments_present(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context()
            context.tool_state["attachments"] = [{"name": "doc.pdf"}]
            result = _build(context)
        assert "## Citation Rules" in result

    def test_citation_rules_included_for_web_only_run(self) -> None:
        """`chatMode="web_search"` never prefetches, so `final_results` is
        empty for the whole run — but `web_search`/`fetch_url` results DO
        carry `url/Citation ID:` markers, so the citation format has to be
        in the prompt anyway."""
        with patch(self._FA_PATCH, return_value=False):
            context = make_context()
            context.tool_state["web_search_config"] = {"provider": "duckduckgo"}
            result = _build(context)
        assert "## Citation Rules" in result

    def test_citation_rules_included_when_knowledge_available_but_not_yet_retrieved(self) -> None:
        """Knowledge runs that retrieve via a tool call rather than prefetch
        have nothing in `final_results` on turn 0 — the turn where the model
        plans its answer — so gating on retrieved results alone is too late."""
        with patch(self._FA_PATCH, return_value=False):
            context = make_context(has_knowledge=True)
            result = _build(context)
        assert "## Citation Rules" in result

    def test_no_citation_rules_when_nothing_citable_can_reach_the_run(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context()
            result = _build(context)
        assert "## Citation Rules" not in result

    def test_citation_rules_present_when_only_a_composed_child_can_retrieve(self) -> None:
        """`react`/`deep`/`planExecute` modes push retrieval and web fetching
        down into `internal_exploration_agent`/`web_agent`, so the parent's
        `final_results` is empty on the turn it plans its answer — yet the
        parent is the only agent that writes citations, since a child's text is
        just a tool result. Both composed shapes must still get the rules."""
        with patch(self._FA_PATCH, return_value=False):
            knowledge = make_context(has_knowledge=True)
            assert "## Citation Rules" in _build(
                knowledge, tool_names=["internal_exploration_agent"]
            )

            web = make_context()
            web.tool_state["web_search_config"] = {"provider": "duckduckgo"}
            assert "## Citation Rules" in _build(web, tool_names=["web_agent"])

    def test_citation_rules_state_both_id_shapes_and_the_markdown_form(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context(has_knowledge=True)
            result = _build(context)
        assert "[source](ref5)" in result
        assert "url/Citation ID:" in result

    def test_citation_rules_forbid_using_a_citation_id_as_a_url(self) -> None:
        """Regression guard: a Citation ID is an opaque system token, not a URL.
        The rules section must make this clear to prevent the model from using
        a Citation ID as a display link."""
        with patch(self._FA_PATCH, return_value=False):
            context = make_context(has_knowledge=True)
            result = _build(context)
        assert "opaque system token" in result or "Citation ID is NOT a URL" in result or "opaque" in result


class TestRecordIdShorteningPromptNote:
    """The "R<n>" short-label note in the Record & Block Structure section
    is only accurate — and should only be shown — when this request opted
    into `RecordIdShortener` (see `ChatQuery.enableRecordIdShortening`,
    disabled by default)."""

    _FA_PATCH = "app.agent_loop_lib.tools.builtin.planning.final_answer.final_answer_enabled"

    def test_short_label_note_absent_when_flag_off_by_default(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context(has_knowledge=True)
            assert context.enable_record_id_shortening is False
            result = _build(context)
        assert "## Citation Rules" in result  # sanity: section is present
        assert "short label" not in result
        assert "R1" not in result and "R2" not in result

    def test_short_label_note_present_when_flag_on(self) -> None:
        with patch(self._FA_PATCH, return_value=False):
            context = make_context(has_knowledge=True, enable_record_id_shortening=True)
            result = _build(context)
        assert "short label" in result
        assert "`R1`, `R2`" in result


class TestAnswerConfidence:
    """`AnswerFinalizer` parses a trailing `---\\nConfidence: <level>` off the
    answer (`parse_confidence_from_answer`) and puts it on `completion_data`
    for the frontend's confidence indicator — the section is gated behind
    PIPESHUB_ENABLE_CONFIDENCE (default False)."""

    def test_confidence_section_absent_when_flag_off(self) -> None:
        """By default (flag off) the answer_confidence section is omitted."""
        with patch("app.agents.agent_loop.prompt_builder.confidence_enabled", return_value=False):
            result = _build(make_context())
        assert "## Answer Confidence" not in result

    def test_confidence_trailer_requested_when_flag_on(self) -> None:
        """When PIPESHUB_ENABLE_CONFIDENCE is true the section must appear."""
        with patch("app.agents.agent_loop.prompt_builder.confidence_enabled", return_value=True):
            result = _build(make_context())
        assert "## Answer Confidence" in result
        assert "Confidence: <Very High | High | Medium | Low>" in result

    def test_confidence_rubric_included_when_flag_on(self) -> None:
        with patch("app.agents.agent_loop.prompt_builder.confidence_enabled", return_value=True):
            result = _build(make_context())
        assert "never a turn that calls" in result


class TestFindingInformation:
    """The single source-precedence section, built from `ToolSurfaces` —
    replaces the old web-search/hybrid/service-only/tool-selection/
    internal-knowledge-first sections. Must never appear with nothing
    granted, must always list every granted surface, and must only add
    the parallelism rule once two or more surfaces are actually present."""

    def test_no_section_when_nothing_granted(self) -> None:
        context = make_context()
        result = _build(context, tool_names=[])
        assert "## Finding Information" not in result

    def test_section_present_when_retrieval_granted(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["retrieval__search_internal_knowledge"])
        assert "## Finding Information" in result
        section = result.split("## Finding Information", 1)[1]
        assert "retrieval__search_internal_knowledge" in section

    def test_section_present_when_only_web_search_configured(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_search", "fetch_url"])
        assert "## Finding Information" in result
        section = result.split("## Finding Information", 1)[1]
        assert "web_search" in section or "fetch_url" in section

    def test_parallelism_rule_only_with_two_or_more_surfaces(self) -> None:
        single = make_context(has_knowledge=True)
        single_result = _build(single, tool_names=["retrieval__search_internal_knowledge"])
        single_section = single_result.split("## Finding Information", 1)[1]
        assert "IN PARALLEL" not in single_section

        multi = make_context(has_knowledge=True)
        multi.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        multi_result = _build(
            multi,
            tool_names=["retrieval__search_internal_knowledge", "jira_search_issues"],
        )
        multi_section = multi_result.split("## Finding Information", 1)[1]
        assert "IN PARALLEL" in multi_section

    def test_live_only_apps_noted_when_knowledge_and_service_tools_both_present(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(
            context,
            tool_names=["retrieval__search_internal_knowledge", "jira_search_issues"],
        )
        section = result.split("## Finding Information", 1)[1]
        assert "Jira" in section
        assert "no indexed knowledge base" in section

    def test_no_live_only_note_when_no_service_tools(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["retrieval__search_internal_knowledge"])
        section = result.split("## Finding Information", 1)[1]
        assert "no indexed knowledge base" not in section

    def test_skip_list_and_search_depth_present_when_any_surface_granted(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["retrieval__search_internal_knowledge"])
        section = result.split("## Finding Information", 1)[1]
        assert "greetings" in section.lower()
        assert "reformulate" in section.lower() or "several" in section.lower()

    def test_disagreement_rule_present_when_multiple_surfaces(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["retrieval__search_internal_knowledge", "web_search"])
        section = result.split("## Finding Information", 1)[1]
        assert "disagree" in section.lower()

    def test_no_forbidden_bullets_style(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(
            context,
            tool_names=["retrieval__search_internal_knowledge", "jira_search_issues"],
        )
        assert "❌" not in result


class TestCodeExecutionSteering:
    """`run_code`/`coding_agent` being available must trigger the mandatory
    file-generation/API-call steering rule. Network/credentials/prefer-API
    facts now live entirely in `run_code`'s own function-calling schema
    (see `test_coding_sandbox.py`), so this section no longer names any
    web tool at all."""

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

    def test_steering_states_network_state_without_naming_web_tools(self) -> None:
        """The section states whether the sandbox has network access, but
        the fetch-first / prefer-API-over-search guidance is entirely the
        schema's job now — naming `web_search`/`fetch_url` here would risk
        naming a tool that was never granted (see the Part 0 fix)."""
        context = make_context()
        result = _build(context, tool_names=["run_code"], sandbox_has_network=False)
        section = result.split("## Code Execution", 1)[1]
        assert "NO network access" in section
        assert "web_search" not in section
        assert "fetch_url" not in section

    def test_steering_advertises_network_access_when_sandbox_networked(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code"], sandbox_has_network=True)
        assert "network access available" in result
        assert "NO network access" not in result


class TestIdentityAndOperatingRules:
    def test_identity_block_present_when_no_custom_prompt(self) -> None:
        context = make_context()
        result = _build(context)
        assert "PipesHub workplace agent" in result

    def test_identity_block_absent_when_custom_prompt_set(self) -> None:
        context = make_context(system_prompt="You are Aria, a witty legal assistant.")
        result = _build(context)
        assert "PipesHub workplace agent" not in result

    def test_operating_rules_and_response_format_always_present(self) -> None:
        with patch("app.agent_loop_lib.tools.builtin.planning.final_answer.final_answer_enabled", return_value=False):
            context = make_context()
            result = _build(context)
        assert "## Operating Rules" in result
        assert "## Response Format" in result

    def test_operating_rules_present_even_with_custom_prompt(self) -> None:
        context = make_context(system_prompt="You are Aria, a witty legal assistant.")
        result = _build(context)
        assert "## Operating Rules" in result

    def test_no_connector_guidance_in_output(self) -> None:
        with pytest.raises(ImportError):
            import app.modules.agents.prompts.connector_guidance  # noqa: F401

        context = make_context(agent_toolsets=[{"name": "jira-cloud"}])
        result = _build(context)
        assert "R-SLACK-1" not in result

    def test_follow_up_and_intent_resolution_merged_into_one_bullet(self) -> None:
        context = make_context()
        result = _build(context)
        section = result.split("## Operating Rules", 1)[1].split("\n## ", 1)[0]
        assert "self-contained request" in section
        assert "conversation history" in section
        assert "never ask the user to repeat" in section

    def test_operating_rules_identical_regardless_of_granted_tools(self) -> None:
        """Behavioral policy sections must not vary with the tool surface —
        only the surface-aware sections (finding_information, available_tools,
        code_execution) should change."""
        context = make_context()
        with_tool = _build(context, tool_names=["internaltools__ask_user_question"])
        without_tool = _build(context, tool_names=["web_search"])
        section_with = with_tool.split("## Operating Rules", 1)[1].split("\n## ", 1)[0]
        section_without = without_tool.split("## Operating Rules", 1)[1].split("\n## ", 1)[0]
        assert section_with == section_without


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

    def test_flat_run_code_unaffected_when_not_composed(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code", "web_search"])
        section = result.split("## Code Execution", 1)[1]
        assert "use `run_code`" in section
        assert "coding_agent" not in section


class TestComposedFindingInformation:
    """Composition swaps the delegate name; the section must reflect
    whichever surface is actually granted this turn, never both."""

    def test_finding_information_references_internal_exploration_agent_when_composed(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["internal_exploration_agent"])
        section = result.split("## Finding Information", 1)[1]
        assert "internal_exploration_agent" in section
        assert "retrieval__search_internal_knowledge" not in section

    def test_finding_information_keeps_flat_retrieval_name_when_not_composed(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["retrieval__search_internal_knowledge"])
        section = result.split("## Finding Information", 1)[1]
        assert "retrieval__search_internal_knowledge" in section

    def test_finding_information_references_web_agent_when_composed(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"enabled": True}
        result = _build(context, tool_names=["web_agent"])
        section = result.split("## Finding Information", 1)[1]
        assert "web_agent" in section
        assert "`web_search`" not in section


class TestFullRecordEscalation:
    """`dynamic_fetch_full_record` doesn't exist on turn 1 — mentioning it
    before `hooks/citations.py` has actually granted it would invite a
    tool-not-found call. Once granted, the section states only what the
    tool's own schema cannot: that in-context blocks are a ranked sample
    and IDs must be batched into ONE call."""

    def test_no_section_before_the_dynamic_tool_is_granted(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context, tool_names=["knowledgegraph__search"])
        assert "## Reading Records in Full" not in result

    def test_section_included_once_dynamic_tool_is_granted(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(
            context,
            tool_names=["knowledgegraph__search", "knowledgegraph__fetch_record"],
        )
        assert "## Reading Records in Full" in result
        section = result.split("## Reading Records in Full", 1)[1]
        assert "knowledgegraph__fetch_record" in section
        assert "ranked fragments" in section
        assert "ONE call" in section
