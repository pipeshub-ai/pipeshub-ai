"""Unit tests for app.modules.agents.qna.chat_state — state builders and helpers."""

from unittest.mock import MagicMock

import pytest

from app.modules.agents.qna.chat_state import (
    _build_tool_to_toolset_map,
    _extract_kb_record_groups,
    _extract_knowledge_connector_ids,
    _extract_tools_from_toolsets,
    build_initial_state,
    cleanup_old_tool_results,
    cleanup_state_after_retrieval,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_deps():
    """Return a dict of mock dependencies required by build_initial_state."""
    return {
        "llm": MagicMock(),
        "logger": MagicMock(),
        "retrieval_service": MagicMock(),
        "graph_provider": MagicMock(),
        "reranker_service": MagicMock(),
        "config_service": MagicMock(),
        "model_name": "gpt-test",
        "model_key": "test-model-key",
    }


@pytest.fixture
def minimal_chat_query():
    return {"query": "What is the revenue?"}


@pytest.fixture
def minimal_user_info():
    return {
        "orgId": "org-1",
        "userId": "user-1",
        "userEmail": "user@test.com",
        "sendUserInfo": True,
    }


# ===================================================================
# build_initial_state
# ===================================================================
class TestBuildInitialState:
    def test_default_values(self, mock_deps, minimal_chat_query, minimal_user_info):
        state = build_initial_state(
            chat_query=minimal_chat_query,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert state["query"] == "What is the revenue?"
        assert state["limit"] == 50
        assert state["messages"] == []
        assert state["previous_conversations"] == []
        assert state["quick_mode"] is False
        assert state["retrieval_mode"] == "HYBRID"
        assert state["chat_mode"] == "standard"
        assert state["graph_type"] == "legacy"
        assert state["decomposed_queries"] == []
        assert state["rewritten_queries"] == []
        assert state["expanded_queries"] == []
        assert state["web_search_queries"] == []
        assert state["search_results"] == []
        assert state["final_results"] == []
        assert state["response"] is None
        assert state["error"] is None
        assert state["org_id"] == "org-1"
        assert state["user_id"] == "user-1"
        assert state["user_email"] == "user@test.com"
        assert state["send_user_info"] is True
        assert state["system_prompt"] == "You are an enterprise questions answering expert"
        assert state["pending_tool_calls"] is False
        assert state["tool_results"] is None
        assert state["retry_count"] == 0
        assert state["max_retries"] == 1
        assert state["is_retry"] is False
        assert state["iteration_count"] == 0
        assert state["is_continue"] is False
        assert state["force_final_response"] is False
        assert state["loop_detected"] is False
        assert state["has_knowledge"] is False
        assert state["virtual_record_id_to_result"] == {}
        assert state["record_label_to_uuid_map"] == {}
        assert state["is_multimodal_llm"] is False
        assert state["model_name"] == "gpt-test"
        assert state["model_key"] == "test-model-key"

    def test_custom_chat_query_fields(self, mock_deps, minimal_user_info):
        cq = {
            "query": "q",
            "limit": 10,
            "quickMode": True,
            "chatMode": "analysis",
            "retrievalMode": "VECTOR",
            "systemPrompt": "Be brief",
            "instructions": "Focus on finance",
            "timezone": "America/New_York",
            "currentTime": "2025-01-01T00:00:00",
            "outputFilePath": "/tmp/out.txt",
            "conversationId": "conv-123",
            "previousConversations": [{"role": "user_query", "content": "hi"}],
        }
        state = build_initial_state(
            chat_query=cq,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert state["limit"] == 10
        assert state["quick_mode"] is True
        assert state["chat_mode"] == "analysis"
        assert state["retrieval_mode"] == "VECTOR"
        assert state["system_prompt"] == "Be brief"
        assert state["instructions"] == "Focus on finance"
        assert state["timezone"] == "America/New_York"
        assert state["current_time"] == "2025-01-01T00:00:00"
        assert state["output_file_path"] == "/tmp/out.txt"
        assert state["conversation_id"] == "conv-123"
        assert len(state["previous_conversations"]) == 1

    def test_with_toolsets(self, mock_deps, minimal_user_info):
        cq = {
            "query": "q",
            "toolsets": [
                {
                    "name": "slack",
                    "instanceId": "inst-1",
                    "tools": [
                        {"fullName": "slack.send_message", "name": "send_message"},
                        {"name": "list_channels"},
                    ],
                    "selectedTools": [],
                }
            ],
        }
        state = build_initial_state(
            chat_query=cq,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert "slack.send_message" in state["tools"]
        assert "slack.list_channels" in state["tools"]
        assert state["tool_to_toolset_map"]["slack.send_message"] == "inst-1"

    def test_with_knowledge(self, mock_deps, minimal_user_info):
        cq = {
            "query": "q",
            "knowledge": [
                {
                    "connectorId": "conn-1",
                    "filters": {"recordGroups": ["kb-group-1"]},
                },
                {
                    "connectorId": "conn-2",
                    "filters": {},
                },
            ],
        }
        state = build_initial_state(
            chat_query=cq,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert state["apps"] == ["conn-1", "conn-2"]
        assert state["kb"] == ["kb-group-1"]
        assert state["has_knowledge"] is True

    def test_has_knowledge_true_when_agent_knowledge_present(self, mock_deps, minimal_user_info):
        """Even with NO_KB_SELECTED sentinel, has_knowledge is True because
        agent_knowledge (the raw knowledge array) is non-empty."""
        cq = {
            "query": "q",
            "knowledge": [
                {"connectorId": None, "filters": {"recordGroups": ["NO_KB_SELECTED"]}},
            ],
        }
        state = build_initial_state(
            chat_query=cq,
            user_info=minimal_user_info,
            **mock_deps,
        )
        # agent_knowledge is truthy (list with 1 item), so has_knowledge is True
        assert state["has_knowledge"] is True
        # But kb should only contain the sentinel, and apps should be empty
        assert state["kb"] == ["NO_KB_SELECTED"]
        assert state["apps"] == []

    def test_has_knowledge_false_when_no_knowledge(self, mock_deps, minimal_user_info):
        """When knowledge is empty, has_knowledge is False."""
        cq = {
            "query": "q",
            "knowledge": [],
        }
        state = build_initial_state(
            chat_query=cq,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert state["has_knowledge"] is False

    def test_graph_type_parameter(self, mock_deps, minimal_chat_query, minimal_user_info):
        state = build_initial_state(
            chat_query=minimal_chat_query,
            user_info=minimal_user_info,
            graph_type="enhanced",
            **mock_deps,
        )
        assert state["graph_type"] == "enhanced"

    def test_org_info_defaults_to_none(self, mock_deps, minimal_chat_query, minimal_user_info):
        state = build_initial_state(
            chat_query=minimal_chat_query,
            user_info=minimal_user_info,
            **mock_deps,
        )
        assert state["org_info"] is None

    def test_org_info_passed(self, mock_deps, minimal_chat_query, minimal_user_info):
        org = {"name": "Acme"}
        state = build_initial_state(
            chat_query=minimal_chat_query,
            user_info=minimal_user_info,
            org_info=org,
            **mock_deps,
        )
        assert state["org_info"] == {"name": "Acme"}


# ===================================================================
# cleanup_state_after_retrieval
# ===================================================================
class TestCleanupStateAfterRetrieval:
    def test_clears_intermediate_fields(self):
        state = {
            "final_results": [{"doc": 1}],
            "decomposed_queries": ["q1"],
            "rewritten_queries": ["rw1"],
            "expanded_queries": ["eq1"],
            "web_search_queries": ["ws1"],
            "search_results": [{"doc": 2}],
            "query_analysis": {
                "intent": "search",
                "complexity": "simple",
                "needs_tools": False,
                "verbose_field": "should be dropped",
            },
        }
        cleanup_state_after_retrieval(state)
        assert state["decomposed_queries"] == []
        assert state["rewritten_queries"] == []
        assert state["expanded_queries"] == []
        assert state["web_search_queries"] == []
        assert state["search_results"] == []
        # query_analysis should only keep intent, complexity, needs_tools
        assert "verbose_field" not in state["query_analysis"]
        assert state["query_analysis"]["intent"] == "search"
        assert state["query_analysis"]["complexity"] == "simple"
        assert state["query_analysis"]["needs_tools"] is False

    def test_no_op_when_final_results_is_none(self):
        state = {
            "final_results": None,
            "decomposed_queries": ["q1"],
            "query_analysis": None,
        }
        cleanup_state_after_retrieval(state)
        # Should not clear anything
        assert state["decomposed_queries"] == ["q1"]

    def test_no_op_when_query_analysis_is_none(self):
        state = {
            "final_results": [{"doc": 1}],
            "decomposed_queries": ["q1"],
            "rewritten_queries": [],
            "expanded_queries": [],
            "web_search_queries": [],
            "search_results": [],
            "query_analysis": None,
        }
        cleanup_state_after_retrieval(state)
        assert state["decomposed_queries"] == []
        assert state["query_analysis"] is None


# ===================================================================
# cleanup_old_tool_results
# ===================================================================
class TestCleanupOldToolResults:
    def test_no_trim_when_under_limit(self):
        state = {
            "all_tool_results": [{"r": i} for i in range(5)],
            "tool_execution_summary": {"total": 5},
            "tool_repetition_warnings": ["warn1"],
        }
        cleanup_old_tool_results(state, keep_last_n=10)
        assert len(state["all_tool_results"]) == 5
        assert state["tool_execution_summary"] == {"total": 5}

    def test_trims_when_over_limit(self):
        state = {
            "all_tool_results": [{"r": i} for i in range(20)],
            "tool_execution_summary": {"total": 20},
            "tool_repetition_warnings": ["warn"],
        }
        cleanup_old_tool_results(state, keep_last_n=5)
        assert len(state["all_tool_results"]) == 5
        # Should keep the last 5
        assert state["all_tool_results"][0]["r"] == 15
        assert state["tool_execution_summary"] == {}
        assert state["tool_repetition_warnings"] == []

    def test_custom_keep_last_n(self):
        state = {
            "all_tool_results": [{"r": i} for i in range(10)],
            "tool_execution_summary": {},
            "tool_repetition_warnings": [],
        }
        cleanup_old_tool_results(state, keep_last_n=3)
        assert len(state["all_tool_results"]) == 3
        assert state["all_tool_results"][0]["r"] == 7

    def test_empty_results(self):
        state = {
            "all_tool_results": [],
            "tool_execution_summary": {},
            "tool_repetition_warnings": [],
        }
        cleanup_old_tool_results(state)
        assert state["all_tool_results"] == []

    def test_missing_key_defaults_to_empty(self):
        state = {}
        cleanup_old_tool_results(state)
        # Should not crash; no all_tool_results key -> get returns []
        assert state.get("all_tool_results") is None or state.get("all_tool_results") == []


# ===================================================================
# _build_tool_to_toolset_map
# ===================================================================
class TestBuildToolToToolsetMap:
    def test_empty_toolsets(self):
        assert _build_tool_to_toolset_map([]) == {}
        assert _build_tool_to_toolset_map(None) == {}

    def test_single_toolset_with_tools(self):
        toolsets = [
            {
                "name": "slack",
                "instanceId": "inst-1",
                "tools": [
                    {"fullName": "slack.send_message", "name": "send_message"},
                ],
                "selectedTools": [],
            }
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert result == {"slack.send_message": "inst-1"}

    def test_falls_back_to_constructed_name(self):
        toolsets = [
            {
                "name": "jira",
                "instanceId": "inst-2",
                "tools": [{"name": "create_issue"}],
                "selectedTools": [],
            }
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert result == {"jira.create_issue": "inst-2"}

    def test_selected_tools_fallback(self):
        toolsets = [
            {
                "name": "slack",
                "instanceId": "inst-3",
                "tools": [],
                "selectedTools": ["send_message", "slack.list_channels"],
            }
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert "slack.send_message" in result
        assert "slack.list_channels" in result

    def test_skips_toolset_without_instance_id(self):
        toolsets = [
            {
                "name": "slack",
                "instanceId": "",
                "tools": [{"fullName": "slack.send"}],
            }
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert result == {}

    def test_non_dict_toolset_is_skipped(self):
        toolsets = ["not-a-dict", None, 42]
        result = _build_tool_to_toolset_map(toolsets)
        assert result == {}

    def test_non_dict_tool_is_skipped(self):
        toolsets = [
            {
                "name": "s",
                "instanceId": "i",
                "tools": ["string-tool", None],
                "selectedTools": [],
            }
        ]
        result = _build_tool_to_toolset_map(toolsets)
        assert result == {}


# ===================================================================
# _extract_tools_from_toolsets
# ===================================================================
class TestExtractToolsFromToolsets:
    def test_empty(self):
        assert _extract_tools_from_toolsets([]) == []
        assert _extract_tools_from_toolsets(None) == []

    def test_extracts_fullName(self):
        toolsets = [
            {
                "name": "slack",
                "tools": [{"fullName": "slack.send_message"}],
                "selectedTools": [],
            }
        ]
        result = _extract_tools_from_toolsets(toolsets)
        assert result == ["slack.send_message"]

    def test_constructs_from_toolName(self):
        toolsets = [
            {
                "name": "jira",
                "tools": [{"toolName": "create_issue"}],
                "selectedTools": [],
            }
        ]
        result = _extract_tools_from_toolsets(toolsets)
        assert result == ["jira.create_issue"]

    def test_constructs_from_name_field(self):
        toolsets = [
            {
                "name": "jira",
                "tools": [{"name": "list_issues"}],
                "selectedTools": [],
            }
        ]
        result = _extract_tools_from_toolsets(toolsets)
        assert result == ["jira.list_issues"]

    def test_selected_tools_used_when_no_tools(self):
        toolsets = [
            {
                "name": "slack",
                "tools": [],
                "selectedTools": ["send_message", "slack.list_channels"],
            }
        ]
        result = _extract_tools_from_toolsets(toolsets)
        assert "slack.send_message" in result
        assert "slack.list_channels" in result

    def test_selected_tools_not_used_when_tools_present(self):
        toolsets = [
            {
                "name": "slack",
                "tools": [{"fullName": "slack.send_message"}],
                "selectedTools": ["list_channels"],
            }
        ]
        result = _extract_tools_from_toolsets(toolsets)
        assert result == ["slack.send_message"]

    def test_non_dict_toolset_skipped(self):
        result = _extract_tools_from_toolsets(["not-a-dict"])
        assert result == []


# ===================================================================
# _extract_knowledge_connector_ids
# ===================================================================
class TestExtractKnowledgeConnectorIds:
    def test_empty(self):
        assert _extract_knowledge_connector_ids([]) == []
        assert _extract_knowledge_connector_ids(None) == []

    def test_extracts_connector_ids(self):
        knowledge = [
            {"connectorId": "c1"},
            {"connectorId": "c2"},
        ]
        assert _extract_knowledge_connector_ids(knowledge) == ["c1", "c2"]

    def test_skips_none_connector_id(self):
        knowledge = [
            {"connectorId": "c1"},
            {"connectorId": None},
            {},
        ]
        assert _extract_knowledge_connector_ids(knowledge) == ["c1"]

    def test_non_dict_knowledge_entry_skipped(self):
        knowledge = [{"connectorId": "c1"}, "not-a-dict", 42]
        assert _extract_knowledge_connector_ids(knowledge) == ["c1"]


# ===================================================================
# _extract_kb_record_groups
# ===================================================================
class TestExtractKbRecordGroups:
    def test_empty(self):
        assert _extract_kb_record_groups([]) == []
        assert _extract_kb_record_groups(None) == []

    def test_extracts_record_groups(self):
        knowledge = [
            {"filters": {"recordGroups": ["kb-1", "kb-2"]}},
            {"filters": {"recordGroups": ["kb-3"]}},
        ]
        assert _extract_kb_record_groups(knowledge) == ["kb-1", "kb-2", "kb-3"]

    def test_handles_string_filters_json(self):
        import json
        knowledge = [
            {"filters": json.dumps({"recordGroups": ["kb-j1"]})},
        ]
        assert _extract_kb_record_groups(knowledge) == ["kb-j1"]

    def test_handles_invalid_json_string_filters(self):
        knowledge = [
            {"filters": "not-json"},
        ]
        assert _extract_kb_record_groups(knowledge) == []

    def test_handles_empty_filters(self):
        knowledge = [
            {"filters": {}},
            {"filters": None},
            {},
        ]
        assert _extract_kb_record_groups(knowledge) == []

    def test_handles_empty_record_groups(self):
        knowledge = [{"filters": {"recordGroups": []}}]
        assert _extract_kb_record_groups(knowledge) == []

    def test_non_list_record_groups_skipped(self):
        knowledge = [{"filters": {"recordGroups": "not-a-list"}}]
        assert _extract_kb_record_groups(knowledge) == []

    def test_non_dict_knowledge_entry_skipped(self):
        knowledge = [{"filters": {"recordGroups": ["kb-1"]}}, "bad", None]
        assert _extract_kb_record_groups(knowledge) == ["kb-1"]
