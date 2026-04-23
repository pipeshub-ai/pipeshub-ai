"""
Tests for app.modules.agents.deep.orchestrator_critic.

Covers:
  - _parse_critic_response: clean / fenced / trailing-garbage / unknown-decision /
    invalid-input handling.
  - critic_node: APPROVE and REVISE happy paths, fail-open behaviour on LLM
    failure, fail-open on parse failure, critic_done is always set.
  - route_after_critic: error short-circuit, approved+tasks → dispatch,
    approved+can_answer_directly → respond, approved+no-tasks → respond,
    not-approved → orchestrator (revise).
  - inject_critic_feedback_into_messages: no-op when empty, full injection
    structure (AIMessage with prior plan + HumanMessage with feedback +
    issue list when present), no-mutation guarantee.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.modules.agents.deep.orchestrator_critic import (
    CriticVerdict,
    _parse_critic_response,
    critic_node,
    inject_critic_feedback_into_messages,
    route_after_critic,
)


def _log() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


# ============================================================================
# 1. _parse_critic_response
# ============================================================================

class TestParseCriticResponse:
    def test_clean_approve(self):
        raw = json.dumps({
            "decision": "approve",
            "confidence": "High",
            "issues": [],
            "feedback_for_orchestrator": "",
        })
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.decision == "approve"
        assert verdict.issues == []
        assert verdict.confidence == "High"

    def test_clean_revise_with_issues(self):
        raw = json.dumps({
            "decision": "revise",
            "confidence": "Medium",
            "issues": [
                {"severity": "major", "rule": "I1", "description": "bad", "fix": "fix it"}
            ],
            "feedback_for_orchestrator": "Add a retrieval task.",
        })
        verdict = _parse_critic_response(raw, _log())
        assert verdict.decision == "revise"
        assert len(verdict.issues) == 1
        assert verdict.feedback_for_orchestrator == "Add a retrieval task."

    def test_markdown_fenced(self):
        raw = "```json\n" + json.dumps({"decision": "approve"}) + "\n```"
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.decision == "approve"

    def test_trailing_garbage_dropped(self):
        raw = json.dumps({"decision": "approve"}) + "\nfollow-up text"
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.decision == "approve"

    def test_unknown_decision_with_issues_becomes_revise(self):
        raw = json.dumps({
            "decision": "maybe",
            "issues": [{"severity": "major", "description": "x", "fix": "y"}],
        })
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.decision == "revise"

    def test_unknown_decision_without_issues_becomes_approve(self):
        raw = json.dumps({"decision": "weird", "issues": []})
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.decision == "approve"

    def test_issues_not_list_coerced_to_empty(self):
        raw = json.dumps({"decision": "approve", "issues": "not a list"})
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.issues == []

    def test_no_json_returns_none(self):
        verdict = _parse_critic_response("just plain text, no json", _log())
        assert verdict is None

    def test_empty_string_returns_none(self):
        assert _parse_critic_response("", _log()) is None

    def test_default_confidence_when_missing(self):
        raw = json.dumps({"decision": "approve"})
        verdict = _parse_critic_response(raw, _log())
        assert verdict is not None
        assert verdict.confidence == "High"  # default


# ============================================================================
# 2. critic_node
# ============================================================================

class TestCriticNode:

    def _state(self, **overrides):
        state = {
            "logger": _log(),
            "llm": AsyncMock(),
            "query": "find recent bugs",
            "task_plan": {
                "can_answer_directly": False,
                "reasoning": "search jira",
                "tasks": [{"task_id": "t1", "domains": ["jira"]}],
            },
            "_critic_available_domains": ["jira", "retrieval"],
            "has_knowledge": False,
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_approve_path_sets_state(self):
        verdict_json = json.dumps({"decision": "approve", "confidence": "High",
                                   "issues": [], "feedback_for_orchestrator": ""})
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=verdict_json))
        state = self._state(llm=llm)

        result = await critic_node(state, {"configurable": {}}, MagicMock())

        assert result["critic_approved"] is True
        assert result["critic_feedback"] == ""
        assert result["critic_issues"] is None
        assert result["critic_done"] is True

    @pytest.mark.asyncio
    async def test_revise_path_stores_feedback(self):
        verdict_json = json.dumps({
            "decision": "revise",
            "confidence": "Medium",
            "issues": [{"severity": "major", "rule": "I1",
                        "description": "missing date filter", "fix": "add filter"}],
            "feedback_for_orchestrator": "Add a date filter to t1.",
        })
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=verdict_json))
        state = self._state(llm=llm)

        result = await critic_node(state, {"configurable": {}}, MagicMock())

        assert result["critic_approved"] is False
        assert result["critic_feedback"] == "Add a date filter to t1."
        assert result["critic_issues"] is not None
        assert len(result["critic_issues"]) == 1
        assert result["critic_done"] is True

    @pytest.mark.asyncio
    async def test_llm_failure_fails_open_to_approve(self):
        """Critic bugs / LLM errors must NOT block execution — fail-open to approve."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        state = self._state(llm=llm)

        result = await critic_node(state, {"configurable": {}}, MagicMock())

        assert result["critic_approved"] is True
        assert result["critic_feedback"] == ""
        assert result["critic_done"] is True

    @pytest.mark.asyncio
    async def test_parse_failure_fails_open_to_approve(self):
        """If the verdict JSON is unparseable, default to approve (fail-open)."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="garbage"))
        state = self._state(llm=llm)

        result = await critic_node(state, {"configurable": {}}, MagicMock())

        assert result["critic_approved"] is True
        assert result["critic_done"] is True

    @pytest.mark.asyncio
    async def test_messages_sent_have_system_then_human(self):
        """SystemMessage must come first (identity/rubric), HumanMessage second
        (per-request evidence)."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(
            return_value=MagicMock(content=json.dumps({"decision": "approve"}))
        )
        state = self._state(llm=llm)

        await critic_node(state, {"configurable": {}}, MagicMock())

        sent = llm.ainvoke.call_args[0][0]
        assert isinstance(sent[0], SystemMessage)
        assert isinstance(sent[1], HumanMessage)
        # Human evidence must include the user query AND the plan's task_id
        assert "find recent bugs" in sent[1].content
        assert "t1" in sent[1].content

    @pytest.mark.asyncio
    async def test_critic_done_set_even_on_failure(self):
        """critic_done must be True after any code path so the routing function
        can bypass the critic on the next orchestrator pass."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=Exception("boom"))
        state = self._state(llm=llm)

        result = await critic_node(state, {"configurable": {}}, MagicMock())

        assert result["critic_done"] is True


# ============================================================================
# 3. route_after_critic
# ============================================================================

class TestRouteAfterCritic:
    def test_error_short_circuits_to_respond(self):
        state = {"error": {"message": "x"}, "critic_approved": True}
        assert route_after_critic(state) == "respond"

    def test_approved_with_tasks_dispatches(self):
        state = {
            "error": None,
            "critic_approved": True,
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert route_after_critic(state) == "dispatch"

    def test_approved_with_direct_answer_responds(self):
        state = {
            "error": None,
            "critic_approved": True,
            "execution_plan": {"can_answer_directly": True},
            "sub_agent_tasks": [],
        }
        assert route_after_critic(state) == "respond"

    def test_approved_no_tasks_responds(self):
        state = {
            "error": None,
            "critic_approved": True,
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [],
        }
        assert route_after_critic(state) == "respond"

    def test_not_approved_routes_to_orchestrator_for_revision(self):
        state = {
            "error": None,
            "critic_approved": False,
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert route_after_critic(state) == "orchestrator"

    def test_missing_execution_plan_with_tasks_dispatches(self):
        state = {
            "error": None,
            "critic_approved": True,
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert route_after_critic(state) == "dispatch"


# ============================================================================
# 4. inject_critic_feedback_into_messages
# ============================================================================

class TestInjectCriticFeedback:
    def test_no_feedback_returns_messages_unchanged(self):
        original = [SystemMessage(content="hi")]
        out = inject_critic_feedback_into_messages(original, {"critic_feedback": ""})
        assert out is original or out == original

    def test_with_feedback_adds_two_messages(self):
        original = [SystemMessage(content="hi")]
        state = {
            "critic_feedback": "Use jira instead of slack.",
            "task_plan": {"tasks": [{"task_id": "t1", "domains": ["slack"]}]},
            "critic_issues": None,
        }
        out = inject_critic_feedback_into_messages(original, state)
        # Original + AIMessage(prior plan) + HumanMessage(feedback)
        assert len(out) == len(original) + 2
        assert isinstance(out[-2], AIMessage)
        assert isinstance(out[-1], HumanMessage)
        # Prior plan JSON appears in the AIMessage
        assert "t1" in out[-2].content
        # Feedback appears in the HumanMessage
        assert "Use jira instead of slack" in out[-1].content

    def test_with_issues_renders_issue_list(self):
        original: list = []
        state = {
            "critic_feedback": "fix it",
            "task_plan": {"tasks": []},
            "critic_issues": [
                {"severity": "major", "rule": "I1",
                 "description": "missing date filter", "fix": "add a date filter"},
                {"severity": "minor", "rule": "Q5",
                 "description": "task description vague", "fix": "be more specific"},
            ],
        }
        out = inject_critic_feedback_into_messages(original, state)
        body = out[-1].content
        assert "missing date filter" in body
        assert "add a date filter" in body
        assert "task description vague" in body
        # Severity tag rendered (uppercased per implementation)
        assert "MAJOR" in body
        assert "MINOR" in body
        # Rule tags rendered
        assert "I1" in body and "Q5" in body

    def test_does_not_mutate_input_messages(self):
        original = [SystemMessage(content="hi")]
        state = {
            "critic_feedback": "x",
            "task_plan": {"tasks": []},
            "critic_issues": None,
        }
        inject_critic_feedback_into_messages(original, state)
        assert len(original) == 1  # untouched

    def test_no_issues_omits_issue_section(self):
        original: list = []
        state = {
            "critic_feedback": "Generic feedback only.",
            "task_plan": {"tasks": []},
            "critic_issues": None,
        }
        out = inject_critic_feedback_into_messages(original, state)
        body = out[-1].content
        # Issue header should not appear when there are no issues.
        assert "Issues found" not in body
        assert "Generic feedback only" in body


# ============================================================================
# 5. CriticVerdict dataclass smoke test
# ============================================================================

class TestCriticVerdictDefaults:
    def test_defaults(self):
        v = CriticVerdict(decision="approve")
        assert v.issues == []
        assert v.feedback_for_orchestrator == ""
        assert v.confidence == "High"
