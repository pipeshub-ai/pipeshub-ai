"""
Tests for app.modules.agents.deep.orchestrator_reflection.

Covers:
  - _parse_plan: JSON parsing, markdown stripping, raw_decode trailing-garbage
    behavior, default-key population, invalid input handling.
  - _validate_plan: every validation rule (top-level types, missing fields,
    bad domains, forward / self / unknown depends_on, duplicate ids,
    complex/batch_strategy, multi_step/sub_steps, cycle detection).
  - _find_cycle: cycle detection on directly-constructed dep graphs.
  - run_orchestrator_with_reflection: success-on-first-try, parse-then-success
    retry, validation-then-success retry, retry exhaustion (raises
    OrchestratorReflectionError), and config forwarding to llm.ainvoke.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.agents.deep.orchestrator_reflection import (
    MAX_REFLECTION_RETRIES,
    OrchestratorReflectionError,
    ParseResult,
    ValidationResult,
    _append_parse_reflection,
    _append_plan_reflection,
    _find_cycle,
    _parse_plan,
    _validate_plan,
    run_orchestrator_with_reflection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


def _valid_plan() -> dict:
    return {
        "can_answer_directly": False,
        "reasoning": "Need to query Jira",
        "tasks": [
            {
                "task_id": "t1",
                "description": "Search Jira issues",
                "domains": ["jira"],
                "depends_on": [],
            }
        ],
    }


# ============================================================================
# 1. _parse_plan
# ============================================================================

class TestParsePlan:
    def test_clean_json(self):
        raw = json.dumps(_valid_plan())
        result = _parse_plan(raw, _log())
        assert result.ok is True
        assert result.plan["tasks"][0]["task_id"] == "t1"

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(_valid_plan()) + "\n```"
        result = _parse_plan(raw, _log())
        assert result.ok is True
        assert result.plan["can_answer_directly"] is False

    def test_markdown_fenced_no_lang_tag(self):
        raw = "```\n" + json.dumps(_valid_plan()) + "\n```"
        result = _parse_plan(raw, _log())
        assert result.ok is True

    def test_trailing_garbage_dropped(self):
        """raw_decode silently consumes the first JSON object and ignores trailing tool calls / status."""
        raw = json.dumps(_valid_plan()) + '\n{"tool": "search", "params": {}}\n"running"'
        result = _parse_plan(raw, _log())
        assert result.ok is True
        assert "tasks" in result.plan

    def test_json_embedded_after_text(self):
        raw = "Here is the plan:\n" + json.dumps(_valid_plan())
        result = _parse_plan(raw, _log())
        assert result.ok is True

    def test_non_dict_top_level_array_fails(self):
        result = _parse_plan("[1, 2, 3]", _log())
        assert result.ok is False
        assert "No valid JSON object" in result.error

    def test_completely_malformed(self):
        result = _parse_plan("not even close to json", _log())
        assert result.ok is False

    def test_empty_string(self):
        result = _parse_plan("", _log())
        assert result.ok is False

    def test_missing_keys_get_defaults(self):
        """A bare {} is parseable; defaults are filled in by setdefault()."""
        result = _parse_plan("{}", _log())
        assert result.ok is True
        assert result.plan["can_answer_directly"] is False
        assert result.plan["tasks"] == []
        assert result.plan["reasoning"] == ""

    def test_existing_keys_preserved(self):
        raw = '{"can_answer_directly": true, "reasoning": "hi"}'
        result = _parse_plan(raw, _log())
        assert result.ok is True
        assert result.plan["can_answer_directly"] is True
        assert result.plan["reasoning"] == "hi"
        # tasks defaulted to []
        assert result.plan["tasks"] == []


# ============================================================================
# 2. _validate_plan
# ============================================================================

class TestValidatePlan:

    AVAILABLE = {"jira", "slack", "retrieval"}

    def test_valid_plan_passes(self):
        result = _validate_plan(_valid_plan(), self.AVAILABLE, _log())
        assert result.ok is True
        assert result.issues == []

    def test_can_answer_directly_must_be_bool(self):
        plan = _valid_plan()
        plan["can_answer_directly"] = "false"  # string, not bool
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("can_answer_directly" in i for i in result.issues)

    def test_tasks_must_be_list(self):
        plan = {"can_answer_directly": False, "tasks": {}, "reasoning": ""}
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        # tasks-not-a-list short-circuits — only one issue reported.
        assert any("'tasks' must be a list" in i for i in result.issues)

    def test_can_answer_false_with_empty_tasks(self):
        plan = {"can_answer_directly": False, "tasks": [], "reasoning": ""}
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("at least one task is required" in i for i in result.issues)

    def test_can_answer_true_with_empty_tasks_ok(self):
        plan = {"can_answer_directly": True, "tasks": [], "reasoning": ""}
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is True

    def test_non_dict_task(self):
        plan = _valid_plan()
        plan["tasks"] = ["not a dict"]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("expected a dict" in i for i in result.issues)

    def test_missing_task_id(self):
        plan = _valid_plan()
        plan["tasks"][0].pop("task_id")
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("task_id" in i for i in result.issues)

    def test_non_string_task_id(self):
        plan = _valid_plan()
        plan["tasks"][0]["task_id"] = 123
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("task_id" in i for i in result.issues)

    def test_missing_description(self):
        plan = _valid_plan()
        plan["tasks"][0]["description"] = ""
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("description" in i for i in result.issues)

    def test_missing_domains(self):
        plan = _valid_plan()
        plan["tasks"][0]["domains"] = []
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("'domains' must be a non-empty list" in i for i in result.issues)

    def test_unknown_domain(self):
        plan = _valid_plan()
        plan["tasks"][0]["domains"] = ["confluence"]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("'confluence' is not available" in i for i in result.issues)

    def test_depends_on_forward_reference(self):
        plan = _valid_plan()
        plan["tasks"] = [
            {"task_id": "t1", "description": "first", "domains": ["jira"], "depends_on": ["t2"]},
            {"task_id": "t2", "description": "second", "domains": ["jira"], "depends_on": []},
        ]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("forward reference" in i for i in result.issues)

    def test_depends_on_self_reference(self):
        plan = _valid_plan()
        plan["tasks"][0]["depends_on"] = ["t1"]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("cannot depend on itself" in i for i in result.issues)

    def test_depends_on_unknown_id(self):
        plan = _valid_plan()
        plan["tasks"][0]["depends_on"] = ["does_not_exist"]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("does_not_exist" in i for i in result.issues)

    def test_depends_on_must_be_list(self):
        plan = _valid_plan()
        plan["tasks"][0]["depends_on"] = "t1"
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("depends_on" in i and "list" in i for i in result.issues)

    def test_duplicate_task_ids(self):
        plan = _valid_plan()
        plan["tasks"] = [
            {"task_id": "t1", "description": "a", "domains": ["jira"], "depends_on": []},
            {"task_id": "t1", "description": "b", "domains": ["jira"], "depends_on": []},
        ]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("duplicate task_id" in i for i in result.issues)

    def test_complex_requires_batch_strategy(self):
        plan = _valid_plan()
        plan["tasks"][0]["complexity"] = "complex"
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("batch_strategy" in i for i in result.issues)

    def test_complex_with_incomplete_batch_strategy(self):
        plan = _valid_plan()
        plan["tasks"][0]["complexity"] = "complex"
        plan["tasks"][0]["batch_strategy"] = {"page_size": 50}  # missing max_pages, scope_query
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("max_pages" in i for i in result.issues)
        assert any("scope_query" in i for i in result.issues)

    def test_complex_with_full_batch_strategy_ok(self):
        plan = _valid_plan()
        plan["tasks"][0]["complexity"] = "complex"
        plan["tasks"][0]["batch_strategy"] = {
            "page_size": 50,
            "max_pages": 5,
            "scope_query": "bug",
        }
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is True

    def test_multi_step_requires_sub_steps(self):
        plan = _valid_plan()
        plan["tasks"][0]["multi_step"] = True
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("sub_steps" in i for i in result.issues)

    def test_multi_step_sub_steps_must_be_strings(self):
        plan = _valid_plan()
        plan["tasks"][0]["multi_step"] = True
        plan["tasks"][0]["sub_steps"] = ["ok", 123]
        result = _validate_plan(plan, self.AVAILABLE, _log())
        assert result.ok is False
        assert any("sub_steps must be strings" in i for i in result.issues)

    def test_circular_dependency_detected(self):
        """Cycles are caught only when other validation passes — so use a clean
        dep cycle that bypasses the forward-reference check by being mutual
        between tasks that already exist."""
        plan = {
            "can_answer_directly": False,
            "reasoning": "",
            "tasks": [
                {"task_id": "a", "description": "x", "domains": ["jira"], "depends_on": []},
                {"task_id": "b", "description": "y", "domains": ["jira"], "depends_on": ["a"]},
            ],
        }
        # Clean validation first
        first = _validate_plan(plan, self.AVAILABLE, _log())
        assert first.ok is True
        # Now manually inject a cycle that the forward-ref check would also catch,
        # so we go through the path differently — test _find_cycle directly instead.
        cycle = _find_cycle({"a": ["b"], "b": ["a"]})
        assert cycle is not None
        assert "a" in cycle and "b" in cycle


# ============================================================================
# 3. _find_cycle
# ============================================================================

class TestFindCycle:
    def test_no_cycle_returns_none(self):
        assert _find_cycle({"a": ["b"], "b": ["c"], "c": []}) is None

    def test_simple_two_node_cycle(self):
        cycle = _find_cycle({"a": ["b"], "b": ["a"]})
        assert cycle is not None
        assert "a" in cycle and "b" in cycle

    def test_self_cycle(self):
        cycle = _find_cycle({"a": ["a"]})
        assert cycle is not None
        assert "a" in cycle

    def test_three_node_cycle(self):
        cycle = _find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})
        assert cycle is not None

    def test_disconnected_graph_no_cycle(self):
        assert _find_cycle({"a": [], "b": [], "c": []}) is None

    def test_unknown_dep_ignored(self):
        # A dep that points outside the known set must not crash or be
        # mis-reported as a cycle.
        assert _find_cycle({"a": ["unknown"]}) is None


# ============================================================================
# 4. Reflection prompt builders
# ============================================================================

class TestReflectionPromptBuilders:
    def test_append_parse_reflection_extends_messages(self):
        original = [MagicMock()]
        out = _append_parse_reflection(original, raw="bad", error="boom")
        # Must not mutate the original
        assert len(out) == len(original) + 2
        # Last message contains corrective instructions
        last = out[-1].content
        assert "boom" in last or "Parse error" in last
        assert "Output ONLY" in last or "Output only" in last

    def test_append_plan_reflection_includes_real_domains(self):
        """Fix 1 from the docstring: available_domains must surface in the prompt."""
        original = [MagicMock()]
        out = _append_plan_reflection(
            original,
            plan={"tasks": []},
            issues=["bad domain 'foo'"],
            available_domains={"jira", "slack"},
        )
        last = out[-1].content
        # Must show the real domain list (sorted) — not a placeholder
        assert "jira" in last and "slack" in last
        assert "bad domain 'foo'" in last

    def test_append_plan_reflection_lists_every_issue(self):
        out = _append_plan_reflection(
            [],
            plan={},
            issues=["issue A", "issue B", "issue C"],
            available_domains=set(),
        )
        last = out[-1].content
        for token in ("issue A", "issue B", "issue C"):
            assert token in last


# ============================================================================
# 5. run_orchestrator_with_reflection — end-to-end
# ============================================================================

class TestRunOrchestratorWithReflection:

    @pytest.mark.asyncio
    async def test_succeeds_first_try(self):
        plan_json = json.dumps(_valid_plan())
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=plan_json))
        plan = await run_orchestrator_with_reflection(
            llm=llm,
            messages=[],
            available_domains={"jira"},
            log=_log(),
        )
        assert plan["tasks"][0]["task_id"] == "t1"
        assert llm.ainvoke.await_count == 1

    @pytest.mark.asyncio
    async def test_parse_failure_then_success(self):
        good_json = json.dumps(_valid_plan())
        responses = [
            MagicMock(content="not json at all"),  # parse fails
            MagicMock(content=good_json),          # then succeeds
        ]
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=responses)
        plan = await run_orchestrator_with_reflection(
            llm=llm,
            messages=[],
            available_domains={"jira"},
            log=_log(),
        )
        assert plan["tasks"][0]["task_id"] == "t1"
        assert llm.ainvoke.await_count == 2

    @pytest.mark.asyncio
    async def test_validation_failure_then_success(self):
        bad_plan = _valid_plan()
        bad_plan["tasks"][0]["domains"] = ["unknown_domain"]
        responses = [
            MagicMock(content=json.dumps(bad_plan)),
            MagicMock(content=json.dumps(_valid_plan())),
        ]
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=responses)
        plan = await run_orchestrator_with_reflection(
            llm=llm,
            messages=[],
            available_domains={"jira"},
            log=_log(),
        )
        assert plan["tasks"][0]["domains"] == ["jira"]
        assert llm.ainvoke.await_count == 2

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
        with pytest.raises(OrchestratorReflectionError):
            await run_orchestrator_with_reflection(
                llm=llm,
                messages=[],
                available_domains={"jira"},
                log=_log(),
            )
        # Initial attempt + MAX_REFLECTION_RETRIES retries
        assert llm.ainvoke.await_count == MAX_REFLECTION_RETRIES + 1

    @pytest.mark.asyncio
    async def test_validation_retries_exhausted_raises(self):
        bad_plan = _valid_plan()
        bad_plan["tasks"][0]["domains"] = ["unknown"]
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(bad_plan)))
        with pytest.raises(OrchestratorReflectionError) as excinfo:
            await run_orchestrator_with_reflection(
                llm=llm,
                messages=[],
                available_domains={"jira"},
                log=_log(),
            )
        # Error message must include the validator's complaint
        assert "unknown" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_config_forwarded_to_every_invoke(self):
        """Fix 2: config must be passed on retry calls too, not silently dropped."""
        bad_then_good = [
            MagicMock(content="not json"),
            MagicMock(content=json.dumps(_valid_plan())),
        ]
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=bad_then_good)

        sentinel_config = {"opik": "trace-id-xyz"}
        await run_orchestrator_with_reflection(
            llm=llm,
            messages=[],
            available_domains={"jira"},
            log=_log(),
            config=sentinel_config,
        )
        # Both calls must receive config=sentinel_config
        assert llm.ainvoke.await_count == 2
        for call in llm.ainvoke.await_args_list:
            assert call.kwargs.get("config") is sentinel_config

    @pytest.mark.asyncio
    async def test_no_config_means_no_config_kwarg(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(_valid_plan())))
        await run_orchestrator_with_reflection(
            llm=llm,
            messages=[],
            available_domains={"jira"},
            log=_log(),
            config=None,
        )
        # When config is None, the kwarg must not be forwarded.
        for call in llm.ainvoke.await_args_list:
            assert "config" not in call.kwargs


# ============================================================================
# 6. Dataclasses (smoke)
# ============================================================================

class TestDataclasses:
    def test_parse_result_defaults(self):
        r = ParseResult(ok=False)
        assert r.plan == {}
        assert r.raw == ""
        assert r.error == ""

    def test_validation_result_defaults(self):
        r = ValidationResult(ok=True)
        assert r.issues == []
