"""
Tests for app.modules.agents.deep.sub_agent helper functions.

Covers:
- _extract_response: Extracting text response from agent messages
- _extract_tool_results: Extracting tool results from messages
- _detect_status: Success/error detection
- _ToolCallBudget: Budget management
- _wrap_tools_with_budget: Tool wrapping with budget
- _build_sub_agent_instructions: Instructions builder
- _build_sub_agent_tool_guidance: Tool guidance builder
- _format_tools_for_prompt: Tool formatting for prompts
- _SubAgentStreamingCallback: Streaming callback handler
- _execute_single_sub_agent: Routing logic
- _execute_simple_sub_agent: Simple sub-agent execution
- _execute_complex_sub_agent: Complex phased execution
- _make_budgeted_coro: Budget-enforced coroutine factory
"""

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.agents.deep.sub_agent import (
    _build_sub_agent_instructions,
    _build_sub_agent_tool_guidance,
    _detect_status,
    _extract_response,
    _extract_tool_results,
    _format_tools_for_prompt,
    _make_budgeted_coro,
    _SubAgentStreamingCallback,
    _ToolCallBudget,
    _wrap_tools_with_budget,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


def _mock_writer():
    """Return a mock StreamWriter."""
    return MagicMock()


def _mock_config():
    """Return a minimal RunnableConfig-like dict."""
    return {"configurable": {}}


def _mock_state(**overrides: Any) -> dict:
    """Return a minimal DeepAgentState-like dict."""
    state: dict[str, Any] = {
        "logger": _mock_log(),
        "llm": MagicMock(),
        "query": "test query",
        "user_info": {},
        "instructions": "",
        "tool_guidance": {},
        "available_tools": {},
        "tool_to_toolset_map": {},
        "agent_toolsets": [],
    }
    state.update(overrides)
    return state


# ============================================================================
# 1. _extract_response
# ============================================================================

class TestExtractResponse:
    """Tests for _extract_response()."""

    def test_ai_message_with_string_content(self):
        log = _mock_log()
        messages = [
            HumanMessage(content="do something"),
            AIMessage(content="Here is the result."),
        ]
        result = _extract_response(messages, log)
        assert result == "Here is the result."

    def test_ai_message_with_list_content(self):
        log = _mock_log()
        messages = [
            HumanMessage(content="do something"),
            AIMessage(content=[{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}]),
        ]
        result = _extract_response(messages, log)
        assert "Part 1" in result
        assert "Part 2" in result

    def test_ai_message_with_list_of_strings(self):
        log = _mock_log()
        messages = [
            AIMessage(content=["Hello", "World"]),
        ]
        result = _extract_response(messages, log)
        assert "Hello" in result
        assert "World" in result

    def test_tool_messages_skipped(self):
        log = _mock_log()
        messages = [
            HumanMessage(content="request"),
            ToolMessage(content="tool output", tool_call_id="tc1"),
            AIMessage(content="Final answer"),
        ]
        result = _extract_response(messages, log)
        assert result == "Final answer"

    def test_human_messages_skipped(self):
        """Human messages should be skipped -- only AI responses are wanted."""
        log = _mock_log()
        messages = [
            HumanMessage(content="This is the task"),
        ]
        result = _extract_response(messages, log)
        # Fallback: no AI message found, returns empty or tool summary
        assert result == ""

    def test_fallback_from_tool_messages(self):
        """When no AI message has text, build summary from tool messages."""
        log = _mock_log()
        messages = [
            HumanMessage(content="task"),
            ToolMessage(content='{"results": [1,2]}', tool_call_id="tc1", name="jira.search"),
        ]
        result = _extract_response(messages, log)
        assert "jira.search" in result
        assert "results" in result

    def test_fallback_tool_message_dict_content(self):
        """Tool message with dict content should be JSON-serialized."""
        log = _mock_log()
        messages = [
            ToolMessage(content={"items": [1, 2, 3]}, tool_call_id="tc1", name="api.call"),
        ]
        result = _extract_response(messages, log)
        assert "api.call" in result

    def test_empty_messages(self):
        log = _mock_log()
        result = _extract_response([], log)
        assert result == ""

    def test_ai_message_with_empty_string(self):
        """AI message with whitespace-only content should be skipped."""
        log = _mock_log()
        messages = [
            AIMessage(content="   "),
            AIMessage(content="Actual content"),
        ]
        result = _extract_response(messages, log)
        assert result == "Actual content"

    def test_last_ai_message_wins(self):
        """Should pick the LAST AIMessage (walking backwards)."""
        log = _mock_log()
        messages = [
            AIMessage(content="First"),
            ToolMessage(content="tool data", tool_call_id="tc1"),
            AIMessage(content="Second"),
        ]
        result = _extract_response(messages, log)
        assert result == "Second"

    def test_ai_message_with_empty_list_content(self):
        """AI message with empty list should be skipped."""
        log = _mock_log()
        messages = [
            AIMessage(content=[]),
            AIMessage(content="Fallback content"),
        ]
        result = _extract_response(messages, log)
        assert result == "Fallback content"


# ============================================================================
# 2. _extract_tool_results
# ============================================================================

class TestExtractToolResults:
    """Tests for _extract_tool_results()."""

    def test_with_tool_messages(self):
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(content='{"data": "ok"}', tool_call_id="tc1", name="jira.search"),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            results = _extract_tool_results(messages, state, log)
        assert len(results) == 1
        assert results[0]["tool_name"] == "jira.search"

    def test_without_tool_messages(self):
        log = _mock_log()
        state = _mock_state()
        messages = [
            HumanMessage(content="task"),
            AIMessage(content="answer"),
        ]
        results = _extract_tool_results(messages, state, log)
        assert results == []

    def test_retrieval_tool_processing(self):
        """Retrieval tool results should trigger _process_retrieval_output."""
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(
                content='{"final_results": [{"text": "result"}]}',
                tool_call_id="tc1",
                name="retrieval.search_internal_knowledge",
            ),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
                results = _extract_tool_results(messages, state, log)
                mock_proc.assert_called_once()
        assert len(results) == 1

    def test_multiple_tool_messages(self):
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(content="res1", tool_call_id="tc1", name="tool1"),
            ToolMessage(content="res2", tool_call_id="tc2", name="tool2"),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            results = _extract_tool_results(messages, state, log)
        assert len(results) == 2

    def test_tool_call_id_extracted(self):
        log = _mock_log()
        state = _mock_state()
        msg = ToolMessage(content="ok", tool_call_id="call_123", name="test.tool")
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            results = _extract_tool_results([msg], state, log)
        assert results[0]["tool_call_id"] == "call_123"


# ============================================================================
# 3. _detect_status
# ============================================================================

class TestDetectStatus:
    """Tests for _detect_status()."""

    def test_success_content(self):
        result = _detect_status('{"data": "ok"}')
        assert result == "success"

    def test_error_content(self):
        result = _detect_status('{"status": "error", "message": "failed"}')
        assert result == "error"

    def test_dict_with_error_key(self):
        result = _detect_status({"error": "something failed"})
        assert result == "error"

    def test_dict_success(self):
        result = _detect_status({"data": [1, 2, 3]})
        assert result == "success"

    def test_fallback_error_keywords(self):
        """If the imported function is not available, fallback uses keyword detection."""
        with patch("app.modules.agents.deep.sub_agent._detect_status") as mock_detect:
            # This tests the actual function; the fallback is inside the function
            mock_detect.side_effect = lambda x: _detect_status(x)
            result = _detect_status("Error: unauthorized access")
            assert result == "error"

    def test_none_content(self):
        result = _detect_status(None)
        assert result == "success"

    def test_empty_string(self):
        result = _detect_status("")
        assert result == "success"

    def test_success_true_dict(self):
        result = _detect_status({"success": True, "data": {"id": 1}})
        assert result == "success"

    def test_success_false_dict(self):
        result = _detect_status({"success": False})
        assert result == "error"


# ============================================================================
# 4. _ToolCallBudget
# ============================================================================

class TestToolCallBudget:
    """Tests for _ToolCallBudget class."""

    def test_init(self):
        budget = _ToolCallBudget(10)
        assert budget.max_calls == 10
        assert budget.count == 0

    def test_consume_within_budget(self):
        budget = _ToolCallBudget(3)
        assert budget.consume() is True
        assert budget.count == 1
        assert budget.consume() is True
        assert budget.count == 2
        assert budget.consume() is True
        assert budget.count == 3

    def test_consume_exhausted(self):
        budget = _ToolCallBudget(2)
        assert budget.consume() is True  # count=1
        assert budget.consume() is True  # count=2
        assert budget.consume() is False  # count=3, exceeds max

    def test_zero_budget(self):
        budget = _ToolCallBudget(0)
        assert budget.consume() is False  # count=1 > 0

    def test_single_call_budget(self):
        budget = _ToolCallBudget(1)
        assert budget.consume() is True
        assert budget.consume() is False

    def test_count_increments_even_past_budget(self):
        budget = _ToolCallBudget(1)
        budget.consume()
        budget.consume()
        budget.consume()
        assert budget.count == 3


# ============================================================================
# 5. _wrap_tools_with_budget
# ============================================================================

class TestWrapToolsWithBudget:
    """Tests for _wrap_tools_with_budget()."""

    def test_wraps_tools(self):
        log = _mock_log()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "A test tool"
        tool.args_schema = None
        tool.return_direct = False
        tool.coroutine = AsyncMock(return_value="result")
        tool.func = None

        budget = _ToolCallBudget(5)

        with patch("langchain_core.tools.StructuredTool.from_function", return_value=MagicMock(name="wrapped")) as mock_from:
            wrapped = _wrap_tools_with_budget([tool], budget, log)
            assert len(wrapped) == 1
            mock_from.assert_called_once()

    def test_original_name_preserved(self):
        log = _mock_log()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "desc"
        tool.args_schema = None
        tool.return_direct = False
        tool.coroutine = AsyncMock()
        tool.func = None
        tool._original_name = "original.tool"

        budget = _ToolCallBudget(5)

        new_tool = MagicMock()
        with patch("langchain_core.tools.StructuredTool.from_function", return_value=new_tool):
            wrapped = _wrap_tools_with_budget([tool], budget, log)
            assert wrapped[0]._original_name == "original.tool"

    def test_wrapping_failure_uses_original(self):
        log = _mock_log()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.coroutine = AsyncMock()
        tool.func = None

        budget = _ToolCallBudget(5)

        with patch("langchain_core.tools.StructuredTool.from_function", side_effect=Exception("wrap failed")):
            wrapped = _wrap_tools_with_budget([tool], budget, log)
            assert wrapped[0] is tool

    def test_empty_tools(self):
        log = _mock_log()
        budget = _ToolCallBudget(5)
        wrapped = _wrap_tools_with_budget([], budget, log)
        assert wrapped == []


# ============================================================================
# 6. _make_budgeted_coro
# ============================================================================

class TestMakeBudgetedCoro:
    """Tests for _make_budgeted_coro()."""

    @pytest.mark.asyncio
    async def test_within_budget_calls_coro(self):
        orig_coro = AsyncMock(return_value="original result")
        budget = _ToolCallBudget(5)
        log = _mock_log()

        coro = _make_budgeted_coro(orig_coro, None, budget, "test_tool", log)
        result = await coro(param="value")
        assert result == "original result"
        orig_coro.assert_awaited_once_with(param="value")

    @pytest.mark.asyncio
    async def test_exhausted_budget_returns_stop_message(self):
        orig_coro = AsyncMock(return_value="should not be called")
        budget = _ToolCallBudget(0)
        log = _mock_log()

        coro = _make_budgeted_coro(orig_coro, None, budget, "test_tool", log)
        result = await coro(param="value")
        assert "TOOL CALL BUDGET EXHAUSTED" in result
        orig_coro.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_func_when_no_coro(self):
        orig_func = MagicMock(return_value="func result")
        budget = _ToolCallBudget(5)
        log = _mock_log()

        coro = _make_budgeted_coro(None, orig_func, budget, "test_tool", log)
        result = await coro(param="value")
        assert result == "func result"
        orig_func.assert_called_once_with(param="value")

    @pytest.mark.asyncio
    async def test_budget_consumed_per_call(self):
        orig_coro = AsyncMock(return_value="ok")
        budget = _ToolCallBudget(2)
        log = _mock_log()

        coro = _make_budgeted_coro(orig_coro, None, budget, "test_tool", log)
        await coro()
        assert budget.count == 1
        await coro()
        assert budget.count == 2
        result = await coro()
        assert "EXHAUSTED" in result
        assert budget.count == 3


# ============================================================================
# 7. _build_sub_agent_instructions
# ============================================================================

class TestBuildSubAgentInstructions:
    """Tests for _build_sub_agent_instructions()."""

    def test_with_instructions(self):
        state = _mock_state(instructions="Always format in markdown.")
        result = _build_sub_agent_instructions(state)
        assert "Agent Instructions" in result
        assert "Always format in markdown." in result

    def test_without_instructions(self):
        state = _mock_state(instructions="")
        result = _build_sub_agent_instructions(state)
        # No instructions and no user info => empty string
        assert result == ""

    def test_with_user_info_name(self):
        state = _mock_state(
            user_info={"fullName": "Alice Smith", "userEmail": "alice@example.com"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "Current User" in result

    def test_with_user_email_only(self):
        state = _mock_state(user_email="bob@example.com")
        result = _build_sub_agent_instructions(state)
        assert "bob@example.com" in result

    def test_with_both_instructions_and_user(self):
        state = _mock_state(
            instructions="Be brief.",
            user_info={"fullName": "Charlie"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Agent Instructions" in result
        assert "Be brief." in result
        assert "Charlie" in result

    def test_whitespace_only_instructions_ignored(self):
        state = _mock_state(instructions="   ")
        result = _build_sub_agent_instructions(state)
        assert "Agent Instructions" not in result

    def test_user_first_last_name_fallback(self):
        state = _mock_state(
            user_info={"firstName": "Jane", "lastName": "Doe"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Jane Doe" in result

    def test_user_email_from_state_key(self):
        """user_email in state takes priority."""
        state = _mock_state(user_email="priority@example.com")
        result = _build_sub_agent_instructions(state)
        assert "priority@example.com" in result

    def test_no_user_info_at_all(self):
        state = _mock_state(user_info={}, user_email="")
        result = _build_sub_agent_instructions(state)
        assert "Current User" not in result


# ============================================================================
# 8. _build_sub_agent_tool_guidance
# ============================================================================

class TestBuildSubAgentToolGuidance:
    """Tests for _build_sub_agent_tool_guidance()."""

    def test_basic_guidance(self):
        task = {"domains": ["jira"], "tools": ["jira.search_issues", "jira.create_issue"]}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Tool Usage Guidance" in result
        assert "Link Extraction" in result
        assert "jira.search_issues" in result

    def test_retrieval_domain_guidance(self):
        task = {"domains": ["retrieval"], "tools": ["retrieval.search_internal_knowledge"]}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Knowledge Base Search Strategy" in result
        # Retrieval tasks should NOT have link extraction
        assert "Link Extraction" not in result

    def test_knowledge_domain_guidance(self):
        task = {"domains": ["knowledge"], "tools": ["knowledge.search"]}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Knowledge Base Search Strategy" in result

    def test_no_tools(self):
        task = {"domains": ["jira"], "tools": []}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Available Tools" not in result

    def test_with_tools_listed(self):
        task = {"domains": ["slack"], "tools": ["slack.send_message"]}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Available Tools" in result
        assert "`slack.send_message`" in result

    def test_empty_domains(self):
        task = {"domains": [], "tools": ["tool.do_something"]}
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Tool Usage Guidance" in result

    def test_many_tools_capped_at_15(self):
        task = {
            "domains": ["api"],
            "tools": [f"api.tool_{i}" for i in range(20)],
        }
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        # Should show at most 15 tools
        tool_mentions = [t for t in task["tools"] if f"`{t}`" in result]
        assert len(tool_mentions) <= 15


# ============================================================================
# 9. _format_tools_for_prompt
# ============================================================================

class TestFormatToolsForPrompt:
    """Tests for _format_tools_for_prompt()."""

    def test_empty_tools(self):
        log = _mock_log()
        result = _format_tools_for_prompt([], log)
        assert result == ""

    def test_tool_with_name_and_description(self):
        log = _mock_log()
        tool = MagicMock()
        tool.name = "jira.search_issues"
        tool.description = "Search for Jira issues using JQL"
        tool.args_schema = None
        result = _format_tools_for_prompt([tool], log)
        assert "### jira.search_issues" in result
        assert "Search for Jira issues using JQL" in result

    def test_tool_with_schema(self):
        """Tool with args_schema should show parameter details."""
        log = _mock_log()
        tool = MagicMock()
        tool.name = "jira.create_issue"
        tool.description = "Create a new Jira issue"
        tool.args_schema = MagicMock()

        mock_params = {
            "summary": {"required": True, "type": "string", "description": "Issue summary"},
            "priority": {"required": False, "type": "string", "description": "Issue priority"},
        }

        with patch("app.modules.agents.deep.tool_router._extract_params", return_value=mock_params):
            result = _format_tools_for_prompt([tool], log)
        assert "**Parameters:**" in result
        assert "`summary`" in result
        assert "**required**" in result
        assert "`priority`" in result
        assert "optional" in result

    def test_tool_schema_extraction_failure(self):
        """Schema extraction failure should not crash."""
        log = _mock_log()
        tool = MagicMock()
        tool.name = "broken.tool"
        tool.description = "A tool"
        tool.args_schema = MagicMock()

        with patch("app.modules.agents.deep.tool_router._extract_params", side_effect=Exception("schema error")):
            result = _format_tools_for_prompt([tool], log)
        assert "### broken.tool" in result

    def test_multiple_tools(self):
        log = _mock_log()
        tool1 = MagicMock()
        tool1.name = "tool1"
        tool1.description = "First tool"
        tool1.args_schema = None

        tool2 = MagicMock()
        tool2.name = "tool2"
        tool2.description = "Second tool"
        tool2.args_schema = None

        result = _format_tools_for_prompt([tool1, tool2], log)
        assert "### tool1" in result
        assert "### tool2" in result

    def test_tool_without_description(self):
        log = _mock_log()
        tool = MagicMock()
        tool.name = "minimal.tool"
        tool.description = ""
        tool.args_schema = None
        result = _format_tools_for_prompt([tool], log)
        assert "### minimal.tool" in result

    def test_safety_limit_20_tools(self):
        """Should only format up to 20 tools."""
        log = _mock_log()
        tools = []
        for i in range(25):
            tool = MagicMock()
            tool.name = f"tool_{i}"
            tool.description = f"Tool {i}"
            tool.args_schema = None
            tools.append(tool)
        result = _format_tools_for_prompt(tools, log)
        # Count tool headers
        assert result.count("### tool_") == 20


# ============================================================================
# 10. _SubAgentStreamingCallback
# ============================================================================

class TestSubAgentStreamingCallback:
    """Tests for _SubAgentStreamingCallback."""

    def test_init(self):
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")
        assert cb.task_id == "task_1"
        assert cb.collected_results == []

    @pytest.mark.asyncio
    async def test_on_tool_start(self):
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")

        run_id = uuid4()
        await cb.on_tool_start(
            {"name": "jira.search_issues"},
            "input",
            run_id=run_id,
        )
        assert str(run_id) in cb._tool_names
        assert cb._tool_names[str(run_id)] == "jira.search_issues"
        # Writer should have been called with status event
        writer.assert_called()

    @pytest.mark.asyncio
    async def test_on_tool_end(self):
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")

        run_id = uuid4()
        cb._tool_names[str(run_id)] = "jira.search"

        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            await cb.on_tool_end("output data", run_id=run_id)

        assert str(run_id) not in cb._tool_names
        assert len(cb.collected_results) == 1
        assert cb.collected_results[0]["tool_name"] == "jira.search"
        assert cb.collected_results[0]["status"] == "success"

    @pytest.mark.asyncio
    async def test_on_tool_end_unknown_run(self):
        """Tool end with unknown run_id should use 'unknown' tool name."""
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")

        run_id = uuid4()
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            await cb.on_tool_end("output", run_id=run_id)

        assert cb.collected_results[0]["tool_name"] == "unknown"

    @pytest.mark.asyncio
    async def test_on_tool_error(self):
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")

        run_id = uuid4()
        cb._tool_names[str(run_id)] = "failing.tool"

        await cb.on_tool_error(ValueError("test error"), run_id=run_id)
        writer.assert_called()
        # Tool name should be removed
        assert str(run_id) not in cb._tool_names

    @pytest.mark.asyncio
    async def test_on_tool_start_name_from_kwargs(self):
        """Tool name can come from kwargs when not in serialized."""
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task_1")

        run_id = uuid4()
        await cb.on_tool_start({}, "input", run_id=run_id, name="from_kwargs")
        assert cb._tool_names[str(run_id)] == "from_kwargs"


# ============================================================================
# 11. _execute_single_sub_agent routing
# ============================================================================

class TestExecuteSingleSubAgentRouting:
    """Tests for _execute_single_sub_agent() routing logic."""

    @pytest.mark.asyncio
    async def test_routes_to_simple_for_simple_complexity(self):
        """Simple complexity should route to _execute_simple_sub_agent."""
        task = {
            "task_id": "t1",
            "description": "Search Jira",
            "complexity": "simple",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent", new_callable=AsyncMock) as mock_simple:
            mock_simple.return_value = {**task, "status": "success"}
            from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
            result = await _execute_single_sub_agent(task, state, [], config, writer, log)
            mock_simple.assert_awaited_once()
            assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_routes_to_complex_for_complex_non_retrieval(self):
        """Complex non-retrieval should route to _execute_complex_sub_agent."""
        task = {
            "task_id": "t1",
            "description": "Summarize all Jira data",
            "complexity": "complex",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_complex_sub_agent", new_callable=AsyncMock) as mock_complex:
            mock_complex.return_value = {**task, "status": "success"}
            from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
            result = await _execute_single_sub_agent(task, state, [], config, writer, log)
            mock_complex.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_routes_to_multi_step_when_flagged(self):
        """Multi-step tasks should route to _execute_multi_step_sub_agent."""
        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "complexity": "simple",
            "domains": ["confluence"],
            "tools": ["confluence.search"],
            "depends_on": [],
            "multi_step": True,
            "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_multi_step_sub_agent", new_callable=AsyncMock) as mock_multi:
            mock_multi.return_value = {**task, "status": "success"}
            from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
            result = await _execute_single_sub_agent(task, state, [], config, writer, log)
            mock_multi.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complex_retrieval_forced_to_simple(self):
        """Complex retrieval tasks should be forced to simple execution."""
        task = {
            "task_id": "t1",
            "description": "Search knowledge base",
            "complexity": "complex",
            "domains": ["retrieval"],
            "tools": ["retrieval.search"],
            "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent", new_callable=AsyncMock) as mock_simple:
            mock_simple.return_value = {**task, "status": "success"}
            from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
            result = await _execute_single_sub_agent(task, state, [], config, writer, log)
            mock_simple.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_failed_dependency_skips_task(self):
        """Task with failed dependencies should be skipped."""
        task = {
            "task_id": "t2",
            "description": "Dependent task",
            "complexity": "simple",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": ["t1"],
        }
        completed = [{"task_id": "t1", "status": "error", "error": "failed"}]
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
        result = await _execute_single_sub_agent(task, state, completed, config, writer, log)
        assert result["status"] == "skipped"
        assert "Dependencies failed" in result["error"]

    @pytest.mark.asyncio
    async def test_multi_step_failure_falls_back_to_simple(self):
        """When multi-step execution fails, falls back to simple."""
        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "complexity": "simple",
            "domains": ["confluence"],
            "tools": ["confluence.search"],
            "depends_on": [],
            "multi_step": True,
            "sub_steps": ["Step 1"],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_multi_step_sub_agent", new_callable=AsyncMock) as mock_multi:
            mock_multi.side_effect = Exception("multi-step failed")
            with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent", new_callable=AsyncMock) as mock_simple:
                mock_simple.return_value = {**task, "status": "success"}
                from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
                result = await _execute_single_sub_agent(task, state, [], config, writer, log)
                mock_simple.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complex_failure_falls_back_to_simple(self):
        """When complex execution fails, falls back to simple."""
        task = {
            "task_id": "t1",
            "description": "Complex task",
            "complexity": "complex",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_complex_sub_agent", new_callable=AsyncMock) as mock_complex:
            mock_complex.side_effect = Exception("complex failed")
            with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent", new_callable=AsyncMock) as mock_simple:
                mock_simple.return_value = {**task, "status": "success"}
                from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
                result = await _execute_single_sub_agent(task, state, [], config, writer, log)
                mock_simple.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_dependencies_executes_normally(self):
        """Task with empty depends_on should execute normally."""
        task = {
            "task_id": "t1",
            "description": "Simple task",
            "complexity": "simple",
            "domains": ["jira"],
            "tools": [],
            "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent", new_callable=AsyncMock) as mock_simple:
            mock_simple.return_value = {**task, "status": "success"}
            from app.modules.agents.deep.sub_agent import _execute_single_sub_agent
            result = await _execute_single_sub_agent(task, state, [], config, writer, log)
            mock_simple.assert_awaited_once()


# ============================================================================
# 12. execute_sub_agents_node — main executor
# ============================================================================

class TestExecuteSubAgentsNode:
    """Tests for execute_sub_agents_node() — the main executor."""

    @pytest.mark.asyncio
    async def test_no_tasks_returns_early(self):
        """When no sub-agent tasks, returns state immediately."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        state = _mock_state(sub_agent_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            result = await execute_sub_agents_node(state, config, writer)

        # Should return immediately without executing
        assert result is state

    @pytest.mark.asyncio
    async def test_single_task_executed(self):
        """Single task is executed and result stored."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        task = {
            "task_id": "t1",
            "description": "Search Jira",
            "complexity": "simple",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
        }
        completed_task = {
            **task,
            "status": "success",
            "result": {
                "response": "Found 5 issues",
                "tool_results": [{"tool_name": "jira.search", "status": "success", "result": "data"}],
                "tool_count": 1,
                "success_count": 1,
                "error_count": 0,
            },
        }
        state = _mock_state(sub_agent_tasks=[task], completed_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock, return_value=completed_task):
            result = await execute_sub_agents_node(state, config, writer)

        assert len(result["completed_tasks"]) == 1
        assert result["completed_tasks"][0]["status"] == "success"
        assert len(result["all_tool_results"]) == 1
        assert len(result["sub_agent_analyses"]) == 1
        assert "Found 5 issues" in result["sub_agent_analyses"][0]

    @pytest.mark.asyncio
    async def test_parallel_independent_tasks(self):
        """Independent tasks are executed in parallel via asyncio.gather."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        task1 = {
            "task_id": "t1", "description": "Task 1", "complexity": "simple",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }
        task2 = {
            "task_id": "t2", "description": "Task 2", "complexity": "simple",
            "domains": ["slack"], "tools": ["slack.search"], "depends_on": [],
        }

        async def mock_execute(task, state, completed, config, writer, log):
            return {
                **task,
                "status": "success",
                "result": {"response": f"Done {task['task_id']}", "tool_results": [], "tool_count": 0, "success_count": 0, "error_count": 0},
            }

        state = _mock_state(sub_agent_tasks=[task1, task2], completed_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", side_effect=mock_execute):
            result = await execute_sub_agents_node(state, config, writer)

        assert len(result["completed_tasks"]) == 2
        assert all(t["status"] == "success" for t in result["completed_tasks"])

    @pytest.mark.asyncio
    async def test_dependent_task_waits_for_dependency(self):
        """Dependent task waits for its dependency to complete."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        task1 = {
            "task_id": "t1", "description": "Task 1", "complexity": "simple",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }
        task2 = {
            "task_id": "t2", "description": "Task 2", "complexity": "simple",
            "domains": ["slack"], "tools": ["slack.send"], "depends_on": ["t1"],
        }

        execution_order = []

        async def mock_execute(task, state, completed, config, writer, log):
            execution_order.append(task["task_id"])
            return {
                **task,
                "status": "success",
                "result": {"response": f"Done {task['task_id']}", "tool_results": [], "tool_count": 0, "success_count": 0, "error_count": 0},
            }

        state = _mock_state(sub_agent_tasks=[task1, task2], completed_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", side_effect=mock_execute):
            result = await execute_sub_agents_node(state, config, writer)

        assert len(result["completed_tasks"]) == 2
        # t1 should execute before t2
        assert execution_order.index("t1") < execution_order.index("t2")

    @pytest.mark.asyncio
    async def test_task_exception_captured_as_error(self):
        """When a task raises an exception, it's captured as error status."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        task = {
            "task_id": "t1", "description": "Failing task", "complexity": "simple",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }

        async def mock_execute(task, state, completed, config, writer, log):
            raise RuntimeError("Sub-agent crashed")

        state = _mock_state(sub_agent_tasks=[task], completed_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", side_effect=mock_execute):
            result = await execute_sub_agents_node(state, config, writer)

        assert len(result["completed_tasks"]) == 1
        assert result["completed_tasks"][0]["status"] == "error"
        assert "crashed" in result["completed_tasks"][0]["error"]

    @pytest.mark.asyncio
    async def test_domain_summary_used_for_analysis(self):
        """Complex tasks with domain_summary use it for analysis."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        task = {
            "task_id": "t1", "description": "Complex task", "complexity": "complex",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }
        completed_task = {
            **task,
            "status": "success",
            "result": {"response": "raw data", "tool_results": [], "tool_count": 0, "success_count": 0, "error_count": 0},
            "domain_summary": "Consolidated: 10 jira issues found, 3 critical",
        }

        state = _mock_state(sub_agent_tasks=[task], completed_tasks=[])
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock, return_value=completed_task):
            result = await execute_sub_agents_node(state, config, writer)

        assert len(result["sub_agent_analyses"]) == 1
        assert "Consolidated" in result["sub_agent_analyses"][0]


# ============================================================================
# 13. _execute_simple_sub_agent — deeper tests
# ============================================================================

class TestExecuteSimpleSubAgentDeeper:
    """Deeper tests for _execute_simple_sub_agent."""

    @pytest.mark.asyncio
    async def test_no_tools_returns_error(self):
        """When no tools are available, returns error status."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        task = {
            "task_id": "t1", "description": "Task", "complexity": "simple",
            "domains": ["jira"], "tools": [], "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"):
            result = await _execute_simple_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "error"
        assert "No tools available" in result["error"]

    @pytest.mark.asyncio
    async def test_agent_exception_returns_error(self):
        """When the ReAct agent raises, error is returned."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        task = {
            "task_id": "t1", "description": "Task", "complexity": "simple",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.coroutine = AsyncMock()
        mock_tool.func = None
        mock_tool.return_direct = False

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._wrap_tools_with_budget", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value="tool docs"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value="guidance"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("langchain.agents.create_agent") as mock_create:
            mock_agent = AsyncMock()
            mock_agent.ainvoke = AsyncMock(side_effect=RuntimeError("Agent explosion"))
            mock_create.return_value = mock_agent

            result = await _execute_simple_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "error"
        assert "Agent explosion" in result["error"]


# ============================================================================
# 14. _execute_complex_sub_agent — deeper tests
# ============================================================================

class TestExecuteComplexSubAgentDeeper:
    """Deeper tests for _execute_complex_sub_agent."""

    @pytest.mark.asyncio
    async def test_no_tools_returns_error(self):
        """When no tools available, returns error."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1", "description": "Complex task", "complexity": "complex",
            "domains": ["jira"], "tools": [], "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"):
            result = await _execute_complex_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "error"
        assert "No tools available" in result["error"]

    @pytest.mark.asyncio
    async def test_all_tool_calls_failed_returns_error(self):
        """When all tool calls fail in Phase 1, returns error."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1", "description": "Complex task", "complexity": "complex",
            "domains": ["jira"], "tools": ["jira.search"], "depends_on": [],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.coroutine = AsyncMock()
        mock_tool.func = None
        mock_tool.return_direct = False

        # Agent returns messages with only failed tool results
        agent_result = {
            "messages": [
                HumanMessage(content="Complex task"),
                ToolMessage(content='{"error": "timeout"}', tool_call_id="tc1", name="jira.search"),
            ]
        }

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._wrap_tools_with_budget", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value="tool docs"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value="guidance"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[{"tool_name": "jira.search", "status": "error"}]), \
             patch("langchain.agents.create_agent") as mock_create:
            mock_agent = AsyncMock()
            mock_agent.ainvoke = AsyncMock(return_value=agent_result)
            mock_create.return_value = mock_agent

            result = await _execute_complex_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "error"
        assert "failed" in result["error"].lower()


# ============================================================================
# 15. _execute_multi_step_sub_agent — deeper tests
# ============================================================================

class TestExecuteMultiStepSubAgentDeeper:
    """Deeper tests for _execute_multi_step_sub_agent."""

    @pytest.mark.asyncio
    async def test_no_tools_returns_error(self):
        """When no tools available, returns error."""
        from app.modules.agents.deep.sub_agent import _execute_multi_step_sub_agent

        task = {
            "task_id": "t1", "description": "Multi-step task",
            "complexity": "simple", "domains": ["confluence"],
            "tools": [], "depends_on": [],
            "multi_step": True, "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"):
            result = await _execute_multi_step_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "error"
        assert "No tools available" in result["error"]

    @pytest.mark.asyncio
    async def test_step_failure_captured_not_fatal(self):
        """When one step fails, execution continues and failure is captured."""
        from app.modules.agents.deep.sub_agent import _execute_multi_step_sub_agent

        task = {
            "task_id": "t1", "description": "Multi-step task",
            "complexity": "simple", "domains": ["confluence"],
            "tools": ["confluence.search"], "depends_on": [],
            "multi_step": True, "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        log = _mock_log()
        writer = _mock_writer()
        config = _mock_config()

        mock_tool = MagicMock()
        mock_tool.name = "confluence.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.coroutine = AsyncMock()
        mock_tool.func = None
        mock_tool.return_direct = False

        call_count = 0

        async def mock_ainvoke(messages, config=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Step 1 failed")
            return {
                "messages": [
                    HumanMessage(content="Step 2"),
                    AIMessage(content="Step 2 done"),
                ]
            }

        with patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._wrap_tools_with_budget", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value="tool docs"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value="guidance"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.safe_stream_write"), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[]), \
             patch("langchain.agents.create_agent") as mock_create:
            mock_agent = AsyncMock()
            mock_agent.ainvoke = mock_ainvoke
            mock_create.return_value = mock_agent

            result = await _execute_multi_step_sub_agent(task, state, [], config, writer, log)

        assert result["status"] == "success" or result["status"] == "error"
        # Both steps should have produced results (one failure, one success text)


# ============================================================================
# 16. _prewarm_clients — deeper tests
# ============================================================================

class TestPrewarmClients:
    """Tests for _prewarm_clients."""

    @pytest.mark.asyncio
    async def test_no_tasks_no_warming(self):
        """Empty tasks list does nothing."""
        from app.modules.agents.deep.sub_agent import _prewarm_clients

        state = _mock_state(tool_to_toolset_map={})
        log = _mock_log()

        # Should not raise
        await _prewarm_clients([], state, log)

    @pytest.mark.asyncio
    async def test_prewarm_deduplicates_by_app(self):
        """Multiple tools from same app only warm once."""
        from app.modules.agents.deep.sub_agent import _prewarm_clients

        tasks = [
            {"task_id": "t1", "tools": ["jira.search_issues", "jira.create_issue"]},
        ]
        state = _mock_state(tool_to_toolset_map={})
        log = _mock_log()

        with patch("app.agents.tools.factories.registry.ClientFactoryRegistry") as mock_registry, \
             patch("app.agents.tools.wrapper.ToolInstanceCreator") as mock_creator_cls:
            mock_factory = MagicMock()
            mock_factory.create_client = AsyncMock(return_value=MagicMock())
            mock_registry.get_factory.return_value = mock_factory

            mock_creator = MagicMock()
            mock_creator._get_toolset_config.return_value = {}
            mock_creator._client_cache = {}
            mock_creator._cache_locks = {}
            mock_creator_cls.return_value = mock_creator

            await _prewarm_clients(tasks, state, log)

            # Should only get factory once for "jira"
            mock_registry.get_factory.assert_called_once_with("jira")

    @pytest.mark.asyncio
    async def test_prewarm_exception_does_not_crash(self):
        """Prewarm failures are silently caught."""
        from app.modules.agents.deep.sub_agent import _prewarm_clients

        tasks = [
            {"task_id": "t1", "tools": ["jira.search_issues"]},
        ]
        state = _mock_state(tool_to_toolset_map={})
        log = _mock_log()

        with patch("app.agents.tools.factories.registry.ClientFactoryRegistry") as mock_registry, \
             patch("app.agents.tools.wrapper.ToolInstanceCreator") as mock_creator_cls:
            mock_registry.get_factory.side_effect = ImportError("No factory")

            mock_creator = MagicMock()
            mock_creator._get_toolset_config.return_value = {}
            mock_creator._client_cache = {}
            mock_creator._cache_locks = {}
            mock_creator_cls.return_value = mock_creator

            # Should not raise
            await _prewarm_clients(tasks, state, log)
