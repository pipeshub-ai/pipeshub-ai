"""
Additional coverage tests for app.modules.agents.qna.chat_state

Targets uncovered helper functions:
- _build_tool_to_toolset_map
- _extract_tools_from_toolsets
- _extract_knowledge_connector_ids
- _extract_kb_record_groups
- _build_web_search_tool_config
- cleanup_state_after_retrieval
- cleanup_old_tool_results
"""

import pytest

from app.modules.agents.qna.chat_state import (
    _build_tool_to_toolset_map,
    _build_web_search_tool_config,
    _extract_kb_record_groups,
    _extract_knowledge_connector_ids,
    _extract_tools_from_toolsets,
    cleanup_old_tool_results,
    cleanup_state_after_retrieval,
)


# ---------------------------------------------------------------------------
# _build_tool_to_toolset_map
# ---------------------------------------------------------------------------

class TestBuildToolToToolsetMap:
    def test_empty_input(self):
        assert _build_tool_to_toolset_map([]) == {}
        assert _build_tool_to_toolset_map(None) == {}

    def test_single_toolset_with_tools(self):
        toolsets = [{
            "name": "jira",
            "instanceId": "inst1",
            "tools": [{"fullName": "jira.search_issues"}, {"fullName": "jira.get_issue"}],
        }]
        result = _build_tool_to_toolset_map(toolsets)
        assert result["jira.search_issues"] == "inst1"
        assert result["jira.get_issue"] == "inst1"

    def test_toolset_without_instance_id_skipped(self):
        toolsets = [{
            "name": "jira",
            "instanceId": "",
            "tools": [{"fullName": "jira.search"}],
        }]
        assert _build_tool_to_toolset_map(toolsets) == {}

    def test_toolset_with_selected_tools_fallback(self):
        toolsets = [{
            "name": "slack",
            "instanceId": "inst2",
            "tools": [],
            "selectedTools": ["send_message", "search_messages"],
        }]
        result = _build_tool_to_toolset_map(toolsets)
        assert result["slack.send_message"] == "inst2"
        assert result["slack.search_messages"] == "inst2"

    def test_selected_tools_with_dot_prefix(self):
        toolsets = [{
            "name": "slack",
            "instanceId": "inst3",
            "tools": [],
            "selectedTools": ["slack.send_message"],
        }]
        result = _build_tool_to_toolset_map(toolsets)
        assert result["slack.send_message"] == "inst3"

    def test_tool_name_constructed_from_name_field(self):
        toolsets = [{
            "name": "jira",
            "instanceId": "inst1",
            "tools": [{"name": "search"}],
        }]
        result = _build_tool_to_toolset_map(toolsets)
        assert "jira.search" in result

    def test_multiple_toolsets(self):
        toolsets = [
            {"name": "jira", "instanceId": "i1", "tools": [{"fullName": "jira.search"}]},
            {"name": "slack", "instanceId": "i2", "tools": [{"fullName": "slack.send"}]},
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert result["jira.search"] == "i1"
        assert result["slack.send"] == "i2"


# ---------------------------------------------------------------------------
# _extract_tools_from_toolsets
# ---------------------------------------------------------------------------

class TestExtractToolsFromToolsets:
    def test_empty_input(self):
        assert _extract_tools_from_toolsets([]) == []
        assert _extract_tools_from_toolsets(None) == []

    def test_single_toolset(self):
        toolsets = [{"name": "jira", "tools": [{"fullName": "jira.search"}, {"fullName": "jira.get"}]}]
        result = _extract_tools_from_toolsets(toolsets)
        assert "jira.search" in result
        assert "jira.get" in result

    def test_selected_tools_fallback(self):
        toolsets = [{"name": "slack", "tools": [], "selectedTools": ["send", "search"]}]
        result = _extract_tools_from_toolsets(toolsets)
        assert "slack.send" in result
        assert "slack.search" in result

    def test_tool_name_from_toolName_field(self):
        toolsets = [{"name": "confluence", "tools": [{"toolName": "get_page"}]}]
        result = _extract_tools_from_toolsets(toolsets)
        assert "confluence.get_page" in result

    def test_non_dict_toolset_skipped(self):
        toolsets = ["invalid", {"name": "jira", "tools": [{"fullName": "jira.search"}]}]
        result = _extract_tools_from_toolsets(toolsets)
        assert result == ["jira.search"]


# ---------------------------------------------------------------------------
# _extract_knowledge_connector_ids
# ---------------------------------------------------------------------------

class TestExtractKnowledgeConnectorIds:
    def test_empty_input(self):
        assert _extract_knowledge_connector_ids([]) == []
        assert _extract_knowledge_connector_ids(None) == []

    def test_single_connector(self):
        knowledge = [{"connectorId": "conn1"}]
        result = _extract_knowledge_connector_ids(knowledge)
        assert result == ["conn1"]

    def test_kb_prefix_excluded(self):
        knowledge = [
            {"connectorId": "conn1"},
            {"connectorId": "knowledgeBase_kb1"},
        ]
        result = _extract_knowledge_connector_ids(knowledge)
        assert result == ["conn1"]

    def test_deduplication(self):
        knowledge = [
            {"connectorId": "conn1"},
            {"connectorId": "conn1"},
            {"connectorId": "conn2"},
        ]
        result = _extract_knowledge_connector_ids(knowledge)
        assert result == ["conn1", "conn2"]

    def test_non_string_connector_skipped(self):
        knowledge = [{"connectorId": 123}, {"connectorId": "valid"}]
        result = _extract_knowledge_connector_ids(knowledge)
        assert result == ["valid"]

    def test_missing_connector_id(self):
        knowledge = [{"other": "data"}]
        result = _extract_knowledge_connector_ids(knowledge)
        assert result == []


# ---------------------------------------------------------------------------
# _extract_kb_record_groups
# ---------------------------------------------------------------------------

class TestExtractKbRecordGroups:
    def test_empty_input(self):
        assert _extract_kb_record_groups([]) == []
        assert _extract_kb_record_groups(None) == []

    def test_dict_filters_with_record_groups(self):
        knowledge = [{"filters": {"recordGroups": ["rg1", "rg2"]}}]
        result = _extract_kb_record_groups(knowledge)
        assert result == ["rg1", "rg2"]

    def test_string_filters_parsed_as_json(self):
        import json
        knowledge = [{"filters": json.dumps({"recordGroups": ["rg1"]})}]
        result = _extract_kb_record_groups(knowledge)
        assert result == ["rg1"]

    def test_invalid_json_filters_handled(self):
        knowledge = [{"filters": "not valid json"}]
        result = _extract_kb_record_groups(knowledge)
        assert result == []

    def test_no_record_groups_key(self):
        knowledge = [{"filters": {"other": "data"}}]
        result = _extract_kb_record_groups(knowledge)
        assert result == []

    def test_multiple_knowledge_items(self):
        knowledge = [
            {"filters": {"recordGroups": ["rg1"]}},
            {"filters": {"recordGroups": ["rg2", "rg3"]}},
        ]
        result = _extract_kb_record_groups(knowledge)
        assert result == ["rg1", "rg2", "rg3"]

    def test_empty_record_groups(self):
        knowledge = [{"filters": {"recordGroups": []}}]
        result = _extract_kb_record_groups(knowledge)
        assert result == []


# ---------------------------------------------------------------------------
# _build_web_search_tool_config
# ---------------------------------------------------------------------------

class TestBuildWebSearchToolConfig:
    def test_no_config_returns_none(self):
        assert _build_web_search_tool_config({}) is None

    def test_valid_config(self):
        chat_query = {
            "webSearchConfig": {
                "provider": "tavily",
                "configuration": {"apiKey": "xxx"},
            }
        }
        result = _build_web_search_tool_config(chat_query)
        assert result["provider"] == "tavily"
        assert result["configuration"]["apiKey"] == "xxx"

    def test_provider_normalized_to_lowercase(self):
        chat_query = {"webSearchConfig": {"provider": "TAVILY"}}
        result = _build_web_search_tool_config(chat_query)
        assert result["provider"] == "tavily"

    def test_empty_provider_returns_none(self):
        chat_query = {"webSearchConfig": {"provider": ""}}
        assert _build_web_search_tool_config(chat_query) is None

    def test_non_dict_config_returns_none(self):
        chat_query = {"webSearchConfig": "not a dict"}
        assert _build_web_search_tool_config(chat_query) is None

    def test_non_dict_configuration_defaults_to_empty(self):
        chat_query = {"webSearchConfig": {"provider": "tavily", "configuration": "invalid"}}
        result = _build_web_search_tool_config(chat_query)
        assert result["configuration"] == {}


# ---------------------------------------------------------------------------
# cleanup_state_after_retrieval
# ---------------------------------------------------------------------------

class TestCleanupStateAfterRetrieval:
    def test_cleans_up_with_final_results(self):
        state = {
            "final_results": [{"data": "x"}],
            "decomposed_queries": ["q1"],
            "rewritten_queries": ["rq1"],
            "expanded_queries": ["eq1"],
            "web_search_queries": ["wq1"],
            "search_results": [{"r": 1}],
            "query_analysis": {"intent": "search", "complexity": "simple", "extra": "foo"},
        }
        cleanup_state_after_retrieval(state)
        assert state["decomposed_queries"] == []
        assert state["search_results"] == []
        assert state["query_analysis"]["intent"] == "search"
        assert "extra" not in state["query_analysis"]

    def test_no_final_results_no_cleanup(self):
        state = {
            "decomposed_queries": ["q1"],
            "query_analysis": None,
        }
        cleanup_state_after_retrieval(state)
        assert state["decomposed_queries"] == ["q1"]

    def test_no_query_analysis_no_error(self):
        state = {"final_results": [{"x": 1}]}
        cleanup_state_after_retrieval(state)
        assert state["final_results"] == [{"x": 1}]


# ---------------------------------------------------------------------------
# cleanup_old_tool_results
# ---------------------------------------------------------------------------

class TestCleanupOldToolResults:
    def test_short_list_unchanged(self):
        state = {"all_tool_results": [1, 2, 3]}
        cleanup_old_tool_results(state, keep_last_n=10)
        assert state["all_tool_results"] == [1, 2, 3]

    def test_trims_to_keep_last_n(self):
        state = {"all_tool_results": list(range(20))}
        cleanup_old_tool_results(state, keep_last_n=5)
        assert state["all_tool_results"] == [15, 16, 17, 18, 19]
        assert state.get("tool_execution_summary") == {}
        assert state.get("tool_repetition_warnings") == []

    def test_empty_list(self):
        state = {"all_tool_results": []}
        cleanup_old_tool_results(state)
        assert state["all_tool_results"] == []

    def test_exactly_at_limit(self):
        state = {"all_tool_results": list(range(10))}
        cleanup_old_tool_results(state, keep_last_n=10)
        assert state["all_tool_results"] == list(range(10))
