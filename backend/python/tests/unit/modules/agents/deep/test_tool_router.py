"""Unit tests for app.modules.agents.deep.tool_router — pure functions."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.deep.tool_router import (
    _extract_params,
    _filter_tools_by_relevance,
    _format_tool_params,
    _get_type_name,
    _get_type_name_v1,
    _is_knowledge_task,
    _MAX_TOOLS_PER_TASK,
    assign_tools_to_tasks,
    build_domain_description,
    get_tools_for_sub_agent,
    group_tools_by_domain,
    UTILITY_DOMAINS,
    _DOMAIN_ALIASES,
)

log = logging.getLogger("test")


# ============================================================================
# _is_knowledge_task
# ============================================================================

class TestIsKnowledgeTask:
    def test_search_knowledge(self):
        assert _is_knowledge_task({"description": "Search knowledge base for policies"}) is True

    def test_find_information(self):
        assert _is_knowledge_task({"description": "Find information about onboarding"}) is True

    def test_look_up(self):
        assert _is_knowledge_task({"description": "Look up employee benefits"}) is True

    def test_what_is(self):
        assert _is_knowledge_task({"description": "What is our deployment process?"}) is True

    def test_retrieval_keyword(self):
        assert _is_knowledge_task({"description": "Retrieval of internal docs"}) is True

    def test_search_documents(self):
        assert _is_knowledge_task({"description": "Search documents for API spec"}) is True

    def test_non_knowledge_task(self):
        assert _is_knowledge_task({"description": "Create a Jira ticket"}) is False

    def test_empty_description(self):
        assert _is_knowledge_task({"description": ""}) is False

    def test_no_description(self):
        assert _is_knowledge_task({}) is False

    def test_case_insensitive(self):
        assert _is_knowledge_task({"description": "SEARCH KNOWLEDGE for X"}) is True


# ============================================================================
# _filter_tools_by_relevance
# ============================================================================

class TestFilterToolsByRelevance:
    def test_returns_max_tools(self):
        tools = [f"outlook.tool_{i}" for i in range(20)]
        task = {"description": "search for calendar events", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        assert len(result) == _MAX_TOOLS_PER_TASK

    def test_keyword_matching_scores_higher(self):
        tools = ["outlook.search_events", "outlook.delete_folder", "outlook.get_calendar"]
        task = {"description": "search for calendar events", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        # "search_events" should score higher due to word matches
        assert "outlook.search_events" in result

    def test_bigram_bonus(self):
        tools = ["outlook.get_recurring_events", "outlook.send_message", "outlook.delete_event"]
        task = {"description": "get recurring events for next week", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        assert result[0] == "outlook.get_recurring_events"

    def test_tool_description_scoring(self):
        schema_tool = MagicMock()
        schema_tool.description = "Search for emails matching specific criteria"
        tools = ["outlook.search_emails", "outlook.delete_folder"]
        task = {"description": "find emails about budget", "task_id": "t1"}
        result = _filter_tools_by_relevance(
            tools, task, {"outlook.search_emails": schema_tool}, log
        )
        assert "outlook.search_emails" in result

    def test_no_keyword_matches_returns_original_order(self):
        tools = ["a.tool1", "a.tool2", "a.tool3"]
        task = {"description": "zzzzz completely unrelated", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        assert result == tools[:_MAX_TOOLS_PER_TASK]

    def test_short_words_ignored(self):
        tools = ["a.is", "a.at", "a.search_documents"]
        task = {"description": "is at search documents", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        # "search_documents" should score higher due to longer words
        assert "a.search_documents" in result

    def test_tools_without_dot_separator(self):
        tools = ["simple_tool", "another_tool"]
        task = {"description": "simple operation", "task_id": "t1"}
        result = _filter_tools_by_relevance(tools, task, {}, log)
        assert "simple_tool" in result


# ============================================================================
# _extract_params
# ============================================================================

class TestExtractParams:
    def test_pydantic_v2_model(self):
        from pydantic import BaseModel, Field

        class TestSchema(BaseModel):
            query: str = Field(description="The search query")
            limit: int = Field(default=10, description="Max results")

        params = _extract_params(TestSchema)
        assert "query" in params
        assert params["query"]["type"] == "str"
        assert params["query"]["description"] == "The search query"
        assert "limit" in params

    def test_dict_schema(self):
        schema = {
            "properties": {
                "name": {"type": "string", "description": "User name"},
                "age": {"type": "integer", "description": "User age"},
            },
            "required": ["name"],
        }
        params = _extract_params(schema)
        assert params["name"]["required"] is True
        assert params["age"]["required"] is False
        assert params["name"]["type"] == "string"

    def test_empty_schema(self):
        assert _extract_params({}) == {}

    def test_no_properties_in_dict(self):
        schema = {"type": "object"}
        params = _extract_params(schema)
        assert params == {}

    def test_exception_returns_empty(self):
        # A schema that causes errors
        assert _extract_params(42) == {}


# ============================================================================
# _format_tool_params
# ============================================================================

class TestFormatToolParams:
    def test_no_schema_returns_empty(self):
        tool = MagicMock(spec=[])
        assert _format_tool_params(tool) == ""

    def test_formats_params(self):
        from pydantic import BaseModel, Field

        class MySchema(BaseModel):
            query: str = Field(description="Search query")
            limit: int = Field(default=5, description="Max items")

        tool = MagicMock()
        tool.args_schema = MySchema
        result = _format_tool_params(tool)
        assert "Parameters:" in result
        assert "query" in result
        assert "Search query" in result

    def test_truncates_long_description(self):
        from pydantic import BaseModel, Field

        class MySchema(BaseModel):
            field: str = Field(description="A" * 200)

        tool = MagicMock()
        tool.args_schema = MySchema
        result = _format_tool_params(tool)
        assert "..." in result


# ============================================================================
# _get_type_name
# ============================================================================

class TestGetTypeName:
    def test_simple_type(self):
        field = MagicMock()
        field.annotation = str
        assert _get_type_name(field) == "str"

    def test_optional_type(self):
        from typing import Optional
        field = MagicMock()
        field.annotation = Optional[int]
        result = _get_type_name(field)
        assert result == "int"

    def test_error_returns_any(self):
        field = MagicMock()
        field.annotation = None  # Will cause error
        del field.annotation  # Make it not exist
        result = _get_type_name(field)
        assert result == "any"


# ============================================================================
# _get_type_name_v1
# ============================================================================

class TestGetTypeNameV1:
    def test_simple_type(self):
        field = MagicMock()
        field.outer_type_ = str
        assert _get_type_name_v1(field) == "str"

    def test_error_returns_any(self):
        field = MagicMock(spec=[])
        result = _get_type_name_v1(field)
        assert result == "any"


# ============================================================================
# build_domain_description
# ============================================================================

class TestBuildDomainDescription:
    def test_empty_groups(self):
        result = build_domain_description({})
        assert "No external tool domains configured" in result

    def test_utility_domain_excluded(self):
        groups = {"utility": ["calculator.calc"], "outlook": ["outlook.search"]}
        result = build_domain_description(groups)
        assert "utility" not in result.lower().split("###")
        assert "### outlook" in result

    def test_tool_with_description(self):
        tool = MagicMock()
        tool.description = "Search events in Outlook"
        tool.args_schema = None
        state = {"schema_tool_map": {"outlook.search": tool}}
        groups = {"outlook": ["outlook.search"]}
        result = build_domain_description(groups, state=state)
        assert "search" in result.lower()
        assert "Search events" in result

    def test_tool_without_description(self):
        tool = MagicMock()
        tool.description = ""
        tool.args_schema = None
        state = {"schema_tool_map": {"outlook.search": tool}}
        groups = {"outlook": ["outlook.search"]}
        result = build_domain_description(groups, state=state)
        assert "search" in result.lower()

    def test_truncates_long_description(self):
        tool = MagicMock()
        tool.description = "X" * 200
        tool.args_schema = None
        state = {"schema_tool_map": {"outlook.search": tool}}
        groups = {"outlook": ["outlook.search"]}
        result = build_domain_description(groups, state=state)
        assert "..." in result

    def test_more_than_12_tools_shows_count(self):
        tools = [f"outlook.tool_{i}" for i in range(15)]
        groups = {"outlook": tools}
        result = build_domain_description(groups)
        assert "and 3 more tools" in result

    def test_no_state_still_works(self):
        groups = {"slack": ["slack.send_message"]}
        result = build_domain_description(groups, state=None)
        assert "### slack" in result

    def test_sanitized_name_fallback(self):
        tool = MagicMock()
        tool.description = "Send msg"
        tool.args_schema = None
        state = {"schema_tool_map": {"slack_send_message": tool}}
        groups = {"slack": ["slack.send_message"]}
        result = build_domain_description(groups, state=state)
        assert "Send msg" in result

    def test_tool_not_in_schema_map(self):
        state = {"schema_tool_map": {}}
        groups = {"slack": ["slack.send_message"]}
        result = build_domain_description(groups, state=state)
        assert "send_message" in result


# ============================================================================
# assign_tools_to_tasks
# ============================================================================

class TestAssignToolsToTasks:
    def _make_state(self, has_knowledge=False, schema_tool_map=None):
        return {
            "logger": log,
            "has_knowledge": has_knowledge,
            "schema_tool_map": schema_tool_map or {},
        }

    def test_assigns_domain_tools(self):
        tasks = [{"task_id": "t1", "domains": ["outlook"]}]
        groups = {"outlook": ["outlook.search", "outlook.create"], "utility": ["calc"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "outlook.search" in result[0]["tools"]
        assert "outlook.create" in result[0]["tools"]
        assert "calc" in result[0]["tools"]  # utility always included

    def test_deduplicates_tools(self):
        tasks = [{"task_id": "t1", "domains": ["outlook"]}]
        groups = {"outlook": ["outlook.search"], "utility": ["outlook.search"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        assert result[0]["tools"].count("outlook.search") == 1

    def test_adds_retrieval_for_knowledge_task(self):
        tasks = [{"task_id": "t1", "domains": ["retrieval"],
                  "description": "Search knowledge for X"}]
        groups = {"retrieval": ["retrieval.search"], "utility": ["calc"]}
        state = self._make_state(has_knowledge=True)
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "retrieval.search" in result[0]["tools"]

    def test_no_retrieval_without_knowledge(self):
        tasks = [{"task_id": "t1", "domains": ["retrieval"],
                  "description": "Search knowledge for X"}]
        groups = {"retrieval": ["retrieval.search"], "utility": ["calc"]}
        state = self._make_state(has_knowledge=False)
        result = assign_tools_to_tasks(tasks, groups, state)
        # retrieval not added via knowledge path, but it IS the domain
        assert "retrieval.search" in result[0]["tools"]

    def test_domain_alias_normalization(self):
        tasks = [{"task_id": "t1", "domains": ["googledrive"]}]
        groups = {"google_drive": ["google_drive.list_files"], "utility": ["calc"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "google_drive.list_files" in result[0]["tools"]

    def test_knowledge_detection_from_description(self):
        tasks = [{"task_id": "t1", "domains": ["other"],
                  "description": "Find information about deployment"}]
        groups = {"other": ["other.tool"], "retrieval": ["retrieval.search"], "utility": ["calc"]}
        state = self._make_state(has_knowledge=True)
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "retrieval.search" in result[0]["tools"]

    def test_knowledgehub_added_when_domain_matches(self):
        tasks = [{"task_id": "t1", "domains": ["knowledgehub"]}]
        groups = {"knowledgehub": ["kh.list"], "utility": ["calc"]}
        state = self._make_state(has_knowledge=True)
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "kh.list" in result[0]["tools"]

    def test_multi_step_task_skips_filtering(self):
        # Even with > 8 tools, multi-step tasks get all tools
        tools = [f"outlook.tool_{i}" for i in range(15)]
        tasks = [{"task_id": "t1", "domains": ["outlook"],
                  "multi_step": True, "sub_steps": [{"step": "1"}]}]
        groups = {"outlook": tools, "utility": ["calc"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        # All 15 outlook tools + utility
        assert len([t for t in result[0]["tools"] if t.startswith("outlook.")]) == 15


# ============================================================================
# get_tools_for_sub_agent
# ============================================================================

class TestGetToolsForSubAgent:
    def test_filters_by_original_name(self):
        tool1 = MagicMock()
        tool1.name = "outlook_search"
        tool1._original_name = "outlook.search"
        tool2 = MagicMock()
        tool2.name = "slack_send"
        tool2._original_name = "slack.send"
        state = {"cached_structured_tools": [tool1, tool2]}
        result = get_tools_for_sub_agent(["outlook.search"], state)
        assert len(result) == 1
        assert result[0] is tool1

    def test_filters_by_sanitized_name(self):
        tool = MagicMock()
        tool.name = "outlook_search"
        tool._original_name = "outlook.search"
        state = {"cached_structured_tools": [tool]}
        result = get_tools_for_sub_agent(["outlook_search"], state)
        assert len(result) == 1

    def test_dot_replacement_fallback(self):
        tool = MagicMock()
        tool.name = "outlook_search"
        tool._original_name = "outlook_search"
        state = {"cached_structured_tools": [tool]}
        result = get_tools_for_sub_agent(["outlook.search"], state)
        assert len(result) == 1

    def test_empty_assigned_names(self):
        tool = MagicMock()
        tool.name = "tool"
        tool._original_name = "tool"
        state = {"cached_structured_tools": [tool]}
        result = get_tools_for_sub_agent([], state)
        assert result == []

    def test_no_cached_tools_fallback(self):
        # When cache is None, it tries to load fresh via get_agent_tools_with_schemas
        with patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
                    return_value=[]) as mock_load:
            state = {"cached_structured_tools": None, "logger": log}
            result = get_tools_for_sub_agent(["tool"], state)
            mock_load.assert_called_once()
            assert result == []


# ============================================================================
# group_tools_by_domain
# ============================================================================

class TestGroupToolsByDomain:
    """Tests for group_tools_by_domain — covers dot-prefix grouping,
    utility classification, alias normalization, caching, and error handling."""

    def _make_tool(self, sanitized_name: str, original_name: str = None):
        tool = MagicMock()
        tool.name = sanitized_name
        tool._original_name = original_name or sanitized_name
        return tool

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_groups_tools_by_dot_prefix(self, mock_get_tools):
        """Tools with 'domain.action' original names are grouped under 'domain'."""
        t1 = self._make_tool("outlook_search_events", "outlook.search_events")
        t2 = self._make_tool("outlook_create_event", "outlook.create_event")
        t3 = self._make_tool("slack_send_message", "slack.send_message")
        mock_get_tools.return_value = [t1, t2, t3]

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert "outlook" in groups
        assert len(groups["outlook"]) == 2
        assert "outlook.search_events" in groups["outlook"]
        assert "outlook.create_event" in groups["outlook"]
        assert "slack" in groups
        assert "slack.send_message" in groups["slack"]

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_utility_domain_classification(self, mock_get_tools):
        """Tools from known utility domains are merged into 'utility'."""
        t1 = self._make_tool("calculator_calculate", "calculator.calculate")
        t2 = self._make_tool("datetime_now", "datetime.get_current_datetime")
        t3 = self._make_tool("web_search_query", "web_search.query")
        mock_get_tools.return_value = [t1, t2, t3]

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert "utility" in groups
        assert len(groups["utility"]) == 3
        # They should not appear as separate domain keys
        assert "calculator" not in groups
        assert "datetime" not in groups
        assert "web_search" not in groups

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_alias_normalization(self, mock_get_tools):
        """googledrive and google-drive are both normalized to google_drive."""
        t1 = self._make_tool("googledrive_list", "googledrive.list_files")
        mock_get_tools.return_value = [t1]

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert "google_drive" in groups
        assert "googledrive" not in groups
        assert "googledrive.list_files" in groups["google_drive"]

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_no_dot_treated_as_utility(self, mock_get_tools):
        """Tools without dot in name default to 'utility' domain."""
        t1 = self._make_tool("simple_tool", "simple_tool")
        mock_get_tools.return_value = [t1]

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert "utility" in groups
        assert "simple_tool" in groups["utility"]

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_caches_tools_and_schema_map(self, mock_get_tools):
        """Structured tools and schema_tool_map are cached in state."""
        t1 = self._make_tool("outlook_search", "outlook.search")
        mock_get_tools.return_value = [t1]

        state = {"logger": log}
        group_tools_by_domain(state)

        assert state["cached_structured_tools"] == [t1]
        assert "outlook.search" in state["schema_tool_map"]
        assert "outlook_search" in state["schema_tool_map"]

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_schema_map_has_both_original_and_sanitized(self, mock_get_tools):
        """Both the original (dotted) and sanitized names map to the tool."""
        t1 = self._make_tool("jira_create_issue", "jira.create_issue")
        mock_get_tools.return_value = [t1]

        state = {"logger": log}
        group_tools_by_domain(state)

        assert state["schema_tool_map"]["jira.create_issue"] is t1
        assert state["schema_tool_map"]["jira_create_issue"] is t1

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_exception_returns_empty_groups(self, mock_get_tools):
        """When get_agent_tools_with_schemas raises, an empty group dict is returned."""
        mock_get_tools.side_effect = RuntimeError("import error")

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert groups == {}
        assert state["cached_structured_tools"] == []

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_empty_tools_returns_empty_groups(self, mock_get_tools):
        """No tools registered produces empty groups."""
        mock_get_tools.return_value = []

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert groups == {}

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_mixed_utility_and_domain_tools(self, mock_get_tools):
        """Mix of utility and domain-specific tools are grouped correctly."""
        t1 = self._make_tool("outlook_search", "outlook.search")
        t2 = self._make_tool("calculator_calc", "calculator.calc")
        t3 = self._make_tool("jira_create", "jira.create")
        t4 = self._make_tool("utility_helper", "utility.helper")
        mock_get_tools.return_value = [t1, t2, t3, t4]

        state = {"logger": log}
        groups = group_tools_by_domain(state)

        assert "outlook" in groups
        assert "jira" in groups
        assert "utility" in groups
        assert len(groups["utility"]) == 2  # calculator.calc + utility.helper

    @patch("app.modules.agents.qna.tool_system.get_agent_tools_with_schemas")
    def test_state_without_logger_uses_module_logger(self, mock_get_tools):
        """When state has no 'logger' key, the module-level logger is used."""
        t1 = self._make_tool("slack_send", "slack.send")
        mock_get_tools.return_value = [t1]

        state = {}  # no logger key
        groups = group_tools_by_domain(state)

        assert "slack" in groups
        assert state["cached_structured_tools"] == [t1]


# ============================================================================
# Additional coverage tests for tool_router
# ============================================================================

class TestAssignToolsToTasksEdgeCases:
    """Cover uncovered branches in assign_tools_to_tasks and _filter_tools_by_relevance."""

    def _make_state(self, has_knowledge=False, schema_tool_map=None):
        return {
            "logger": log,
            "has_knowledge": has_knowledge,
            "schema_tool_map": schema_tool_map or {},
        }

    def test_domain_not_normalized_falls_back_to_original(self):
        """When normalized domain not in groups, falls back to original domain (line 145-146)."""
        tasks = [{"task_id": "t1", "domains": ["custom_domain"]}]
        groups = {"custom_domain": ["custom_domain.tool1"], "utility": ["calc"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "custom_domain.tool1" in result[0]["tools"]

    def test_filtering_triggered_with_many_tools(self):
        """When domain has > _MAX_TOOLS_PER_TASK tools, filtering kicks in (lines 151-154)."""
        tools = [f"outlook.tool_{i}" for i in range(20)]
        tasks = [{"task_id": "t1", "domains": ["outlook"],
                  "description": "search for events matching calendar"}]
        groups = {"outlook": tools, "utility": ["calc"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        # Should be filtered down
        outlook_tools = [t for t in result[0]["tools"] if t.startswith("outlook.")]
        assert len(outlook_tools) <= _MAX_TOOLS_PER_TASK

    def test_no_utility_group_doesnt_crash(self):
        """When no utility group exists, no crash (line 159→163)."""
        tasks = [{"task_id": "t1", "domains": ["slack"]}]
        groups = {"slack": ["slack.send"]}
        state = self._make_state()
        result = assign_tools_to_tasks(tasks, groups, state)
        assert "slack.send" in result[0]["tools"]


class TestBuildDomainDescriptionEdgeCases:
    """Cover uncovered branches in build_domain_description."""

    def test_tool_without_schema_in_map(self):
        """Tool not in schema_tool_map (line 273-275)."""
        state = {"schema_tool_map": {"other_tool": MagicMock()}}
        groups = {"slack": ["slack.send_message"]}
        result = build_domain_description(groups, state=state)
        assert "send_message" in result

    def test_tool_with_params(self):
        """Tool with args_schema produces parameter output (line 273)."""
        from pydantic import BaseModel, Field

        class TestSchema(BaseModel):
            query: str = Field(description="Search text")

        tool = MagicMock()
        tool.description = "Search messages"
        tool.args_schema = TestSchema
        state = {"schema_tool_map": {"slack.search": tool}}
        groups = {"slack": ["slack.search"]}
        result = build_domain_description(groups, state=state)
        assert "Parameters:" in result


class TestExtractParamsEdgeCases:
    """Cover uncovered branches in _extract_params."""

    def test_pydantic_v1_style_fields(self):
        """Test _extract_params with Pydantic v1-style __fields__ (lines 358-365)."""
        mock_schema = MagicMock(spec=[])
        mock_schema.model_fields = None
        del mock_schema.model_fields  # Remove v2 field

        field_info = MagicMock()
        field_info.required = True
        field_info.field_info = MagicMock()
        field_info.field_info.description = "A search query"
        field_info.outer_type_ = str

        mock_schema.__fields__ = {"query": field_info}

        params = _extract_params(mock_schema)
        assert "query" in params
        assert params["query"]["required"] is True
        assert params["query"]["description"] == "A search query"

    def test_exception_in_extract_returns_empty(self):
        """_extract_params returns {} on exception (line 380)."""
        result = _extract_params(None)
        assert result == {}


class TestGetTypeNameV1EdgeCases:
    """Cover uncovered branches in _get_type_name_v1."""

    def test_optional_type_v1(self):
        """Pydantic v1 Optional[str] -> 'str' (lines 410-412)."""
        from typing import Optional
        field = MagicMock()
        field.outer_type_ = Optional[str]
        result = _get_type_name_v1(field)
        assert result == "str"

    def test_non_named_type_v1(self):
        """Type without __name__ uses str() fallback (line 415)."""
        from typing import List
        field = MagicMock()
        field.outer_type_ = List[str]
        result = _get_type_name_v1(field)
        assert "list" in result.lower()

    def test_annotation_without_name(self):
        """_get_type_name with type lacking __name__ uses str() (line 398)."""
        from typing import List
        field = MagicMock()
        field.annotation = List[str]
        result = _get_type_name(field)
        assert "list" in result.lower()


class TestFilterToolsEdgeCases:
    """Cover remaining _filter_tools_by_relevance branches."""

    def test_tool_with_schema_description(self):
        """Tool description from schema_tool_map is used for scoring (lines 466-471)."""
        schema_tool = MagicMock()
        schema_tool.description = "Search for calendar events with specific criteria"
        tools = [f"outlook.tool_{i}" for i in range(15)]
        tools[0] = "outlook.search_events"
        task = {"description": "search calendar events", "task_id": "t1"}
        result = _filter_tools_by_relevance(
            tools, task, {"outlook.search_events": schema_tool}, log
        )
        assert "outlook.search_events" in result
