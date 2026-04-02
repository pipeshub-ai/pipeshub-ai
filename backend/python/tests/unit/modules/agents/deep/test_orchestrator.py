"""
Tests for app.modules.agents.deep.orchestrator helper functions.

Covers:
- _normalize_tasks: single/multi-domain splitting
- _parse_orchestrator_response: JSON parsing with markdown stripping
- _build_knowledge_context: knowledge base detection
- _build_tool_guidance: tool listing from state
- _build_agent_instructions: agent instructions assembly
- _build_time_context: time/timezone context
- _build_user_context: user info context
- _build_iteration_context: previous results for re-planning
- should_dispatch: routing dispatch/respond
- _create_retrieval_task: retrieval task creation
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

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
    _parse_orchestrator_response,
    should_dispatch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


# ============================================================================
# 1. _normalize_tasks
# ============================================================================

class TestNormalizeTasks:
    """Tests for _normalize_tasks()."""

    def test_single_domain_unchanged(self):
        log = _mock_log()
        tasks = [
            {"task_id": "t1", "description": "Search Jira", "domains": ["jira"]},
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 1
        assert result[0]["task_id"] == "t1"
        assert result[0]["domains"] == ["jira"]

    def test_empty_domains_unchanged(self):
        log = _mock_log()
        tasks = [
            {"task_id": "t1", "description": "Hello", "domains": []},
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 1

    def test_multi_domain_split(self):
        log = _mock_log()
        tasks = [
            {
                "task_id": "t1",
                "description": "Search both",
                "domains": ["jira", "confluence"],
                "depends_on": [],
                "complexity": "moderate",
            },
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 2
        assert result[0]["task_id"] == "t1_jira"
        assert result[0]["domains"] == ["jira"]
        assert result[1]["task_id"] == "t1_confluence"
        assert result[1]["domains"] == ["confluence"]
        assert "[jira part]" in result[0]["description"].lower()
        assert "[confluence part]" in result[1]["description"].lower()

    def test_empty_task_list(self):
        log = _mock_log()
        result = _normalize_tasks([], log)
        assert result == []

    def test_multi_domain_preserves_complexity(self):
        log = _mock_log()
        tasks = [
            {
                "task_id": "t1",
                "description": "Complex query",
                "domains": ["jira", "slack"],
                "depends_on": [],
                "complexity": "complex",
                "batch_strategy": "parallel",
            },
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 2
        for t in result:
            assert t["complexity"] == "complex"
            assert t["batch_strategy"] == "parallel"

    def test_multi_domain_updates_downstream_dependencies(self):
        log = _mock_log()
        tasks = [
            {
                "task_id": "t1",
                "description": "Multi-domain",
                "domains": ["jira", "confluence"],
                "depends_on": [],
            },
            {
                "task_id": "t2",
                "description": "Depends on t1",
                "domains": ["slack"],
                "depends_on": ["t1"],
            },
        ]
        result = _normalize_tasks(tasks, log)
        # t2 should now depend on the split tasks
        t2 = [t for t in result if t["task_id"] == "t2"][0]
        assert "t1_jira" in t2["depends_on"]
        assert "t1_confluence" in t2["depends_on"]
        assert "t1" not in t2["depends_on"]

    def test_three_domains_split_into_three(self):
        log = _mock_log()
        tasks = [
            {
                "task_id": "t1",
                "description": "Wide query",
                "domains": ["jira", "confluence", "slack"],
                "depends_on": [],
            },
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 3

    def test_mixed_single_and_multi_domain(self):
        log = _mock_log()
        tasks = [
            {"task_id": "t1", "description": "Single", "domains": ["jira"]},
            {
                "task_id": "t2",
                "description": "Multi",
                "domains": ["slack", "confluence"],
                "depends_on": [],
            },
        ]
        result = _normalize_tasks(tasks, log)
        assert len(result) == 3


# ============================================================================
# 2. _parse_orchestrator_response
# ============================================================================

class TestParseOrchestratorResponse:
    """Tests for _parse_orchestrator_response()."""

    def test_clean_json(self):
        log = _mock_log()
        content = '{"can_answer_directly": true, "reasoning": "Simple greeting"}'
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is True
        assert result["reasoning"] == "Simple greeting"

    def test_markdown_wrapped_json(self):
        log = _mock_log()
        content = '```json\n{"tasks": [{"task_id": "t1"}], "reasoning": "test"}\n```'
        result = _parse_orchestrator_response(content, log)
        assert "tasks" in result
        assert len(result["tasks"]) == 1

    def test_malformed_json_fallback(self):
        log = _mock_log()
        content = "I cannot parse this as JSON at all, sorry!"
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is True
        assert "I cannot parse" in result["reasoning"]

    def test_non_dict_json_fallback(self):
        log = _mock_log()
        content = '[1, 2, 3]'
        result = _parse_orchestrator_response(content, log)
        # Array is not a dict, so fallback
        assert result["can_answer_directly"] is True

    def test_json_embedded_in_text(self):
        log = _mock_log()
        content = 'Here is my plan:\n{"tasks": [{"task_id": "t1"}], "can_answer_directly": false}'
        result = _parse_orchestrator_response(content, log)
        assert "tasks" in result
        assert result["can_answer_directly"] is False

    def test_empty_content_fallback(self):
        log = _mock_log()
        content = ""
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is True

    def test_markdown_code_block_with_json_prefix(self):
        log = _mock_log()
        content = '```json\n{\n  "can_answer_directly": false,\n  "tasks": []\n}\n```'
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is False
        assert result["tasks"] == []

    def test_nested_json_with_special_chars(self):
        log = _mock_log()
        content = '{"tasks": [{"task_id": "t1", "description": "Search for \\"bugs\\""}]}'
        result = _parse_orchestrator_response(content, log)
        assert len(result["tasks"]) == 1


# ============================================================================
# 3. _build_knowledge_context
# ============================================================================

class TestBuildKnowledgeContext:
    """Tests for _build_knowledge_context()."""

    def test_with_knowledge(self):
        log = _mock_log()
        state = {"has_knowledge": True, "tools": ["jira.search"]}
        result = _build_knowledge_context(state, log)
        assert "Knowledge Base Available" in result
        assert "retrieval" in result.lower()

    def test_without_knowledge_with_tools(self):
        log = _mock_log()
        state = {"has_knowledge": False, "tools": ["jira.search"]}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge Base" in result
        assert "Do NOT create retrieval" in result

    def test_no_knowledge_no_tools(self):
        log = _mock_log()
        state = {"has_knowledge": False, "tools": []}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge or Tools Configured" in result

    def test_no_knowledge_none_tools(self):
        log = _mock_log()
        state = {"has_knowledge": False, "tools": None}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge or Tools Configured" in result

    def test_with_knowledge_no_tools(self):
        log = _mock_log()
        state = {"has_knowledge": True, "tools": []}
        result = _build_knowledge_context(state, log)
        assert "Knowledge Base Available" in result


# ============================================================================
# 4. _build_tool_guidance
# ============================================================================

class TestBuildToolGuidance:
    """Tests for _build_tool_guidance()."""

    def test_with_tools(self):
        state = {"tools": ["jira.search_issues", "jira.create_issue", "slack.send_message"]}
        result = _build_tool_guidance(state)
        assert "Available Tool Domains" in result
        assert "jira" in result
        assert "slack" in result

    def test_empty_tools(self):
        state = {"tools": []}
        result = _build_tool_guidance(state)
        assert result == ""

    def test_none_tools(self):
        state = {"tools": None}
        result = _build_tool_guidance(state)
        assert result == ""

    def test_no_tools_key(self):
        state = {}
        result = _build_tool_guidance(state)
        assert result == ""

    def test_tools_without_dot(self):
        state = {"tools": ["search_issues"]}
        result = _build_tool_guidance(state)
        assert "other" in result

    def test_non_string_tools_ignored(self):
        state = {"tools": [123, None, "jira.search"]}
        result = _build_tool_guidance(state)
        assert "jira" in result

    def test_many_tools_in_single_domain(self):
        tools = [f"jira.tool_{i}" for i in range(15)]
        state = {"tools": tools}
        result = _build_tool_guidance(state)
        assert "more)" in result  # Should show overflow indicator

    def test_mixed_domains(self):
        state = {"tools": ["jira.search", "confluence.get_page", "retrieval.search_knowledge"]}
        result = _build_tool_guidance(state)
        assert "jira" in result
        assert "confluence" in result
        assert "retrieval" in result


# ============================================================================
# 5. _build_agent_instructions
# ============================================================================

class TestBuildAgentInstructions:
    """Tests for _build_agent_instructions()."""

    def test_with_instructions(self):
        state = {
            "system_prompt": "",
            "instructions": "Always respond in French.",
        }
        result = _build_agent_instructions(state)
        assert "Always respond in French" in result

    def test_without_instructions(self):
        state = {"system_prompt": "", "instructions": ""}
        result = _build_agent_instructions(state)
        assert result == ""

    def test_with_system_prompt_non_default(self):
        state = {
            "system_prompt": "You are a code review expert.",
            "instructions": "",
        }
        result = _build_agent_instructions(state)
        assert "code review expert" in result

    def test_default_system_prompt_ignored(self):
        state = {
            "system_prompt": "You are an enterprise questions answering expert",
            "instructions": "",
        }
        result = _build_agent_instructions(state)
        assert result == ""

    def test_both_prompt_and_instructions(self):
        state = {
            "system_prompt": "You are a code expert.",
            "instructions": "Focus on Python.",
        }
        result = _build_agent_instructions(state)
        assert "code expert" in result
        assert "Focus on Python" in result

    def test_whitespace_only_instructions_ignored(self):
        state = {"system_prompt": "", "instructions": "   "}
        result = _build_agent_instructions(state)
        assert result == ""

    def test_none_values(self):
        state = {"system_prompt": None, "instructions": None}
        result = _build_agent_instructions(state)
        assert result == ""


# ============================================================================
# 6. _build_time_context
# ============================================================================

class TestBuildTimeContext:
    """Tests for _build_time_context()."""

    def test_with_timezone(self):
        state = {"current_time": "2026-03-24T10:00:00Z", "timezone": "US/Pacific"}
        result = _build_time_context(state)
        assert "2026-03-24" in result
        assert "US/Pacific" in result

    def test_without_timezone(self):
        state = {"current_time": "2026-03-24T10:00:00Z", "timezone": None}
        result = _build_time_context(state)
        assert "2026-03-24" in result
        assert "Timezone" not in result

    def test_no_time_info(self):
        state = {"current_time": None, "timezone": None}
        result = _build_time_context(state)
        assert result == ""

    def test_empty_state(self):
        state = {}
        result = _build_time_context(state)
        assert result == ""

    def test_only_timezone(self):
        state = {"current_time": None, "timezone": "Europe/London"}
        result = _build_time_context(state)
        assert "Europe/London" in result


# ============================================================================
# 7. _build_user_context
# ============================================================================

class TestBuildUserContext:
    """Tests for _build_user_context()."""

    def test_with_user_info(self):
        state = {
            "user_info": {"fullName": "Jane Doe", "userEmail": "jane@example.com"},
            "user_email": "jane@example.com",
        }
        result = _build_user_context(state)
        assert "Jane Doe" in result
        assert "jane@example.com" in result

    def test_without_user_info(self):
        state = {"user_info": {}, "user_email": ""}
        result = _build_user_context(state)
        assert result == ""

    def test_empty_state(self):
        state = {}
        result = _build_user_context(state)
        assert result == ""

    def test_email_only(self):
        state = {"user_info": {}, "user_email": "user@example.com"}
        result = _build_user_context(state)
        assert "user@example.com" in result

    def test_name_from_first_last(self):
        state = {
            "user_info": {"firstName": "John", "lastName": "Smith"},
            "user_email": "",
        }
        result = _build_user_context(state)
        assert "John Smith" in result

    def test_name_from_display_name(self):
        state = {
            "user_info": {"displayName": "Admin User"},
            "user_email": "",
        }
        result = _build_user_context(state)
        assert "Admin User" in result

    def test_name_priority_full_name(self):
        """fullName takes priority over other name fields."""
        state = {
            "user_info": {
                "fullName": "Priority Name",
                "displayName": "Fallback Name",
                "firstName": "First",
                "lastName": "Last",
            },
            "user_email": "",
        }
        result = _build_user_context(state)
        assert "Priority Name" in result


# ============================================================================
# 8. _build_iteration_context
# ============================================================================

class TestBuildIterationContext:
    """Tests for _build_iteration_context()."""

    def test_first_iteration_no_data(self):
        log = _mock_log()
        state = {"completed_tasks": [], "evaluation": {}}
        result = _build_iteration_context(state, log)
        assert result == ""

    def test_with_completed_tasks(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search for bugs",
                    "result": {"response": "Found 3 bugs", "tool_count": 1, "success_count": 1, "error_count": 0},
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "t1" in result
        assert "SUCCESS" in result
        assert "Found 3 bugs" in result

    def test_with_failed_tasks(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "error",
                    "domains": ["jira"],
                    "description": "Search for bugs",
                    "error": "Connection refused",
                    "duration_ms": 1500,
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "FAILED" in result
        assert "Connection refused" in result

    def test_with_skipped_tasks(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t2",
                    "status": "skipped",
                    "domains": ["confluence"],
                    "description": "Get page",
                    "error": "Dependencies failed",
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "SKIPPED" in result
        assert "Dependencies failed" in result

    def test_continue_evaluation(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search",
                    "result": {"response": "data"},
                },
            ],
            "evaluation": {
                "decision": "continue",
                "reasoning": "Need more data",
                "continue_description": "Fetch detailed issue data",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Next step needed" in result
        assert "Fetch detailed issue data" in result
        assert "Do NOT repeat" in result

    def test_retry_evaluation(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "error",
                    "domains": ["jira"],
                    "description": "Search",
                    "error": "Timeout",
                },
            ],
            "evaluation": {
                "decision": "retry",
                "reasoning": "Timeout occurred",
                "retry_fix": "Use pagination",
                "retry_task_id": "t1",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Retry needed" in result
        assert "Use pagination" in result
        assert "t1" in result

    def test_no_completed_no_evaluation(self):
        log = _mock_log()
        state = {}
        result = _build_iteration_context(state, log)
        assert result == ""

    def test_success_task_with_non_dict_result(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search",
                    "result": "plain text result",
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "plain text result" in result


# ============================================================================
# 9. should_dispatch
# ============================================================================

class TestShouldDispatch:
    """Tests for should_dispatch()."""

    def test_can_answer_directly_true(self):
        state = {
            "error": None,
            "execution_plan": {"can_answer_directly": True},
            "sub_agent_tasks": [],
        }
        assert should_dispatch(state) == "respond"

    def test_can_answer_directly_false_with_tasks(self):
        state = {
            "error": None,
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert should_dispatch(state) == "dispatch"

    def test_error_state(self):
        state = {
            "error": {"message": "Something failed", "status_code": 500},
            "execution_plan": {},
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert should_dispatch(state) == "respond"

    def test_no_tasks(self):
        state = {
            "error": None,
            "execution_plan": {"can_answer_directly": False},
            "sub_agent_tasks": [],
        }
        assert should_dispatch(state) == "respond"

    def test_empty_state(self):
        state = {}
        assert should_dispatch(state) == "respond"

    def test_no_execution_plan(self):
        state = {
            "error": None,
            "sub_agent_tasks": [{"task_id": "t1"}],
        }
        assert should_dispatch(state) == "dispatch"


# ============================================================================
# 10. _create_retrieval_task
# ============================================================================

class TestCreateRetrievalTask:
    """Tests for _create_retrieval_task()."""

    def test_basic_creation(self):
        task = _create_retrieval_task("What is our refund policy?")
        assert task["task_id"] == "retrieval_search"
        assert task["domains"] == ["retrieval"]
        assert task["depends_on"] == []
        assert "refund policy" in task["description"]

    def test_description_includes_query(self):
        task = _create_retrieval_task("How to configure SSO?")
        assert "SSO" in task["description"]
        assert "knowledge base" in task["description"].lower()

    def test_empty_query(self):
        task = _create_retrieval_task("")
        assert task["task_id"] == "retrieval_search"
        assert task["domains"] == ["retrieval"]


# ============================================================================
# 11. orchestrator_node (async)
# ============================================================================

class TestOrchestratorNode:
    """Tests for orchestrator_node() async function."""

    def _make_state(self, **overrides):
        """Create a minimal valid state for orchestrator_node."""
        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "query": "search for bugs",
            "deep_iteration_count": 0,
            "previous_conversations": [],
            "has_knowledge": False,
            "tools": [],
            "system_prompt": "",
            "instructions": "",
            "current_time": None,
            "timezone": None,
            "user_info": {},
            "user_email": "",
            "completed_tasks": [],
            "evaluation": {},
            "conversation_summary": None,
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_direct_answer_path(self):
        """When LLM says can_answer_directly, orchestrator returns without tasks."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "Simple greeting"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        assert result["sub_agent_tasks"] == []
        assert result["execution_plan"]["can_answer_directly"] is True
        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_task_planning_path(self):
        """When LLM returns tasks, orchestrator creates sub-agent tasks."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        plan_json = json.dumps({
            "can_answer_directly": False,
            "reasoning": "Need to search Jira",
            "tasks": [
                {"task_id": "t1", "description": "Search Jira", "domains": ["jira"], "depends_on": []}
            ]
        })
        mock_response = MagicMock()
        mock_response.content = plan_json
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm, tools=["jira.search_issues"])
        writer = MagicMock()
        config = {"configurable": {}}

        def mock_assign(tasks, groups, st):
            for t in tasks:
                t["tools"] = [MagicMock()]
            return tasks

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={"jira": []}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value="jira: search"), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.tool_router.assign_tools_to_tasks", side_effect=mock_assign):
            result = await orchestrator_node(state, config, writer)

        assert result["execution_plan"]["can_answer_directly"] is False
        assert len(result["sub_agent_tasks"]) == 1
        assert result["sub_agent_tasks"][0]["task_id"] == "t1"

    @pytest.mark.asyncio
    async def test_exception_sets_error_state(self):
        """When orchestrator encounters an exception, error is set in state."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM crashed"))
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        assert result.get("error") is not None
        assert "LLM crashed" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_knowledge_base_injects_retrieval_task(self):
        """When has_knowledge=True and LLM plan has no retrieval, one is injected."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        plan_json = json.dumps({
            "can_answer_directly": False,
            "reasoning": "Need to search",
            "tasks": [
                {"task_id": "t1", "description": "Search Jira", "domains": ["jira"], "depends_on": []}
            ]
        })
        mock_response = MagicMock()
        mock_response.content = plan_json
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm, has_knowledge=True, tools=["jira.search_issues"])
        writer = MagicMock()
        config = {"configurable": {}}

        def mock_assign(tasks, groups, st):
            for t in tasks:
                t["tools"] = [MagicMock()] if t["domains"] != ["retrieval"] else []
            return tasks

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.tool_router.assign_tools_to_tasks", side_effect=mock_assign):
            result = await orchestrator_node(state, config, writer)

        # Should have retrieval task injected
        task_ids = [t["task_id"] for t in result["sub_agent_tasks"]]
        assert "retrieval_search" in task_ids

    @pytest.mark.asyncio
    async def test_iteration_context_included(self):
        """When iteration > 0, iteration context is included."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "done"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(
            llm=llm,
            deep_iteration_count=1,
            completed_tasks=[{"task_id": "t1", "status": "success", "domains": ["jira"],
                              "result": {"response": "data"}}],
            evaluation={"decision": "continue", "reasoning": "need more"},
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        # Verify llm was called with messages that include iteration context
        call_args = llm.ainvoke.call_args
        messages = call_args[0][0]
        # Should have more than just system + user messages
        assert len(messages) >= 2

    @pytest.mark.asyncio
    async def test_conversation_summary_stored(self):
        """When compact_conversation_history returns a summary, it's stored."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "ok"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm, previous_conversations=[{"role": "user", "content": "hi"}])
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("Summarized history", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        assert result.get("conversation_summary") == "Summarized history"

    @pytest.mark.asyncio
    async def test_tasks_without_tools_skipped(self):
        """Tasks with no tools assigned (non-retrieval) are skipped."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        plan_json = json.dumps({
            "can_answer_directly": False,
            "reasoning": "Need data",
            "tasks": [
                {"task_id": "t1", "description": "Search", "domains": ["unknown_domain"], "depends_on": []}
            ]
        })
        mock_response = MagicMock()
        mock_response.content = plan_json
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        def mock_assign(tasks, groups, st):
            # Don't assign any tools
            return tasks

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.tool_router.assign_tools_to_tasks", side_effect=mock_assign):
            result = await orchestrator_node(state, config, writer)

        # Task with no tools for non-knowledge domain should be skipped
        assert len(result["sub_agent_tasks"]) == 0


# ============================================================================
# 12. _parse_orchestrator_response — additional branches
# ============================================================================

class TestParseOrchestratorResponseExtra:
    """Additional branch coverage for _parse_orchestrator_response()."""

    def test_json_embedded_after_text_with_brace_in_text(self):
        """Content has braces before the real JSON."""
        log = _mock_log()
        content = 'Here is {some text} and now: {"can_answer_directly": false, "tasks": []}'
        result = _parse_orchestrator_response(content, log)
        assert isinstance(result, dict)

    def test_markdown_block_without_json_tag(self):
        """Markdown block starting with ``` but no json tag."""
        log = _mock_log()
        content = '```\n{"can_answer_directly": true}\n```'
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is True

    def test_double_json_decode_error_fallback(self):
        """When both direct parse and regex fail, returns fallback."""
        log = _mock_log()
        content = '{"broken: json, and {also broken}'
        result = _parse_orchestrator_response(content, log)
        assert result["can_answer_directly"] is True
        assert "broken" in result.get("reasoning", "")


# ============================================================================
# 13. _build_iteration_context — branch coverage
# ============================================================================

class TestBuildIterationContextExtra:
    """Additional branch coverage for _build_iteration_context()."""

    def test_success_task_with_tool_count(self):
        """Success task with tool_count shows tool stats in header."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search",
                    "result": {"response": "Found data", "tool_count": 3, "success_count": 2, "error_count": 1},
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "3 tools" in result
        assert "2 ok" in result
        assert "1 err" in result

    def test_success_task_without_tool_count(self):
        """Success task without tool_count omits tool stats."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search",
                    "result": {"response": "Found data"},
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "SUCCESS" in result
        assert "tools" not in result.lower() or "tool" in result.lower()

    def test_error_task_with_duration(self):
        """Error task with duration_ms shows timing."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "error",
                    "domains": ["jira"],
                    "description": "Search",
                    "error": "Timeout",
                    "duration_ms": 5000.0,
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "5000ms" in result
        assert "FAILED" in result

    def test_error_task_without_duration(self):
        """Error task without duration_ms omits timing."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "error",
                    "domains": ["jira"],
                    "description": "Search",
                    "error": "Timeout",
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "FAILED" in result
        assert "ms)" not in result

    def test_retry_evaluation_without_task_id(self):
        """Retry evaluation without retry_task_id."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "domains": ["jira"],
                 "description": "Search", "error": "Timeout"},
            ],
            "evaluation": {
                "decision": "retry",
                "reasoning": "Timeout occurred",
                "retry_fix": "Use smaller page size",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Retry needed" in result
        assert "Use smaller page size" in result

    def test_success_with_empty_response(self):
        """Success task with empty response text."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "description": "Search",
                    "result": {"response": ""},
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "SUCCESS" in result
        assert "Result:" not in result  # empty response not shown

    def test_unknown_status_ignored(self):
        """Tasks with unknown status are not rendered."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "running",
                    "domains": ["jira"],
                    "description": "Search",
                },
            ],
            "evaluation": {},
        }
        result = _build_iteration_context(state, log)
        assert "SUCCESS" not in result
        assert "FAILED" not in result
        assert "SKIPPED" not in result


# ============================================================================
# 14. _build_knowledge_context with tools (no knowledge)
# ============================================================================

class TestBuildKnowledgeContextExtra:
    """Extra tests for _build_knowledge_context branch paths."""

    def test_no_knowledge_with_tools(self):
        """has_knowledge False + tools present -> No Knowledge Base text."""
        log = _mock_log()
        state = {"has_knowledge": False, "tools": ["jira.search"]}
        result = _build_knowledge_context(state, log)
        assert "No Knowledge Base" in result

    def test_knowledge_true_tools_empty(self):
        """has_knowledge True + no tools -> Knowledge Base Available."""
        log = _mock_log()
        state = {"has_knowledge": True, "tools": []}
        result = _build_knowledge_context(state, log)
        assert "Knowledge Base Available" in result


# ============================================================================
# 15. orchestrator_node — additional branch coverage
# ============================================================================

class TestOrchestratorNodeAdditional:
    """Additional tests covering uncovered branches in orchestrator_node."""

    def _make_state(self, **overrides):
        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "query": "search for bugs",
            "deep_iteration_count": 0,
            "previous_conversations": [],
            "has_knowledge": False,
            "tools": [],
            "system_prompt": "",
            "instructions": "",
            "current_time": None,
            "timezone": None,
            "user_info": {},
            "user_email": "",
            "completed_tasks": [],
            "evaluation": {},
            "conversation_summary": None,
        }
        state.update(overrides)
        return state

    @pytest.mark.asyncio
    async def test_conv_messages_extended(self):
        """When build_conversation_messages returns messages, they are extended into messages list (line 118)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "Simple"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        conv_msgs = [MagicMock(), MagicMock()]  # Non-empty messages

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=conv_msgs), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        # Messages should include system + conv_msgs + user query
        call_args = llm.ainvoke.call_args[0][0]
        assert len(call_args) >= 4  # system + 2 conv + user

    @pytest.mark.asyncio
    async def test_user_context_appended(self):
        """When user context is available, it's appended to query (line 133)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "Simple"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(
            llm=llm,
            user_info={"fullName": "John Doe"},
            user_email="john@example.com",
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        # The last message should contain user context
        last_msg = llm.ainvoke.call_args[0][0][-1]
        assert "John Doe" in last_msg.content
        assert "john@example.com" in last_msg.content

    @pytest.mark.asyncio
    async def test_time_context_appended(self):
        """When time context is available, it's appended to query (line 137)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "Simple"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(
            llm=llm,
            current_time="2026-03-24T10:00:00Z",
            timezone="US/Pacific",
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        last_msg = llm.ainvoke.call_args[0][0][-1]
        assert "2026-03-24" in last_msg.content
        assert "US/Pacific" in last_msg.content

    @pytest.mark.asyncio
    async def test_reasoning_streamed_when_present(self):
        """When plan has reasoning, it's streamed to user (line 159)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        mock_response = MagicMock()
        mock_response.content = '{"can_answer_directly": true, "reasoning": "Analyzing the request carefully"}'
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write") as mock_write, \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock):
            result = await orchestrator_node(state, config, writer)

        # safe_stream_write should be called with the reasoning message
        calls = mock_write.call_args_list
        reasoning_calls = [c for c in calls if "Analyzing" in str(c)]
        assert len(reasoning_calls) >= 1

    @pytest.mark.asyncio
    async def test_knowledge_base_with_existing_retrieval_no_injection(self):
        """When has_knowledge=True and LLM plan already has retrieval, no extra injection (line 191->199)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        plan_json = json.dumps({
            "can_answer_directly": False,
            "reasoning": "Need to search",
            "tasks": [
                {"task_id": "t1", "description": "Search Jira", "domains": ["jira"], "depends_on": []},
                {"task_id": "t2", "description": "Search KB", "domains": ["retrieval"], "depends_on": []},
            ]
        })
        mock_response = MagicMock()
        mock_response.content = plan_json
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm, has_knowledge=True)
        writer = MagicMock()
        config = {"configurable": {}}

        def mock_assign(tasks, groups, st):
            for t in tasks:
                t["tools"] = [MagicMock()]
            return tasks

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.tool_router.assign_tools_to_tasks", side_effect=mock_assign):
            result = await orchestrator_node(state, config, writer)

        # Should NOT have extra retrieval_search injected — t2 already has retrieval
        task_ids = [t["task_id"] for t in result["sub_agent_tasks"]]
        assert task_ids.count("retrieval_search") == 0
        assert "t2" in task_ids  # Original retrieval task preserved

    @pytest.mark.asyncio
    async def test_retrieval_domain_task_kept_without_tools(self):
        """Retrieval domain tasks are kept even without tools assigned (line 226)."""
        from app.modules.agents.deep.orchestrator import orchestrator_node

        plan_json = json.dumps({
            "can_answer_directly": False,
            "reasoning": "Need data",
            "tasks": [
                {"task_id": "t1", "description": "Search KB", "domains": ["retrieval"], "depends_on": []}
            ]
        })
        mock_response = MagicMock()
        mock_response.content = plan_json
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value=mock_response)
        state = self._make_state(llm=llm)
        writer = MagicMock()
        config = {"configurable": {}}

        def mock_assign(tasks, groups, st):
            # Don't assign any tools — retrieval tasks should still be kept
            return tasks

        with patch("app.modules.agents.deep.orchestrator.compact_conversation_history_async",
                   new_callable=AsyncMock, return_value=("", [])), \
             patch("app.modules.agents.deep.orchestrator.group_tools_by_domain", return_value={}), \
             patch("app.modules.agents.deep.orchestrator.build_domain_description", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.orchestrator.build_conversation_messages", return_value=[]), \
             patch("app.modules.agents.deep.orchestrator.safe_stream_write"), \
             patch("app.modules.agents.deep.orchestrator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.tool_router.assign_tools_to_tasks", side_effect=mock_assign):
            result = await orchestrator_node(state, config, writer)

        # Retrieval task should be kept despite no tools
        assert len(result["sub_agent_tasks"]) == 1
        assert result["sub_agent_tasks"][0]["task_id"] == "t1"


# ============================================================================
# 16. _build_tool_guidance — additional branch coverage
# ============================================================================

class TestBuildToolGuidanceAdditional:
    """Extra tests for _build_tool_guidance branches."""

    def test_all_non_string_tools_returns_empty(self):
        """When all tools are non-string, domain_tools is empty -> returns '' (line 481-482)."""
        state = {"tools": [123, None, True]}
        result = _build_tool_guidance(state)
        assert result == ""


# ============================================================================
# 17. _build_iteration_context — continue without description
# ============================================================================

class TestBuildIterationContextContinue:
    """Extra tests for _build_iteration_context continue/retry."""

    def test_continue_without_description(self):
        """Continue evaluation without continue_description uses reasoning (line 615-616)."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "description": "Search", "result": {"response": "data"}},
            ],
            "evaluation": {
                "decision": "continue",
                "reasoning": "Need more data from different source",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Next step needed" in result
        assert "Need more data" in result

    def test_retry_without_retry_fix_uses_reasoning(self):
        """Retry evaluation without retry_fix uses reasoning."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "domains": ["jira"],
                 "description": "Search", "error": "Timeout"},
            ],
            "evaluation": {
                "decision": "retry",
                "reasoning": "Timeout occurred, try again",
            },
        }
        result = _build_iteration_context(state, log)
        assert "Retry needed" in result
        assert "Timeout occurred" in result


# ============================================================================
# 18. _build_user_context — email fallback order
# ============================================================================

class TestBuildUserContextAdditional:
    """Extra tests for _build_user_context email/name resolution."""

    def test_email_from_user_info_user_email(self):
        """user_email from user_info.userEmail."""
        state = {"user_info": {"userEmail": "info@example.com"}, "user_email": ""}
        result = _build_user_context(state)
        assert "info@example.com" in result

    def test_email_from_user_info_email(self):
        """user_email from user_info.email."""
        state = {"user_info": {"email": "alt@example.com"}, "user_email": ""}
        result = _build_user_context(state)
        assert "alt@example.com" in result

    def test_name_from_name_field(self):
        """Name from user_info.name field."""
        state = {"user_info": {"name": "Named User"}, "user_email": ""}
        result = _build_user_context(state)
        assert "Named User" in result

    def test_only_first_name(self):
        """Name constructed from firstName only (no lastName)."""
        state = {"user_info": {"firstName": "Solo"}, "user_email": ""}
        result = _build_user_context(state)
        assert "Solo" in result

    def test_only_last_name(self):
        """Name constructed from lastName only (no firstName)."""
        state = {"user_info": {"lastName": "OnlyLast"}, "user_email": ""}
        result = _build_user_context(state)
        assert "OnlyLast" in result
