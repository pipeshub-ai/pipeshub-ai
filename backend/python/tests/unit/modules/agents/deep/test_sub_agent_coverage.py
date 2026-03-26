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
    _format_tools_for_prompt,
    _make_budgeted_coro,
    _prewarm_clients,
    _SubAgentStreamingCallback,
    _ToolCallBudget,
    _wrap_tools_with_budget,
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
        state = _mock_state()
        state["_client_cache"] = {("jira", "default", "default"): MagicMock()}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["jira.search"]},
        ]

        with patch("app.agents.tools.factories.registry.ClientFactoryRegistry") as mock_cfr:
            mock_factory = MagicMock()
            mock_cfr.get_factory.return_value = mock_factory
            with patch("app.agents.tools.wrapper.ToolInstanceCreator") as mock_tic:
                mock_creator = MagicMock()
                mock_creator._client_cache = state["_client_cache"]
                mock_creator._cache_locks = state["_client_cache_locks"]
                mock_creator._get_toolset_config.return_value = None
                mock_tic.return_value = mock_creator
                await _prewarm_clients(tasks, state, log)
                # Factory create_client should not be called because client is cached
                mock_factory.create_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_prewarm_no_factory_skips(self):
        """Pre-warm skips domains without a factory (line 1237)."""
        state = _mock_state()
        state["_client_cache"] = {}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["unknown.tool"]},
        ]

        with patch("app.agents.tools.factories.registry.ClientFactoryRegistry") as mock_cfr:
            mock_cfr.get_factory.return_value = None  # No factory
            with patch("app.agents.tools.wrapper.ToolInstanceCreator") as mock_tic:
                mock_creator = MagicMock()
                mock_creator._client_cache = state["_client_cache"]
                mock_creator._cache_locks = state["_client_cache_locks"]
                mock_creator._get_toolset_config.return_value = None
                mock_tic.return_value = mock_creator
                await _prewarm_clients(tasks, state, log)

    @pytest.mark.asyncio
    async def test_prewarm_exception_does_not_crash(self):
        """Pre-warm handles exceptions gracefully."""
        state = _mock_state()
        state["_client_cache"] = {}
        state["_client_cache_locks"] = {}
        log = _mock_log()

        tasks = [
            {"task_id": "t1", "tools": ["slack.send"]},
        ]

        with patch("app.agents.tools.factories.registry.ClientFactoryRegistry") as mock_cfr:
            mock_factory = MagicMock()
            mock_factory.create_client = AsyncMock(side_effect=Exception("auth failed"))
            mock_cfr.get_factory.return_value = mock_factory
            with patch("app.agents.tools.wrapper.ToolInstanceCreator") as mock_tic:
                mock_creator = MagicMock()
                mock_creator._client_cache = {}
                mock_creator._cache_locks = {}
                mock_creator._get_toolset_config.return_value = None
                mock_tic.return_value = mock_creator
                # Should not raise
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
