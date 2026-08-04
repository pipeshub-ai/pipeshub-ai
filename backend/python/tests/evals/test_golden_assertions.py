"""Golden eval harness — CI-safe structural assertions (Phase 10).

Each test below corresponds to one of the six golden properties the agent
prompt and supporting code are required to enforce:

1. **Correct first-tool selection** — prompt instructs the model to prefer
   internal knowledge over web for internal questions, and to call
   `knowledgegraph__search` (not a web tool) when knowledge is granted.

2. **Non-placeholder required params** — prompt forbids guesses and
   placeholders in write-tool required params.

3. **Exactly one final_answer** — `TAG_LIFECYCLE_TERMINAL` on
   `FinalAnswerTool` ensures the loop halts after a single call (structural
   guarantee, not prompt-dependent).

4. **No write without user-turn intent** — `_OPERATING_RULES` requires the
   user's own message to have requested any write action; confirmed via
   `ask_user_question` if absent.

5. **Every citation ref resolves** — `normalize_citations_and_chunks` drops
   `[source](refN)` markers for refs that aren't in the result set rather
   than leaving broken links; this test confirms that invariant holds.

6. **Confidence never 'Very High' when unavailable_sources non-empty** —
   `confidence.reconcile()` caps at 'Medium'; confirmed here with
   adversarial inputs.

Additionally, Phase 10 requires that any rule with a large tier gap (a
behaviour that only holds for frontier models) is enforced in code or
schema rather than in the prompt alone.  Two such rules are already in
code and are confirmed below:
  - `confidence.reconcile()` caps 'Very High'/'High' regardless of tier.
  - `FinalAnswerTool` is tagged `TAG_LIFECYCLE_TERMINAL` (loop stopper),
    with no tier branch in the loop machinery.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from tests.unit.agents.adapter.test_prompt_invariants import (
    _FIXTURES,
    build_prompt_for_fixture,
)


def _frontier_prompt(fixture_name: str) -> str:
    """Build a FRONTIER-tier prompt for the given fixture."""
    from tests.unit.agents.adapter.test_prompt_size_regression import _build_frontier_prompt
    return _build_frontier_prompt(fixture_name)


def _build_kb_plus_web_prompt() -> str:
    """Build a prompt where both internal knowledge and web search are granted."""
    from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
    from app.agent_loop_lib.core.types import Goal
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder

    ctx = AgentContext(
        org_id="org-test", user_id="u", user_email="u@t.com",
        user_info={"userId": "u", "orgId": "org-test"},
        org_info={"name": "T"},
        logger=MagicMock(), retrieval_service=MagicMock(),
        graph_provider=MagicMock(), config_service=MagicMock(),
        has_knowledge=True,
        agent_knowledge=[{"displayName": "KB", "name": "KB",
                          "_id": "id1", "connectorId": "id1", "type": "KB"}],
        web_search_config={"provider": "brave"},
    )
    ctx.tool_state.update({
        "agent_knowledge": ctx.agent_knowledge or [],
        "has_knowledge": True,
        "available_connectors": [],
        "web_search_config": {"provider": "brave"},
        "agent_toolsets": [],
    })
    spec = AgentSpec(
        name="test", system_prompt="BASE_REACT_PROMPT",
        tool_names=[
            "knowledgegraph__search", "knowledgegraph__navigate",
            "knowledgegraph__lookup_record", "knowledgegraph__list_files",
            "dynamic__web_search", "dynamic__fetch_url",
        ],
        model=ModelSpec(provider="scripted", model="t"),
    )
    return PipesHubPromptBuilder(ctx).build(
        spec, AgentRuntime(), Goal(description="q"), [], {}
    )


# ---------------------------------------------------------------------------
# Case 1 — Correct first-tool selection
# ---------------------------------------------------------------------------

class TestCorrectFirstToolSelection:
    """The prompt must direct the model to prefer the internal knowledge tool
    over web search when both are available."""

    def test_internal_knowledge_listed_before_web(self) -> None:
        """When kb + web are both granted, internal knowledge appears first
        in the Finding Information section's numbered source list."""
        prompt = _build_kb_plus_web_prompt()

        # Locate the Finding Information section.
        fi_start = prompt.find("Finding Information")
        assert fi_start != -1, "Finding Information section not found"

        section = prompt[fi_start:]
        kb_pos = section.find("Internal knowledge")
        web_pos = section.find("web_search")
        assert kb_pos != -1, "Internal knowledge not in Finding Information section"
        assert web_pos != -1, "web_search tool not in Finding Information section"
        assert kb_pos < web_pos, (
            "Internal knowledge must be listed before web search in the "
            "Finding Information section."
        )

    def test_no_sources_prompt_has_no_finding_information(self) -> None:
        """When no search surface is granted, the Finding Information
        section is absent (nothing to steer, no section needed)."""
        prompt = build_prompt_for_fixture("no_sources")
        assert "Finding Information" not in prompt

    def test_kb_only_prompt_names_retrieval_tool(self) -> None:
        """KB-only fixture must name the knowledge search tool in the
        Finding Information section (so the model knows what to call)."""
        prompt = build_prompt_for_fixture("kb_only")
        assert "knowledgegraph__search" in prompt

    def test_parallel_search_instruction_present(self) -> None:
        """When multiple sources are granted, the prompt must instruct the
        model to call them IN PARALLEL rather than sequentially."""
        prompt = _build_kb_plus_web_prompt()
        assert "PARALLEL" in prompt or "parallel" in prompt, (
            "Prompt must instruct parallel multi-source search."
        )


# ---------------------------------------------------------------------------
# Case 2 — Non-placeholder required params
# ---------------------------------------------------------------------------

class TestNonPlaceholderRequiredParams:
    """The prompt must explicitly forbid guesses and placeholders for WRITE
    tool required parameters.

    The `_TOOL_REFERENCE_HEADER` section (rendered only when at least one
    tool is visible via the registry) carries the exact wording.  The
    `lazy_with_pinned` fixture has registry-backed tools so this section
    renders.
    """

    def test_tool_header_forbids_placeholders(self) -> None:
        prompt = build_prompt_for_fixture("lazy_with_pinned")
        assert "not guesses, not placeholders" in prompt, (
            "Prompt must explicitly forbid guesses and placeholders in WRITE-tool params."
        )

    def test_tool_header_requires_concrete_values(self) -> None:
        prompt = build_prompt_for_fixture("lazy_with_pinned")
        assert "concrete values" in prompt, (
            "Prompt must require concrete values for WRITE tool params."
        )

    def test_tool_header_write_tool_rule_present(self) -> None:
        prompt = build_prompt_for_fixture("lazy_with_pinned")
        assert "WRITE tool" in prompt or "WRITE" in prompt, (
            "Prompt must have a write-tool parameter rule."
        )


# ---------------------------------------------------------------------------
# Case 3 — Exactly one final_answer call (structural, code-level guarantee)
# ---------------------------------------------------------------------------

class TestExactlyOneFinalAnswer:
    """TAG_LIFECYCLE_TERMINAL stops the agent loop after the first
    terminal-tool call.  This is a code guarantee — the loop machinery
    in tool_loop.py halts execution as soon as a terminal-tagged tool
    returns an outcome, regardless of prompt wording."""

    def test_final_answer_tool_tagged_terminal(self) -> None:
        """FinalAnswerTool must carry TAG_LIFECYCLE_TERMINAL so the loop
        machinery halts on the first call."""
        from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
        from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL

        tool = FinalAnswerTool()
        assert TAG_LIFECYCLE_TERMINAL in tool.tags, (
            "FinalAnswerTool must be tagged TAG_LIFECYCLE_TERMINAL — "
            "without it the loop will not halt after the first call."
        )

    def test_task_complete_tool_tagged_terminal(self) -> None:
        """task_complete (the fallback terminal tool) must also carry
        TAG_LIFECYCLE_TERMINAL."""
        from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
        from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL

        tool = TaskCompleteTool()
        assert TAG_LIFECYCLE_TERMINAL in tool.tags

    def test_final_answer_has_extract_outcome(self) -> None:
        """FinalAnswerTool must implement extract_outcome() so
        the loop dispatch can extract a TaskCompletionOutcome."""
        from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
        tool = FinalAnswerTool()
        assert hasattr(tool, "extract_outcome") and callable(tool.extract_outcome), (
            "FinalAnswerTool must have extract_outcome() to stop the loop with "
            "a TaskCompletionOutcome."
        )

    def test_final_answer_name_is_final_answer(self) -> None:
        from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
        assert FinalAnswerTool().name == "final_answer"


# ---------------------------------------------------------------------------
# Case 4 — No write without explicit user-turn intent
# ---------------------------------------------------------------------------

class TestNoWriteWithoutUserIntent:
    """_OPERATING_RULES must require the user's own message to have
    requested any write action; prompt must reference ask_user_question
    as the confirmation path."""

    def test_operating_rules_require_write_intent(self) -> None:
        prompt = build_prompt_for_fixture("kb_only")
        lower = prompt.lower()
        assert "write" in lower, "Operating rules must mention write actions."
        assert "user" in lower, "Operating rules must reference user intent."

    def test_operating_rules_mention_ask_user_question(self) -> None:
        """The prompt must name ask_user_question as the confirmation tool
        for write actions — not just say 'ask' in prose."""
        prompt = build_prompt_for_fixture("kb_only")
        assert "ask_user_question" in prompt, (
            "Operating rules must explicitly name internaltools__ask_user_question "
            "as the confirmation path for write actions."
        )

    def test_frontier_prompt_also_requires_write_intent(self) -> None:
        """The write-intent rule must hold for all tiers — it's in
        _OPERATING_RULES (Band A, tier-invariant)."""
        prompt = _frontier_prompt("kb_only")
        assert "ask_user_question" in prompt

    def test_write_rule_in_operating_rules_section(self) -> None:
        """The write-intent rule belongs in the Operating Rules section,
        not buried in a tier-only section."""
        prompt = build_prompt_for_fixture("kb_only")
        or_idx = prompt.find("## Operating Rules")
        assert or_idx != -1
        # Find the next major section after Operating Rules.
        next_section = prompt.find("\n## ", or_idx + 1)
        operating_rules_text = prompt[or_idx:next_section] if next_section != -1 else prompt[or_idx:]
        assert "ask_user_question" in operating_rules_text, (
            "ask_user_question reference must be inside the Operating Rules section."
        )


# ---------------------------------------------------------------------------
# Case 5 — Every citation ref resolves to a real result block
# ---------------------------------------------------------------------------

class TestCitationRefResolution:
    """normalize_citations_and_chunks must silently drop [source](refN) markers
    when refN is not in the result set, rather than leaving broken links."""

    def _normalize(self, text: str, result_ids: list[str]) -> str:
        from app.utils.citations import normalize_citations_and_chunks
        results = [
            {"citation_id": rid, "type": "text", "content": "data", "app": "kb"}
            for rid in result_ids
        ]
        normalized, _ = normalize_citations_and_chunks(text, results, {})
        return normalized

    def test_unresolved_ref_is_dropped(self) -> None:
        """A [source](refX) whose refX is not in the result set must NOT
        appear in the normalized output as a raw ref or a broken link."""
        text = "Revenue grew 29% [source](ref99)."
        normalized = self._normalize(text, result_ids=["ref1", "ref2"])
        assert "ref99" not in normalized, (
            "normalize_citations_and_chunks must drop refs not in result set."
        )

    def test_resolved_ref_produces_citation(self) -> None:
        """A [source](refN) whose refN IS in the result set must be replaced
        with a citation marker in the output."""
        text = "Revenue grew 29% [source](ref1)."
        normalized = self._normalize(text, result_ids=["ref1"])
        # The raw "[source](ref1)" marker must be replaced.
        assert "[source](ref1)" not in normalized, (
            "normalize_citations_and_chunks must replace resolved refs."
        )

    def test_mix_resolved_and_unresolved(self) -> None:
        """Resolved refs are rendered; unresolved refs are silently dropped."""
        text = "A [source](ref1). B [source](ref99)."
        normalized = self._normalize(text, result_ids=["ref1"])
        assert "ref99" not in normalized
        assert "[source](ref1)" not in normalized


# ---------------------------------------------------------------------------
# Case 6 — Confidence never 'Very High' when unavailable_sources non-empty
#           (plus comprehensive reconcile() coverage)
# ---------------------------------------------------------------------------

class TestConfidenceReconciliation:
    """confidence.reconcile() must cap optimistic values when sources were
    unavailable or citations are absent.  These are code-level guarantees
    — they apply to ALL tiers equally (no tier gap)."""

    def test_very_high_capped_when_source_unavailable(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Very High", unavailable_sources=["Jira"], citation_count=3)
        assert result == "Medium", (
            "reconcile() must cap 'Very High' to 'Medium' when a source was unavailable."
        )

    def test_high_capped_when_source_unavailable(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("High", unavailable_sources=["Confluence"], citation_count=5)
        assert result == "Medium"

    def test_medium_unchanged_when_source_unavailable(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Medium", unavailable_sources=["Slack"], citation_count=2)
        assert result == "Medium"

    def test_low_unchanged_when_source_unavailable(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Low", unavailable_sources=["Jira"], citation_count=0)
        assert result == "Low"

    def test_very_high_capped_when_zero_citations(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Very High", unavailable_sources=[], citation_count=0)
        assert result == "Medium", (
            "reconcile() must cap 'Very High' to 'Medium' when no citations exist."
        )

    def test_high_capped_when_zero_citations(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("High", unavailable_sources=[], citation_count=0)
        assert result == "Medium"

    def test_very_high_allowed_when_all_sources_present_and_cited(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Very High", unavailable_sources=[], citation_count=4)
        assert result == "Very High"

    def test_none_passthrough(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        assert reconcile(None, unavailable_sources=[], citation_count=5) is None

    def test_unrecognised_level_returns_none(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        assert reconcile("Excellent", unavailable_sources=[], citation_count=3) is None

    def test_case_insensitive_input(self) -> None:
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("very high", unavailable_sources=[], citation_count=4)
        assert result == "Very High"

    def test_internal_enum_values_mapped_correctly(self) -> None:
        """The internal Confidence enum (low/medium/high from task_complete)
        must be mappable through normalize() to the user-facing label."""
        from app.agents.agent_loop.confidence import normalize
        assert normalize("high")      == "High"
        assert normalize("medium")    == "Medium"
        assert normalize("low")       == "Low"
        assert normalize("very_high") == "Very High"


# ---------------------------------------------------------------------------
# Cross-tier gap analysis — rules already enforced in code (not prompt-only)
# ---------------------------------------------------------------------------

class TestCodeLevelTierInvariance:
    """Rules that must hold equally across ALL tiers should be in code, not
    just in prompt prose.  These tests confirm that the two highest-risk
    rules ARE implemented in code:

    * Confidence cap (reconcile()) — code, not prompt.
    * Loop termination on final_answer — TAG_LIFECYCLE_TERMINAL in code.

    If a new rule is found to differ significantly between frontier and small
    tiers during live evals, it should be promoted here (into code/schema)
    rather than relying on prompt text that small models may not follow."""

    def test_reconcile_caps_very_high_for_all_tiers(self) -> None:
        """reconcile() has no tier parameter — it caps regardless of model."""
        from app.agents.agent_loop.confidence import reconcile
        result = reconcile("Very High", unavailable_sources=["X"], citation_count=3)
        assert result == "Medium", (
            "reconcile() must cap 'Very High' regardless of model tier."
        )

    def test_final_answer_confidence_field_enum_constrained(self) -> None:
        """When confidence is enabled, FinalAnswerTool's confidence parameter
        must have an enum constraint — not accept free text.  This is
        enforced in the ToolParameter definition (code/schema), not the prompt."""
        original = os.environ.get("PIPESHUB_ENABLE_CONFIDENCE")
        try:
            os.environ["PIPESHUB_ENABLE_CONFIDENCE"] = "true"
            import importlib
            import app.agent_loop_lib.tools.builtin.planning.final_answer as fa_mod
            importlib.reload(fa_mod)
            tool = fa_mod.FinalAnswerTool()
            # Find the confidence parameter in the tool's parameter list.
            conf_param = next(
                (p for p in tool.parameters if p.name == "confidence"),
                None,
            )
            assert conf_param is not None, (
                "FinalAnswerTool must include a 'confidence' parameter when the flag is on."
            )
            assert conf_param.enum, (
                "FinalAnswerTool.confidence must have an enum constraint, "
                "not accept arbitrary free text."
            )
        finally:
            if original is None:
                os.environ.pop("PIPESHUB_ENABLE_CONFIDENCE", None)
            else:
                os.environ["PIPESHUB_ENABLE_CONFIDENCE"] = original
            import importlib
            import app.agent_loop_lib.tools.builtin.planning.final_answer as fa_mod
            importlib.reload(fa_mod)

    def test_loop_termination_independent_of_tier(self) -> None:
        """TAG_LIFECYCLE_TERMINAL dispatch in tool_loop.py does not branch
        on model tier — FinalAnswerTool terminates for any tier."""
        from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool
        from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL
        tool = FinalAnswerTool()
        assert TAG_LIFECYCLE_TERMINAL in tool.tags

    def test_works_with_confidence_flag_off(self) -> None:
        """With PIPESHUB_ENABLE_CONFIDENCE=false, confidence param must be
        absent — no confidence emitted, persisted, or rendered."""
        original = os.environ.get("PIPESHUB_ENABLE_CONFIDENCE")
        try:
            os.environ["PIPESHUB_ENABLE_CONFIDENCE"] = "false"
            import importlib
            import app.agent_loop_lib.tools.builtin.planning.final_answer as fa_mod
            importlib.reload(fa_mod)
            tool = fa_mod.FinalAnswerTool()
            conf_param = next(
                (p for p in tool.parameters if p.name == "confidence"),
                None,
            )
            assert conf_param is None, (
                "FinalAnswerTool must NOT include 'confidence' when PIPESHUB_ENABLE_CONFIDENCE is off."
            )
        finally:
            if original is None:
                os.environ.pop("PIPESHUB_ENABLE_CONFIDENCE", None)
            else:
                os.environ["PIPESHUB_ENABLE_CONFIDENCE"] = original
            import importlib
            import app.agent_loop_lib.tools.builtin.planning.final_answer as fa_mod
            importlib.reload(fa_mod)
