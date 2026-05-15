"""
Additional coverage tests for app.modules.agents.deep.orchestrator

Targets helper functions:
- should_dispatch
- _normalize_tasks
- _build_knowledge_context
- _build_tool_guidance
- _build_agent_instructions
- _build_time_context
- _build_user_context
- _build_iteration_context
- _create_retrieval_task
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.deep.orchestrator import (
    _build_agent_instructions,
    _build_iteration_context,
    _build_knowledge_context,
    _build_time_context,
    _build_tool_guidance,
    _build_user_context,
    _create_retrieval_task,
    _normalize_tasks,
    should_dispatch,
)

log = logging.getLogger("test_orch_helpers")
log.setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# should_dispatch
# ---------------------------------------------------------------------------

class TestShouldDispatch:
    def test_error_in_state_returns_respond(self):
        state = {"error": {"message": "something failed"}}
        assert should_dispatch(state) == "respond"

    def test_can_answer_directly_returns_respond(self):
        state = {"execution_plan": {"can_answer_directly": True}}
        assert should_dispatch(state) == "respond"

    def test_no_tasks_returns_respond(self):
        state = {"execution_plan": {"can_answer_directly": False}, "sub_agent_tasks": []}
        assert should_dispatch(state) == "respond"

    def test_tasks_present_returns_dispatch(self):
        state = {
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert should_dispatch(state) == "dispatch"

    def test_empty_state_returns_respond(self):
        assert should_dispatch({}) == "respond"


# ---------------------------------------------------------------------------
# _normalize_tasks
# ---------------------------------------------------------------------------

class TestNormalizeTasks:
    def test_single_domain_unchanged(self):
        tasks = [{"task_id": "t1", "description": "search", "domains": ["jira"]}]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 1
        assert result[0]["domains"] == ["jira"]

    def test_empty_domains_unchanged(self):
        tasks = [{"task_id": "t1", "description": "do stuff", "domains": []}]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 1

    def test_multi_domain_split(self):
        tasks = [{"task_id": "t1", "description": "search both", "domains": ["jira", "confluence"]}]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 2
        assert result[0]["domains"] == ["jira"]
        assert result[1]["domains"] == ["confluence"]
        assert "jira" in result[0]["task_id"]
        assert "confluence" in result[1]["task_id"]

    def test_multi_domain_preserves_dependencies(self):
        tasks = [
            {"task_id": "t1", "description": "search", "domains": ["jira", "confluence"], "depends_on": ["t0"]},
        ]
        result = _normalize_tasks(tasks, log)
        for task in result:
            assert "t0" in task["depends_on"]

    def test_later_task_dependencies_updated(self):
        tasks = [
            {"task_id": "t1", "description": "split me", "domains": ["jira", "confluence"]},
            {"task_id": "t2", "description": "depends on t1", "domains": ["slack"], "depends_on": ["t1"]},
        ]
        result = _normalize_tasks(tasks, log)
        # t2 should now depend on both split tasks
        t2 = [t for t in result if t["task_id"] == "t2"][0]
        assert "t1_jira" in t2["depends_on"]
        assert "t1_confluence" in t2["depends_on"]
        assert "t1" not in t2["depends_on"]

    def test_preserves_scoped_instructions(self):
        tasks = [{
            "task_id": "t1",
            "description": "multi",
            "domains": ["a", "b"],
            "scoped_instructions": "Be concise.",
        }]
        result = _normalize_tasks(tasks, log)
        for t in result:
            assert t.get("scoped_instructions") == "Be concise."


# ---------------------------------------------------------------------------
# _build_tool_guidance
# ---------------------------------------------------------------------------

class TestBuildToolGuidance:
    def test_no_tools(self):
        state = {"tools": []}
        assert _build_tool_guidance(state) == ""

    def test_none_tools(self):
        state = {"tools": None}
        assert _build_tool_guidance(state) == ""

    def test_single_domain_tool(self):
        state = {"tools": ["jira.search_issues", "jira.get_issue"]}
        result = _build_tool_guidance(state)
        assert "jira" in result
        assert "search_issues" in result
        assert "get_issue" in result

    def test_multiple_domains(self):
        state = {"tools": ["jira.search", "confluence.get_page", "slack.send_message"]}
        result = _build_tool_guidance(state)
        assert "jira" in result
        assert "confluence" in result
        assert "slack" in result

    def test_tool_without_dot_goes_to_other(self):
        state = {"tools": ["plain_tool"]}
        result = _build_tool_guidance(state)
        assert "other" in result
        assert "plain_tool" in result

    def test_non_string_tools_skipped(self):
        state = {"tools": [None, 123, "jira.search"]}
        result = _build_tool_guidance(state)
        assert "jira" in result

    def test_truncates_at_max_display(self):
        tools = [f"domain.tool_{i}" for i in range(15)]
        state = {"tools": tools}
        result = _build_tool_guidance(state)
        assert "more" in result


# ---------------------------------------------------------------------------
# _build_agent_instructions
# ---------------------------------------------------------------------------

class TestBuildAgentInstructions:
    def test_no_instructions_or_prompt(self):
        state = {}
        assert _build_agent_instructions(state) == ""

    def test_default_system_prompt_excluded(self):
        state = {"system_prompt": "You are an enterprise questions answering expert"}
        assert _build_agent_instructions(state) == ""

    def test_custom_system_prompt_included(self):
        state = {"system_prompt": "You are a legal compliance assistant."}
        result = _build_agent_instructions(state)
        assert "## Agent Role" in result
        assert "legal compliance assistant" in result

    def test_instructions_included(self):
        state = {"instructions": "Always provide references."}
        result = _build_agent_instructions(state)
        assert "## Agent Instructions" in result
        assert "Always provide references." in result

    def test_both_prompt_and_instructions(self):
        state = {
            "system_prompt": "You are a security expert.",
            "instructions": "Focus on CVEs.",
        }
        result = _build_agent_instructions(state)
        assert "## Agent Role" in result
        assert "## Agent Instructions" in result

    def test_empty_instructions_excluded(self):
        state = {"instructions": "   "}
        assert _build_agent_instructions(state) == ""


# ---------------------------------------------------------------------------
# _build_time_context
# ---------------------------------------------------------------------------

class TestBuildTimeContext:
    def test_with_time_and_timezone(self):
        state = {"current_time": "2026-05-14T10:00:00Z", "timezone": "UTC"}
        result = _build_time_context(state)
        assert isinstance(result, str)

    def test_empty_state(self):
        result = _build_time_context({})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _build_user_context
# ---------------------------------------------------------------------------

class TestBuildUserContext:
    def test_no_user_info(self):
        state = {}
        assert _build_user_context(state) == ""

    def test_email_only(self):
        state = {"user_email": "alice@corp.com"}
        result = _build_user_context(state)
        assert "alice@corp.com" in result

    def test_name_and_email_from_user_info(self):
        state = {"user_info": {"fullName": "Alice Smith", "userEmail": "alice@corp.com"}}
        result = _build_user_context(state)
        assert "Alice Smith" in result
        assert "alice@corp.com" in result

    def test_first_last_name_fallback(self):
        state = {"user_info": {"firstName": "Bob", "lastName": "Jones"}}
        result = _build_user_context(state)
        assert "Bob Jones" in result

    def test_display_name_used(self):
        state = {"user_info": {"displayName": "Charlie"}}
        result = _build_user_context(state)
        assert "Charlie" in result

    def test_empty_user_info(self):
        state = {"user_info": {}}
        assert _build_user_context(state) == ""


# ---------------------------------------------------------------------------
# _build_iteration_context
# ---------------------------------------------------------------------------

class TestBuildIterationContext:
    def test_no_completed_no_evaluation(self):
        state = {}
        assert _build_iteration_context(state, log) == ""

    def test_success_task(self):
        state = {
            "completed_tasks": [{
                "task_id": "t1",
                "status": "success",
                "domains": ["jira"],
                "description": "Search tickets",
                "result": {"response": "Found 5 tickets", "tool_count": 2, "success_count": 2, "error_count": 0},
            }],
        }
        result = _build_iteration_context(state, log)
        assert "t1" in result
        assert "SUCCESS" in result
        assert "Found 5 tickets" in result

    def test_error_task(self):
        state = {
            "completed_tasks": [{
                "task_id": "t1",
                "status": "error",
                "domains": ["confluence"],
                "description": "Fetch page",
                "error": "Timeout after 30s",
                "duration_ms": 30000,
            }],
        }
        result = _build_iteration_context(state, log)
        assert "FAILED" in result
        assert "Timeout" in result

    def test_skipped_task(self):
        state = {
            "completed_tasks": [{
                "task_id": "t1",
                "status": "skipped",
                "domains": ["jira"],
                "description": "Update ticket",
                "error": "Dependencies failed",
            }],
        }
        result = _build_iteration_context(state, log)
        assert "SKIPPED" in result

    def test_continue_evaluation(self):
        state = {
            "completed_tasks": [{"task_id": "t1", "status": "success", "domains": [], "result": "ok"}],
            "evaluation": {
                "decision": "continue",
                "continue_description": "Need to update tickets after search",
                "reasoning": "Search done, update pending",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Next step needed" in result
        assert "update tickets" in result

    def test_retry_evaluation(self):
        state = {
            "completed_tasks": [{"task_id": "t1", "status": "error", "domains": [], "error": "timeout"}],
            "evaluation": {
                "decision": "retry",
                "retry_fix": "increase timeout",
                "retry_task_id": "t1",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Retry needed" in result
        assert "increase timeout" in result


# ---------------------------------------------------------------------------
# _create_retrieval_task
# ---------------------------------------------------------------------------

class TestCreateRetrievalTask:
    def test_basic_structure(self):
        task = _create_retrieval_task("What is our vacation policy?")
        assert task["task_id"] == "retrieval_search"
        assert task["domains"] == ["retrieval"]
        assert task["depends_on"] == []
        assert "vacation policy" in task["description"]

    def test_includes_query_in_description(self):
        task = _create_retrieval_task("How do I submit expenses?")
        assert "expenses" in task["description"]

    def test_suggests_multiple_search_queries(self):
        task = _create_retrieval_task("test")
        assert "multiple" in task["description"].lower() or "diverse" in task["description"].lower()


# ---------------------------------------------------------------------------
# _build_knowledge_context
# ---------------------------------------------------------------------------

class TestBuildKnowledgeContext:
    def test_no_knowledge_no_tools(self):
        state = {"has_knowledge": False, "tools": []}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge or Tools" in result

    def test_no_knowledge_with_tools(self):
        state = {"has_knowledge": False, "tools": ["jira.search"]}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge Base Configured" in result
        assert "Do NOT create retrieval tasks" in result

    def test_has_knowledge_basic(self):
        state = {
            "has_knowledge": True,
            "agent_knowledge": [],
            "connector_configs": {},
        }
        with patch("app.modules.agents.deep.orchestrator.classify_knowledge_sources", return_value=([], [])):
            with patch("app.modules.agents.deep.orchestrator.build_connector_routing_rules", return_value=""):
                result = _build_knowledge_context(state, log)
                assert "Knowledge Sources Available" in result
                assert "MANDATORY RULE" in result

    def test_has_knowledge_with_sources(self):
        state = {
            "has_knowledge": True,
            "agent_knowledge": [{"type": "connector", "connectorId": "c1"}],
            "connector_configs": {"c1": {"name": "Confluence"}},
        }
        with patch(
            "app.modules.agents.deep.orchestrator.classify_knowledge_sources",
            return_value=([], [{"connector_id": "c1", "name": "Confluence"}]),
        ):
            with patch(
                "app.modules.agents.deep.orchestrator.build_connector_routing_rules",
                return_value="Routing: search c1",
            ):
                result = _build_knowledge_context(state, log)
                assert "Routing: search c1" in result
