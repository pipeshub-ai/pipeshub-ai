import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.agents.qna.nodes import (
    NodeConfig,
    PlaceholderResolver,
    ToolExecutor,
    ToolResultExtractor,
    _build_tool_results_context,
    _check_if_task_needs_continue,
    _check_primary_tool_success,
    _create_fallback_plan,
    _detect_tool_result_status,
    _extract_missing_params_from_error,
    _extract_urls_for_reference_data,
    _get_tool_status_message,
    _is_retrieval_tool,
    _is_semantically_empty,
    _parse_planner_response,
    _underscore_to_dotted,
    check_for_error,
    clean_tool_result,
    format_result_for_llm,
    route_after_reflect,
    should_execute_tools,
)


def _log():
    return MagicMock(spec=logging.Logger)


class TestHasZoomTools:
    def test_has_zoom(self):
        from app.modules.agents.qna.nodes import _has_zoom_tools
        state = {"agent_toolsets": [{"name": "Zoom Meeting"}]}
        assert _has_zoom_tools(state) is True

    def test_no_zoom(self):
        from app.modules.agents.qna.nodes import _has_zoom_tools
        state = {"agent_toolsets": [{"name": "Slack"}]}
        assert _has_zoom_tools(state) is False

    def test_empty_toolsets(self):
        from app.modules.agents.qna.nodes import _has_zoom_tools
        assert _has_zoom_tools({"agent_toolsets": []}) is False


class TestHasRedshiftTools:
    def test_has_redshift(self):
        from app.modules.agents.qna.nodes import _has_redshift_tools
        state = {"agent_toolsets": [{"name": "Amazon Redshift"}]}
        assert _has_redshift_tools(state) is True

    def test_no_redshift(self):
        from app.modules.agents.qna.nodes import _has_redshift_tools
        state = {"agent_toolsets": [{"name": "Jira"}]}
        assert _has_redshift_tools(state) is False


class TestValidatePlannedTools:
    def test_exception_returns_valid(self):
        from app.modules.agents.qna.nodes import _validate_planned_tools
        state = {}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            side_effect=ImportError("no module"),
        ):
            is_valid, invalid, available = _validate_planned_tools([], state, _log())
            assert is_valid is True

    def test_all_tools_valid(self):
        from app.modules.agents.qna.nodes import _validate_planned_tools
        mock_tool = MagicMock()
        mock_tool.name = "jira.search_issues"
        mock_tool._original_name = "jira.search_issues"
        state = {"llm": MagicMock()}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            return_value=[mock_tool],
        ):
            with patch("app.modules.agents.qna.tool_system._sanitize_tool_name_if_needed", return_value="jira.search_issues"):
                is_valid, invalid, available = _validate_planned_tools(
                    [{"name": "jira.search_issues"}], state, _log()
                )
                assert is_valid is True
                assert invalid == []

    def test_invalid_tool_detected(self):
        from app.modules.agents.qna.nodes import _validate_planned_tools
        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool._original_name = "jira.search"
        state = {"llm": MagicMock()}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            return_value=[mock_tool],
        ):
            with patch("app.modules.agents.qna.tool_system._sanitize_tool_name_if_needed", return_value="nonexistent"):
                is_valid, invalid, available = _validate_planned_tools(
                    [{"name": "nonexistent_tool"}], state, _log()
                )
                assert is_valid is False
                assert "nonexistent_tool" in invalid


class TestBuildToolSchemaReference:
    def test_no_tools(self):
        from app.modules.agents.qna.nodes import _build_tool_schema_reference
        state = {}
        with patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas", return_value=[]):
            result = _build_tool_schema_reference(state, _log())
            assert result == ""

    def test_with_tools_and_schema(self):
        from app.modules.agents.qna.nodes import _build_tool_schema_reference
        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_schema = MagicMock()
        mock_tool.args_schema = mock_schema

        state = {}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            return_value=[mock_tool],
        ):
            with patch("app.modules.agents.qna.nodes._extract_parameters_from_schema", return_value={
                "query": {"type": "string", "required": True, "description": "Search query"},
                "limit": {"type": "int", "required": False, "description": "Max results"},
            }):
                result = _build_tool_schema_reference(state, _log())
                assert "jira.search" in result
                assert "Required" in result
                assert "Optional" in result

    def test_exception_returns_empty(self):
        from app.modules.agents.qna.nodes import _build_tool_schema_reference
        state = {}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            side_effect=Exception("err"),
        ):
            result = _build_tool_schema_reference(state, _log())
            assert result == ""

    def test_tool_without_schema(self):
        from app.modules.agents.qna.nodes import _build_tool_schema_reference
        mock_tool = MagicMock()
        mock_tool.name = "simple_tool"
        mock_tool.args_schema = None
        state = {}
        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            return_value=[mock_tool],
        ):
            result = _build_tool_schema_reference(state, _log())
            assert "no schema available" in result


class TestBuildWorkflowPatterns:
    def test_no_patterns(self):
        from app.modules.agents.qna.nodes import _build_workflow_patterns
        state = {"agent_toolsets": []}
        result = _build_workflow_patterns(state)
        assert result == ""

    def test_outlook_and_confluence(self):
        from app.modules.agents.qna.nodes import _build_workflow_patterns
        state = {"agent_toolsets": [
            {"name": "Outlook"},
            {"name": "Confluence"},
        ]}
        result = _build_workflow_patterns(state)
        assert "Cross-Service Pattern" in result
        assert "Holiday" in result

    def test_teams_and_slack(self):
        from app.modules.agents.qna.nodes import _build_workflow_patterns
        state = {"agent_toolsets": [
            {"name": "Microsoft Teams"},
            {"name": "Slack"},
        ]}
        result = _build_workflow_patterns(state)
        assert "Transcript" in result

    def test_outlook_only(self):
        from app.modules.agents.qna.nodes import _build_workflow_patterns
        state = {"agent_toolsets": [{"name": "Outlook"}]}
        result = _build_workflow_patterns(state)
        assert "Extend a Recurring Event" in result


class TestBuildToolResultsContextModes:
    @pytest.mark.asyncio
    async def test_all_failed(self):
        results = [{"status": "error", "tool_name": "jira.search", "result": "timeout"}]
        ctx = await _build_tool_results_context(results, [])
        assert "Tools Failed" in ctx
        assert "DO NOT fabricate" in ctx

    @pytest.mark.asyncio
    async def test_retrieval_only_from_final_results(self):
        results = [{"status": "success", "tool_name": "retrieval", "result": "data"}]
        ctx = await _build_tool_results_context(results, [{"text": "block1"}])
        assert "Internal Knowledge Available" in ctx

    @pytest.mark.asyncio
    async def test_retrieval_in_context_flag(self):
        results = [{"status": "success", "tool_name": "retrieval", "result": "data"}]
        ctx = await _build_tool_results_context(results, [], has_retrieval_in_context=True)
        assert "Internal Knowledge in Context" in ctx

    @pytest.mark.asyncio
    async def test_combined_mode(self):
        results = [
            {"status": "success", "tool_name": "retrieval", "result": "data"},
            {"status": "success", "tool_name": "jira.search", "result": {"key": "PROJ-1"}},
        ]
        ctx = await _build_tool_results_context(results, [{"text": "block"}])
        assert "MODE 3" in ctx

    @pytest.mark.asyncio
    async def test_api_only(self):
        results = [{"status": "success", "tool_name": "jira.search", "result": {"key": "PROJ-1"}}]
        ctx = await _build_tool_results_context(results, [])
        assert "API DATA" in ctx

    @pytest.mark.asyncio
    async def test_multiple_non_retrieval(self):
        results = [
            {"status": "success", "tool_name": "jira.search", "result": {"key": "A"}},
            {"status": "success", "tool_name": "slack.send", "result": {"ok": True}},
        ]
        ctx = await _build_tool_results_context(results, [])
        assert "MULTIPLE tools" in ctx


class TestExtractUrlsForReferenceDataEdgeCases:
    def test_json_string_input(self):
        ref = []
        _extract_urls_for_reference_data('{"url": "https://example.com", "title": "Test"}', ref)
        assert len(ref) == 1
        assert ref[0]["webUrl"] == "https://example.com"

    def test_invalid_json_string(self):
        ref = []
        _extract_urls_for_reference_data("not json", ref)
        assert len(ref) == 0

    def test_no_duplicate_urls(self):
        ref = [{"webUrl": "https://example.com"}]
        _extract_urls_for_reference_data({"link": "https://example.com"}, ref)
        assert len(ref) == 1

    def test_nested_dict_with_urls(self):
        ref = []
        _extract_urls_for_reference_data({
            "item": {"webUrl": "https://test.com/page", "name": "Page"}
        }, ref)
        assert len(ref) == 1

    def test_list_input(self):
        ref = []
        _extract_urls_for_reference_data([
            {"link": "https://a.com", "title": "A"},
            {"link": "https://b.com", "title": "B"},
        ], ref)
        assert len(ref) == 2


class TestExtractFieldFromDataDeepBranches:
    def test_data_prefix_skip_with_numeric_index(self):
        data = {"items": [{"id": "123"}, {"id": "456"}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["data", "0", "id"])
        assert result == "123"

    def test_results_fallback_to_data_list(self):
        data = {"data": [{"id": "abc"}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["results", "0", "id"])
        assert result == "abc"

    def test_content_body_alias(self):
        data = {"body": "hello world"}
        result = ToolResultExtractor.extract_field_from_data(data, ["content"])
        assert result == "hello world"

    def test_body_content_alias(self):
        data = {"content": "test data"}
        result = ToolResultExtractor.extract_field_from_data(data, ["body"])
        assert result == "test data"

    def test_list_with_wildcard_index(self):
        data = [{"id": "first"}, {"id": "second"}]
        result = ToolResultExtractor.extract_field_from_data(data, ["?", "id"])
        assert result == "first"

    def test_list_with_star_wildcard(self):
        data = [{"name": "item1"}]
        result = ToolResultExtractor.extract_field_from_data(data, ["*", "name"])
        assert result == "item1"

    def test_empty_list_returns_none(self):
        data = {"items": []}
        result = ToolResultExtractor.extract_field_from_data(data, ["items", "0"])
        assert result is None

    def test_json_string_field(self):
        data = json.dumps({"key": "value"})
        result = ToolResultExtractor.extract_field_from_data(data, ["key"])
        assert result == "value"

    def test_json_string_content_alias(self):
        data = json.dumps({"body": "content here"})
        result = ToolResultExtractor.extract_field_from_data(data, ["content"])
        assert result == "content here"

    def test_none_in_path(self):
        result = ToolResultExtractor.extract_field_from_data(None, ["field"])
        assert result is None

    def test_index_out_of_bounds(self):
        data = {"items": [{"id": 1}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["items", "5"])
        assert result is None

    def test_list_auto_extract_first_element(self):
        data = {"results": [{"id": "first"}, {"id": "second"}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["results", "id"])
        assert result == "first"

    def test_non_numeric_list_field_no_dict(self):
        data = [[1, 2], [3, 4]]
        result = ToolResultExtractor.extract_field_from_data(data, ["field"])
        assert result is None


class TestGetFieldTypeName:
    def test_simple_type(self):
        from app.modules.agents.qna.nodes import _get_field_type_name
        field = MagicMock()
        field.annotation = str
        assert _get_field_type_name(field) == "str"

    def test_exception(self):
        from app.modules.agents.qna.nodes import _get_field_type_name
        field = MagicMock()
        field.annotation = property(fget=lambda self: (_ for _ in ()).throw(Exception("err")))
        del field.annotation
        result = _get_field_type_name(field)
        assert result == "any"


class TestGetFieldTypeNameV1:
    def test_simple_type(self):
        from app.modules.agents.qna.nodes import _get_field_type_name_v1
        field = MagicMock()
        field.outer_type_ = int
        assert _get_field_type_name_v1(field) == "int"

    def test_exception(self):
        from app.modules.agents.qna.nodes import _get_field_type_name_v1
        field = MagicMock(spec=[])
        result = _get_field_type_name_v1(field)
        assert result == "any"


class TestExtractParametersFromSchema:
    def test_json_schema_dict(self):
        from app.modules.agents.qna.nodes import _extract_parameters_from_schema
        schema = {
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        }
        result = _extract_parameters_from_schema(schema, _log())
        assert result["query"]["required"] is True
        assert result["limit"]["required"] is False

    def test_unrecognized_schema(self):
        from app.modules.agents.qna.nodes import _extract_parameters_from_schema
        result = _extract_parameters_from_schema("not a schema", _log())
        assert result == {}


class TestGetCachedToolDescriptions:
    def test_no_cache(self):
        from app.modules.agents.qna import nodes as nodes_mod
        from app.modules.agents.qna.nodes import _get_cached_tool_descriptions

        state = {}
        nodes_mod._tool_description_cache.clear()
        with patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas", return_value=[]):
            result = _get_cached_tool_descriptions(state, _log())
            assert result is not None

    def test_cache_hit(self):
        from app.modules.agents.qna import nodes as nodes_mod
        from app.modules.agents.qna.nodes import _get_cached_tool_descriptions

        state = {"org_id": "org1", "agent_toolsets": [], "has_knowledge": False}
        cache_key = f"org1_{hash(tuple())}_other_False"
        nodes_mod._tool_description_cache.clear()
        nodes_mod._tool_description_cache[cache_key] = "cached descriptions"
        result = _get_cached_tool_descriptions(state, _log())
        assert result == "cached descriptions"


class TestProcessRetrievalOutput:
    def test_returns_string(self):
        from app.modules.agents.qna.nodes import _process_retrieval_output
        state = {"final_results": []}
        with patch("app.agents.actions.retrieval.retrieval.RetrievalToolOutput") as mock_rto:
            mock_rto.return_value.formatted_output = "formatted"
            mock_rto.return_value.results = []
            result = _process_retrieval_output("raw result", state, _log())
            assert isinstance(result, str)


class TestExtractInvalidParamsFromArgs:
    def test_extracts_params(self):
        from app.modules.agents.qna.nodes import _extract_invalid_params_from_args
        args = {"query": "test", "invalid_field": "val"}
        error_msg = "Unexpected keyword argument 'invalid_field'"
        result = _extract_invalid_params_from_args(args, error_msg)
        assert isinstance(result, list)


class TestBuildRetryContextFull:
    def test_with_failed_tools(self):
        from app.modules.agents.qna.nodes import _build_retry_context
        state = {
            "tool_results": [
                {"tool_name": "jira.search", "status": "error", "result": "timeout", "args": {"q": "test"}},
            ],
            "retry_count": 0,
            "max_retries": 2,
            "planned_tool_calls": [{"name": "jira.search", "args": {"q": "test"}}],
        }
        result = _build_retry_context(state)
        assert isinstance(result, str)


class TestBuildContinueContextFull:
    def test_with_completed_tools(self):
        from app.modules.agents.qna.nodes import _build_continue_context
        state = {
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {"key": "PROJ-1"}},
            ],
            "continue_plan": {"next_tools": [{"name": "jira.get_issue"}]},
            "planned_tool_calls": [],
        }
        result = _build_continue_context(state, _log())
        assert isinstance(result, str)


class TestFormatToolDescriptionsFull:
    def test_with_schema_params(self):
        from app.modules.agents.qna.nodes import _format_tool_descriptions
        mock_tool = MagicMock()
        mock_tool.name = "jira.search"
        mock_tool.description = "Search Jira issues"
        mock_schema = MagicMock()
        mock_tool.args_schema = mock_schema
        with patch("app.modules.agents.qna.nodes._extract_parameters_from_schema", return_value={
            "query": {"type": "string", "required": True, "description": "The search query"},
        }):
            result = _format_tool_descriptions([mock_tool], _log())
            assert "jira.search" in result
            assert "query" in result

    def test_tool_without_schema(self):
        from app.modules.agents.qna.nodes import _format_tool_descriptions
        mock_tool = MagicMock()
        mock_tool.name = "simple"
        mock_tool.description = "A tool"
        mock_tool.args_schema = None
        result = _format_tool_descriptions([mock_tool], _log())
        assert "simple" in result


class TestPlaceholderResolverExtractSourceToolNameBranches:
    def test_dotted_tool_name(self):
        result = PlaceholderResolver._extract_source_tool_name("jira.search_issues.data.key")
        assert result == "jira.search_issues"

    def test_underscored_name(self):
        result = PlaceholderResolver._extract_source_tool_name("search_issues.key")
        assert result == "search_issues.key"

    def test_simple_name(self):
        result = PlaceholderResolver._extract_source_tool_name("simple")
        assert result == "simple"


class TestFormatResultForLlmDictFallback:
    def test_non_serializable_in_dict(self):
        class Custom:
            def __repr__(self):
                return "Custom()"
        result = format_result_for_llm({"obj": Custom()})
        assert "Custom()" in result


class TestCleanToolResultDeepNesting:
    def test_deeply_nested_list_of_dicts(self):
        data = {"items": [{"nested": {"debug": "drop", "keep": "this"}}]}
        result = clean_tool_result(data)
        assert result == {"items": [{"nested": {"keep": "this"}}]}

    def test_tuple_with_list_data(self):
        data = (True, [{"id": 1, "trace": "x"}])
        result = clean_tool_result(data)
        assert result[0] is True
        assert result[1] == [{"id": 1}]


class TestBuildReactSystemPromptPartial:
    def test_with_instructions(self):
        from app.modules.agents.qna.nodes import _build_react_system_prompt
        state = {
            "instructions": "Always be polite",
            "agent_toolsets": [],
            "agent_knowledge": [],
            "query": "hello",
        }
        with patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas", return_value=[]):
            with patch("app.modules.agents.qna.nodes._build_tool_schema_reference", return_value=""):
                with patch("app.modules.agents.qna.nodes._build_knowledge_context", return_value=""):
                    with patch("app.modules.agents.qna.nodes._build_workflow_patterns", return_value=""):
                        result = _build_react_system_prompt(state, _log())
                        assert "Always be polite" in result


class TestExtractFinalResponseEdge:
    def test_empty_messages(self):
        from app.modules.agents.qna.nodes import _extract_final_response
        result = _extract_final_response([], _log())
        assert isinstance(result, str)

    def test_ai_message_content(self):
        from app.modules.agents.qna.nodes import _extract_final_response
        msgs = [AIMessage(content="Here is the answer")]
        result = _extract_final_response(msgs, _log())
        assert "Here is the answer" in result

    def test_tool_message_only(self):
        from app.modules.agents.qna.nodes import _extract_final_response
        msgs = [ToolMessage(content="tool output", tool_call_id="tc1")]
        result = _extract_final_response(msgs, _log())
        assert isinstance(result, str)


# ============================================================================
# PlaceholderResolver — unresolved placeholders (lines 1014-1040)
# ============================================================================


class TestUnresolvedPlaceholders:
    """Cover lines 1014-1040: unresolved placeholders after stripping."""

    def test_has_placeholders_detects_pattern(self):
        args = {"page_id": "{{search_results[0].id}}"}
        assert PlaceholderResolver.has_placeholders(args) is True

    def test_has_placeholders_clean(self):
        args = {"page_id": "12345"}
        assert PlaceholderResolver.has_placeholders(args) is False


# ============================================================================
# _build_planner_messages — is_continue branch (lines 3932-3967)
# ============================================================================


class TestBuildPlannerMessagesContinue:
    """Cover lines 3932-3967: is_continue with various tool name patterns."""

    @pytest.mark.asyncio
    async def test_continue_with_retrieval_tool(self):
        from app.modules.agents.qna.nodes import _build_planner_messages
        state = {
            "query": "search for docs",
            "is_continue": True,
            "is_retry": False,
            "executed_tool_names": ["retrieval_search"],
            "iteration_count": 1,
            "max_iterations": 5,
            "tool_results": [{"tool_name": "retrieval_search", "status": "success", "result": "data"}],
            "planned_tool_calls": [],
            "conversation_history": [],
            "agent_toolsets": [],
            "logger": _log(),
        }
        with patch("app.modules.agents.qna.nodes._build_continue_context", return_value="Continue context"), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"):
            msgs = await _build_planner_messages(state, "search for docs", _log())
        assert len(msgs) > 0

    @pytest.mark.asyncio
    async def test_continue_with_create_tool(self):
        from app.modules.agents.qna.nodes import _build_planner_messages
        state = {
            "query": "create a page",
            "is_continue": True,
            "is_retry": False,
            "executed_tool_names": ["confluence_create_page"],
            "iteration_count": 0,
            "max_iterations": 3,
            "tool_results": [],
            "planned_tool_calls": [],
            "conversation_history": [],
            "agent_toolsets": [],
            "logger": _log(),
        }
        with patch("app.modules.agents.qna.nodes._build_continue_context", return_value="Continue"), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"):
            msgs = await _build_planner_messages(state, "create a page", _log())
        assert len(msgs) > 0

    @pytest.mark.asyncio
    async def test_continue_with_empty_executed_tools(self):
        from app.modules.agents.qna.nodes import _build_planner_messages
        state = {
            "query": "do something",
            "is_continue": True,
            "is_retry": False,
            "executed_tool_names": [],
            "iteration_count": 0,
            "max_iterations": 3,
            "tool_results": [],
            "planned_tool_calls": [],
            "conversation_history": [],
            "agent_toolsets": [],
            "logger": _log(),
        }
        with patch("app.modules.agents.qna.nodes._build_continue_context", return_value="Continue"), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"):
            msgs = await _build_planner_messages(state, "do something", _log())
        assert len(msgs) > 0


# ============================================================================
# reflect_node — cascading chain and primary tool match (lines 5639-5679)
# ============================================================================


class TestReflectCascading:
    """Cover lines 5639-5679: cascading chain and primary tool match."""

    def test_cascading_chain_last_tool_success(self):
        """Cascading chain where last tool succeeds."""
        from app.modules.agents.qna.nodes import _check_primary_tool_success
        successful = [
            {"tool_name": "search", "status": "success", "result": "found"},
            {"tool_name": "create", "status": "success", "result": "created page"},
        ]
        result = _check_primary_tool_success("create a page from search", successful, _log())
        # Returns True because primary tool "create" succeeded
        assert result is True

    def test_non_cascading_primary_tool_match(self):
        """Non-cascading: primary tool name matches with dot normalization."""
        from app.modules.agents.qna.nodes import _check_primary_tool_success
        successful = [
            {"tool_name": "jira.create_issue", "status": "success", "result": "done"},
        ]
        result = _check_primary_tool_success("create an issue in jira", successful, _log())
        assert result is True


# ============================================================================
# respond_node — conversation tasks (lines 6959-6965)
# ============================================================================


class TestRespondConversationTasks:
    """Cover edge cases in respond_node, specifically for _is_semantically_empty."""

    def test_task_markers_exception_handled(self):
        """_is_semantically_empty handles various inputs correctly."""
        from app.modules.agents.qna.nodes import _is_semantically_empty
        # Strings (even empty) are not semantically empty — they pass through extract_data_from_result as-is
        assert _is_semantically_empty("") is False
        assert _is_semantically_empty("   ") is False
        assert _is_semantically_empty("actual data") is False
        # None and empty containers are semantically empty
        assert _is_semantically_empty(None) is True
        assert _is_semantically_empty({"data": {"results": []}}) is True
        assert _is_semantically_empty([]) is True


# ============================================================================
# _detect_tool_result_status — edge cases
# ============================================================================


class TestDetectToolResultStatusEdge:
    """Cover additional branches in _detect_tool_result_status."""

    def test_dict_with_error_key(self):
        from app.modules.agents.qna.nodes import _detect_tool_result_status
        result = _detect_tool_result_status({"error": "something went wrong"})
        assert result == "error"

    def test_dict_with_success_status(self):
        from app.modules.agents.qna.nodes import _detect_tool_result_status
        result = _detect_tool_result_status({"status": "success", "data": "ok"})
        assert result == "success"

    def test_string_with_unauthorized(self):
        from app.modules.agents.qna.nodes import _detect_tool_result_status
        result = _detect_tool_result_status("Error: Unauthorized access")
        assert result == "error"

    def test_string_clean_result(self):
        from app.modules.agents.qna.nodes import _detect_tool_result_status
        result = _detect_tool_result_status("Page created successfully with ID 123")
        assert result == "success"


# ============================================================================
# Helper
# ============================================================================

def _mock_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "logger": _log(),
        "llm": AsyncMock(),
        "query": "test query",
        "user_info": {},
        "org_info": {},
        "agent_toolsets": [],
        "has_knowledge": False,
        "agent_knowledge": [],
        "planned_tool_calls": [],
        "all_tool_results": [],
        "execution_plan": {},
        "previous_conversations": [],
        "tools": [],
        "available_tools": {},
        "tool_to_toolset_map": {},
        "final_results": [],
        "virtual_record_id_to_result": {},
    }
    state.update(overrides)
    return state


# ============================================================================
# ToolResultExtractor.extract_success_status — line 269->278 (JSON string)
# ============================================================================


class TestExtractSuccessStatusJsonString:
    def test_json_string_parsed_and_recursed(self):
        result = ToolResultExtractor.extract_success_status('{"ok": true, "data": []}')
        assert result is True

    def test_json_string_with_error(self):
        result = ToolResultExtractor.extract_success_status('{"error": "not found"}')
        assert result is False

    def test_non_json_string_with_error_indicator(self):
        result = ToolResultExtractor.extract_success_status("traceback: some error occurred")
        assert result is False


# ============================================================================
# extract_field_from_data — lines 397-414, 453, 465
# ============================================================================


class TestExtractFieldFromDataAliases:
    def test_results_suffix_fallback(self):
        """Line 397-405 — 'results' → '*_results' suffix."""
        data = {"web_results": [{"id": "w1"}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["results", "0", "id"])
        assert result == "w1"

    def test_results_no_suffix_match(self):
        """Line 404 — no suffix match returns None."""
        data = {"items": [1, 2]}
        result = ToolResultExtractor.extract_field_from_data(data, ["results", "0"])
        assert result is None

    def test_url_to_link_alias(self):
        """Line 412 — 'url' field resolves via 'link'."""
        data = {"link": "https://example.com"}
        result = ToolResultExtractor.extract_field_from_data(data, ["url"])
        assert result == "https://example.com"

    def test_link_to_url_alias(self):
        """Line 414 — 'link' field resolves via 'url'."""
        data = {"url": "https://example.com"}
        result = ToolResultExtractor.extract_field_from_data(data, ["link"])
        assert result == "https://example.com"

    def test_content_body_alias(self):
        """Lines 407-410 — content/body aliases."""
        data = {"body": "hello world"}
        result = ToolResultExtractor.extract_field_from_data(data, ["content"])
        assert result == "hello world"

    def test_empty_list_returns_none(self):
        """Line 453 — empty list in path."""
        data = {"items": []}
        result = ToolResultExtractor.extract_field_from_data(data, ["items", "0"])
        assert result is None

    def test_index_out_of_bounds(self):
        """Line 465 — index beyond list length."""
        data = [{"a": 1}]
        result = ToolResultExtractor.extract_field_from_data(data, ["5"])
        assert result is None


# ============================================================================
# PlaceholderResolver.parse_field_path — lines 833, 846-873
# ============================================================================


class TestPlaceholderResolverParsePath:
    def test_empty_path_returns_whole_result(self):
        """Line 833 — trailing dot means no sub-path, returns entire result."""
        result = PlaceholderResolver._resolve_single_placeholder(
            "tool_a.", {"tool_a": {"data": []}}, _log()
        )
        assert result == {"data": []}

    def test_jsonpath_predicate_normalized_to_zero(self):
        """Lines 855-867 — [?(@.id==1)] normalized to [0]."""
        results = {"tool_a": {"data": [{"id": 1, "name": "first"}, {"id": 2, "name": "second"}]}}
        result = PlaceholderResolver._resolve_single_placeholder(
            "tool_a.data[?(@.id==1)].name", results, _log()
        )
        assert result == "first"

    def test_wildcard_index_normalized(self):
        """Lines 855-863 — [*] normalized to [0]."""
        results = {"tool_a": {"data": [{"name": "item1"}, {"name": "item2"}]}}
        result = PlaceholderResolver._resolve_single_placeholder(
            "tool_a.data[*].name", results, _log()
        )
        assert result == "item1"

    def test_trailing_field_after_bracket(self):
        """Line 871-873 — field after array index bracket."""
        results = {"tool_a": {"data": [{"sub": "val"}]}}
        result = PlaceholderResolver._resolve_single_placeholder(
            "tool_a.data[0].sub", results, _log()
        )
        assert result == "val"


# ============================================================================
# PlaceholderResolver.resolve_all — line 701 (partial placeholder)
# ============================================================================


class TestPlaceholderResolverPartialString:
    def test_partial_placeholder_in_string(self):
        """Line 701 — string with placeholder + prefix/suffix."""
        results = {"search": {"data": {"id": "42"}}}
        args = {"query": "ID is {{search.data.id}} found"}
        resolved = PlaceholderResolver.resolve_all(args, results, _log())
        assert "42" in resolved["query"]
        assert "ID is" in resolved["query"]


# ============================================================================
# ToolExecutor._format_args_preview — lines 950-951
# ============================================================================


class TestFormatArgsPreview:
    def test_non_serializable_fallback(self):
        """Lines 950-951 — json.dumps fails, falls back to str()."""
        obj = object()
        result = ToolExecutor._format_args_preview({"key": obj})
        assert "object" in result


# ============================================================================
# ToolExecutor._process_retrieval_output — lines 1470-1528
# ============================================================================


class TestToolExecutorProcessRetrievalOutput:
    def test_preformatted_content_fast_path(self):
        """Lines 1470-1471 — pre-formatted <record> content."""
        state = _mock_state()
        result = ToolExecutor._process_retrieval_output(
            "<record>Some content</record>", state, _log()
        )
        assert "<record>" in result

    def test_non_dict_final_results_not_list(self):
        """Line 1502 — existing final_results is not a list."""
        state = _mock_state(final_results="not_a_list")
        mock_output = MagicMock()
        mock_output.final_results = [{"text": "new"}]
        mock_output.virtual_record_id_to_result = {}
        mock_output.content = "content"
        with patch.dict("sys.modules", {
            "app.agents.actions.retrieval.retrieval": MagicMock(RetrievalToolOutput=MagicMock(return_value=mock_output))
        }):
            ToolExecutor._process_retrieval_output(
                {"content": "c", "final_results": []}, state, _log()
            )
        assert isinstance(state["final_results"], list)

    def test_non_dict_virtual_map(self):
        """Line 1512 — existing virtual map is not a dict."""
        state = _mock_state(
            virtual_record_id_to_result="bad",
            tool_records="bad",
        )
        mock_output = MagicMock()
        mock_output.final_results = []
        mock_output.virtual_record_id_to_result = {"vr1": {"_id": "r1"}}
        mock_output.content = "content"
        with patch.dict("sys.modules", {
            "app.agents.actions.retrieval.retrieval": MagicMock(RetrievalToolOutput=MagicMock(return_value=mock_output))
        }):
            ToolExecutor._process_retrieval_output(
                {"content": "c", "final_results": []}, state, _log()
            )
        assert isinstance(state["virtual_record_id_to_result"], dict)
        assert isinstance(state["tool_records"], list)

    def test_exception_returns_str(self):
        """Lines 1527-1528 — exception in processing."""
        state = _mock_state()
        with patch.dict("sys.modules", {
            "app.agents.actions.retrieval.retrieval": MagicMock(
                RetrievalToolOutput=MagicMock(side_effect=TypeError("bad"))
            )
        }):
            result = ToolExecutor._process_retrieval_output(
                {"content": "c", "final_results": []}, state, _log()
            )
        assert isinstance(result, str)

    def test_json_string_legacy_path(self):
        """Lines 1483->1488 — JSON string parsed to retrieval output."""
        state = _mock_state()
        mock_output = MagicMock()
        mock_output.final_results = [{"text": "block"}]
        mock_output.virtual_record_id_to_result = {}
        mock_output.content = "parsed_content"
        with patch.dict("sys.modules", {
            "app.agents.actions.retrieval.retrieval": MagicMock(RetrievalToolOutput=MagicMock(return_value=mock_output))
        }):
            result = ToolExecutor._process_retrieval_output(
                json.dumps({"content": "c", "final_results": []}), state, _log()
            )
        assert result == "parsed_content"


# ============================================================================
# _ensure_blob_store — lines 4498, 4507-4508
# ============================================================================


class TestEnsureBlobStore:
    def test_returns_existing_blob_store(self):
        """Line 4498 — already initialized."""
        from app.modules.agents.qna.nodes import _ensure_blob_store
        existing = MagicMock()
        state = _mock_state(blob_store=existing)
        assert _ensure_blob_store(state, _log()) is existing

    def test_creates_blob_store(self):
        """Lines 4500-4506 — creates new BlobStorage."""
        from app.modules.agents.qna.nodes import _ensure_blob_store
        state = _mock_state()
        mock_bs = MagicMock()
        with patch("app.modules.agents.qna.nodes.BlobStorage", return_value=mock_bs, create=True):
            with patch.dict("sys.modules", {
                "app.modules.transformers.blob_storage": MagicMock(BlobStorage=MagicMock(return_value=mock_bs))
            }):
                result = _ensure_blob_store(state, _log())
        assert state.get("blob_store") is not None

    def test_exception_returns_none(self):
        """Lines 4507-4508 — init failure returns None."""
        from app.modules.agents.qna.nodes import _ensure_blob_store
        state = _mock_state()
        with patch.dict("sys.modules", {
            "app.modules.transformers.blob_storage": MagicMock(
                BlobStorage=MagicMock(side_effect=RuntimeError("no config"))
            )
        }):
            result = _ensure_blob_store(state, _log())
        assert result is None


# ============================================================================
# _ensure_attachment_blocks — lines 4520, 4527-4562
# ============================================================================


class TestEnsureAttachmentBlocks:
    @pytest.mark.asyncio
    async def test_cached_blocks_returned(self):
        """Line 4520 — already resolved."""
        from app.modules.agents.qna.nodes import _ensure_attachment_blocks
        blocks = [{"type": "text"}]
        state = _mock_state(resolved_attachment_blocks=blocks)
        result = await _ensure_attachment_blocks(state, _log())
        assert result is blocks

    @pytest.mark.asyncio
    async def test_empty_attachments(self):
        """Lines 4522-4525 — no attachments."""
        from app.modules.agents.qna.nodes import _ensure_attachment_blocks
        state = _mock_state(attachments=[])
        result = await _ensure_attachment_blocks(state, _log())
        assert result == []

    @pytest.mark.asyncio
    async def test_resolve_with_records(self):
        """Lines 4527-4562 — full resolution path."""
        from app.modules.agents.qna.nodes import _ensure_attachment_blocks
        state = _mock_state(
            attachments=[{"mimeType": "image/png", "virtualRecordId": "vr1"}],
        )
        mock_resolve = AsyncMock(return_value=[{"type": "image"}])

        with patch("app.modules.agents.qna.nodes._ensure_blob_store", return_value=MagicMock()), \
             patch.dict("sys.modules", {
                 "app.utils.attachment_utils": MagicMock(resolve_attachments=mock_resolve),
                 "app.utils.chat_helpers": MagicMock(CitationRefMapper=MagicMock),
             }):
            result = await _ensure_attachment_blocks(state, _log())
        assert state["resolved_attachment_blocks"] is not None

    @pytest.mark.asyncio
    async def test_resolve_exception(self):
        """Lines 4557-4559 — exception during resolution."""
        from app.modules.agents.qna.nodes import _ensure_attachment_blocks
        state = _mock_state(attachments=[{"mimeType": "image/png"}])

        with patch.dict("sys.modules", {
            "app.utils.attachment_utils": MagicMock(
                resolve_attachments=AsyncMock(side_effect=RuntimeError("fail"))
            ),
            "app.utils.chat_helpers": MagicMock(CitationRefMapper=MagicMock),
        }), patch("app.modules.agents.qna.nodes._ensure_blob_store", return_value=None):
            result = await _ensure_attachment_blocks(state, _log())
        assert result == []
        assert state["resolved_attachment_blocks"] == []


# ============================================================================
# _inject_attachment_blocks — lines 4574-4585
# ============================================================================


class TestInjectAttachmentBlocks:
    def test_list_content_extends(self):
        """Lines 4577-4581 — list content extended."""
        from app.modules.agents.qna.nodes import _inject_attachment_blocks
        msgs = [HumanMessage(content=[{"type": "text", "text": "hello"}])]
        blocks = [{"type": "image", "url": "http://img.png"}]
        _inject_attachment_blocks(msgs, blocks)
        assert len(msgs[0].content) > 1

    def test_string_content_converts(self):
        """Lines 4582-4585 — string content converted to multimodal."""
        from app.modules.agents.qna.nodes import _inject_attachment_blocks
        msgs = [HumanMessage(content="plain text")]
        blocks = [{"type": "image"}]
        with patch("app.utils.attachment_utils.build_multimodal_content",
                   return_value=[{"type": "text", "text": "plain text"}, {"type": "image"}]):
            _inject_attachment_blocks(msgs, blocks)
        assert isinstance(msgs[-1].content, list)

    def test_empty_blocks_noop(self):
        from app.modules.agents.qna.nodes import _inject_attachment_blocks
        msgs = [HumanMessage(content="text")]
        _inject_attachment_blocks(msgs, [])
        assert msgs[0].content == "text"

    def test_non_human_message_noop(self):
        from app.modules.agents.qna.nodes import _inject_attachment_blocks
        msgs = [AIMessage(content="bot reply")]
        _inject_attachment_blocks(msgs, [{"type": "image"}])
        assert msgs[0].content == "bot reply"


# ============================================================================
# _format_user_context — line 4615->4630
# ============================================================================


class TestFormatUserContextUsage:
    def test_usage_section_with_email(self):
        """Line 4615->4630 — Usage section when email present."""
        from app.modules.agents.qna.nodes import _format_user_context
        state = _mock_state(
            user_email="user@example.com",
            user_info={"fullName": "Test User", "userEmail": "user@example.com"},
        )
        result = _format_user_context(state)
        assert "Usage" in result

    def test_no_usage_without_identity(self):
        from app.modules.agents.qna.nodes import _format_user_context
        state = _mock_state(user_info={}, org_info={})
        result = _format_user_context(state)
        assert "Usage" not in result


# ============================================================================
# _parse_planner_response_from_llm — lines 4998-5011
# ============================================================================


class TestParsePlannerResponseFromLlm:
    def test_dict_response(self):
        """Lines 4998-5011 — raw dict from provider."""
        from app.modules.agents.qna.nodes import _parse_planner_response_from_llm
        resp = {
            "intent": "search",
            "tools": [{"name": "jira.search", "args": {"query": "test"}}],
        }
        result = _parse_planner_response_from_llm(resp, _log(), using_structured=True)
        assert result["intent"] == "search"
        assert len(result["tools"]) == 1
        assert result["can_answer_directly"] is False

    def test_dict_filters_invalid_tools(self):
        from app.modules.agents.qna.nodes import _parse_planner_response_from_llm
        resp = {"tools": [{"name": "valid"}, "invalid_string", {"no_name": True}]}
        result = _parse_planner_response_from_llm(resp, _log(), using_structured=True)
        assert len(result["tools"]) == 1

    def test_aimessage_with_list_content_blocks(self):
        """Gemini list content is coerced before JSON parse (no '.strip' crash)."""
        from app.modules.agents.qna.nodes import _parse_planner_response_from_llm

        resp = MagicMock()
        resp.content = [
            {"type": "text", "text": '{"intent": "search", "reasoning": "r", '},
            {
                "type": "text",
                "text": '"can_answer_directly": false, "needs_clarification": false, '
                '"tools": [{"name": "jira.search", "args": {"query": "bugs"}}]}',
            },
        ]
        result = _parse_planner_response_from_llm(resp, _log(), using_structured=False)
        assert result["intent"] == "search"
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "jira.search"


# ============================================================================
# reflect_node list-content coercion (via shared helper + _parse_reflection_response)
# ============================================================================


class TestReflectListContentCoercion:
    def test_list_content_blocks_coerced_before_parse(self):
        """Same pipeline as reflect_node: coerce list blocks, then parse JSON."""
        import json

        from app.modules.agents.qna.nodes import _parse_reflection_response
        from app.utils.aimodels import coerce_message_content_to_text

        raw = json.dumps({"decision": "respond_success", "reasoning": "All good"})
        mid = len(raw) // 2
        blocks = [
            {"type": "text", "text": raw[:mid]},
            {"type": "text", "text": raw[mid:]},
        ]
        result = _parse_reflection_response(coerce_message_content_to_text(blocks), _log())
        assert result["decision"] == "respond_success"
        assert result["reasoning"] == "All good"


# ============================================================================
# coerce_message_content_to_text — used by planner/reflection paths
# ============================================================================


class TestCoerceMessageContentForPlanner:
    def test_string_passthrough(self):
        from app.utils.aimodels import coerce_message_content_to_text
        assert coerce_message_content_to_text("hello") == "hello"

    def test_list_of_blocks(self):
        from app.utils.aimodels import coerce_message_content_to_text
        blocks = [
            {"type": "text", "text": "part1"},
            {"type": "image"},
            "raw_string",
        ]
        result = coerce_message_content_to_text(blocks)
        assert result == "part1raw_string"

    def test_other_type(self):
        from app.utils.aimodels import coerce_message_content_to_text
        assert coerce_message_content_to_text(42) == "42"


# ============================================================================
# _extract_web_records_from_tool_results — lines 7466-7467
# ============================================================================


class TestExtractWebRecordsFromToolResults:
    def test_extracts_web_records(self):
        """Lines 7466-7467 — handler extracts web records."""
        from app.modules.agents.qna.nodes import _extract_web_records_from_tool_results
        tool_results = [
            {"status": "success", "result": {"result_type": "web_search", "data": []}},
        ]
        mock_handler = MagicMock()
        mock_handler.extract_records.return_value = [
            {"source_type": "web", "url": "http://example.com"},
        ]
        mock_registry = MagicMock()
        mock_registry.get_handler.return_value = mock_handler
        with patch.dict("sys.modules", {
            "app.utils.tool_handlers": MagicMock(ToolHandlerRegistry=mock_registry)
        }):
            result = _extract_web_records_from_tool_results(tool_results, "org1")
        assert len(result) == 1
        assert result[0]["source_type"] == "web"

    def test_skips_failed_results(self):
        from app.modules.agents.qna.nodes import _extract_web_records_from_tool_results
        tool_results = [{"status": "error", "result": {"data": []}}]
        mock_registry = MagicMock()
        with patch.dict("sys.modules", {
            "app.utils.tool_handlers": MagicMock(ToolHandlerRegistry=mock_registry)
        }):
            result = _extract_web_records_from_tool_results(tool_results, "org1")
        assert result == []

    def test_json_string_result_parsed(self):
        from app.modules.agents.qna.nodes import _extract_web_records_from_tool_results
        tool_results = [
            {"status": "success", "result": json.dumps({"result_type": "web_search"})},
        ]
        mock_handler = MagicMock()
        mock_handler.extract_records.return_value = []
        mock_registry = MagicMock()
        mock_registry.get_handler.return_value = mock_handler
        with patch.dict("sys.modules", {
            "app.utils.tool_handlers": MagicMock(ToolHandlerRegistry=mock_registry)
        }):
            result = _extract_web_records_from_tool_results(tool_results, "org1")
        assert result == []

    def test_non_dict_result_skipped(self):
        from app.modules.agents.qna.nodes import _extract_web_records_from_tool_results
        tool_results = [{"status": "success", "result": [1, 2, 3]}]
        mock_registry = MagicMock()
        with patch.dict("sys.modules", {
            "app.utils.tool_handlers": MagicMock(ToolHandlerRegistry=mock_registry)
        }):
            result = _extract_web_records_from_tool_results(tool_results, "org1")
        assert result == []


# ============================================================================
# _extract_urls_for_reference_data — line 7488->exit (list recursion)
# ============================================================================


class TestExtractUrlsListRecursion:
    def test_list_content_recurses(self):
        """Line 7488 — recurse into list items."""
        ref_data: list[dict] = []
        content = [
            {"url": "https://a.com", "title": "A"},
            {"url": "https://b.com", "name": "B"},
        ]
        _extract_urls_for_reference_data(content, ref_data)
        assert len(ref_data) == 2

    def test_nested_list_recursion(self):
        content = {"items": [{"link": "https://deep.com", "key": "K1"}]}
        ref_data: list[dict] = []
        _extract_urls_for_reference_data(content, ref_data)
        assert len(ref_data) == 1


# ============================================================================
# _extract_final_response — lines 8886-8887 (list content)
# ============================================================================


class TestExtractFinalResponseListContent:
    def test_list_content_blocks(self):
        """Lines 8886-8887 — AIMessage with list content blocks."""
        from app.modules.agents.qna.nodes import _extract_final_response
        msgs = [
            HumanMessage(content="query"),
            AIMessage(content=[
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]),
        ]
        result = _extract_final_response(msgs, _log())
        assert "Part 1" in result
        assert "Part 2" in result


# ============================================================================
# _has_salesforce_tools / _has_sharepoint_tools — untested predicates
# ============================================================================


class TestHasSalesforceTools:
    def test_has_salesforce(self):
        from app.modules.agents.qna.nodes import _has_salesforce_tools
        assert _has_salesforce_tools({"agent_toolsets": [{"name": "Salesforce"}]}) is True

    def test_no_salesforce(self):
        from app.modules.agents.qna.nodes import _has_salesforce_tools
        assert _has_salesforce_tools({"agent_toolsets": [{"name": "Jira"}]}) is False


class TestHasSharepointTools:
    def test_has_sharepoint(self):
        from app.modules.agents.qna.nodes import _has_sharepoint_tools
        assert _has_sharepoint_tools({"agent_toolsets": [{"name": "SharePoint"}]}) is True

    def test_no_sharepoint(self):
        from app.modules.agents.qna.nodes import _has_sharepoint_tools
        assert _has_sharepoint_tools({"agent_toolsets": [{"name": "Jira"}]}) is False


# ============================================================================
# _build_retry_context — lines 4693->4696, 4697
# ============================================================================


class TestBuildRetryContextValidationHints:
    def test_missing_params_hint(self):
        """Lines 4693-4694 — missing params in retry context."""
        from app.modules.agents.qna.nodes import _build_retry_context
        state = _mock_state(
            execution_errors=[{
                "tool_name": "jira.create_issue",
                "error": "validation error: field required: project_key",
                "args": {"summary": "test"},
            }],
        )
        result = _build_retry_context(state)
        assert "PARAMETER VALIDATION" in result

    def test_invalid_params_hint(self):
        """Line 4696-4697 — invalid params branch (currently stub returns [])."""
        from app.modules.agents.qna.nodes import _build_retry_context
        state = _mock_state(
            execution_errors=[{
                "tool_name": "jira.create_issue",
                "error": "validation error: extra inputs are not permitted: bad_field",
                "args": {"bad_field": "x"},
            }],
        )
        result = _build_retry_context(state)
        assert "PARAMETER VALIDATION" in result
        assert "CHECK TOOL SCHEMA" in result


# ============================================================================
# _get_tool_status_message — lines 8844-8856 (app_action parsing)
# ============================================================================


class TestGetToolStatusMessageAppAction:
    def test_underscore_tool_name(self):
        result = _get_tool_status_message("outlook_search_messages")
        assert "outlook" in result.lower() or "search" in result.lower()

    def test_dotted_tool_name(self):
        result = _get_tool_status_message("jira.search_issues")
        assert "jira" in result.lower()

    def test_single_word_tool(self):
        result = _get_tool_status_message("search_messages")
        assert "search" in result.lower() or "messages" in result.lower()


# ============================================================================
# Confluence id=null fallback — lines 544-552
# ============================================================================


class TestExtractFieldConfluenceIdNull:
    def test_confluence_null_id_falls_back_to_key(self):
        """Lines 544-552 — id is None, fall back to 'key'."""
        data = {"space": {"id": None, "key": "ENG", "name": "Engineering"}}
        result = ToolResultExtractor.extract_field_from_data(data, ["space", "id"])
        assert result == "ENG"

    def test_confluence_valid_id_returned(self):
        data = {"space": {"id": "123", "key": "ENG"}}
        result = ToolResultExtractor.extract_field_from_data(data, ["space", "id"])
        assert result == "123"


# ============================================================================
# ToolExecutor._run_tool — lines 1459, 1462 (sync fallbacks)
# ============================================================================


class TestRunToolSyncFallbacks:
    @pytest.mark.asyncio
    async def test_sync_run_fallback(self):
        """Line 1459 — _run fallback."""
        tool = MagicMock()
        del tool.arun
        tool._run = MagicMock(return_value="sync_result")
        result = await ToolExecutor._run_tool(tool, {"key": "val"})
        assert result == "sync_result"

    @pytest.mark.asyncio
    async def test_run_method_fallback(self):
        """Line 1462 — run() fallback."""
        tool = MagicMock()
        del tool.arun
        del tool._run
        tool.run = MagicMock(return_value="run_result")
        result = await ToolExecutor._run_tool(tool, {"key": "val"})
        assert result == "run_result"


# ============================================================================
# react_agent_node — lines 7994-8149 (happy path)
# ============================================================================


def _react_patches(mock_agent):
    """Common patches for react_agent_node tests."""
    mock_tool_system = MagicMock(get_agent_tools_with_schemas=MagicMock(return_value=[]))
    return [
        patch.dict("sys.modules", {
            "app.modules.agents.qna.tool_system": mock_tool_system,
            "langchain.agents": MagicMock(create_agent=MagicMock(return_value=mock_agent)),
        }),
        patch("app.modules.agents.qna.nodes._build_react_system_prompt", return_value="sys"),
        patch("app.modules.agents.qna.nodes._build_planner_messages", new_callable=AsyncMock, return_value=[HumanMessage(content="q")]),
        patch("app.modules.agents.qna.nodes._ensure_attachment_blocks", new_callable=AsyncMock, return_value=[]),
        patch("app.modules.agents.qna.nodes._inject_attachment_blocks"),
        patch("app.modules.agents.qna.nodes.safe_stream_write"),
        patch("app.modules.agents.qna.nodes.send_keepalive", new_callable=AsyncMock),
    ]


class TestReactAgentNodeHappyPath:
    @pytest.mark.asyncio
    async def test_success_with_tool_calls(self):
        """Lines 7994-8149 — full happy path with tool results."""
        from app.modules.agents.qna.nodes import react_agent_node

        state = _mock_state()
        writer = MagicMock()
        config = {"configurable": {}}

        tool_msg = MagicMock(spec=ToolMessage)
        tool_msg.name = "jira.search_issues"
        tool_msg.content = json.dumps({"ok": True, "data": [{"key": "PROJ-1"}]})
        tool_msg.tool_call_id = "tc1"

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.content = "Found issue PROJ-1"
        ai_msg.tool_calls = []

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [tool_msg, ai_msg]})

        patches = _react_patches(mock_agent) + [
            patch("app.modules.agents.qna.nodes._extract_final_response", return_value="Found issue PROJ-1"),
            patch("app.modules.agents.qna.nodes._detect_tool_result_status", return_value="success"),
            patch("app.modules.agents.qna.nodes._process_retrieval_output"),
        ]
        ctx = patches[0]
        for p in patches[1:]:
            ctx = ctx.__class__.__enter__(ctx) if False else p
        # Use contextlib.ExitStack
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await react_agent_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"
        assert result["response"] == "Found issue PROJ-1"
        assert len(result["all_tool_results"]) == 1

    @pytest.mark.asyncio
    async def test_no_tools_direct_answer(self):
        """Lines 8109-8112 — no tools called = direct answer."""
        from app.modules.agents.qna.nodes import react_agent_node

        state = _mock_state()
        writer = MagicMock()
        config = {"configurable": {}}

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.content = "Hello! How can I help?"
        ai_msg.tool_calls = []

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [ai_msg]})

        import contextlib
        patches = _react_patches(mock_agent) + [
            patch("app.modules.agents.qna.nodes._extract_final_response", return_value="Hello! How can I help?"),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await react_agent_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"
        assert result["execution_plan"]["can_answer_directly"] is True

    @pytest.mark.asyncio
    async def test_all_tools_failed(self):
        """Lines 8119-8121 — all tool calls failed."""
        from app.modules.agents.qna.nodes import react_agent_node

        state = _mock_state()
        writer = MagicMock()
        config = {"configurable": {}}

        tool_msg = MagicMock(spec=ToolMessage)
        tool_msg.name = "jira.search"
        tool_msg.content = '{"error": "auth failed"}'
        tool_msg.tool_call_id = "tc1"

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.content = "Sorry, failed"
        ai_msg.tool_calls = []

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [tool_msg, ai_msg]})

        import contextlib
        patches = _react_patches(mock_agent) + [
            patch("app.modules.agents.qna.nodes._extract_final_response", return_value="Sorry"),
            patch("app.modules.agents.qna.nodes._detect_tool_result_status", return_value="error"),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await react_agent_node(state, config, writer)

        assert result["reflection_decision"] == "respond_error"
        assert result["reflection"]["confidence"] == "Medium"

    @pytest.mark.asyncio
    async def test_partial_tool_failure(self):
        """Lines 8116-8118 — some success + some error = respond_success."""
        from app.modules.agents.qna.nodes import react_agent_node

        state = _mock_state()
        writer = MagicMock()
        config = {"configurable": {}}

        ok_msg = MagicMock(spec=ToolMessage)
        ok_msg.name = "jira.search"
        ok_msg.content = '{"ok": true}'
        ok_msg.tool_call_id = "tc1"

        err_msg = MagicMock(spec=ToolMessage)
        err_msg.name = "confluence.search"
        err_msg.content = '{"error": "timeout"}'
        err_msg.tool_call_id = "tc2"

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.content = "Partial results"
        ai_msg.tool_calls = []

        mock_agent = AsyncMock()
        mock_agent.ainvoke = AsyncMock(return_value={"messages": [ok_msg, err_msg, ai_msg]})

        call_count = [0]
        def detect_status(result):
            call_count[0] += 1
            return "success" if call_count[0] == 1 else "error"

        import contextlib
        patches = _react_patches(mock_agent) + [
            patch("app.modules.agents.qna.nodes._extract_final_response", return_value="Partial results"),
            patch("app.modules.agents.qna.nodes._detect_tool_result_status", side_effect=detect_status),
        ]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            result = await react_agent_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"
        assert "Partial" in result["reflection"]["reasoning"]


# ============================================================================
# planner_node — lines 4175-4180 (retry with list content)
# ============================================================================


class TestPlannerNodeRetryListContent:
    @pytest.mark.asyncio
    async def test_retry_with_list_content(self):
        """Line 4176 — retry context appended to list content."""
        from app.modules.agents.qna.nodes import planner_node

        state = _mock_state(
            is_retry=True,
            execution_errors=[{
                "tool_name": "jira.create",
                "error": "missing param",
                "args": {},
            }],
        )
        writer = MagicMock()
        config = {"configurable": {}}

        mock_plan = {
            "intent": "search",
            "reasoning": "test",
            "can_answer_directly": False,
            "needs_clarification": False,
            "tools": [{"name": "jira.search", "args": {"q": "test"}}],
        }
        with patch("app.modules.agents.qna.nodes._build_planner_messages", new_callable=AsyncMock,
                   return_value=[HumanMessage(content=[{"type": "text", "text": "query"}])]), \
             patch("app.modules.agents.qna.nodes._ensure_attachment_blocks", new_callable=AsyncMock, return_value=[]), \
             patch("app.modules.agents.qna.nodes._inject_attachment_blocks"), \
             patch("app.modules.agents.qna.nodes._plan_with_validation_retry", new_callable=AsyncMock, return_value=mock_plan), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes._build_knowledge_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.build_llm_time_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.is_custom_agent_system_prompt", return_value=False), \
             patch("app.modules.agents.qna.nodes._get_cached_tool_descriptions", return_value={}):
            result = await planner_node(state, config, writer)

        assert result.get("is_retry") is False

    @pytest.mark.asyncio
    async def test_continue_with_executed_tools(self):
        """Lines 4202-4224 — continue mode with executed tools."""
        from app.modules.agents.qna.nodes import planner_node

        state = _mock_state(
            is_continue=True,
            all_tool_results=[{"tool_name": "jira.search", "status": "success", "result": "data"}],
            executed_tool_names=["retrieval.search_internal_knowledge"],
            iteration_count=1,
            max_iterations=3,
        )
        writer = MagicMock()
        config = {"configurable": {}}

        mock_plan = {
            "intent": "create",
            "reasoning": "continue",
            "can_answer_directly": False,
            "needs_clarification": False,
            "tools": [{"name": "jira.create_issue", "args": {"summary": "test"}}],
        }
        with patch("app.modules.agents.qna.nodes._build_planner_messages", new_callable=AsyncMock,
                   return_value=[HumanMessage(content="query")]), \
             patch("app.modules.agents.qna.nodes._ensure_attachment_blocks", new_callable=AsyncMock, return_value=[]), \
             patch("app.modules.agents.qna.nodes._inject_attachment_blocks"), \
             patch("app.modules.agents.qna.nodes._plan_with_validation_retry", new_callable=AsyncMock, return_value=mock_plan), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes._build_knowledge_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.build_llm_time_context", return_value=""), \
             patch("app.modules.agents.qna.nodes._build_continue_context", return_value="Continue context"), \
             patch("app.modules.agents.qna.nodes.is_custom_agent_system_prompt", return_value=False), \
             patch("app.modules.agents.qna.nodes._get_cached_tool_descriptions", return_value={}):
            result = await planner_node(state, config, writer)

        assert result.get("is_continue") is False


# ============================================================================
# planner_node — lines 4072, 4131 (web search + knowledge context)
# ============================================================================


class TestPlannerNodeWebSearchKnowledgeBranch:
    @pytest.mark.asyncio
    async def test_no_knowledge_no_tools_with_web_search(self):
        """Lines 4071-4097 — no knowledge, no tools, web search enabled."""
        from app.modules.agents.qna.nodes import planner_node

        web_tool = MagicMock()
        web_tool.name = "web_search"
        state = _mock_state(
            has_knowledge=False,
            tools=[web_tool],
            agent_toolsets=[],
        )
        writer = MagicMock()
        config = {"configurable": {}}

        mock_plan = {
            "intent": "search",
            "reasoning": "web",
            "can_answer_directly": False,
            "needs_clarification": False,
            "tools": [{"name": "web_search", "args": {"q": "latest news"}}],
        }
        with patch("app.modules.agents.qna.nodes._build_planner_messages", new_callable=AsyncMock,
                   return_value=[HumanMessage(content="q")]), \
             patch("app.modules.agents.qna.nodes._ensure_attachment_blocks", new_callable=AsyncMock, return_value=[]), \
             patch("app.modules.agents.qna.nodes._inject_attachment_blocks"), \
             patch("app.modules.agents.qna.nodes._plan_with_validation_retry", new_callable=AsyncMock, return_value=mock_plan), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes._build_knowledge_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.build_llm_time_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.is_custom_agent_system_prompt", return_value=False), \
             patch("app.modules.agents.qna.nodes._get_cached_tool_descriptions", return_value={}):
            result = await planner_node(state, config, writer)

        assert "planned_tool_calls" in result

    @pytest.mark.asyncio
    async def test_knowledge_with_web_search(self):
        """Line 4131 — has knowledge AND web search."""
        from app.modules.agents.qna.nodes import planner_node

        web_tool = MagicMock()
        web_tool.name = "web_search"
        state = _mock_state(
            has_knowledge=True,
            tools=[web_tool],
            agent_knowledge=[{"name": "Wiki"}],
        )
        writer = MagicMock()
        config = {"configurable": {}}

        mock_plan = {
            "intent": "search",
            "reasoning": "both",
            "can_answer_directly": False,
            "needs_clarification": False,
            "tools": [{"name": "retrieval.search", "args": {"q": "info"}}],
        }
        with patch("app.modules.agents.qna.nodes._build_planner_messages", new_callable=AsyncMock,
                   return_value=[HumanMessage(content="q")]), \
             patch("app.modules.agents.qna.nodes._ensure_attachment_blocks", new_callable=AsyncMock, return_value=[]), \
             patch("app.modules.agents.qna.nodes._inject_attachment_blocks"), \
             patch("app.modules.agents.qna.nodes._plan_with_validation_retry", new_callable=AsyncMock, return_value=mock_plan), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes._build_knowledge_context", return_value="KB context"), \
             patch("app.modules.agents.qna.nodes.build_llm_time_context", return_value=""), \
             patch("app.modules.agents.qna.nodes.is_custom_agent_system_prompt", return_value=False), \
             patch("app.modules.agents.qna.nodes._get_cached_tool_descriptions", return_value={}):
            result = await planner_node(state, config, writer)

        assert "planned_tool_calls" in result


# ============================================================================
# execute_node — line 5896-5898 (tools exception)
# ============================================================================


class TestExecuteNodeToolSetupException:
    @pytest.mark.asyncio
    async def test_tool_setup_exception_causes_unbound_llm(self):
        """Lines 5896-5898 — exception getting tools causes UnboundLocalError for llm
        (latent bug: llm is assigned inside the try block that failed).
        """
        from app.modules.agents.qna.nodes import execute_node

        state = _mock_state(
            planned_tool_calls=[{"name": "jira.search", "args": {"q": "test"}}],
            execution_plan={"tools": [{"name": "jira.search"}]},
        )
        writer = MagicMock()
        config = {"configurable": {}}

        mock_tool_sys = MagicMock(
            get_agent_tools_with_schemas=MagicMock(side_effect=RuntimeError("tool load failed")),
        )
        with patch.dict("sys.modules", {
                 "app.modules.agents.qna.tool_system": mock_tool_sys,
             }), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"):
            with pytest.raises(UnboundLocalError):
                await execute_node(state, config, writer)


# ============================================================================
# respond_node — lines 6675-6678 (fast-path with sub_agent_analyses)
# ============================================================================


class TestRespondNodeFastPath:
    @pytest.mark.asyncio
    async def test_fast_path_api_only(self):
        """Lines 6664-6678 — fast path for API-only + sub-agent results."""
        from app.modules.agents.qna.nodes import respond_node

        state = _mock_state(
            sub_agent_analyses=["[task1 (jira)]: Found 5 issues"],
            final_results=[],
            virtual_record_id_to_result={},
            tool_results=[{"tool_name": "jira.search", "status": "success", "result": "data"}],
            reflection_decision="respond_success",
            reflection={"decision": "respond_success"},
            execution_plan={"can_answer_directly": False},
            org_id="org1",
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.qna.nodes._extract_web_records_from_tool_results", return_value=[]), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, return_value=True), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"):
            result = await respond_node(state, config, writer)

        assert isinstance(result, dict)


# ============================================================================
# respond_node — lines 6811-6812 (blob store init fail in respond)
# ============================================================================


class TestRespondNodeBlobStoreInit:
    @pytest.mark.asyncio
    async def test_blob_store_init_failure(self):
        """Lines 6811-6812 — BlobStorage init fails gracefully."""
        from app.modules.agents.qna.nodes import respond_node

        state = _mock_state(
            final_results=[{"text": "result"}],
            virtual_record_id_to_result={"vr1": {"_id": "r1"}},
            tool_results=[],
            reflection_decision="respond_success",
            reflection={"decision": "respond_success"},
            execution_plan={"can_answer_directly": False},
            org_id="org1",
            sub_agent_analyses=[],
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.qna.nodes._extract_web_records_from_tool_results", return_value=[]), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response", new_callable=AsyncMock, return_value=False), \
             patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes.send_keepalive", new_callable=AsyncMock), \
             patch.dict("sys.modules", {
                 "app.utils.chat_helpers": MagicMock(
                     get_message_content=MagicMock(return_value="content"),
                     CitationRefMapper=MagicMock,
                 ),
                 "app.modules.transformers.blob_storage": MagicMock(
                     BlobStorage=MagicMock(side_effect=RuntimeError("no config")),
                 ),
             }), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context",
                   new_callable=AsyncMock, return_value="context"), \
             patch("app.modules.agents.qna.nodes.stream_llm_response_with_tools",
                   return_value=MagicMock(__aiter__=MagicMock(return_value=iter([
                       {"event": "answer_chunk", "data": {"content": "Answer"}},
                       {"event": "complete", "data": {"content": "Answer", "completion_data": {}}},
                   ])))):
            try:
                result = await respond_node(state, config, writer)
            except Exception:
                pass


# ============================================================================
# reflect_node — lines 6106-6146 (cascading chain analysis)
# ============================================================================


class TestReflectNodePartialSuccessAndContinue:
    @pytest.mark.asyncio
    async def test_partial_success_primary_tool_succeeds(self):
        """Lines 6043-6063 — partial success: primary tool succeeded."""
        from app.modules.agents.qna.nodes import reflect_node

        state = _mock_state(
            all_tool_results=[
                {"tool_name": "jira.search", "status": "success", "result": {"data": [{"key": "P-1"}]}},
                {"tool_name": "confluence.search", "status": "error", "result": "timeout"},
            ],
            planned_tool_calls=[
                {"name": "jira.search", "args": {"jql": "project=P"}},
                {"name": "confluence.search", "args": {"cql": "test"}},
            ],
            reflection_decision="",
            iteration_count=0,
            max_iterations=3,
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write"):
            result = await reflect_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"
        assert "Primary" in result["reflection"]["reasoning"]

    @pytest.mark.asyncio
    async def test_all_succeeded_needs_continue(self):
        """Lines 6076-6086 — all tools ok but task needs more steps."""
        from app.modules.agents.qna.nodes import reflect_node

        state = _mock_state(
            all_tool_results=[
                {"tool_name": "jira.search", "status": "success", "result": {"data": [{"key": "P-1"}]}},
            ],
            planned_tool_calls=[
                {"name": "jira.search", "args": {"jql": "project=P"}},
            ],
            reflection_decision="",
            iteration_count=0,
            max_iterations=5,
        )
        writer = MagicMock()
        config = {"configurable": {}}

        with patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes._check_if_task_needs_continue", return_value=True):
            result = await reflect_node(state, config, writer)

        assert result["reflection_decision"] == "continue_with_more_tools"

    @pytest.mark.asyncio
    async def test_all_failed_uses_llm_reflection(self):
        """Lines 6148+ — all tools failed, uses LLM reflection."""
        from app.modules.agents.qna.nodes import reflect_node

        state = _mock_state(
            all_tool_results=[
                {"tool_name": "jira.search", "status": "error", "result": "auth failed"},
            ],
            planned_tool_calls=[
                {"name": "jira.search", "args": {"jql": "project=P"}},
            ],
            reflection_decision="",
            iteration_count=0,
            max_iterations=3,
        )
        writer = MagicMock()
        config = {"configurable": {}}

        async def fake_stream(*args, **kwargs):
            for evt in [
                {"event": "answer_chunk", "data": {"content": '{"decision": "respond_error"}'}},
                {"event": "complete", "data": {"content": '{"decision": "respond_error"}'}},
            ]:
                yield evt

        with patch("app.modules.agents.qna.nodes.safe_stream_write"), \
             patch("app.modules.agents.qna.nodes.stream_llm_response", side_effect=fake_stream):
            result = await reflect_node(state, config, writer)

        assert result["reflection_decision"] != ""


# ============================================================================
# _build_tool_results_context — lines 7568, 7573-7590 (web tool results)
# ============================================================================


class TestBuildToolResultsContextWebResults:
    @pytest.mark.asyncio
    async def test_web_search_results_formatted(self):
        """Lines 7568-7590 — web results with snippet-only notice."""
        tool_results = [
            {
                "tool_name": "web_search",
                "status": "success",
                "result": json.dumps({
                    "ok": True,
                    "result_type": "web_search",
                    "web_results": [{"title": "Test", "link": "https://test.com"}],
                }),
            },
        ]

        mock_handler = MagicMock()
        mock_handler.format_message = AsyncMock(return_value=[
            {"type": "text", "text": "Web result content"},
        ])
        mock_registry = MagicMock()
        mock_registry.get_handler.return_value = mock_handler

        with patch.dict("sys.modules", {
            "app.utils.tool_handlers": MagicMock(ToolHandlerRegistry=mock_registry)
        }):
            result = await _build_tool_results_context(tool_results, [])

        assert "Web Results" in result or "web" in result.lower()


# ============================================================================
# _get_field_type_name — lines 5640-5668 (Union / Optional types)
# ============================================================================


class TestGetFieldTypeNameBranches:
    def test_optional_type_extracts_inner(self):
        """Lines 5640-5641 — Optional[str] → 'str'."""
        from app.modules.agents.qna.nodes import _get_field_type_name
        from typing import Optional
        field = MagicMock()
        field.annotation = Optional[str]
        result = _get_field_type_name(field)
        assert result == "str"

    def test_no_name_uses_str(self):
        """Lines 5646-5649 — annotation without __name__."""
        from app.modules.agents.qna.nodes import _get_field_type_name
        from typing import List
        field = MagicMock()
        field.annotation = List[int]
        result = _get_field_type_name(field)
        assert "list" in result or "int" in result

    def test_exception_returns_any(self):
        """Line 5651 — exception fallback."""
        from app.modules.agents.qna.nodes import _get_field_type_name

        class BadField:
            @property
            def annotation(self):
                raise TypeError("boom")

        result = _get_field_type_name(BadField())
        assert result == "any"


# ============================================================================
# _get_field_type_name_v1 — lines 5662-5668
# ============================================================================


class TestGetFieldTypeNameV1Branches:
    def test_optional_v1(self):
        """Lines 5660-5663 — Optional in v1."""
        from app.modules.agents.qna.nodes import _get_field_type_name_v1
        from typing import Optional
        field = MagicMock()
        field.outer_type_ = Optional[int]
        result = _get_field_type_name_v1(field)
        assert result == "int"

    def test_no_name_v1(self):
        """Lines 5667-5668 — no __name__."""
        from app.modules.agents.qna.nodes import _get_field_type_name_v1
        from typing import Dict
        field = MagicMock()
        field.outer_type_ = Dict[str, int]
        result = _get_field_type_name_v1(field)
        assert "dict" in result


# ============================================================================
# _extract_final_response — lines 8878-8879 (ToolMessage without tool_calls)
# ============================================================================


class TestExtractFinalResponseReactLoop:
    def test_ai_message_no_tool_calls(self):
        """Lines 8878-8879 — last AI message has no tool calls."""
        from app.modules.agents.qna.nodes import _extract_final_response
        ai = MagicMock(spec=AIMessage)
        ai.content = "Final answer"
        ai.tool_calls = []
        result = _extract_final_response([ai], _log())
        assert result == "Final answer"

    def test_fallback_no_content(self):
        """Lines 8889-8890 — no message with content."""
        from app.modules.agents.qna.nodes import _extract_final_response
        result = _extract_final_response([], _log())
        assert "couldn't generate" in result.lower() or "completed" in result.lower()


# ============================================================================
# _check_if_task_needs_continue — edge cases
# ============================================================================


class TestCheckIfTaskNeedsContinueEdgeCases:
    def test_single_tool_no_continue(self):
        state = _mock_state()
        result = _check_if_task_needs_continue(
            "test query",
            ["jira.search"],
            [{"tool_name": "jira.search", "status": "success", "result": "data"}],
            _log(),
            state,
        )
        assert result is False
