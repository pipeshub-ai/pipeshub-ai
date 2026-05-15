"""
Unit tests for app.modules.agents.deep.prompts

Verifies that all prompt templates are properly defined strings containing
expected format placeholders and structural content.
"""

import pytest

from app.modules.agents.deep.prompts import (
    BATCH_SUMMARIZATION_PROMPT,
    DOMAIN_CONSOLIDATION_PROMPT,
    EVALUATOR_PROMPT,
    MINI_ORCHESTRATOR_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
    SUB_AGENT_SYSTEM_PROMPT,
    SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS,
)


# ---------------------------------------------------------------------------
# ORCHESTRATOR_SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestOrchestratorSystemPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(ORCHESTRATOR_SYSTEM_PROMPT, str)
        assert len(ORCHESTRATOR_SYSTEM_PROMPT) > 100

    def test_contains_required_placeholders(self):
        for placeholder in [
            "{agent_instructions}",
            "{tool_domains}",
            "{knowledge_context}",
            "{tool_guidance}",
            "{capability_summary}",
            "{time_context}",
        ]:
            assert placeholder in ORCHESTRATOR_SYSTEM_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = ORCHESTRATOR_SYSTEM_PROMPT.format(
            agent_instructions="",
            tool_domains="## Domains\n- jira",
            knowledge_context="## KB\nInternal knowledge.",
            tool_guidance="",
            capability_summary="Can answer: yes",
            time_context="UTC 2026",
        )
        assert isinstance(result, str)
        assert "task orchestrator" in result

    def test_contains_can_answer_directly_key(self):
        assert "can_answer_directly" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_contains_response_format_json_section(self):
        assert "json" in ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "tasks" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_contains_decomposition_constraints(self):
        assert "One domain per task" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_multi_step_guidance_present(self):
        assert "multi_step" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "sub_steps" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_scoped_instructions_guidance_present(self):
        assert "scoped_instructions" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_empty_agent_instructions_still_formats(self):
        result = ORCHESTRATOR_SYSTEM_PROMPT.format(
            agent_instructions="",
            tool_domains="",
            knowledge_context="",
            tool_guidance="",
            capability_summary="",
            time_context="",
        )
        assert "task orchestrator" in result


# ---------------------------------------------------------------------------
# SUB_AGENT_SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSubAgentSystemPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(SUB_AGENT_SYSTEM_PROMPT, str)
        assert len(SUB_AGENT_SYSTEM_PROMPT) > 100

    def test_contains_required_placeholders(self):
        for placeholder in [
            "{agent_instructions}",
            "{task_description}",
            "{task_context}",
            "{tool_schemas}",
            "{tool_guidance}",
            "{time_context}",
            "{task_scope_block}",
        ]:
            assert placeholder in SUB_AGENT_SYSTEM_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = SUB_AGENT_SYSTEM_PROMPT.format(
            agent_instructions="",
            task_description="Fetch all open Jira tickets",
            task_scope_block="",
            task_context="Previous: none",
            tool_schemas="[search_jira]",
            tool_guidance="",
            time_context="",
        )
        assert isinstance(result, str)
        assert "focused task executor" in result

    def test_contains_parallelism_guidance(self):
        assert "PARALLEL" in SUB_AGENT_SYSTEM_PROMPT or "parallel" in SUB_AGENT_SYSTEM_PROMPT

    def test_contains_data_completeness_guidance(self):
        assert "ALL data" in SUB_AGENT_SYSTEM_PROMPT or "ALL fields" in SUB_AGENT_SYSTEM_PROMPT

    def test_contains_retrieval_connector_scoping(self):
        assert "connector_ids" in SUB_AGENT_SYSTEM_PROMPT
        assert "search_internal_knowledge" in SUB_AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# MINI_ORCHESTRATOR_PROMPT
# ---------------------------------------------------------------------------

class TestMiniOrchestratorPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(MINI_ORCHESTRATOR_PROMPT, str)
        assert len(MINI_ORCHESTRATOR_PROMPT) > 50

    def test_contains_required_placeholders(self):
        for placeholder in [
            "{agent_instructions}",
            "{task_description}",
            "{sub_steps}",
            "{tool_schemas}",
            "{task_context}",
            "{time_context}",
            "{tool_guidance}",
            "{task_scope_block}",
        ]:
            assert placeholder in MINI_ORCHESTRATOR_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = MINI_ORCHESTRATOR_PROMPT.format(
            agent_instructions="",
            task_description="Find and update tickets",
            task_scope_block="",
            sub_steps="1. Find\n2. Update",
            tool_schemas="[jira_search]",
            task_context="",
            time_context="",
            tool_guidance="",
        )
        assert isinstance(result, str)
        assert "multi-step" in result.lower() or "step" in result.lower()

    def test_references_sequential_execution(self):
        assert "sequentially" in MINI_ORCHESTRATOR_PROMPT or "step" in MINI_ORCHESTRATOR_PROMPT


# ---------------------------------------------------------------------------
# EVALUATOR_PROMPT
# ---------------------------------------------------------------------------

class TestEvaluatorPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(EVALUATOR_PROMPT, str)
        assert len(EVALUATOR_PROMPT) > 100

    def test_contains_required_placeholders(self):
        for placeholder in [
            "{agent_instructions}",
            "{query}",
            "{task_plan}",
            "{results_summary}",
        ]:
            assert placeholder in EVALUATOR_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = EVALUATOR_PROMPT.format(
            agent_instructions="",
            query="What are the open tickets?",
            task_plan='{"tasks": []}',
            results_summary="### t1 — SUCCESS",
        )
        assert isinstance(result, str)
        assert "decision" in result.lower()

    def test_contains_all_four_decisions(self):
        for decision in ["respond_success", "respond_error", "retry", "continue"]:
            assert decision in EVALUATOR_PROMPT

    def test_json_response_format_described(self):
        assert "json" in EVALUATOR_PROMPT.lower()
        assert "decision" in EVALUATOR_PROMPT

    def test_contains_confidence_field(self):
        assert "confidence" in EVALUATOR_PROMPT.lower()


# ---------------------------------------------------------------------------
# SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS
# ---------------------------------------------------------------------------

class TestSummaryReplaySystemInstructions:
    def test_is_nonempty_string(self):
        assert isinstance(SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS, str)
        assert len(SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS) > 20

    def test_contains_summarize_instruction(self):
        lower = SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS.lower()
        assert "summar" in lower

    def test_no_format_placeholders(self):
        # This prompt is used as-is (no .format() call), so it must not
        # contain unescaped curly braces that would break .format().
        import re
        # Find lone { or } that are not doubled
        single_braces = re.findall(r'(?<!\{)\{(?!\{)|(?<!\})\}(?!\})', SUMMARY_REPLAY_SYSTEM_INSTRUCTIONS)
        assert single_braces == [], f"Unexpected format placeholders: {single_braces}"


# ---------------------------------------------------------------------------
# BATCH_SUMMARIZATION_PROMPT
# ---------------------------------------------------------------------------

class TestBatchSummarizationPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(BATCH_SUMMARIZATION_PROMPT, str)
        assert len(BATCH_SUMMARIZATION_PROMPT) > 100

    def test_contains_required_placeholders(self):
        for placeholder in ["{data_type}", "{batch_number}", "{total_batches}", "{raw_data}"]:
            assert placeholder in BATCH_SUMMARIZATION_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = BATCH_SUMMARIZATION_PROMPT.format(
            data_type="emails",
            batch_number=1,
            total_batches=3,
            raw_data="[{subject: 'Hello', from: 'alice@example.com'}]",
        )
        assert isinstance(result, str)
        assert "emails" in result

    def test_contains_critical_rules(self):
        assert "CRITICAL" in BATCH_SUMMARIZATION_PROMPT

    def test_contains_link_requirement(self):
        assert "link" in BATCH_SUMMARIZATION_PROMPT.lower() or "url" in BATCH_SUMMARIZATION_PROMPT.lower()


# ---------------------------------------------------------------------------
# DOMAIN_CONSOLIDATION_PROMPT
# ---------------------------------------------------------------------------

class TestDomainConsolidationPrompt:
    def test_is_nonempty_string(self):
        assert isinstance(DOMAIN_CONSOLIDATION_PROMPT, str)
        assert len(DOMAIN_CONSOLIDATION_PROMPT) > 100

    def test_contains_required_placeholders(self):
        for placeholder in ["{domain}", "{task_description}", "{time_context}", "{batch_summaries}"]:
            assert placeholder in DOMAIN_CONSOLIDATION_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_format_with_all_placeholders(self):
        result = DOMAIN_CONSOLIDATION_PROMPT.format(
            domain="jira",
            task_description="Summarise all Jira tickets",
            time_context="UTC 2026",
            batch_summaries="### Batch 1\n- Ticket A",
        )
        assert isinstance(result, str)
        assert "jira" in result.lower()

    def test_contains_preserve_all_items_rule(self):
        assert "PRESERVE ALL" in DOMAIN_CONSOLIDATION_PROMPT or "every item" in DOMAIN_CONSOLIDATION_PROMPT.lower()

    def test_contains_overview_section_heading(self):
        assert "Overview" in DOMAIN_CONSOLIDATION_PROMPT

    def test_contains_highlights_section(self):
        assert "Highlight" in DOMAIN_CONSOLIDATION_PROMPT or "highlight" in DOMAIN_CONSOLIDATION_PROMPT
