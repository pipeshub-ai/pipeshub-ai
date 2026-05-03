import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.agents.qna.nodes import (
    NodeConfig,
    PlaceholderResolver,
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
        assert ref[0]["url"] == "https://example.com"

    def test_invalid_json_string(self):
        ref = []
        _extract_urls_for_reference_data("not json", ref)
        assert len(ref) == 0

    def test_no_duplicate_urls(self):
        ref = [{"url": "https://example.com"}]
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
            msgs = _build_planner_messages(state, "search for docs", _log())
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
            msgs = _build_planner_messages(state, "create a page", _log())
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
            msgs = _build_planner_messages(state, "do something", _log())
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
