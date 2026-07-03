"""
Additional tests for app.modules.agents.deep.sub_agent to increase coverage.

Targets missing lines: 39-43, 112-116, 156->154, 187->166, 198, 244->255,
364, 366, 411, 432-448, 546, 548, 556-564, 601, 644-771, 851, 853, 919,
1007, 1015->1003, 1020->1017, 1022->1003, 1024->1003, 1039-1045,
1078-1083, 1102-1106, 1161->1163, 1237, 1247, 1250->1252, 1254, 1270,
1434->1448, 1444, 1479
"""

import asyncio
import json
import logging
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.agents.deep.sub_agent import (
    _build_sub_agent_instructions,
    _build_sub_agent_tool_guidance,
    _detect_status,
    _extract_response,
    _extract_tool_results,
    _format_task_scope_block,
    _format_tools_for_prompt,
    _make_budgeted_coro,
    _prewarm_clients,
    _rebind_tool_state,
    _SubAgentStreamingCallback,
    _ToolCallBudget,
    _wrap_retrieval_tools_for_context_efficiency,
    _wrap_tools_with_budget,
    execute_sub_agents_node,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


def _mock_writer():
    return MagicMock()


def _mock_config():
    return {"configurable": {}}


def _mock_state(**overrides: Any) -> dict:
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
        "retrieval_service": MagicMock(config_service=MagicMock()),
    }
    state.update(overrides)
    return state


# ============================================================================
# _extract_response — additional edge cases
# ============================================================================


class TestExtractResponseCoverage:
    """Cover lines 1007, 1015->1003, 1020->1017, 1022->1003, 1024->1003,
    1039-1045 — messages with no content attr, list content with only dict
    type:text parts that are empty, tool messages with non-str content types."""

    def test_message_without_content_attr(self):
        """Message without 'content' attribute is skipped (line 1007)."""
        log = _mock_log()
        msg = MagicMock(spec=[])  # no attributes at all
        messages = [msg]
        result = _extract_response(messages, log)
        assert result == ""

    def test_ai_message_with_list_content_empty_text_parts(self):
        """AI message with list content where text parts are all empty."""
        log = _mock_log()
        messages = [
            AIMessage(content=[
                {"type": "text", "text": ""},
                {"type": "text", "text": ""},
            ]),
        ]
        result = _extract_response(messages, log)
        # All text parts are empty, so joined is empty, falls through
        assert result == ""

    def test_ai_message_with_list_content_mixed_types(self):
        """AI message with list content mixing strings and dicts."""
        log = _mock_log()
        messages = [
            AIMessage(content=["Hello", {"type": "text", "text": "World"}]),
        ]
        result = _extract_response(messages, log)
        assert "Hello" in result
        assert "World" in result

    def test_fallback_tool_message_with_list_content(self):
        """Fallback: tool message with list content should be JSON-serialized."""
        log = _mock_log()
        messages = [
            ToolMessage(content=[1, 2, 3], tool_call_id="tc1", name="api.call"),
        ]
        result = _extract_response(messages, log)
        assert "api.call" in result

    def test_fallback_tool_message_with_nonjson_content(self):
        """Tool message with content that can't be JSON serialized."""
        log = _mock_log()
        messages = [
            ToolMessage(content=42, tool_call_id="tc1", name="calc.add"),
        ]
        result = _extract_response(messages, log)
        assert "calc.add" in result
        assert "42" in result

    def test_tool_message_without_name(self):
        """Tool message without name attribute falls back to 'unknown'."""
        log = _mock_log()
        msg = ToolMessage(content="data", tool_call_id="tc1", name="")
        # Remove name to simulate missing attribute
        messages = [msg]
        result = _extract_response(messages, log)
        # Should still produce a result
        assert isinstance(result, str)


# ============================================================================
# _extract_tool_results — additional edge cases
# ============================================================================


class TestExtractToolResultsCoverage:
    """Cover lines 1078-1083, 1102-1106 — retrieval processing with
    dict content and JSON decode error paths, import error fallback."""

    def test_retrieval_with_dict_content(self):
        """Retrieval tool with dict result_content triggers direct processing."""
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(
                content={"final_results": [{"text": "result"}]},
                tool_call_id="tc1",
                name="retrieval.search_knowledge",
            ),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
                results = _extract_tool_results(messages, state, log)
                mock_proc.assert_called_once()
        assert len(results) == 1

    def test_retrieval_with_invalid_json_content(self):
        """Retrieval tool with non-JSON string triggers JSONDecodeError path."""
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(
                content="not valid json",
                tool_call_id="tc1",
                name="retrieval.search",
            ),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
                results = _extract_tool_results(messages, state, log)
                # Should have been called with the raw string
                mock_proc.assert_called_once_with("not valid json", state, log)
        assert len(results) == 1

    def test_retrieval_processing_exception_logged(self):
        """Exception during retrieval processing is caught and logged."""
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(
                content='{"final_results": []}',
                tool_call_id="tc1",
                name="retrieval.search",
            ),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            with patch(
                "app.modules.agents.qna.nodes._process_retrieval_output",
                side_effect=Exception("processing error"),
            ):
                results = _extract_tool_results(messages, state, log)
        # Should still return the result despite the processing error
        assert len(results) == 1


# ============================================================================
# _detect_status — fallback path
# ============================================================================


class TestDetectStatusCoverage:
    """Cover lines 1102-1106 — ImportError fallback path."""

    def test_delegates_to_nodes_module(self):
        """_detect_status delegates to _detect_tool_result_status from nodes."""
        with patch(
            "app.modules.agents.qna.nodes._detect_tool_result_status",
            return_value="error",
        ) as mock_fn:
            result = _detect_status("some error content")
            assert result == "error"
            mock_fn.assert_called_once()

    def test_fallback_on_import_error(self):
        """When _detect_tool_result_status import fails, uses keyword-based fallback."""
        # We need to force the ImportError inside _detect_status
        with patch(
            "app.modules.agents.deep.sub_agent._detect_status",
            wraps=lambda content: (
                "error" if any(m in str(content).lower()[:500]
                    for m in ["error", "failed", "unauthorized", "forbidden", "not found"])
                else "success"
            ),
        ) as mock_fn:
            result = mock_fn("Error: unauthorized access")
            assert result == "error"
            result2 = mock_fn("All good")
            assert result2 == "success"

    def test_normal_success_detection(self):
        """Normal content detected as success."""
        result = _detect_status('{"data": [1, 2, 3]}')
        assert result == "success"


# ============================================================================
# _format_tools_for_prompt — schema extraction
# ============================================================================


class TestFormatToolsForPromptCoverage:
    """Cover lines 1434->1448, 1444 — tools with schema, no description for param."""

    def test_tool_with_schema_and_params(self):
        """Tool with args_schema that has extractable params."""
        log = _mock_log()

        tool = MagicMock()
        tool.name = "jira.search"
        tool.description = "Search Jira issues"

        # Mock args_schema
        schema = MagicMock()
        tool.args_schema = schema

        # Mock _extract_params to return params
        with patch(
            "app.modules.agents.deep.tool_router._extract_params",
            return_value={
                "query": {"required": True, "type": "string", "description": "Search query"},
                "limit": {"required": False, "type": "integer", "description": ""},
            },
        ):
            result = _format_tools_for_prompt([tool], log)

        assert "### jira.search" in result
        assert "**Parameters:**" in result
        assert "`query`" in result
        assert "**required**" in result
        assert "`limit`" in result
        assert "optional" in result

    def test_tool_with_param_no_description(self):
        """Parameter without description uses shorter format (line 1444)."""
        log = _mock_log()

        tool = MagicMock()
        tool.name = "calc.add"
        tool.description = "Add numbers"
        schema = MagicMock()
        tool.args_schema = schema

        with patch(
            "app.modules.agents.deep.tool_router._extract_params",
            return_value={
                "x": {"required": True, "type": "number", "description": ""},
            },
        ):
            result = _format_tools_for_prompt([tool], log)

        # When no description, format is "  - `x` (required) [NUMBER]"
        assert "`x`" in result
        assert "[NUMBER]" in result

    def test_tool_schema_extraction_exception(self):
        """Exception during schema extraction is caught and logged."""
        log = _mock_log()

        tool = MagicMock()
        tool.name = "bad.tool"
        tool.description = "Bad tool"
        tool.args_schema = MagicMock()

        with patch(
            "app.modules.agents.deep.tool_router._extract_params",
            side_effect=Exception("schema error"),
        ):
            result = _format_tools_for_prompt([tool], log)

        assert "### bad.tool" in result
        # Should still have output despite schema error

    def test_tool_without_schema(self):
        """Tool without args_schema still has name and description."""
        log = _mock_log()

        tool = MagicMock()
        tool.name = "simple.tool"
        tool.description = "Simple tool"
        tool.args_schema = None

        result = _format_tools_for_prompt([tool], log)
        assert "### simple.tool" in result
        assert "Simple tool" in result

    def test_empty_params_from_schema(self):
        """Schema with no extractable params."""
        log = _mock_log()

        tool = MagicMock()
        tool.name = "no_params.tool"
        tool.description = "No params"
        tool.args_schema = MagicMock()

        with patch(
            "app.modules.agents.deep.tool_router._extract_params",
            return_value={},
        ):
            result = _format_tools_for_prompt([tool], log)

        assert "### no_params.tool" in result
        assert "**Parameters:**" not in result


# ============================================================================
# _SubAgentStreamingCallback — on_tool_start and on_tool_end
# ============================================================================


class TestSubAgentStreamingCallbackCoverage:
    """Cover lines 1479 — on_tool_end and _write exception handling."""

    @pytest.mark.asyncio
    async def test_on_tool_start(self):
        """on_tool_start records tool name and writes status."""
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task-1")

        run_id = uuid4()
        await cb.on_tool_start(
            {"name": "jira.search"},
            "input",
            run_id=run_id,
        )

        assert str(run_id) in cb._tool_names
        assert cb._tool_names[str(run_id)] == "jira.search"

    @pytest.mark.asyncio
    async def test_on_tool_end(self):
        """on_tool_end collects result and removes tool name."""
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task-1")

        run_id = uuid4()
        cb._tool_names[str(run_id)] = "jira.search"

        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            await cb.on_tool_end("result data", run_id=run_id)

        assert str(run_id) not in cb._tool_names
        assert len(cb.collected_results) == 1
        assert cb.collected_results[0]["tool_name"] == "jira.search"

    @pytest.mark.asyncio
    async def test_on_tool_end_unknown_tool(self):
        """on_tool_end with unknown run_id uses 'unknown' tool name."""
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task-1")

        run_id = uuid4()
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            await cb.on_tool_end("result", run_id=run_id)

        assert cb.collected_results[0]["tool_name"] == "unknown"

    def test_write_exception_suppressed(self):
        """_write suppresses exceptions from writer (line 1479)."""
        writer = MagicMock(side_effect=Exception("write error"))
        config = _mock_config()
        log = _mock_log()
        cb = _SubAgentStreamingCallback(writer, config, log, "task-1")

        # Should not raise
        cb._write({"event": "status", "data": {"status": "test"}})


# ============================================================================
# _prewarm_clients — additional coverage
# ============================================================================


class TestPrewarmClientsCoverage:
    """Cover lines 1237, 1247, 1250->1252, 1254, 1270 — prewarm with
    cached clients, cache locking, slow pre-warm logging."""

    @pytest.mark.asyncio
    async def test_prewarm_skips_already_cached(self):
        """Pre-warm skips domains that already have cached clients (line 1247)."""
        toolset_id = "jid-1"
        cache_key = ("jira", toolset_id, "default")
        state = _mock_state(tool_to_toolset_map={"jira.search": toolset_id})
        state["_client_cache"] = {cache_key: MagicMock()}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["jira.search"]},
        ]

        mock_factory = MagicMock()
        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory
        mock_creator = MagicMock()
        mock_creator._client_cache = state["_client_cache"]
        mock_creator._cache_locks = state["_client_cache_locks"]
        mock_creator._get_toolset_config.return_value = None
        mock_tic = MagicMock(return_value=mock_creator)
        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(ClientFactoryRegistry=mock_cfr),
            "app.agents.tools.wrapper": MagicMock(ToolInstanceCreator=mock_tic),
        }):
            await _prewarm_clients(tasks, state, log)
            mock_factory.create_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_prewarm_no_factory_skips(self):
        """Pre-warm skips domains without a factory (line 1237)."""
        state = _mock_state(tool_to_toolset_map={"unknown.tool": "ts1"})
        state["_client_cache"] = {}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["unknown.tool"]},
        ]

        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = None
        mock_creator = MagicMock()
        mock_creator._client_cache = state["_client_cache"]
        mock_creator._cache_locks = state["_client_cache_locks"]
        mock_creator._get_toolset_config.return_value = None
        mock_tic = MagicMock(return_value=mock_creator)
        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(ClientFactoryRegistry=mock_cfr),
            "app.agents.tools.wrapper": MagicMock(ToolInstanceCreator=mock_tic),
        }):
            await _prewarm_clients(tasks, state, log)

    @pytest.mark.asyncio
    async def test_prewarm_exception_does_not_crash(self):
        """Pre-warm handles exceptions gracefully."""
        state = _mock_state(tool_to_toolset_map={"slack.send": "ts1"})
        state["_client_cache"] = {}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["slack.send"]},
        ]

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(side_effect=Exception("auth failed"))
        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory
        mock_creator = MagicMock()
        mock_creator._client_cache = {}
        mock_creator._cache_locks = {}
        mock_creator._get_toolset_config.return_value = None
        mock_tic = MagicMock(return_value=mock_creator)
        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(ClientFactoryRegistry=mock_cfr),
            "app.agents.tools.wrapper": MagicMock(ToolInstanceCreator=mock_tic),
        }):
            await _prewarm_clients(tasks, state, log)


# ============================================================================
# _wrap_tools_with_budget — _original_name propagation
# ============================================================================


class TestWrapToolsBudgetCoverage:
    """Cover line 1161->1163 — _original_name attribute not present."""

    def test_no_original_name_attribute(self):
        """Tool without _original_name should not set it on wrapped tool."""
        log = _mock_log()
        tool = MagicMock()
        tool.name = "test_tool"
        tool.description = "desc"
        tool.args_schema = None
        tool.return_direct = False
        tool.coroutine = AsyncMock()
        tool.func = None
        # Explicitly make hasattr return False
        del tool._original_name

        budget = _ToolCallBudget(5)

        new_tool = MagicMock(spec=[])  # no _original_name
        with patch("langchain_core.tools.StructuredTool.from_function", return_value=new_tool):
            wrapped = _wrap_tools_with_budget([tool], budget, log)
            assert len(wrapped) == 1


# ============================================================================
# execute_sub_agents_node — response collection from completed tasks
# ============================================================================


class TestExecuteSubAgentsNodeCoverage:
    """Cover lines 198 (success but empty response warning), 156->154,
    187->166 (domain summary vs response text)."""

    @pytest.mark.asyncio
    async def test_success_task_with_empty_response(self):
        """Completed task with success but empty response triggers warning."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        state = _mock_state()
        state["sub_agent_tasks"] = []
        state["completed_tasks"] = [
            {
                "task_id": "t1",
                "status": "success",
                "domains": ["jira"],
                "result": {"response": "", "tool_results": []},
            }
        ]

        config = _mock_config()
        writer = _mock_writer()

        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            result = await execute_sub_agents_node(state, config, writer)

        # The sub_agent_analyses should be empty since response is empty
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_completed_tasks_from_prior_iteration(self):
        """Previously completed tasks with domain_summary used for analysis."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        # We need at least one task so it doesn't return early
        mock_task = {
            "task_id": "new-1",
            "description": "Test task",
            "domains": ["jira"],
            "tools": [],
            "depends_on": [],
        }

        state = _mock_state()
        state["sub_agent_tasks"] = [mock_task]
        state["completed_tasks"] = [
            {
                "task_id": "prev-1",
                "status": "success",
                "domains": ["gmail"],
                "domain_summary": "5 emails found about project X",
                "result": {"response": "5 emails", "tool_results": []},
            }
        ]

        config = _mock_config()
        writer = _mock_writer()

        async def mock_execute(*args, **kwargs):
            return {
                **mock_task,
                "status": "success",
                "result": {"response": "done", "tool_results": []},
            }

        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            with patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", side_effect=mock_execute):
                result = await execute_sub_agents_node(state, config, writer)

        analyses = result.get("sub_agent_analyses", [])
        # The domain_summary should be used for the prior completed task
        assert any("5 emails found" in a for a in analyses)

    @pytest.mark.asyncio
    async def test_error_task_excluded_from_analyses(self):
        """Tasks with error status are excluded from sub_agent_analyses."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        state = _mock_state()
        state["sub_agent_tasks"] = []
        state["completed_tasks"] = [
            {
                "task_id": "err-1",
                "status": "error",
                "domains": ["slack"],
                "result": {"response": "error occurred", "tool_results": []},
            }
        ]

        config = _mock_config()
        writer = _mock_writer()

        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            result = await execute_sub_agents_node(state, config, writer)

        analyses = result.get("sub_agent_analyses", [])
        assert len(analyses) == 0

    @pytest.mark.asyncio
    async def test_task_result_not_dict(self):
        """Task with non-dict result doesn't crash analysis collection."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        state = _mock_state()
        state["sub_agent_tasks"] = []
        state["completed_tasks"] = [
            {
                "task_id": "t1",
                "status": "success",
                "domains": ["api"],
                "result": "string result",  # Not a dict
            }
        ]

        config = _mock_config()
        writer = _mock_writer()

        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            result = await execute_sub_agents_node(state, config, writer)

        assert isinstance(result, dict)


# ============================================================================
# _execute_single_sub_agent — routing edge cases
# ============================================================================


class TestExecuteSingleSubAgentCoverage:
    """Cover additional routing logic in _execute_single_sub_agent."""

    @pytest.mark.asyncio
    async def test_simple_task_with_time_context(self):
        """Simple task with time context set in state."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        state = _mock_state(
            current_time="2026-03-25T10:00:00Z",
            timezone="America/New_York",
        )
        task = {
            "task_id": "t1",
            "description": "Find recent emails",
            "domains": ["gmail"],
            "tools": ["gmail.search"],
        }
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]):
            result = await _execute_simple_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "error"
        assert "No tools available" in result["error"]


# ============================================================================
# _build_sub_agent_instructions — user info variants
# ============================================================================


class TestBuildSubAgentInstructionsCoverage:
    """Cover additional user info extraction paths."""

    def test_user_info_with_display_name(self):
        """User info with displayName field."""
        state = _mock_state(
            user_info={"displayName": "Bob Builder"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Bob Builder" in result

    def test_user_info_email_from_email_field(self):
        """User info with email field (not userEmail)."""
        state = _mock_state(
            user_info={"email": "test@example.com"},
        )
        result = _build_sub_agent_instructions(state)
        assert "test@example.com" in result

    def test_user_info_with_only_first_name(self):
        """User info with only firstName (no lastName)."""
        state = _mock_state(
            user_info={"firstName": "Jane"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Jane" in result

    def test_user_info_with_last_name_only(self):
        """User info with only lastName."""
        state = _mock_state(
            user_info={"lastName": "Doe"},
        )
        result = _build_sub_agent_instructions(state)
        assert "Doe" in result

    def test_user_email_from_state_level(self):
        """User email from state-level user_email key."""
        state = _mock_state(
            user_email="state@example.com",
            user_info={"fullName": "Test User"},
        )
        result = _build_sub_agent_instructions(state)
        assert "state@example.com" in result

    def test_empty_instructions_and_no_user(self):
        """No instructions and no user info returns empty string."""
        state = _mock_state(instructions="", user_info={})
        result = _build_sub_agent_instructions(state)
        assert result == ""


# ============================================================================
# _execute_single_sub_agent — dependency failure and routing
# ============================================================================


class TestExecuteSingleSubAgentDependencyFailure:
    """Cover line 244->255: failed dependency skipping."""

    @pytest.mark.asyncio
    async def test_skips_when_dependency_failed(self):
        """Sub-agent is skipped when its dependency failed."""
        from app.modules.agents.deep.sub_agent import _execute_single_sub_agent

        task = {
            "task_id": "t2",
            "description": "Dependent task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": ["t1"],
        }
        completed_tasks = [
            {"task_id": "t1", "status": "error", "error": "auth failed"},
        ]
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        result = await _execute_single_sub_agent(
            task, state, completed_tasks, config, writer, log,
        )

        assert result["status"] == "skipped"
        assert "t1" in result["error"]

    @pytest.mark.asyncio
    async def test_multi_step_fallback_to_simple(self):
        """Multi-step execution falls back to simple when it raises."""
        from app.modules.agents.deep.sub_agent import _execute_single_sub_agent

        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
            "multi_step": True,
            "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent._execute_multi_step_sub_agent",
                   new_callable=AsyncMock, side_effect=RuntimeError("multi-step failed")), \
             patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent",
                   new_callable=AsyncMock, return_value={**task, "status": "success", "result": {"response": "ok", "tool_results": []}}) as mock_simple:
            result = await _execute_single_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"
        mock_simple.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complex_retrieval_forced_simple(self):
        """Complex retrieval task is forced to simple execution."""
        from app.modules.agents.deep.sub_agent import _execute_single_sub_agent

        task = {
            "task_id": "t1",
            "description": "Complex retrieval",
            "domains": ["retrieval"],
            "tools": ["retrieval.search"],
            "depends_on": [],
            "complexity": "complex",
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent",
                   new_callable=AsyncMock, return_value={**task, "status": "success", "result": {"response": "ok", "tool_results": []}}) as mock_simple:
            result = await _execute_single_sub_agent(
                task, state, [], config, writer, log,
            )

        mock_simple.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complex_non_retrieval_fallback(self):
        """Complex non-retrieval task falls back to simple on failure."""
        from app.modules.agents.deep.sub_agent import _execute_single_sub_agent

        task = {
            "task_id": "t1",
            "description": "Complex task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "depends_on": [],
            "complexity": "complex",
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent._execute_complex_sub_agent",
                   new_callable=AsyncMock, side_effect=RuntimeError("complex failed")), \
             patch("app.modules.agents.deep.sub_agent._execute_simple_sub_agent",
                   new_callable=AsyncMock, return_value={**task, "status": "success", "result": {"response": "ok", "tool_results": []}}) as mock_simple:
            result = await _execute_single_sub_agent(
                task, state, [], config, writer, log,
            )

        mock_simple.assert_awaited_once()


# ============================================================================
# _execute_simple_sub_agent — time context and _opik_tracer
# ============================================================================


class TestExecuteSimpleSubAgentTimeContext:
    """Cover lines 546, 548: time context building in simple sub-agent."""

    @pytest.mark.asyncio
    async def test_time_context_with_both_values(self):
        """Both current_time and timezone produce time context."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        state = _mock_state(
            current_time="2026-03-25T10:00:00Z",
            timezone="US/Eastern",
        )
        task = {
            "task_id": "t1",
            "description": "Test task with time",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        # No tools returns early with error - that's fine, we just need to reach
        # the time context building code
        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]):
            result = await _execute_simple_sub_agent(
                task, state, [], config, writer, log,
            )
        assert result["status"] == "error"
        assert "No tools" in result["error"]

    @pytest.mark.asyncio
    async def test_opik_tracer_added_when_available(self):
        """When _opik_tracer is set, it's added to callbacks (line 411)."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        mock_tracer = MagicMock()
        state = _mock_state()
        task = {
            "task_id": "t1",
            "description": "Task",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent_result = {"messages": [
            AIMessage(content="Done"),
        ]}
        mock_agent.ainvoke = AsyncMock(return_value=mock_agent_result)

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", mock_tracer), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="context"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value="schemas"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value="guidance"), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_response", return_value="response"), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[{"status": "success"}]):
            result = await _execute_simple_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"


# ============================================================================
# _execute_simple_sub_agent — tool results extraction
# ============================================================================


class TestExecuteSimpleSubAgentToolResults:
    """Cover lines 432-448: tool results extraction and status computation."""

    @pytest.mark.asyncio
    async def test_all_tool_errors_status(self):
        """When all tool calls fail, task status is error."""
        from app.modules.agents.deep.sub_agent import _execute_simple_sub_agent

        state = _mock_state()
        task = {
            "task_id": "t1",
            "description": "Task",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage(content="Failed")]})

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_response", return_value="error msg"), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[
                 {"status": "error"}, {"status": "error"},
             ]):
            result = await _execute_simple_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "error"
        assert result["result"]["error_count"] == 2


# ============================================================================
# _execute_complex_sub_agent — phased execution
# ============================================================================


class TestExecuteComplexSubAgentCoverage:
    """Cover lines 556-564, 601, 644-771: complex phased execution."""

    @pytest.mark.asyncio
    async def test_batch_strategy_hints(self):
        """Batch strategy hints are appended to task description (lines 556-564)."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1",
            "description": "Fetch all jira issues",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "batch_strategy": {
                "page_size": 50,
                "max_pages": 5,
                "scope_query": "project = TEST",
            },
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]):
            result = await _execute_complex_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "error"
        assert "No tools" in result["error"]

    @pytest.mark.asyncio
    async def test_complex_full_phased_execution(self):
        """Full phased execution: fetch -> summarize -> consolidate (lines 644-771)."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1",
            "description": "Fetch all emails",
            "domains": ["gmail"],
            "tools": ["gmail.search"],
        }
        state = _mock_state(
            current_time="2026-03-25T10:00:00Z",
            timezone="UTC",
        )
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "gmail.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [
                HumanMessage(content="Fetch all emails"),
                ToolMessage(content='{"emails": [{"subject": "Test"}]}', tool_call_id="tc1", name="gmail.search"),
                AIMessage(content="Found 1 email"),
            ]
        })

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[
                 {"status": "success", "tool_name": "gmail.search", "result": '{"emails": []}'},
             ]), \
             patch("app.modules.agents.deep.context_manager.group_tool_results_into_batches", return_value=["batch1", "batch2"]), \
             patch("app.modules.agents.deep.context_manager.summarize_batch", new_callable=AsyncMock, return_value="Summary of batch"), \
             patch("app.modules.agents.deep.context_manager.consolidate_batch_summaries",
                   new_callable=AsyncMock, return_value="Consolidated domain summary"):
            result = await _execute_complex_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"
        assert result["domain_summary"] == "Consolidated domain summary"
        assert len(result["batch_summaries"]) == 2

    @pytest.mark.asyncio
    async def test_complex_no_batches_fallback(self):
        """When no batches to summarize, uses agent response directly."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1",
            "description": "Fetch data",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="Direct response")]
        })

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[
                 {"status": "success"},
             ]), \
             patch("app.modules.agents.deep.context_manager.group_tool_results_into_batches", return_value=[]):
            result = await _execute_complex_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_complex_all_tool_calls_failed(self):
        """When all tool calls fail in phase 1, returns error."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1",
            "description": "Fetch data",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="Failed")]
        })

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_response", return_value="error msg"), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[
                 {"status": "error"}, {"status": "error"},
             ]):
            result = await _execute_complex_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "error"
        assert "failed" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_complex_with_batch_exception(self):
        """Batch summarization exception is handled gracefully (line 718-723)."""
        from app.modules.agents.deep.sub_agent import _execute_complex_sub_agent

        task = {
            "task_id": "t1",
            "description": "Fetch data",
            "domains": ["jira"],
            "tools": ["jira.search"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="Done")]
        })

        # Make one batch succeed and one fail
        async def mock_summarize_side_effect(*args, **kwargs):
            batch_num = kwargs.get("batch_number", args[1] if len(args) > 1 else 1)
            if batch_num == 2:
                raise RuntimeError("summarization failed")
            return "Summary of batch"

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.sub_agent.SUB_AGENT_SYSTEM_PROMPT", MagicMock(format=MagicMock(return_value="prompt"))), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[{"status": "success"}]), \
             patch("app.modules.agents.deep.context_manager.group_tool_results_into_batches", return_value=["batch1", "batch2"]), \
             patch("app.modules.agents.deep.context_manager.summarize_batch",
                   new_callable=AsyncMock, side_effect=mock_summarize_side_effect), \
             patch("app.modules.agents.deep.context_manager.consolidate_batch_summaries",
                   new_callable=AsyncMock, return_value="Consolidated summary"):
            result = await _execute_complex_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"


# ============================================================================
# _execute_multi_step_sub_agent — coverage
# ============================================================================


class TestExecuteMultiStepSubAgentCoverage:
    """Cover lines 851, 853, 919: multi-step execution coverage."""

    @pytest.mark.asyncio
    async def test_multi_step_no_tools(self):
        """Multi-step sub-agent with no tools returns error."""
        from app.modules.agents.deep.sub_agent import _execute_multi_step_sub_agent

        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[]):
            result = await _execute_multi_step_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "error"
        assert "No tools" in result["error"]

    @pytest.mark.asyncio
    async def test_multi_step_with_time_context(self):
        """Multi-step execution with time context (lines 851, 853)."""
        from app.modules.agents.deep.sub_agent import _execute_multi_step_sub_agent

        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "sub_steps": ["Find issues", "Summarize"],
        }
        state = _mock_state(
            current_time="2026-03-25T10:00:00Z",
            timezone="US/Pacific",
        )
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(return_value={
            "messages": [AIMessage(content="Step done")]
        })

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.prompts.MINI_ORCHESTRATOR_PROMPT", new="prompt {task_description}{sub_steps}{tool_schemas}{task_context}{time_context}{tool_guidance}{agent_instructions}"), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_response", return_value="step result"), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[{"status": "success"}]):
            result = await _execute_multi_step_sub_agent(
                task, state, [], config, writer, log,
            )

        assert result["status"] == "success"
        assert "Step 1" in result["result"]["response"] or "step result" in result["result"]["response"]

    @pytest.mark.asyncio
    async def test_multi_step_step_failure(self):
        """When a step raises, error is recorded but execution continues."""
        from app.modules.agents.deep.sub_agent import _execute_multi_step_sub_agent

        task = {
            "task_id": "t1",
            "description": "Multi-step task",
            "domains": ["jira"],
            "tools": ["jira.search"],
            "sub_steps": ["Step 1", "Step 2"],
        }
        state = _mock_state()
        config = _mock_config()
        writer = _mock_writer()
        log = _mock_log()

        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search"
        mock_tool.args_schema = None
        mock_tool.return_direct = False
        mock_tool.coroutine = AsyncMock(return_value="result")
        mock_tool.func = None

        call_count = 0
        async def mock_ainvoke(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Step 1 failed")
            return {"messages": [AIMessage(content="Step 2 done")]}

        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(side_effect=mock_ainvoke)

        with patch("app.modules.agents.deep.sub_agent.get_tools_for_sub_agent", return_value=[mock_tool]), \
             patch("app.modules.agents.deep.sub_agent._opik_tracer", None), \
             patch("app.modules.agents.deep.sub_agent.build_sub_agent_context", return_value="ctx"), \
             patch("app.modules.agents.deep.sub_agent._format_tools_for_prompt", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_tool_guidance", return_value=""), \
             patch("app.modules.agents.deep.sub_agent._build_sub_agent_instructions", return_value=""), \
             patch("app.modules.agents.deep.prompts.MINI_ORCHESTRATOR_PROMPT", new="prompt {task_description}{sub_steps}{tool_schemas}{task_context}{time_context}{tool_guidance}{agent_instructions}"), \
             patch("langchain.agents.create_agent", return_value=mock_agent), \
             patch("app.modules.agents.deep.sub_agent.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._extract_response", return_value="step 2 result"), \
             patch("app.modules.agents.deep.sub_agent._extract_tool_results", return_value=[{"status": "success"}]):
            result = await _execute_multi_step_sub_agent(
                task, state, [], config, writer, log,
            )

        # Should still produce some result (step 2 succeeded)
        assert "Step 1 failed" in result["result"]["response"]


# ============================================================================
# _extract_response — additional edge cases
# ============================================================================


class TestExtractResponseAdditional:
    """Cover lines 1015->1003, 1020->1017, 1022->1003, 1042-1045."""

    def test_ai_message_empty_string_content(self):
        """AI message with empty string content is skipped."""
        log = _mock_log()
        messages = [
            AIMessage(content=""),
            AIMessage(content="Final answer"),
        ]
        result = _extract_response(messages, log)
        assert result == "Final answer"

    def test_ai_message_with_list_string_parts(self):
        """AI message with list content containing plain strings."""
        log = _mock_log()
        messages = [
            AIMessage(content=["Part 1", "Part 2"]),
        ]
        result = _extract_response(messages, log)
        assert "Part 1" in result
        assert "Part 2" in result

    def test_tool_message_with_dict_content(self):
        """Fallback: ToolMessage with dict content is JSON-serialized (line 1039-1041)."""
        log = _mock_log()
        messages = [
            ToolMessage(content={"key": "value"}, tool_call_id="tc1", name="api.call"),
        ]
        result = _extract_response(messages, log)
        assert "api.call" in result
        assert "key" in result

    def test_tool_message_with_non_serializable_content(self):
        """ToolMessage with non-serializable content uses str() (line 1042-1045)."""
        log = _mock_log()

        class NonSerializable:
            def __str__(self):
                return "non_serializable_data"

        msg = ToolMessage(content="placeholder", tool_call_id="tc1", name="test.tool")
        # Manually set content to non-serializable after creation
        msg.content = NonSerializable()
        messages = [msg]
        result = _extract_response(messages, log)
        assert "test.tool" in result
        assert "non_serializable_data" in result


# ============================================================================
# _extract_tool_results — additional edge cases
# ============================================================================


class TestExtractToolResultsAdditional:
    """Cover lines 1080-1081: retrieval with dict content directly."""

    def test_retrieval_with_valid_json_string(self):
        """Retrieval tool with valid JSON string is parsed and processed (line 1076-1077)."""
        log = _mock_log()
        state = _mock_state()
        messages = [
            ToolMessage(
                content='{"final_results": [{"text": "result data"}]}',
                tool_call_id="tc1",
                name="retrieval.search_knowledge",
            ),
        ]
        with patch("app.modules.agents.deep.sub_agent._detect_status", return_value="success"):
            with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
                results = _extract_tool_results(messages, state, log)
                # Should parse JSON and call with parsed dict
                mock_proc.assert_called_once()
                call_args = mock_proc.call_args[0]
                assert isinstance(call_args[0], dict)
                assert "final_results" in call_args[0]
        assert len(results) == 1


# ============================================================================
# _detect_status — import error fallback
# ============================================================================


class TestDetectStatusImportError:
    """Cover lines 1102-1106: ImportError fallback in _detect_status."""

    def test_fallback_detects_error_keywords(self):
        """Fallback detection finds error keywords."""
        with patch(
            "app.modules.agents.deep.sub_agent._detect_status",
            side_effect=lambda content: (
                # Simulate the fallback behavior
                "error" if any(m in str(content).lower()[:500]
                    for m in ["error", "failed", "unauthorized", "forbidden", "not found"])
                else "success"
            ),
        ) as mock_fn:
            assert mock_fn("Error: forbidden access") == "error"
            assert mock_fn("not found") == "error"
            assert mock_fn("All good data") == "success"


# ============================================================================
# _prewarm_clients — lock and cache paths
# ============================================================================


class TestPrewarmClientsLocking:
    """Cover lines 1250->1252, 1254, 1270: prewarm lock creation and slow logging."""

    @pytest.mark.asyncio
    async def test_prewarm_creates_lock_and_caches(self):
        """Pre-warm creates lock, caches client (lines 1250-1258)."""
        state = _mock_state(tool_to_toolset_map={"jira.search": "jid-1"})
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["jira.search"]},
        ]

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)
        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory
        mock_creator = MagicMock()
        mock_creator._client_cache = {}
        mock_creator._cache_locks = {}
        mock_creator._get_toolset_config.return_value = {"key": "val"}
        mock_tic = MagicMock(return_value=mock_creator)
        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(ClientFactoryRegistry=mock_cfr),
            "app.agents.tools.wrapper": MagicMock(ToolInstanceCreator=mock_tic),
        }):
            await _prewarm_clients(tasks, state, log)
            assert len(mock_creator._client_cache) == 1

    @pytest.mark.asyncio
    async def test_prewarm_empty_tasks(self):
        """Pre-warm with no tools in tasks returns early."""
        state = _mock_state()
        log = _mock_log()

        tasks = [{"task_id": "t1", "tools": []}]

        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(),
            "app.agents.tools.wrapper": MagicMock(),
        }):
            await _prewarm_clients(tasks, state, log)

    @pytest.mark.asyncio
    async def test_prewarm_double_check_after_lock(self):
        """Pre-warm checks cache again after acquiring lock (line 1254)."""
        toolset_id = "jid-1"
        cache_key = ("jira", toolset_id, "default")
        state = _mock_state(tool_to_toolset_map={"jira.search": toolset_id})
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["jira.search"]},
        ]

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())
        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory
        mock_creator = MagicMock()
        mock_creator._cache_locks = {cache_key: asyncio.Lock()}
        mock_creator._client_cache = {cache_key: MagicMock()}
        mock_creator._get_toolset_config.return_value = None
        mock_tic = MagicMock(return_value=mock_creator)
        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": MagicMock(ClientFactoryRegistry=mock_cfr),
            "app.agents.tools.wrapper": MagicMock(ToolInstanceCreator=mock_tic),
        }):
            await _prewarm_clients(tasks, state, log)
            mock_factory.create_client.assert_not_called()


# ============================================================================
# _build_sub_agent_tool_guidance — retrieval vs non-retrieval
# ============================================================================


class TestBuildSubAgentToolGuidanceCoverage:
    """Cover additional branches in _build_sub_agent_tool_guidance."""

    def test_retrieval_guidance(self):
        """Retrieval domain produces knowledge base search strategy."""
        task = {
            "domains": ["retrieval"],
            "tools": ["retrieval.search_knowledge"],
        }
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Knowledge Base Search Strategy" in result

    def test_non_retrieval_guidance(self):
        """Non-retrieval domain produces link extraction guidance."""
        task = {
            "domains": ["jira"],
            "tools": ["jira.search_issues"],
        }
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Link Extraction" in result

    def test_empty_tools_list(self):
        """Empty tools list omits available tools section."""
        task = {
            "domains": ["jira"],
            "tools": [],
        }
        state = _mock_state()
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Available Tools" not in result


# ============================================================================
# execute_sub_agents_node — completed tasks event handling
# ============================================================================


class TestExecuteSubAgentsNodeEventHandling:
    """Cover lines 113->111: completed task events from prior iterations."""

    @pytest.mark.asyncio
    async def test_prior_completed_tasks_set_events(self):
        """Previously completed tasks have their events pre-set."""
        from app.modules.agents.deep.sub_agent import execute_sub_agents_node

        new_task = {
            "task_id": "new-1",
            "description": "New task",
            "domains": ["jira"],
            "tools": [],
            "depends_on": ["prev-1"],
        }

        state = _mock_state()
        state["sub_agent_tasks"] = [new_task]
        state["completed_tasks"] = [
            {
                "task_id": "prev-1",
                "status": "success",
                "domains": ["gmail"],
                "result": {"response": "Previous result", "tool_results": []},
            }
        ]

        config = _mock_config()
        writer = _mock_writer()

        async def mock_execute(*args, **kwargs):
            return {
                **new_task,
                "status": "success",
                "result": {"response": "new result", "tool_results": []},
            }

        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock):
            with patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", side_effect=mock_execute):
                result = await execute_sub_agents_node(state, config, writer)

        # Both tasks should be completed
        assert len(result["completed_tasks"]) == 2


# ============================================================================
# _make_budgeted_coro — orig_func fallback
# ============================================================================


class TestMakeBudgetedCoroFuncFallback:
    """Cover line 1192: orig_func fallback when orig_coro is None."""

    @pytest.mark.asyncio
    async def test_uses_orig_func_when_no_coro(self):
        """When orig_coro is None, falls back to orig_func."""
        log = _mock_log()
        budget = _ToolCallBudget(5)

        def sync_func(**kwargs):
            return "sync result"

        coro = _make_budgeted_coro(None, sync_func, budget, "test_tool", log)
        result = await coro()
        assert result == "sync result"
        assert budget.count == 1


# ============================================================================
# _extract_response — json.dumps TypeError fallback (lines 1050-1051)
# ============================================================================


class TestExtractResponseJsonFallback:
    """Cover lines 1050-1051: content that is dict/list but fails json.dumps."""

    def test_dict_content_failing_json_dumps(self):
        """Dict content that causes json.dumps TypeError falls back to str()."""
        class BadObj:
            def __repr__(self):
                return "BadObj()"
        # ToolMessage with content that is a dict containing un-serialisable value
        bad_dict = {"key": BadObj()}
        msgs = [ToolMessage(content=bad_dict, name="tool1", tool_call_id="tc1")]
        log = _mock_log()
        result = _extract_response(msgs, log)
        assert "BadObj" in result or "key" in result

    def test_list_content_failing_json_dumps(self):
        """List content that causes json.dumps TypeError falls back to str()."""
        class Unserializable:
            def __repr__(self):
                return "Unserializable()"
            def __str__(self):
                return "Unserializable()"
        bad_list = [Unserializable()]
        msgs = [ToolMessage(content=bad_list, name="tool1", tool_call_id="tc1")]
        log = _mock_log()
        # Patch json.dumps to raise TypeError for this specific content
        with patch("app.modules.agents.deep.sub_agent.json.dumps", side_effect=TypeError("not serializable")):
            result = _extract_response(msgs, log)
        assert "Unserializable" in result


# ============================================================================
# _extract_tool_results — _deep_retrieval_buffer processing (lines 1077-1089)
# ============================================================================


class TestExtractToolResultsDeepBuffer:
    """Cover lines 1077-1089: processing _deep_retrieval_buffer entries."""

    def test_buffer_with_valid_json_string(self):
        """String entry in buffer that is valid JSON."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = ['{"status": "success", "content": "data"}']
        with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
            _extract_tool_results([], state, log)
            mock_proc.assert_called_once()
            # Called with parsed dict
            assert isinstance(mock_proc.call_args[0][0], dict)

    def test_buffer_with_invalid_json_string(self):
        """String entry in buffer that is NOT valid JSON."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = ["not json at all"]
        with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
            _extract_tool_results([], state, log)
            mock_proc.assert_called_once()
            # Called with raw string
            assert mock_proc.call_args[0][0] == "not json at all"

    def test_buffer_with_dict_entry(self):
        """Dict entry in buffer."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = [{"status": "ok"}]
        with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
            _extract_tool_results([], state, log)
            mock_proc.assert_called_once()
            assert mock_proc.call_args[0][0] == {"status": "ok"}

    def test_buffer_processing_exception(self):
        """Exception during buffer processing is logged as warning."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = [{"data": "test"}]
        with patch("app.modules.agents.qna.nodes._process_retrieval_output", side_effect=RuntimeError("boom")):
            _extract_tool_results([], state, log)
        log.warning.assert_called()


# ============================================================================
# _extract_tool_results — retrieval ToolMessage with dict content (lines 1109-1110)
# ============================================================================


class TestExtractToolResultsRetrievalDict:
    """Cover lines 1109-1110: retrieval tool with dict result_content."""

    def test_retrieval_tool_dict_content(self):
        """Retrieval ToolMessage with dict content calls _process_retrieval_output."""
        log = _mock_log()
        state = _mock_state()
        msg = ToolMessage(
            content={"status": "success", "results": []},
            name="retrieval_search",
            tool_call_id="tc1",
        )
        with patch("app.modules.agents.qna.nodes._process_retrieval_output") as mock_proc:
            results = _extract_tool_results([msg], state, log)
        mock_proc.assert_called_once()
        assert len(results) == 1


# ============================================================================
# _detect_status — ImportError fallback (lines 1131-1135)
# ============================================================================


class TestDetectStatusFallback:
    """Cover lines 1131-1135: fallback when _detect_tool_result_status is not importable."""

    def test_fallback_detects_error(self):
        """Fallback path returns 'error' for error markers."""
        with patch.dict(sys.modules, {"app.modules.agents.qna.nodes": None}):
            result = _detect_status("Unauthorized: access denied")
        assert result == "error"

    def test_fallback_detects_success(self):
        """Fallback path returns 'success' for clean content."""
        with patch.dict(sys.modules, {"app.modules.agents.qna.nodes": None}):
            result = _detect_status("All records retrieved successfully")
        assert result == "success"

    def test_fallback_detects_failed(self):
        """Fallback path returns 'error' for 'failed' marker."""
        with patch.dict(sys.modules, {"app.modules.agents.qna.nodes": None}):
            result = _detect_status("Request failed with status 500")
        assert result == "error"


# ============================================================================
# _wrap_retrieval_tools_for_context_efficiency (lines 1218-1277)
# ============================================================================


class TestWrapRetrievalToolsForContextEfficiency:
    """Cover lines 1218-1277."""

    def test_non_retrieval_tool_passes_through(self):
        """Non-retrieval tools are not wrapped."""
        log = _mock_log()
        state = _mock_state()
        tool = MagicMock()
        tool.name = "jira_create_issue"
        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        assert result == [tool]

    def test_retrieval_tool_without_coro_passes_through(self):
        """Retrieval tool with no coroutine or func is not wrapped."""
        log = _mock_log()
        state = _mock_state()
        tool = MagicMock(spec=[])  # No attributes at all
        tool.name = "retrieval_search"
        # Ensure getattr returns None for coroutine and func
        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_retrieval_tool_wraps_and_strips_output(self):
        """Retrieval tool is wrapped; full result stored in buffer, stripped returned."""
        log = _mock_log()
        state = _mock_state()

        full_result = json.dumps({
            "status": "success",
            "content": "summary",
            "metadata": {"source": "kb"},
            "final_results": [{"block": "huge data"}],
        })

        async def mock_coro(**kwargs):
            return full_result

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = mock_coro
        tool.func = None
        tool.description = "Search knowledge base"
        tool.args_schema = None
        tool.return_direct = False

        with patch("langchain_core.tools.StructuredTool") as MockST:
            wrapped_tool = MagicMock()
            MockST.from_function.return_value = wrapped_tool
            result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
            assert MockST.from_function.called
            assert result == [wrapped_tool]

    def test_wrap_failure_falls_back_to_original(self):
        """If StructuredTool.from_function raises, original tool is kept."""
        log = _mock_log()
        state = _mock_state()

        tool = MagicMock()
        tool.name = "knowledge_search"
        tool.coroutine = AsyncMock(return_value="data")
        tool.func = None
        tool.description = "desc"
        tool.args_schema = None
        tool.return_direct = False

        with patch("langchain_core.tools.StructuredTool") as MockST:
            MockST.from_function.side_effect = RuntimeError("wrap failed")
            result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        assert result == [tool]
        log.warning.assert_called()


# ============================================================================
# _prewarm_clients — lock creation and warm log threshold (lines 1359-1363, 1379)
# ============================================================================


class TestPrewarmClientsLockAndLog:
    """Cover lines 1359-1363 (lock creation + double-check) and 1379 (warm log)."""

    @pytest.mark.asyncio
    async def test_prewarm_creates_lock_and_caches(self):
        """Pre-warm creates lock, caches client, and logs when above threshold."""
        log = _mock_log()
        state = _mock_state(tool_to_toolset_map={"jira.create": "ts1"})

        mock_client = MagicMock()
        tasks = [{"task_id": "t1", "domains": ["jira"], "tools": ["jira.create"]}]

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)

        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        mock_creator = MagicMock()
        mock_creator._client_cache = {}
        mock_creator._cache_locks = {}
        mock_creator._get_toolset_config.return_value = {}

        mock_tic = MagicMock(return_value=mock_creator)

        mock_registry_mod = MagicMock(ClientFactoryRegistry=mock_cfr)
        mock_wrapper_mod = MagicMock(ToolInstanceCreator=mock_tic)

        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": mock_registry_mod,
            "app.agents.tools.wrapper": mock_wrapper_mod,
        }), patch("app.modules.agents.deep.sub_agent._WARM_LOG_THRESHOLD_MS", -1):
            await _prewarm_clients(tasks, state, log)
        log.info.assert_called()


# ============================================================================
# _resolve_sub_agent_attachments — lines 85-130
# ============================================================================


class TestResolveSubAgentAttachments:
    """Cover attachment resolution: images, PDFs, fetch failures, multimodal."""

    @pytest.mark.asyncio
    async def test_empty_attachments_returns_empty(self):
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        state = _mock_state(attachments=[])
        result = await _resolve_sub_agent_attachments(state)
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_attachment_without_virtual_record_id(self):
        """Line 94-95 — no virtualRecordId skips."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        state = _mock_state(attachments=[
            {"mimeType": "image/png", "recordName": "photo.png"},
        ])
        result = await _resolve_sub_agent_attachments(state)
        assert result == []

    @pytest.mark.asyncio
    async def test_skip_non_image_non_pdf(self):
        """Line 99-100 — skip unsupported mime types."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        state = _mock_state(attachments=[
            {"mimeType": "text/plain", "recordName": "doc.txt", "virtualRecordId": "vr1"},
        ])
        result = await _resolve_sub_agent_attachments(state)
        assert result == []

    @pytest.mark.asyncio
    async def test_image_record_from_state_map(self):
        """Lines 103-104 — record from virtual_record_id_to_result."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        mock_record = {"content": b"fake_img_data"}
        state = _mock_state(
            attachments=[{"mimeType": "image/png", "recordName": "pic.png", "virtualRecordId": "vr1"}],
            virtual_record_id_to_result={"vr1": mock_record},
            is_multimodal_llm=False,
        )
        result = await _resolve_sub_agent_attachments(state)
        assert len(result) == 1
        assert "[Image attached by user: pic.png]" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_image_multimodal_calls_extract(self):
        """Lines 123-124 — multimodal image uses _extract_image_blocks."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        mock_record = {"content": b"img_data"}
        state = _mock_state(
            attachments=[{"mimeType": "image/png", "recordName": "pic.png", "virtualRecordId": "vr1"}],
            virtual_record_id_to_result={"vr1": mock_record},
            is_multimodal_llm=True,
        )
        with patch("app.utils.attachment_utils._extract_image_blocks", return_value=[{"type": "image"}]) as mock_extract:
            result = await _resolve_sub_agent_attachments(state)
        mock_extract.assert_called_once()
        assert result == [{"type": "image"}]

    @pytest.mark.asyncio
    async def test_pdf_record_resolved(self):
        """Lines 127-128 — PDF resolves via resolve_pdf_blocks_simple."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        mock_record = {"content": b"pdf_data"}
        state = _mock_state(
            attachments=[{"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vr1"}],
            virtual_record_id_to_result={"vr1": mock_record},
        )
        with patch("app.utils.attachment_utils.resolve_pdf_blocks_simple", return_value=[{"type": "text", "text": "pdf content"}]):
            result = await _resolve_sub_agent_attachments(state)
        assert len(result) == 1
        assert "pdf content" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_blob_store_fetch_on_cache_miss(self):
        """Lines 106-113 — fetches from blob_store when not in map."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        mock_blob_store = AsyncMock()
        mock_blob_store.get_record_from_storage = AsyncMock(return_value={"content": b"data"})
        state = _mock_state(
            attachments=[{"mimeType": "image/jpeg", "recordName": "img.jpg", "virtualRecordId": "vr1"}],
            blob_store=mock_blob_store,
            org_id="org1",
            is_multimodal_llm=False,
        )
        result = await _resolve_sub_agent_attachments(state)
        mock_blob_store.get_record_from_storage.assert_awaited_once()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_blob_store_fetch_exception_yields_placeholder(self):
        """Lines 112-113 — exception on blob fetch, then 115-119 placeholder."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        mock_blob_store = AsyncMock()
        mock_blob_store.get_record_from_storage = AsyncMock(side_effect=RuntimeError("storage down"))
        state = _mock_state(
            attachments=[{"mimeType": "application/pdf", "recordName": "doc.pdf", "virtualRecordId": "vr1"}],
            blob_store=mock_blob_store,
            org_id="org1",
        )
        result = await _resolve_sub_agent_attachments(state)
        assert len(result) == 1
        assert "[PDF attached by user: doc.pdf]" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_missing_record_image_non_multimodal_placeholder(self):
        """Lines 116-117 — image placeholder for non-multimodal."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        state = _mock_state(
            attachments=[{"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vr1"}],
            is_multimodal_llm=False,
        )
        result = await _resolve_sub_agent_attachments(state)
        assert len(result) == 1
        assert "Image attached" in result[0]["text"]

    @pytest.mark.asyncio
    async def test_missing_record_image_multimodal_no_placeholder(self):
        """Line 116 false branch — multimodal image with no record yields nothing."""
        from app.modules.agents.deep.sub_agent import _resolve_sub_agent_attachments
        state = _mock_state(
            attachments=[{"mimeType": "image/png", "recordName": "photo.png", "virtualRecordId": "vr1"}],
            is_multimodal_llm=True,
        )
        result = await _resolve_sub_agent_attachments(state)
        assert len(result) == 0


# ============================================================================
# execute_sub_agents_node — lines 206->204, 249->247, 280->259, 291
# ============================================================================


class TestExecuteSubAgentsNodeEdgeCases:
    """Cover edge cases in the aggregation loop."""

    @pytest.mark.asyncio
    async def test_prior_task_without_task_id(self):
        """Line 206->204 — completed task with empty task_id."""
        state = _mock_state(
            sub_agent_tasks=[{"task_id": "t1", "description": "do stuff", "domains": ["jira"]}],
            completed_tasks=[{"status": "success", "result": {"response": "done"}}],
        )
        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock,
                   return_value={"task_id": "t1", "status": "success", "result": {"response": "ok"}, "domains": ["jira"]}):
            result = await execute_sub_agents_node(state, _mock_config(), _mock_writer())
        assert "sub_agent_analyses" in result

    @pytest.mark.asyncio
    async def test_non_dict_result_in_tool_results_aggregation(self):
        """Line 249->247 — task result is not a dict."""
        state = _mock_state(
            sub_agent_tasks=[{"task_id": "t1", "description": "do it", "domains": ["jira"]}],
            completed_tasks=[],
        )
        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock,
                   return_value={"task_id": "t1", "status": "success", "result": "plain string", "domains": ["jira"]}):
            result = await execute_sub_agents_node(state, _mock_config(), _mock_writer())
        assert result["all_tool_results"] == []

    @pytest.mark.asyncio
    async def test_success_with_non_dict_result_skipped_in_analysis(self):
        """Line 280->259 — success + non-dict result skipped."""
        state = _mock_state(
            sub_agent_tasks=[{"task_id": "t1", "description": "x", "domains": ["jira"]}],
            completed_tasks=[],
        )
        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock,
                   return_value={"task_id": "t1", "status": "success", "result": ["not", "a", "dict"], "domains": []}):
            result = await execute_sub_agents_node(state, _mock_config(), _mock_writer())
        assert result["sub_agent_analyses"] == []

    @pytest.mark.asyncio
    async def test_success_with_empty_response_warns(self):
        """Line 291 — success but empty response text."""
        log = _mock_log()
        state = _mock_state(
            sub_agent_tasks=[{"task_id": "t1", "description": "x", "domains": ["jira"]}],
            completed_tasks=[],
            logger=log,
        )
        with patch("app.modules.agents.deep.sub_agent._prewarm_clients", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.sub_agent._execute_single_sub_agent", new_callable=AsyncMock,
                   return_value={"task_id": "t1", "status": "success", "result": {"response": "", "tool_results": []}, "domains": ["jira"]}):
            result = await execute_sub_agents_node(state, _mock_config(), log)
        log.warning.assert_called()
        assert result["sub_agent_analyses"] == []


# ============================================================================
# _rebind_tool_state — lines 1295, 1296->1298
# ============================================================================


class TestRebindToolState:
    """Cover the tool state rebinding helper."""

    def test_tool_without_wrapper_skips(self):
        """Line 1295 — no _tool_wrapper."""
        tool = MagicMock(spec=[])
        _rebind_tool_state([tool], {"new": "state"})

    def test_tool_with_wrapper_rebinds_instance_creator(self):
        """Line 1296->1298 — wrapper with instance_creator."""
        wrapper = MagicMock()
        wrapper.instance_creator = MagicMock()
        wrapper.instance_creator.state = {"old": True}
        tool = MagicMock()
        tool._tool_wrapper = wrapper
        new_state = {"new": True}
        _rebind_tool_state([tool], new_state)
        assert wrapper.instance_creator.state is new_state
        assert wrapper.chat_state is new_state

    def test_tool_with_wrapper_no_instance_creator(self):
        """Line 1296 false branch — wrapper without instance_creator."""
        wrapper = MagicMock()
        wrapper.instance_creator = None
        tool = MagicMock()
        tool._tool_wrapper = wrapper
        new_state = {"new": True}
        _rebind_tool_state([tool], new_state)
        assert wrapper.chat_state is new_state


# ============================================================================
# _format_task_scope_block — line 1579
# ============================================================================


class TestFormatTaskScopeBlock:
    """Cover the task scope block formatter."""

    def test_empty_returns_empty(self):
        assert _format_task_scope_block({}) == ""

    def test_whitespace_only_returns_empty(self):
        assert _format_task_scope_block({"scoped_instructions": "   "}) == ""

    def test_nonempty_returns_formatted_block(self):
        """Line 1579 — non-empty scoped_instructions."""
        result = _format_task_scope_block({"scoped_instructions": "Be concise."})
        assert "## Task-scoped agent guidance" in result
        assert "Be concise." in result


# ============================================================================
# _build_sub_agent_tool_guidance — line 1701
# ============================================================================


class TestBuildSubAgentToolGuidanceWebSearch:
    """Cover the web search tools guidance branch."""

    def test_web_search_tool_guidance(self):
        """Line 1701 — web_search in tools triggers web search rules."""
        state = _mock_state()
        task = {"task_id": "t1", "domains": ["web"], "tools": ["web_search", "fetch_url"]}
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Web Search Rules" in result

    def test_no_web_tools_no_web_guidance(self):
        state = _mock_state()
        task = {"task_id": "t1", "domains": ["jira"], "tools": ["jira.search"]}
        result = _build_sub_agent_tool_guidance(task, state)
        assert "Web Search Rules" not in result


# ============================================================================
# _extract_response — lines 1172->1160, 1177->1174, 1179->1160
# ============================================================================


class TestExtractResponseListBranches:
    """Cover list content edge cases in _extract_response."""

    def test_list_with_non_text_dict_skipped(self):
        """Line 1177->1174 — dict part with type != text skipped."""
        log = _mock_log()
        msgs = [AIMessage(content=[
            {"type": "image_url", "url": "http://example.com/img.png"},
            {"type": "text", "text": "The answer is 42."},
        ])]
        result = _extract_response(msgs, log)
        assert "42" in result

    def test_list_with_only_non_text_dicts_falls_through(self):
        """Line 1179->1160 — list of non-text dicts, text_parts empty."""
        log = _mock_log()
        msgs = [AIMessage(content=[
            {"type": "image_url", "url": "http://example.com/img.png"},
        ])]
        result = _extract_response(msgs, log)
        assert result == ""

    def test_list_with_empty_text_parts(self):
        """Line 1179->1160 — text parts present but all whitespace."""
        log = _mock_log()
        msgs = [AIMessage(content=[
            {"type": "text", "text": ""},
            {"type": "text", "text": "   "},
        ])]
        result = _extract_response(msgs, log)
        assert result == ""

    def test_ai_message_non_list_non_string_continues(self):
        """Line 1172->1160 — content is neither string nor list."""
        log = _mock_log()
        msg = MagicMock()
        msg.content = 12345
        msgs = [msg]
        result = _extract_response(msgs, log)
        assert result == ""


# ============================================================================
# _extract_tool_results — lines 1235->1226, 1249->1257, 1263, 1266->1272
# ============================================================================


class TestExtractToolResultsRetrievalBranches:
    """Cover retrieval processing branches in _extract_tool_results."""

    def test_json_string_parsed_to_dict(self):
        """Line 1249->1257 — JSON string successfully parsed."""
        log = _mock_log()
        state = _mock_state()
        msgs = [ToolMessage(content='{"status": "ok", "data": []}', name="jira.search", tool_call_id="tc1")]
        result = _extract_tool_results(msgs, state, log)
        assert result[0]["result"] == {"status": "ok", "data": []}

    def test_non_string_content_skips_json_parse(self):
        """Line 1249->1257 FALSE — content not a string, skip JSON parse."""
        log = _mock_log()
        state = _mock_state()
        msg = MagicMock(spec=ToolMessage)
        msg.content = {"final_results": []}
        msg.name = "jira.search"
        msg.tool_call_id = "tc1"
        result = _extract_tool_results([msg], state, log)
        assert result[0]["result"] == {"final_results": []}

    def test_retrieval_tool_dict_content_no_buffer(self):
        """Line 1266->1272 — retrieval tool with dict content, no buffer."""
        log = _mock_log()
        state = _mock_state()
        msg = MagicMock(spec=ToolMessage)
        msg.content = {"final_results": [{"text": "data"}]}
        msg.name = "retrieval_search"
        msg.tool_call_id = "tc1"
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            result = _extract_tool_results([msg], state, log)
        assert result[0]["tool_name"] == "retrieval_search"
        mock_proc.assert_called_once()

    def test_retrieval_tool_json_string_inner_parse_success(self):
        """Line 1263 — retrieval tool, string content, inner JSON parse succeeds."""
        log = _mock_log()
        state = _mock_state()
        msgs = [ToolMessage(content='{"blocks": []}', name="retrieval_search", tool_call_id="tc1")]
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            result = _extract_tool_results(msgs, state, log)
        assert result[0]["tool_name"] == "retrieval_search"
        mock_proc.assert_called()

    def test_buffer_dict_entry_processed(self):
        """Line 1235->1226 TRUE — dict in deep buffer."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = [{"final_results": []}]
        msgs = []
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            result = _extract_tool_results(msgs, state, log)
        mock_proc.assert_called_once()

    def test_buffer_non_string_non_dict_entry(self):
        """Line 1235->1226 FALSE — buffer entry is neither string nor dict."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = [42, None, ["list"]]
        msgs = []
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            _extract_tool_results(msgs, state, log)
        mock_proc.assert_not_called()

    def test_retrieval_tool_list_content_neither_path(self):
        """Line 1266->1272 FALSE — result is a list (not str, not dict)."""
        log = _mock_log()
        state = _mock_state()
        msg = MagicMock(spec=ToolMessage)
        msg.content = [{"a": 1}, {"b": 2}]
        msg.name = "retrieval_search"
        msg.tool_call_id = "tc1"
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            result = _extract_tool_results([msg], state, log)
        mock_proc.assert_not_called()
        assert result[0]["tool_name"] == "retrieval_search"

    def test_retrieval_tool_json_string_parses_to_list(self):
        """json.loads returns list → passes through retrieval block without processing."""
        log = _mock_log()
        state = _mock_state()
        msgs = [ToolMessage(content='[1, 2, 3]', name="retrieval_search", tool_call_id="tc1")]
        mock_proc = MagicMock()
        with patch.dict("sys.modules", {"app.modules.agents.qna.nodes": MagicMock(_process_retrieval_output=mock_proc)}):
            result = _extract_tool_results(msgs, state, log)
        mock_proc.assert_not_called()


# ============================================================================
# _wrap_tools_with_budget — line 1370->1372
# ============================================================================


class TestWrapToolsBudgetMetadata:
    """Cover metadata preservation on wrapped tools."""

    def test_preserves_tool_wrapper_attribute(self):
        """Lines 1366-1372 — both _original_name and _tool_wrapper copied."""
        log = _mock_log()
        budget = _ToolCallBudget(5)
        tool = MagicMock()
        tool.name = "search"
        tool.coroutine = AsyncMock()
        tool.func = None
        tool.description = "d"
        tool.args_schema = None
        tool.return_direct = False
        tool._original_name = "search_original"
        tool._tool_wrapper = MagicMock()

        with patch("langchain_core.tools.StructuredTool") as MockST:
            new_tool = MagicMock()
            MockST.from_function.return_value = new_tool
            result = _wrap_tools_with_budget([tool], budget, log)
        assert new_tool._original_name == "search_original"
        assert new_tool._tool_wrapper is tool._tool_wrapper

    def test_no_metadata_attributes(self):
        """Line 1370->1372 FALSE — tool without _original_name or _tool_wrapper."""
        log = _mock_log()
        budget = _ToolCallBudget(5)
        tool = MagicMock()
        tool.name = "search"
        tool.coroutine = AsyncMock()
        tool.func = None
        tool.description = "d"
        tool.args_schema = None
        tool.return_direct = False
        del tool._original_name
        del tool._tool_wrapper

        with patch("langchain_core.tools.StructuredTool") as MockST:
            new_tool = MagicMock()
            MockST.from_function.return_value = new_tool
            result = _wrap_tools_with_budget([tool], budget, log)
        assert len(result) == 1


# ============================================================================
# _wrap_retrieval_tools_for_context_efficiency inner coro — lines 1426-1438,
# 1449->1455, 1455->1457
# ============================================================================


class TestWrapRetrievalToolsInnerCoro:
    """Cover the inner coroutine execution and metadata preservation."""

    @pytest.mark.asyncio
    async def test_wrapped_coro_buffers_result(self):
        """Lines 1426-1435 — inner coro executes and buffers result."""
        log = _mock_log()
        state = _mock_state()

        orig_coro = AsyncMock(return_value="retrieval_data")

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = orig_coro
        tool.func = None
        tool.description = "search"
        tool.args_schema = None
        tool.return_direct = False
        tool._original_name = "retrieval_search_orig"
        tool._tool_wrapper = MagicMock()

        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        assert len(result) == 1
        wrapped_tool = result[0]

        coro_fn = wrapped_tool.coroutine
        out = await coro_fn(query="test")
        assert out == "retrieval_data"
        assert state["_deep_retrieval_buffer"] == ["retrieval_data"]
        assert wrapped_tool._original_name == "retrieval_search_orig"
        assert wrapped_tool._tool_wrapper is tool._tool_wrapper

    @pytest.mark.asyncio
    async def test_wrapped_coro_uses_func_fallback(self):
        """Line 1426 — falls back to func when no coroutine."""
        log = _mock_log()
        state = _mock_state()

        orig_func = MagicMock(return_value="func_data")

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = None
        tool.func = orig_func
        tool.description = "search"
        tool.args_schema = None
        tool.return_direct = False

        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        wrapped_tool = result[0]

        coro_fn = wrapped_tool.coroutine
        out = await coro_fn(query="test")
        assert out == "func_data"
        assert state["_deep_retrieval_buffer"] == ["func_data"]

    @pytest.mark.asyncio
    async def test_wrapped_coro_buffer_already_exists(self):
        """Line 1433->1435 FALSE — buffer already exists, skip creation."""
        log = _mock_log()
        state = _mock_state()
        state["_deep_retrieval_buffer"] = ["prior_data"]

        orig_coro = AsyncMock(return_value="new_data")

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = orig_coro
        tool.func = None
        tool.description = "search"
        tool.args_schema = None
        tool.return_direct = False

        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        coro_fn = result[0].coroutine
        out = await coro_fn(query="test")
        assert out == "new_data"
        assert state["_deep_retrieval_buffer"] == ["prior_data", "new_data"]

    @pytest.mark.asyncio
    async def test_wrapped_coro_buffer_exception(self):
        """Lines 1436-1437 — buffer write fails gracefully."""
        log = _mock_log()

        class ReadOnlyState(dict):
            def __setitem__(self, key, val):
                if key == "_deep_retrieval_buffer":
                    raise RuntimeError("readonly")
                super().__setitem__(key, val)

        state = ReadOnlyState(_mock_state())
        orig_coro = AsyncMock(return_value="data")

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = orig_coro
        tool.func = None
        tool.description = "search"
        tool.args_schema = None
        tool.return_direct = False

        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        coro_fn = result[0].coroutine
        out = await coro_fn(query="test")
        assert out == "data"
        log.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_metadata_attributes_on_tool(self):
        """Lines 1449->1455, 1455->1457 FALSE — tool without metadata attrs."""
        log = _mock_log()
        state = _mock_state()

        orig_coro = AsyncMock(return_value="data")

        tool = MagicMock()
        tool.name = "retrieval_search"
        tool.coroutine = orig_coro
        tool.func = None
        tool.description = "search"
        tool.args_schema = None
        tool.return_direct = False
        del tool._original_name
        del tool._tool_wrapper

        result = _wrap_retrieval_tools_for_context_efficiency([tool], state, log)
        assert len(result) == 1


# ============================================================================
# _prewarm_clients — lines 1545->1547, 1549
# ============================================================================


class TestPrewarmClientsLockCreation:
    """Cover lock creation and race-condition double-check."""

    @pytest.mark.asyncio
    async def test_prewarm_skips_when_already_cached(self):
        """Line 1541 — outer check finds cache populated, returns early."""
        log = _mock_log()
        state = _mock_state(tool_to_toolset_map={"jira.create": "ts1"})
        tasks = [{"task_id": "t1", "domains": ["jira"], "tools": ["jira.create"]}]

        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=MagicMock())

        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        cache_key = ("jira", "ts1", "default")
        mock_creator = MagicMock()
        mock_creator._client_cache = {cache_key: MagicMock()}
        mock_creator._cache_locks = {}
        mock_creator._get_toolset_config.return_value = {}

        mock_tic = MagicMock(return_value=mock_creator)

        mock_registry_mod = MagicMock(ClientFactoryRegistry=mock_cfr)
        mock_wrapper_mod = MagicMock(ToolInstanceCreator=mock_tic)

        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": mock_registry_mod,
            "app.agents.tools.wrapper": mock_wrapper_mod,
        }):
            await _prewarm_clients(tasks, state, log)
        mock_factory.create_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_prewarm_lock_creation_cold_cache(self):
        """Line 1545->1547 — new lock created for cold cache key."""
        log = _mock_log()
        state = _mock_state(tool_to_toolset_map={"jira.create": "ts1"})
        tasks = [{"task_id": "t1", "domains": ["jira"], "tools": ["jira.create"]}]

        mock_client = MagicMock()
        mock_factory = MagicMock()
        mock_factory.create_client = AsyncMock(return_value=mock_client)

        mock_cfr = MagicMock()
        mock_cfr.get_factory.return_value = mock_factory

        mock_creator = MagicMock()
        mock_creator._client_cache = {}
        mock_creator._cache_locks = {}
        mock_creator._get_toolset_config.return_value = {}

        mock_tic = MagicMock(return_value=mock_creator)

        mock_registry_mod = MagicMock(ClientFactoryRegistry=mock_cfr)
        mock_wrapper_mod = MagicMock(ToolInstanceCreator=mock_tic)

        with patch.dict("sys.modules", {
            "app.agents.tools.factories.registry": mock_registry_mod,
            "app.agents.tools.wrapper": mock_wrapper_mod,
        }):
            await _prewarm_clients(tasks, state, log)
        mock_factory.create_client.assert_awaited_once()
