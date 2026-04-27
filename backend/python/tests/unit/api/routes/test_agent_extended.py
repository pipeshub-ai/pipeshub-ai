"""
Extended tests for app/api/routes/agent.py helper functions.

Targets additional coverage for:
- _build_routing_context: with user_query and bot_response turns
- _build_routing_context: truncation behavior
- _filter_knowledge_by_enabled_sources: KB with string filters (JSON parse)
- _filter_knowledge_by_enabled_sources: KB with invalid JSON string filters
- _filter_knowledge_by_enabled_sources: non-dict entries skipped
- _filter_knowledge_by_enabled_sources: KB with no record groups (filters is not dict)
- _parse_models: model as dict with modelName
- _parse_models: model as dict without modelName
- _parse_models: model as dict with isReasoning=True
- _parse_models: mixed dict and string entries
- _parse_toolsets: toolset with instanceId
- _parse_toolsets: duplicate toolset names
- _parse_toolsets: non-dict entries skipped
- _parse_toolsets: toolset without tools list
- _parse_knowledge_sources: multiple sources with filters
- _enrich_agent_models: model with comma-separated model names
- _enrich_agent_models: model key not found in configs
- _enrich_agent_models: no models in agent
- _enrich_agent_models: exception handled
- _parse_request_body: unicode JSON
- _validate_required_fields: whitespace-only values
- _create_knowledge_edges: empty knowledge sources
- _create_knowledge_edges: batch upsert failure
- _create_knowledge_edges: batch create edges failure
- _create_toolset_edges: empty toolsets
- _create_toolset_edges: batch upsert returns None
- _select_agent_graph_for_query: quick mode
- stream_response: various paths
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse


# ============================================================================
# _build_routing_context extended
# ============================================================================


class TestBuildRoutingContextExtended:
    def test_with_user_query_and_bot_response(self):
        from app.api.routes.agent import _build_routing_context

        query_info = {
            "previous_conversations": [
                {"role": "user_query", "content": "What is our leave policy?"},
                {"role": "bot_response", "content": "The leave policy states that...\nMore details here."},
            ]
        }
        result = _build_routing_context(query_info)
        assert "Prior conversation:" in result
        assert "User: What is our leave policy?" in result
        # Only first line of bot response
        assert "The leave policy states that..." in result
        assert "More details here." not in result

    def test_truncates_long_user_query(self):
        from app.api.routes.agent import _build_routing_context

        long_query = "x" * 300
        query_info = {
            "previous_conversations": [
                {"role": "user_query", "content": long_query},
            ]
        }
        result = _build_routing_context(query_info)
        # User content truncated to 200 chars
        assert len(result.split("User: ")[1].split("\n")[0]) == 200

    def test_truncates_long_bot_response(self):
        from app.api.routes.agent import _build_routing_context

        long_response = "y" * 200
        query_info = {
            "previous_conversations": [
                {"role": "bot_response", "content": long_response},
            ]
        }
        result = _build_routing_context(query_info)
        # Bot first line truncated to 150 chars
        assert len(result.split("Assistant: ")[1].split("\n")[0]) == 150

    def test_takes_last_6_entries(self):
        from app.api.routes.agent import _build_routing_context

        convs = [
            {"role": "user_query", "content": f"Question {i}"} for i in range(10)
        ]
        query_info = {"previous_conversations": convs}
        result = _build_routing_context(query_info)
        # Only last 6 entries
        assert "Question 4" in result
        assert "Question 3" not in result

    def test_unknown_role_skipped(self):
        from app.api.routes.agent import _build_routing_context

        query_info = {
            "previous_conversations": [
                {"role": "system", "content": "System message"},
            ]
        }
        result = _build_routing_context(query_info)
        # Unknown role produces no turns, returns empty
        assert result == ""

    def test_empty_previous_conversations(self):
        from app.api.routes.agent import _build_routing_context

        result = _build_routing_context({"previous_conversations": []})
        assert result == ""


# ============================================================================
# _filter_knowledge_by_enabled_sources extended
# ============================================================================


class TestFilterKnowledgeByEnabledSourcesExtended:
    def test_kb_with_string_filters_json(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [
            {
                "connectorId": "knowledgeBase_1",
                "filters": json.dumps({"recordGroups": ["rg-1"]}),
            }
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 1

    def test_kb_with_invalid_json_string_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [
            {
                "connectorId": "knowledgeBase_1",
                "filters": "not-valid-json",
            }
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        # Invalid JSON => filters_data becomes {}, no record groups => not included
        assert len(result) == 0

    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = ["not-a-dict", None, 123]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["app-1"]})
        assert len(result) == 0

    def test_kb_with_filtersParsed_key(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [
            {
                "connectorId": "knowledgeBase_1",
                "filtersParsed": {"recordGroups": ["rg-1"]},
            }
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 1

    def test_kb_with_non_dict_filters_data(self):
        """When filters_data is not a dict (e.g. a list), record_groups defaults to []."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [
            {
                "connectorId": "knowledgeBase_1",
                "filters": ["not", "a", "dict"],
            }
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 0

    def test_app_connector_not_in_enabled_apps_skipped(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [{"connectorId": "google-drive"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["slack"]})
        assert len(result) == 0

    def test_kb_connector_no_record_groups_not_included_without_match(self):
        """KB with empty record groups not included when enabled_kbs has items."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources

        knowledge = [
            {
                "connectorId": "knowledgeBase_1",
                "filters": {"recordGroups": []},
            }
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        # Empty record groups with no match => not included
        assert len(result) == 0


# ============================================================================
# _parse_models extended
# ============================================================================


class TestParseModelsExtended:
    def test_dict_model_with_model_name(self):
        from app.api.routes.agent import _parse_models

        models = [{"modelKey": "mk1", "modelName": "gpt-4"}]
        entries, has_reasoning = _parse_models(models, MagicMock())
        assert entries == ["mk1_gpt-4"]
        assert has_reasoning is False

    def test_dict_model_without_model_name(self):
        from app.api.routes.agent import _parse_models

        models = [{"modelKey": "mk1"}]
        entries, has_reasoning = _parse_models(models, MagicMock())
        assert entries == ["mk1"]

    def test_dict_model_with_reasoning(self):
        from app.api.routes.agent import _parse_models

        models = [{"modelKey": "mk1", "modelName": "gpt-o1", "isReasoning": True}]
        entries, has_reasoning = _parse_models(models, MagicMock())
        assert has_reasoning is True

    def test_mixed_dict_and_string(self):
        from app.api.routes.agent import _parse_models

        models = [
            {"modelKey": "mk1", "modelName": "m1"},
            "plain-string-model"
        ]
        entries, has_reasoning = _parse_models(models, MagicMock())
        assert entries == ["mk1_m1", "plain-string-model"]

    def test_dict_without_model_key_skipped(self):
        from app.api.routes.agent import _parse_models

        models = [{"modelName": "gpt-4"}]
        entries, has_reasoning = _parse_models(models, MagicMock())
        assert entries == []

    def test_not_a_list(self):
        from app.api.routes.agent import _parse_models

        entries, has_reasoning = _parse_models("not-a-list", MagicMock())
        assert entries == []
        assert has_reasoning is False


# ============================================================================
# _parse_toolsets extended
# ============================================================================


class TestParseToolsetsExtended:
    def test_toolset_with_instance_id(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [
            {
                "name": "slack",
                "displayName": "Slack",
                "type": "connector",
                "instanceId": "inst-123",
                "instanceName": "My Slack",
                "tools": [{"name": "send_message", "fullName": "slack.send_message", "description": "Send msg"}],
            }
        ]
        result = _parse_toolsets(raw)
        assert result["slack"]["instanceId"] == "inst-123"
        assert result["slack"]["instanceName"] == "My Slack"
        assert len(result["slack"]["tools"]) == 1

    def test_duplicate_toolset_name_updates_instance(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [
            {"name": "slack", "displayName": "Slack", "type": "app", "tools": []},
            {"name": "slack", "displayName": "Slack v2", "type": "app", "instanceId": "inst-456", "instanceName": "Slack v2", "tools": []},
        ]
        result = _parse_toolsets(raw)
        # Second entry updates instanceId
        assert result["slack"]["instanceId"] == "inst-456"

    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _parse_toolsets

        raw = ["not-a-dict", 123, None]
        result = _parse_toolsets(raw)
        assert result == {}

    def test_toolset_without_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [{"displayName": "NoName", "tools": []}]
        result = _parse_toolsets(raw)
        assert result == {}

    def test_toolset_with_empty_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [{"name": "", "tools": []}]
        result = _parse_toolsets(raw)
        assert result == {}

    def test_toolset_with_whitespace_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [{"name": "  ", "tools": []}]
        result = _parse_toolsets(raw)
        assert result == {}

    def test_tool_dict_without_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [
            {
                "name": "slack",
                "tools": [{"description": "No name tool"}],
            }
        ]
        result = _parse_toolsets(raw)
        assert len(result["slack"]["tools"]) == 0

    def test_default_display_name(self):
        from app.api.routes.agent import _parse_toolsets

        raw = [{"name": "my_tool", "tools": []}]
        result = _parse_toolsets(raw)
        assert result["my_tool"]["displayName"] == "My Tool"


# ============================================================================
# _parse_knowledge_sources extended
# ============================================================================


class TestParseKnowledgeSourcesExtended:
    def test_multiple_sources(self):
        from app.api.routes.agent import _parse_knowledge_sources

        raw = [
            {"connectorId": "google-drive", "filters": {"type": "doc"}},
            {"connectorId": "confluence", "filters": {"space": "TEAM"}},
        ]
        result = _parse_knowledge_sources(raw)
        assert len(result) == 2
        assert "google-drive" in result
        assert "confluence" in result

    def test_string_filters_parsed(self):
        from app.api.routes.agent import _parse_knowledge_sources

        raw = [{"connectorId": "app1", "filters": '{"key": "value"}'}]
        result = _parse_knowledge_sources(raw)
        assert result["app1"]["filters"] == {"key": "value"}

    def test_invalid_json_string_filters(self):
        from app.api.routes.agent import _parse_knowledge_sources

        raw = [{"connectorId": "app1", "filters": "not-json"}]
        result = _parse_knowledge_sources(raw)
        assert result["app1"]["filters"] == {}

    def test_dict_filters_kept_as_is(self):
        from app.api.routes.agent import _parse_knowledge_sources

        filters = {"type": "pdf", "size": ">1MB"}
        raw = [{"connectorId": "app1", "filters": filters}]
        result = _parse_knowledge_sources(raw)
        assert result["app1"]["filters"] == filters

    def test_no_filters_key(self):
        from app.api.routes.agent import _parse_knowledge_sources

        raw = [{"connectorId": "app1"}]
        result = _parse_knowledge_sources(raw)
        assert result["app1"]["filters"] == {}


# ============================================================================
# _enrich_agent_models extended
# ============================================================================


class TestEnrichAgentModelsExtended:
    @pytest.mark.asyncio
    async def test_comma_separated_model_name(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "modelKey": "mk1",
                    "provider": "openai",
                    "isReasoning": False,
                    "isMultimodal": True,
                    "isDefault": True,
                    "modelFriendlyName": "GPT-4",
                    "configuration": {"model": "gpt-4,gpt-4-turbo"},
                }
            ]
        })
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Should take first model name before comma
        assert agent["models"][0]["modelName"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_model_key_not_found(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["unknown_key"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        logger = MagicMock()
        await _enrich_agent_models(agent, config_service, logger)
        assert agent["models"][0]["provider"] == "unknown"
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_models_in_agent(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": []}
        config_service = AsyncMock()
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Should return early without changes

    @pytest.mark.asyncio
    async def test_models_not_a_list(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": "not-a-list"}
        config_service = AsyncMock()
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Should return early

    @pytest.mark.asyncio
    async def test_exception_handled(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_m1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("etcd down"))
        logger = MagicMock()
        await _enrich_agent_models(agent, config_service, logger)
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_model_entry_with_underscore_format(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_gpt-4"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "modelKey": "mk1",
                    "provider": "openai",
                    "isReasoning": False,
                    "isMultimodal": False,
                    "isDefault": True,
                    "modelFriendlyName": "GPT-4",
                    "configuration": {"model": "gpt-4"},
                }
            ]
        })
        await _enrich_agent_models(agent, config_service, MagicMock())
        assert agent["models"][0]["modelKey"] == "mk1"
        assert agent["models"][0]["modelName"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_model_entry_without_underscore(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {
                    "modelKey": "mk1",
                    "provider": "openai",
                    "modelName": "gpt-4",
                    "configuration": {"model": "gpt-4"},
                }
            ]
        })
        await _enrich_agent_models(agent, config_service, MagicMock())
        assert agent["models"][0]["modelName"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_no_models_key_in_agent(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {}
        config_service = AsyncMock()
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Should not crash

    @pytest.mark.asyncio
    async def test_none_ai_models_config(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        logger = MagicMock()
        await _enrich_agent_models(agent, config_service, logger)
        # Should handle None config gracefully
        assert agent["models"][0]["provider"] == "unknown"


# ============================================================================
# _parse_request_body extended
# ============================================================================


class TestParseRequestBodyExtended:
    def test_unicode_json(self):
        from app.api.routes.agent import _parse_request_body

        body = json.dumps({"name": "Test Agent"}).encode("utf-8")
        result = _parse_request_body(body)
        assert result["name"] == "Test Agent"

    def test_nested_json(self):
        from app.api.routes.agent import _parse_request_body

        data = {"name": "test", "config": {"key": "value", "nested": {"deep": True}}}
        result = _parse_request_body(json.dumps(data).encode("utf-8"))
        assert result["config"]["nested"]["deep"] is True


# ============================================================================
# _validate_required_fields extended
# ============================================================================


class TestValidateRequiredFieldsExtended:
    def test_whitespace_only_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError

        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "   "}, ["name"])

    def test_none_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError

        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])

    def test_empty_string_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError

        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": ""}, ["name"])

    def test_multiple_fields_all_present(self):
        from app.api.routes.agent import _validate_required_fields

        # Should not raise
        _validate_required_fields(
            {"name": "test", "description": "desc", "systemPrompt": "prompt"},
            ["name", "description", "systemPrompt"],
        )

    def test_multiple_fields_second_missing(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError

        with pytest.raises(InvalidRequestError, match="description"):
            _validate_required_fields(
                {"name": "test", "description": ""},
                ["name", "description"],
            )


# ============================================================================
# _create_knowledge_edges
# ============================================================================


class TestCreateKnowledgeEdges:
    @pytest.mark.asyncio
    async def test_empty_knowledge_sources(self):
        from app.api.routes.agent import _create_knowledge_edges

        result = await _create_knowledge_edges(
            "agent-key", {}, "user-key", AsyncMock(), MagicMock()
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_creation(self):
        from app.api.routes.agent import _create_knowledge_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_create_edges = AsyncMock(return_value=True)

        knowledge_sources = {
            "google-drive": {"connectorId": "google-drive", "filters": {"type": "doc"}},
        }
        result = await _create_knowledge_edges(
            "agent-key", knowledge_sources, "user-key", graph_provider, MagicMock()
        )
        assert len(result) == 1
        assert result[0]["connectorId"] == "google-drive"

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self):
        from app.api.routes.agent import _create_knowledge_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)

        knowledge_sources = {
            "app1": {"connectorId": "app1", "filters": {}},
        }
        result = await _create_knowledge_edges(
            "agent-key", knowledge_sources, "user-key", graph_provider, MagicMock()
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_knowledge_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("DB error"))

        knowledge_sources = {
            "app1": {"connectorId": "app1", "filters": {}},
        }
        result = await _create_knowledge_edges(
            "agent-key", knowledge_sources, "user-key", graph_provider, MagicMock()
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_create_edges_exception(self):
        from app.api.routes.agent import _create_knowledge_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_create_edges = AsyncMock(side_effect=Exception("Edge error"))

        knowledge_sources = {
            "app1": {"connectorId": "app1", "filters": {}},
        }
        logger = MagicMock()
        result = await _create_knowledge_edges(
            "agent-key", knowledge_sources, "user-key", graph_provider, logger
        )
        # Knowledge still built even if edges fail
        assert len(result) == 1


# ============================================================================
# _create_toolset_edges
# ============================================================================


class TestCreateToolsetEdges:
    @pytest.mark.asyncio
    async def test_empty_toolsets(self):
        from app.api.routes.agent import _create_toolset_edges

        created, failed = await _create_toolset_edges(
            "agent-key", {}, {"userId": "u1"}, "user-key", AsyncMock(), MagicMock()
        )
        assert created == []
        assert failed == []

    @pytest.mark.asyncio
    @patch("app.agents.constants.toolset_constants.normalize_app_name", side_effect=lambda x: x)
    async def test_successful_creation(self, mock_normalize):
        from app.api.routes.agent import _create_toolset_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_create_edges = AsyncMock(return_value=True)

        toolsets = {
            "slack": {
                "displayName": "Slack",
                "type": "connector",
                "tools": [
                    {"name": "send_msg", "fullName": "slack.send_msg", "description": "Send"},
                ],
                "instanceId": None,
                "instanceName": None,
            }
        }
        created, failed = await _create_toolset_edges(
            "agent-key", toolsets, {"userId": "u1"}, "user-key", graph_provider, MagicMock()
        )
        assert len(created) == 1
        assert created[0]["name"] == "slack"

    @pytest.mark.asyncio
    async def test_batch_upsert_returns_none(self):
        from app.api.routes.agent import _create_toolset_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)

        toolsets = {
            "slack": {
                "displayName": "Slack",
                "type": "app",
                "tools": [],
                "instanceId": None,
                "instanceName": None,
            }
        }
        created, failed = await _create_toolset_edges(
            "agent-key", toolsets, {"userId": "u1"}, "user-key", graph_provider, MagicMock()
        )
        assert created == []
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_toolset_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("DB error"))

        toolsets = {
            "slack": {
                "displayName": "Slack",
                "type": "app",
                "tools": [],
                "instanceId": None,
                "instanceName": None,
            }
        }
        created, failed = await _create_toolset_edges(
            "agent-key", toolsets, {"userId": "u1"}, "user-key", graph_provider, MagicMock()
        )
        assert created == []
        assert len(failed) == 1


# ============================================================================
# _select_agent_graph_for_query: quick mode
# ============================================================================


class TestSelectAgentGraphQuickMode:
    @pytest.mark.asyncio
    async def test_quick_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph

        query_info = {"chatMode": "quick"}
        # chatMode="quick" is not handled explicitly, falls to default
        result = await _select_agent_graph_for_query(query_info, MagicMock(), MagicMock())
        # "quick" is not "deep" or "verification" or "auto", so hits default
        assert result is agent_graph


# ============================================================================
# _get_user_document edge cases
# ============================================================================


class TestGetUserDocumentExtended:
    @pytest.mark.asyncio
    async def test_user_with_all_fields(self):
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "email": "user@example.com",
            "_key": "user-key-123",
            "fullName": "Test User",
        })
        result = await _get_user_document("u1", graph_provider, MagicMock())
        assert result["email"] == "user@example.com"


# ============================================================================
# _get_org_info edge cases
# ============================================================================


class TestGetOrgInfoExtended:
    @pytest.mark.asyncio
    async def test_org_with_enterprise_uppercase(self):
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "ENTERPRISE",
        })
        result = await _get_org_info({"orgId": "org-1"}, graph_provider, MagicMock())
        assert result["accountType"] == "enterprise"


# ============================================================================
# _enrich_user_info
# ============================================================================


class TestEnrichUserInfoExtended:
    @pytest.mark.asyncio
    async def test_all_name_fields(self):
        from app.api.routes.agent import _enrich_user_info

        user_info = {"userId": "u1", "orgId": "o1"}
        user_doc = {
            "email": "user@example.com",
            "_key": "uk1",
            "fullName": "Full Name",
            "firstName": "First",
            "lastName": "Last",
            "displayName": "Display",
        }
        result = await _enrich_user_info(user_info, user_doc)
        assert result["fullName"] == "Full Name"
        assert result["firstName"] == "First"
        assert result["lastName"] == "Last"
        assert result["displayName"] == "Display"
        assert result["userEmail"] == "user@example.com"
        assert result["_key"] == "uk1"

    @pytest.mark.asyncio
    async def test_no_name_fields(self):
        from app.api.routes.agent import _enrich_user_info

        user_info = {"userId": "u1", "orgId": "o1"}
        user_doc = {"email": "user@example.com", "_key": "uk1"}
        result = await _enrich_user_info(user_info, user_doc)
        assert "fullName" not in result
        assert "firstName" not in result

    @pytest.mark.asyncio
    async def test_does_not_mutate_original(self):
        from app.api.routes.agent import _enrich_user_info

        user_info = {"userId": "u1", "orgId": "o1"}
        user_doc = {"email": "user@example.com", "_key": "uk1"}
        result = await _enrich_user_info(user_info, user_doc)
        assert "_key" not in user_info
        assert "_key" in result


# ============================================================================
# AgentError hierarchy
# ============================================================================


class TestAgentErrorHierarchy:
    def test_agent_error_default_status(self):
        from app.api.routes.agent import AgentError

        e = AgentError("custom error")
        assert e.status_code == 500
        assert e.detail == "custom error"

    def test_agent_error_custom_status(self):
        from app.api.routes.agent import AgentError

        e = AgentError("not found", 404)
        assert e.status_code == 404

    def test_agent_not_found_inherits(self):
        from app.api.routes.agent import AgentNotFoundError, AgentError

        e = AgentNotFoundError("some-id")
        assert isinstance(e, AgentError)
        assert isinstance(e, HTTPException)

    def test_permission_denied_message(self):
        from app.api.routes.agent import PermissionDeniedError

        e = PermissionDeniedError("read agent")
        assert "read agent" in e.detail


# ============================================================================
# ChatQuery model
# ============================================================================


class TestChatQueryExtended:
    def test_filters_as_dict(self):
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(
            query="test",
            filters={"apps": ["app1"], "kb": ["kb1"]},
        )
        assert q.filters["apps"] == ["app1"]

    def test_all_optional_fields_none(self):
        from app.api.routes.agent import ChatQuery

        q = ChatQuery(query="test")
        assert q.filters is None
        assert q.systemPrompt is None
        assert q.instructions is None
        assert q.tools is None
        assert q.modelKey is None
        assert q.modelName is None
        assert q.timezone is None
        assert q.currentTime is None
        assert q.conversationId is None


# ============================================================================
# Conditional orchestration helpers
# ============================================================================


class TestConditionalOrchestrationHelpers:
    def test_evaluate_condition_contains_case_insensitive(self):
        from app.api.routes.agent import _evaluate_condition_node

        config = {
            "mode": "contains",
            "expectedValue": "done",
            "caseSensitive": False,
        }
        assert _evaluate_condition_node(config, "Task is DONE successfully", {}) is True

    def test_evaluate_condition_regex(self):
        from app.api.routes.agent import _evaluate_condition_node

        config = {
            "mode": "regex",
            "regexPattern": r"score:\s*(9|10)",
        }
        assert _evaluate_condition_node(config, "Quality score: 9", {}) is True

    def test_evaluate_condition_rules_any(self):
        from app.api.routes.agent import _evaluate_condition_node

        config = {
            "ruleOperator": "any",
            "rules": [
                {"mode": "contains", "expectedValue": "ok"},
                {"mode": "min_length", "minLength": 20},
            ],
        }
        assert _evaluate_condition_node(config, "This response is definitely long enough", {}) is True

    def test_build_conditional_orchestration_graph_happy_path(self):
        from app.api.routes.agent import _build_conditional_orchestration_graph

        agent = {
            "flow": {
                "nodes": [
                    {"id": "a1", "data": {"type": "agent-core", "label": "Agent 1", "config": {}}},
                    {"id": "cond1", "data": {"type": "conditional-check", "label": "Check", "config": {"mode": "contains", "expectedValue": "ok"}}},
                    {"id": "a2", "data": {"type": "agent-core", "label": "Agent 2", "config": {}}},
                    {"id": "out1", "data": {"type": "chat-response", "label": "Output", "config": {}}},
                ],
                "edges": [
                    {"source": "a1", "sourceHandle": "response", "target": "cond1", "targetHandle": "input"},
                    {"source": "cond1", "sourceHandle": "pass", "target": "out1", "targetHandle": "response"},
                    {"source": "cond1", "sourceHandle": "fail", "target": "a2", "targetHandle": "input"},
                ],
            }
        }

        graph = _build_conditional_orchestration_graph(agent)
        assert graph["rootAgentId"] == "a1"
        assert graph["nextByAgentId"]["a1"]["kind"] == "condition"
        assert graph["conditionsById"]["cond1"]["routes"]["pass"]["kind"] == "output"
        assert graph["conditionsById"]["cond1"]["routes"]["fail"]["targetId"] == "a2"

    def test_build_conditional_orchestration_graph_requires_both_outputs(self):
        from app.api.routes.agent import _build_conditional_orchestration_graph, InvalidRequestError

        agent = {
            "flow": {
                "nodes": [
                    {"id": "a1", "data": {"type": "agent-core", "label": "Agent 1", "config": {}}},
                    {"id": "cond1", "data": {"type": "conditional-check", "label": "Check", "config": {}}},
                    {"id": "out1", "data": {"type": "chat-response", "label": "Output", "config": {}}},
                ],
                "edges": [
                    {"source": "a1", "sourceHandle": "response", "target": "cond1", "targetHandle": "input"},
                    {"source": "cond1", "sourceHandle": "pass", "target": "out1", "targetHandle": "response"},
                ],
            }
        }

        with pytest.raises(InvalidRequestError) as exc_info:
            _build_conditional_orchestration_graph(agent)
        assert "both pass and fail outputs connected" in str(exc_info.value)


class TestConditionalOrchestrationHelpersExtended:
    def test_should_use_linear_orchestration_requires_agent_node(self):
        from app.api.routes.agent import _should_use_linear_orchestration

        agent = {
            "orchestrationMode": "linear",
            "flow": {
                "nodes": [{"id": "response", "data": {"type": "chat-response"}}],
                "edges": [],
            },
        }

        assert _should_use_linear_orchestration(agent) is False

    def test_should_use_conditional_orchestration_requires_condition_node(self):
        from app.api.routes.agent import _should_use_conditional_orchestration

        agent = {
            "orchestrationMode": "conditional",
            "flow": {
                "nodes": [{"id": "agent-1", "data": {"type": "agent-core"}}],
                "edges": [],
            },
        }

        assert _should_use_conditional_orchestration(agent) is False

    @pytest.mark.parametrize(
        ("rule", "result_text", "response_data", "expected"),
        [
            ({"mode": "not_contains", "expectedValue": "error"}, "all good", {}, True),
            ({"mode": "equals", "expectedValue": "Done"}, "done", {}, True),
            ({"mode": "not_equals", "expectedValue": "pending"}, "done", {}, True),
            ({"mode": "starts_with", "expectedValue": "task"}, "Task complete", {}, True),
            ({"mode": "ends_with", "expectedValue": "complete"}, "task complete", {}, True),
            ({"mode": "min_length", "minLength": 5}, "  hello  ", {}, True),
            ({"mode": "max_length", "maxLength": 5}, " hey ", {}, True),
            ({"mode": "max_length"}, "long enough", {}, True),
            ({"mode": "is_empty"}, "   ", {}, True),
            ({"mode": "not_empty"}, "value", {}, True),
            (
                {"mode": "json_path_equals", "jsonPath": "result.status", "expectedValue": "ok"},
                "ignored",
                {"result": {"status": "ok"}},
                True,
            ),
        ],
    )
    def test_evaluate_single_condition_rule_modes(self, rule, result_text, response_data, expected):
        from app.api.routes.agent import _evaluate_single_condition_rule

        assert _evaluate_single_condition_rule(rule, result_text, response_data) is expected

    def test_evaluate_single_condition_rule_regex_without_pattern_returns_false(self):
        from app.api.routes.agent import _evaluate_single_condition_rule

        assert _evaluate_single_condition_rule({"mode": "regex"}, "hello", {}) is False

    def test_evaluate_single_condition_rule_json_path_requires_dict(self):
        from app.api.routes.agent import _evaluate_single_condition_rule

        assert _evaluate_single_condition_rule(
            {"mode": "json_path_equals", "jsonPath": "a.b", "expectedValue": "1"},
            "ignored",
            "not-a-dict",
        ) is False

    def test_evaluate_single_condition_rule_invalid_mode_raises(self):
        from app.api.routes.agent import InvalidRequestError, _evaluate_single_condition_rule

        with pytest.raises(InvalidRequestError, match="Unsupported condition mode"):
            _evaluate_single_condition_rule({"mode": "bogus"}, "hello", {})

    def test_evaluate_condition_node_with_no_valid_rules_uses_pass_on_empty(self):
        from app.api.routes.agent import _evaluate_condition_node

        assert _evaluate_condition_node({"rules": [None, "bad"], "passOnEmpty": True}, "hello", {}) is True

    def test_build_conditional_orchestration_graph_requires_condition_block(self):
        from app.api.routes.agent import InvalidRequestError, _build_conditional_orchestration_graph

        agent = {
            "flow": {
                "nodes": [{"id": "a1", "data": {"type": "agent-core", "label": "Agent 1", "config": {}}}],
                "edges": [],
            }
        }

        with pytest.raises(InvalidRequestError, match="requires at least one condition block"):
            _build_conditional_orchestration_graph(agent)

    def test_build_conditional_orchestration_graph_rejects_invalid_condition_handle(self):
        from app.api.routes.agent import InvalidRequestError, _build_conditional_orchestration_graph

        agent = {
            "flow": {
                "nodes": [
                    {"id": "a1", "data": {"type": "agent-core", "label": "Agent 1", "config": {}}},
                    {"id": "cond1", "data": {"type": "conditional-check", "label": "Check", "config": {}}},
                    {"id": "a2", "data": {"type": "agent-core", "label": "Agent 2", "config": {}}},
                    {"id": "out1", "data": {"type": "chat-response", "label": "Output", "config": {}}},
                ],
                "edges": [
                    {"source": "a1", "sourceHandle": "response", "target": "cond1", "targetHandle": "input"},
                    {"source": "cond1", "sourceHandle": "maybe", "target": "out1", "targetHandle": "response"},
                    {"source": "cond1", "sourceHandle": "fail", "target": "a2", "targetHandle": "input"},
                ],
            }
        }

        with pytest.raises(InvalidRequestError, match="pass/fail outputs"):
            _build_conditional_orchestration_graph(agent)


class TestAgentStepHelpers:
    def test_extract_text_from_response_data_prefers_answer_field(self):
        from app.api.routes.agent import _extract_text_from_response_data

        response = JSONResponse(content={"answer": "resolved"})
        assert _extract_text_from_response_data(response) == "resolved"

    def test_extract_text_from_response_data_handles_invalid_json_body(self):
        from app.api.routes.agent import _extract_text_from_response_data

        response = JSONResponse(content={"message": "ok"})
        response.body = b"not-json"
        assert _extract_text_from_response_data(response) == ""

    def test_extract_text_from_response_data_handles_invalid_utf8_body(self):
        from app.api.routes.agent import _extract_text_from_response_data

        response = JSONResponse(content={"message": "ok"})
        response.body = b"\xff"
        assert _extract_text_from_response_data(response) == ""

    @pytest.mark.parametrize(
        ("model_value", "expected"),
        [
            ({"modelKey": "mk", "modelName": "gpt-4"}, ("mk", "gpt-4")),
            ("mk_gpt-4", ("mk", "gpt-4")),
            ("mk", ("mk", None)),
            (None, (None, None)),
        ],
    )
    def test_parse_model_selection(self, model_value, expected):
        from app.api.routes.agent import _parse_model_selection

        assert _parse_model_selection(model_value) == expected

    def test_filter_toolsets_by_enabled_tools_keeps_matching_tools_only(self):
        from app.api.routes.agent import _filter_toolsets_by_enabled_tools

        toolsets = [
            {
                "name": "slack",
                "tools": [
                    {"fullName": "slack.send"},
                    {"fullName": "slack.read"},
                ],
            },
            {
                "name": "jira",
                "tools": [{"fullName": "jira.search"}],
            },
        ]

        filtered = _filter_toolsets_by_enabled_tools(toolsets, ["slack.send"])

        assert filtered == [{"name": "slack", "tools": [{"fullName": "slack.send"}]}]

    @pytest.mark.asyncio
    async def test_load_authenticated_toolsets_builds_user_error_message(self):
        from app.api.routes.agent import _load_authenticated_toolsets

        config_service = AsyncMock()

        async def _get_config(path):
            if path.endswith("slack-inst/user-1"):
                return {"isAuthenticated": True}
            if path.endswith("jira-inst/user-1"):
                return {"isAuthenticated": False}
            return None

        config_service.get_config.side_effect = _get_config
        logger = MagicMock()
        toolsets = [
            {"name": "slack", "displayName": "Slack", "instanceId": "slack-inst", "tools": []},
            {"name": "jira", "displayName": "Jira", "instanceId": "jira-inst", "tools": []},
            {"name": "confluence", "displayName": "Confluence", "instanceId": "conf-inst", "tools": []},
        ]

        with patch("app.agents.constants.toolset_constants.get_toolset_config_path", side_effect=lambda lookup_key, user_id: f"/toolsets/{lookup_key}/{user_id}"):
            configured, configs, error_message = await _load_authenticated_toolsets(
                toolsets,
                agent_id="agent-1",
                user_context={"userId": "user-1"},
                agent={"isServiceAccount": False},
                config_service=config_service,
                logger=logger,
            )

        assert configured == [toolsets[0]]
        assert configs == {"slack-inst": {"isAuthenticated": True}}
        assert "not configured" in error_message
        assert "not authenticated" in error_message
        assert "Settings -> Toolsets" in error_message

    @pytest.mark.asyncio
    async def test_load_authenticated_toolsets_service_account_message(self):
        from app.api.routes.agent import _load_authenticated_toolsets

        config_service = AsyncMock()
        config_service.get_config.return_value = None
        logger = MagicMock()

        with patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/toolsets/slack-inst/agent-1"):
            configured, configs, error_message = await _load_authenticated_toolsets(
                [{"name": "slack", "displayName": "Slack", "instanceId": "slack-inst", "tools": []}],
                agent_id="agent-1",
                user_context={"userId": "user-1"},
                agent={"isServiceAccount": True},
                config_service=config_service,
                logger=logger,
            )

        assert configured == []
        assert configs == {}
        assert "Agent Builder -> Manage Credentials" in error_message

    def test_derive_filters_from_knowledge_defaults_apps_kb_and_placeholder(self):
        from app.api.routes.agent import NO_KB_SELECTED_FILTER, _derive_filters_from_knowledge

        knowledge = [
            {"connectorId": "slack-inst"},
            {"connectorId": "knowledgeBase_1", "filters": json.dumps({"recordGroups": ["rg-1"]})},
        ]

        filters = _derive_filters_from_knowledge(knowledge, None, "agent-1")

        assert filters == {"apps": ["slack-inst"], "kb": ["rg-1"]}
        placeholder_filters = _derive_filters_from_knowledge([], None, "agent-1")
        assert placeholder_filters["kb"] == [NO_KB_SELECTED_FILTER]

    def test_derive_filters_from_knowledge_preserves_requested_keys(self):
        from app.api.routes.agent import _derive_filters_from_knowledge

        knowledge = [{"connectorId": "slack-inst"}]
        filters = _derive_filters_from_knowledge(knowledge, {"apps": ["manual"], "kb": None}, "agentIdPlaceholder")

        assert filters["apps"] == ["manual"]
        assert filters["kb"] == []

    @pytest.mark.asyncio
    async def test_resolve_llm_for_step_returns_fallback_when_no_model_needed(self):
        from app.api.routes.agent import _resolve_llm_for_step

        fallback_llm = MagicMock()
        chat_query = MagicMock(modelKey=None, modelName=None, chatMode="balanced")

        llm = await _resolve_llm_for_step(
            {},
            chat_query,
            {"models": []},
            MagicMock(),
            fallback_llm,
            require_reasoning=False,
        )

        assert llm is fallback_llm

    @pytest.mark.asyncio
    async def test_resolve_llm_for_step_requires_reasoning_model(self):
        from app.api.routes.agent import ReasoningModelRequiredError, _resolve_llm_for_step

        chat_query = MagicMock(modelKey=None, modelName=None, chatMode="balanced")

        with patch("app.api.routes.agent.get_llm_for_chat", new=AsyncMock(return_value=(MagicMock(), {"isReasoning": False}))):
            with pytest.raises(ReasoningModelRequiredError):
                await _resolve_llm_for_step(
                    {},
                    chat_query,
                    {"models": []},
                    MagicMock(),
                    MagicMock(),
                    require_reasoning=True,
                )

    @pytest.mark.asyncio
    async def test_build_step_execution_context_success(self):
        from app.api.routes.agent import ChatQuery, _build_step_execution_context

        chat_query = ChatQuery(
            query="Find docs",
            filters=None,
            tools=["slack.send"],
            quickMode=True,
            chatMode="balanced",
            retrievalMode="hybrid",
            previousConversations=[{"role": "user_query", "content": "Earlier"}],
            conversationId="conv-1",
        )
        step = {
            "toolsets": [{"name": "slack", "tools": [{"fullName": "slack.send"}, {"fullName": "slack.read"}]}],
            "knowledge": [
                {"connectorId": "slack-inst"},
                {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg-1"]}},
            ],
            "systemPrompt": "step prompt",
            "instructions": "step instructions",
        }
        fallback_llm = MagicMock(name="fallback_llm")

        with patch("app.api.routes.agent._load_authenticated_toolsets", new=AsyncMock(return_value=([{"name": "slack", "tools": [{"fullName": "slack.send"}]}], {"slack": {"isAuthenticated": True}}, None))), \
             patch("app.api.routes.agent.fetch_connector_configs", new=AsyncMock(return_value={"slack-inst": {"sync": {}, "indexing": {}}})), \
             patch("app.api.routes.agent._resolve_llm_for_step", new=AsyncMock(return_value=fallback_llm)):
            query_info, llm, error_message = await _build_step_execution_context(
                step=step,
                chat_query=chat_query,
                current_query="Find docs",
                agent={"systemPrompt": "agent prompt", "instructions": "agent instructions", "isServiceAccount": True},
                agent_id="agent-1",
                user_context={"userId": "user-1"},
                config_service=MagicMock(),
                logger=MagicMock(),
                fallback_llm=fallback_llm,
                include_previous_conversations=True,
                require_reasoning=False,
            )

        assert error_message is None
        assert llm is fallback_llm
        assert query_info["filters"] == {"apps": ["slack-inst"], "kb": ["rg-1"]}
        assert query_info["systemPrompt"] == "step prompt"
        assert query_info["instructions"] == "step instructions"
        assert query_info["connector_configs"] == {"slack-inst": {"sync": {}, "indexing": {}}}
        assert query_info["is_service_account"] is True

    @pytest.mark.asyncio
    async def test_build_step_execution_context_returns_toolset_error(self):
        from app.api.routes.agent import ChatQuery, _build_step_execution_context

        with patch("app.api.routes.agent._load_authenticated_toolsets", new=AsyncMock(return_value=([], {}, "missing creds"))):
            query_info, llm, error_message = await _build_step_execution_context(
                step={"toolsets": [], "knowledge": []},
                chat_query=ChatQuery(query="test"),
                current_query="test",
                agent={},
                agent_id="agent-1",
                user_context={"userId": "user-1"},
                config_service=MagicMock(),
                logger=MagicMock(),
                fallback_llm=MagicMock(name="fallback_llm"),
                include_previous_conversations=False,
                require_reasoning=False,
            )

        assert query_info == {}
        assert error_message == "missing creds"
