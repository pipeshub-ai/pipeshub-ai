import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestParseKnowledgeSourcesEdgeCases:
    def test_filters_as_json_string(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": '{"types": ["doc"]}'}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_filters_as_invalid_json_string(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {}

    def test_missing_connector_id(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"filters": {}}]
        result = _parse_knowledge_sources(raw)
        assert result == {}

    def test_empty_connector_id(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "  ", "filters": {}}]
        result = _parse_knowledge_sources(raw)
        assert result == {}


class TestFilterKnowledgeByEnabledSources:
    def test_no_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}, {"connectorId": "c2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert len(result) == 2

    def test_app_filter(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "app1"},
            {"connectorId": "app2"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["app1"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "app1"

    def test_kb_filter_with_matching_record_groups(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg1", "rg2"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_kb_filter_no_matching_record_groups(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg3"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0

    def test_kb_filter_with_json_string_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": '{"recordGroups": ["rg1"]}'},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_kb_filter_invalid_json_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": "not json"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0

    def test_non_dict_skipped(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not a dict", None, 42]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["a1"]})
        assert len(result) == 0


class TestParseToolsetsEdgeCases:
    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(["not dict", 42, None])
        assert result == {}

    def test_missing_name(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"type": "app"}])
        assert result == {}

    def test_duplicate_toolset_updates_instance_id(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "displayName": "Jira", "type": "app", "tools": []},
            {"name": "jira", "displayName": "Jira", "type": "app", "tools": [], "instanceId": "inst-1", "instanceName": "My Jira"},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-1"

    def test_tool_dict_with_name(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "jira", "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}]}]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 1

    def test_tool_dict_without_name(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "jira", "tools": [{"description": "No name"}]}]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 0


class TestParseModelsEdgeCases:
    def test_string_model(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models(["model_key_1"], log)
        assert "model_key_1" in entries

    def test_dict_without_model_key(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models([{"modelName": "name"}], log)
        assert entries == []

    def test_dict_with_key_no_name(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models([{"modelKey": "mk1"}], log)
        assert entries == ["mk1"]


class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enriches_with_matching_config(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key1_name1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [{"modelKey": "key1", "provider": "openai", "configuration": {"model": "gpt-4"}}]
        })
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert isinstance(agent["models"], list)
        assert agent["models"][0]["modelKey"] == "key1"

    @pytest.mark.asyncio
    async def test_no_matching_config(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["unknown_model"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_models(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": []}
        config_service = AsyncMock()
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_none_models(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {}
        config_service = AsyncMock()
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key_name"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("fail"))
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)

    @pytest.mark.asyncio
    async def test_comma_separated_model_name(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [{"modelKey": "key1", "provider": "openai", "configuration": {"model": "gpt-4,gpt-4-turbo"}}]
        })
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"][0]["modelName"] == "gpt-4"


class TestParseRequestBody:
    def test_valid_json(self):
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_body(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"")

    def test_invalid_json(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"not json")


class TestCreateToolsetEdges:
    @pytest.mark.asyncio
    async def test_empty_toolsets(self):
        from app.api.routes.agent import _create_toolset_edges
        log = logging.getLogger("test")
        created, failed = await _create_toolset_edges("ak1", {}, {}, "uk1", AsyncMock(), log)
        assert created == []
        assert failed == []

    @pytest.mark.asyncio
    async def test_batch_upsert_fails(self):
        from app.api.routes.agent import _create_toolset_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=False)
        toolsets = {"jira": {"displayName": "Jira", "type": "app", "tools": [], "instanceId": None, "instanceName": None}}
        user_info = {"userId": "u1"}
        created, failed = await _create_toolset_edges("ak1", toolsets, user_info, "uk1", gp, log)
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_toolset_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("db err"))
        toolsets = {"jira": {"displayName": "Jira", "type": "app", "tools": [], "instanceId": None, "instanceName": None}}
        user_info = {"userId": "u1"}
        created, failed = await _create_toolset_edges("ak1", toolsets, user_info, "uk1", gp, log)
        assert len(failed) == 1


class TestCreateKnowledgeEdges:
    @pytest.mark.asyncio
    async def test_empty_knowledge(self):
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        result = await _create_knowledge_edges("ak1", {}, "uk1", AsyncMock(), log)
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_fails(self):
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=False)
        knowledge = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges("ak1", knowledge, "uk1", gp, log)
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(return_value=True)
        knowledge = {"c1": {"connectorId": "c1", "filters": {"types": ["doc"]}}}
        result = await _create_knowledge_edges("ak1", knowledge, "uk1", gp, log)
        assert len(result) == 1
        assert result[0]["connectorId"] == "c1"

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        knowledge = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges("ak1", knowledge, "uk1", gp, log)
        assert result == []




# ============================================================================
# _parse_web_search — lines 866-874
# ============================================================================


class TestParseWebSearch:
    def test_dict_valid_provider(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search({"provider": "Serper"}) == "serper"

    def test_string_valid_provider(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search("tavily") == "tavily"

    def test_invalid_provider(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search("invalid_provider") is None

    def test_empty_string(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search("") is None

    def test_none(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search(None) is None

    def test_dict_empty_provider(self):
        from app.api.routes.agent import _parse_web_search
        assert _parse_web_search({"provider": ""}) is None


# ============================================================================
# _format_web_search_for_response — lines 883-891
# ============================================================================


class TestFormatWebSearchForResponse:
    def test_valid_provider_dict_with_labels(self):
        from app.api.routes.agent import _format_web_search_for_response
        result = _format_web_search_for_response({
            "provider": "serper",
            "providerKey": "pk1",
            "providerLabel": "Serper API",
        })
        assert result["provider"] == "serper"
        assert result["providerKey"] == "pk1"
        assert result["providerLabel"] == "Serper API"

    def test_valid_provider_string(self):
        from app.api.routes.agent import _format_web_search_for_response
        result = _format_web_search_for_response("duckduckgo")
        assert result == {"provider": "duckduckgo"}

    def test_invalid_returns_none(self):
        from app.api.routes.agent import _format_web_search_for_response
        assert _format_web_search_for_response("bad") is None

    def test_dict_without_labels(self):
        from app.api.routes.agent import _format_web_search_for_response
        result = _format_web_search_for_response({"provider": "exa"})
        assert result == {"provider": "exa"}
        assert "providerKey" not in result


# ============================================================================
# _is_web_search_enabled — line 906
# ============================================================================


class TestIsWebSearchEnabled:
    def test_none_tools_enabled(self):
        from app.api.routes.agent import _is_web_search_enabled
        assert _is_web_search_enabled(None) is True

    def test_explicit_with_web_search(self):
        from app.api.routes.agent import _is_web_search_enabled
        assert _is_web_search_enabled(["jira.search", "web_search"]) is True

    def test_explicit_without_web_search(self):
        from app.api.routes.agent import _is_web_search_enabled
        assert _is_web_search_enabled(["jira.search", "outlook.send"]) is False

    def test_web_search_prefix(self):
        from app.api.routes.agent import _is_web_search_enabled
        assert _is_web_search_enabled(["web_search.fetch_url"]) is True

    def test_empty_list(self):
        from app.api.routes.agent import _is_web_search_enabled
        assert _is_web_search_enabled([]) is False


# ============================================================================
# _resolve_default_web_search_config — lines 926-958
# ============================================================================


class TestResolveDefaultWebSearchConfig:
    @pytest.mark.asyncio
    async def test_config_exception_returns_none(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("fail"))
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_providers_returns_none(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={"providers": []})
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_default_falls_back_duckduckgo(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "serper", "isDefault": False}]
        })
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result == {"provider": "duckduckgo", "configuration": {}}

    @pytest.mark.asyncio
    async def test_valid_default_provider(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "serper", "isDefault": True, "configuration": {"apiKey": "k"}}]
        })
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result["provider"] == "serper"
        assert result["configuration"]["apiKey"] == "k"

    @pytest.mark.asyncio
    async def test_invalid_default_provider_returns_none(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "unknown_provider", "isDefault": True}]
        })
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_configuration_coerced(self):
        from app.api.routes.agent import _resolve_default_web_search_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "tavily", "isDefault": True, "configuration": "bad"}]
        })
        result = await _resolve_default_web_search_config(cs, MagicMock())
        assert result["provider"] == "tavily"
        assert result["configuration"] == {}


# ============================================================================
# _resolve_web_search_tool_config — lines 970-1009
# ============================================================================


class TestResolveWebSearchToolConfig:
    @pytest.mark.asyncio
    async def test_no_provider_returns_none(self):
        from app.api.routes.agent import _resolve_web_search_tool_config
        result = await _resolve_web_search_tool_config(None, AsyncMock(), MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_config_exception_returns_fallback(self):
        from app.api.routes.agent import _resolve_web_search_tool_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(side_effect=RuntimeError("fail"))
        result = await _resolve_web_search_tool_config("serper", cs, MagicMock())
        assert result == {"provider": "serper", "configuration": {}}

    @pytest.mark.asyncio
    async def test_provider_not_in_list(self):
        from app.api.routes.agent import _resolve_web_search_tool_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "tavily", "configuration": {"key": "v"}}]
        })
        result = await _resolve_web_search_tool_config("serper", cs, MagicMock())
        assert result == {"provider": "serper", "configuration": {}}

    @pytest.mark.asyncio
    async def test_provider_found_with_config(self):
        from app.api.routes.agent import _resolve_web_search_tool_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "serper", "configuration": {"apiKey": "k1"}}]
        })
        result = await _resolve_web_search_tool_config("serper", cs, MagicMock())
        assert result["provider"] == "serper"
        assert result["configuration"]["apiKey"] == "k1"

    @pytest.mark.asyncio
    async def test_non_dict_configuration_coerced(self):
        from app.api.routes.agent import _resolve_web_search_tool_config
        cs = AsyncMock()
        cs.get_config = AsyncMock(return_value={
            "providers": [{"provider": "exa", "configuration": "bad"}]
        })
        result = await _resolve_web_search_tool_config("exa", cs, MagicMock())
        assert result == {"provider": "exa", "configuration": {}}


# ============================================================================
# _resolve_service_account_caller_identity — lines 258-276
# ============================================================================


class TestResolveServiceAccountCallerIdentity:
    @pytest.mark.asyncio
    async def test_explicit_caller_fields(self):
        from app.api.routes.agent import _resolve_service_account_caller_identity
        chat_query = MagicMock()
        chat_query.callerDisplayName = "Alice"
        chat_query.callerEmail = "alice@co.com"
        enriched = {"userId": "u1", "email": "sa@bot.com"}

        with patch("app.api.routes.agent._merge_end_user_into_service_account_user_info",
                   return_value={"userId": "u1", "email": "alice@co.com", "fullName": "Alice"}):
            result = await _resolve_service_account_caller_identity(
                enriched, chat_query, {"userId": "u1"}, AsyncMock(), MagicMock()
            )
        assert result["fullName"] == "Alice"

    @pytest.mark.asyncio
    async def test_lookup_from_user_doc(self):
        from app.api.routes.agent import _resolve_service_account_caller_identity
        chat_query = MagicMock()
        chat_query.callerDisplayName = None
        chat_query.callerEmail = None
        enriched = {"userId": "u1"}
        gp = AsyncMock()

        with patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock,
                   return_value={"fullName": "Bob", "email": "bob@co.com"}), \
             patch("app.api.routes.agent._merge_end_user_into_service_account_user_info",
                   return_value={"userId": "u1", "fullName": "Bob"}):
            result = await _resolve_service_account_caller_identity(
                enriched, chat_query, {"userId": "u1"}, gp, MagicMock()
            )
        assert result["fullName"] == "Bob"

    @pytest.mark.asyncio
    async def test_lookup_fails_returns_unchanged(self):
        from app.api.routes.agent import _resolve_service_account_caller_identity
        chat_query = MagicMock()
        chat_query.callerDisplayName = None
        chat_query.callerEmail = None
        enriched = {"userId": "u1"}

        with patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock,
                   side_effect=RuntimeError("fail")):
            result = await _resolve_service_account_caller_identity(
                enriched, chat_query, {"userId": "u1"}, AsyncMock(), MagicMock()
            )
        assert result is enriched

    @pytest.mark.asyncio
    async def test_no_user_id_skips_lookup(self):
        from app.api.routes.agent import _resolve_service_account_caller_identity
        chat_query = MagicMock()
        chat_query.callerDisplayName = None
        chat_query.callerEmail = None
        enriched = {"userId": "u1"}

        result = await _resolve_service_account_caller_identity(
            enriched, chat_query, {}, AsyncMock(), MagicMock()
        )
        assert result is enriched


# ============================================================================
# _enrich_user_info_for_service_account_agent_chat — lines 772, 780, 786-790
# ============================================================================


class TestEnrichUserInfoForServiceAccountChat:
    @pytest.mark.asyncio
    async def test_missing_created_by(self):
        from app.api.routes.agent import _enrich_user_info_for_service_account_agent_chat
        with pytest.raises(HTTPException) as exc_info:
            await _enrich_user_info_for_service_account_agent_chat(
                {}, AsyncMock(), MagicMock()
            )
        assert exc_info.value.status_code == 500
        assert "createdBy" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_creator_doc_not_found(self):
        from app.api.routes.agent import _enrich_user_info_for_service_account_agent_chat
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await _enrich_user_info_for_service_account_agent_chat(
                {"createdBy": "ck1"}, gp, MagicMock()
            )
        assert exc_info.value.status_code == 500
        assert "creator" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_creator_doc_no_user_id(self):
        from app.api.routes.agent import _enrich_user_info_for_service_account_agent_chat
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={"email": "a@b.com"})
        with pytest.raises(HTTPException) as exc_info:
            await _enrich_user_info_for_service_account_agent_chat(
                {"createdBy": "ck1"}, gp, MagicMock()
            )
        assert exc_info.value.status_code == 500
        assert "userId" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import _enrich_user_info_for_service_account_agent_chat
        gp = AsyncMock()
        gp.get_document = AsyncMock(return_value={
            "userId": "u1", "orgId": "o1", "email": "creator@co.com"
        })
        with patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock,
                   return_value={"userId": "u1", "email": "creator@co.com"}):
            result = await _enrich_user_info_for_service_account_agent_chat(
                {"createdBy": "ck1"}, gp, MagicMock()
            )
        assert result["userId"] == "u1"


# ============================================================================
# _load_service_account_agent_for_chat — lines 809-816
# ============================================================================


class TestLoadServiceAccountAgentForChat:
    @pytest.mark.asyncio
    async def test_agent_not_found(self):
        from app.api.routes.agent import _load_service_account_agent_for_chat, AgentNotFoundError
        gp = AsyncMock()
        gp.get_agent = AsyncMock(return_value=None)
        with pytest.raises(AgentNotFoundError):
            await _load_service_account_agent_for_chat("a1", "o1", gp, MagicMock())

    @pytest.mark.asyncio
    async def test_not_service_account(self):
        from app.api.routes.agent import _load_service_account_agent_for_chat, AgentNotFoundError
        gp = AsyncMock()
        gp.get_agent = AsyncMock(return_value={"isServiceAccount": False})
        with pytest.raises(AgentNotFoundError):
            await _load_service_account_agent_for_chat("a1", "o1", gp, MagicMock())

    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import _load_service_account_agent_for_chat
        gp = AsyncMock()
        gp.get_agent = AsyncMock(return_value={"isServiceAccount": True, "createdBy": "ck1"})
        gp.get_document = AsyncMock(return_value={
            "userId": "u1", "orgId": "o1", "email": "c@co.com"
        })
        with patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock,
                   return_value={"userId": "u1"}):
            agent, user_info, perm = await _load_service_account_agent_for_chat(
                "a1", "o1", gp, MagicMock()
            )
        assert agent["isServiceAccount"] is True
        assert perm["role"] == "viewer"


# ============================================================================
# _build_agent_capability_context — lines 637-698
# ============================================================================


class TestBuildAgentCapabilityContext:
    def test_with_knowledge_and_toolsets(self):
        from app.api.routes.agent import _build_agent_capability_context
        query_info = {
            "knowledge": [
                {"connectorId": "c1", "name": "Jira", "type": "jira"},
            ],
            "connector_configs": {},
            "toolsets": [
                {"tools": [
                    {"fullName": "jira.search", "description": "Search Jira issues"},
                    {"fullName": "jira.create", "description": "Create issue"},
                ]},
            ],
        }
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [{"label": "Jira", "type_key": "jira", "filters": None}])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            block, n_k, ic, kb, tools = _build_agent_capability_context(query_info)
        assert n_k == 1
        assert len(tools) == 2
        assert "jira.search" in block

    def test_fallback_filter_counts(self):
        """Line 663 — fallback when no knowledge list."""
        from app.api.routes.agent import _build_agent_capability_context
        query_info = {
            "filters": {"apps": ["app1"], "kb": ["rg1"]},
        }
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            block, n_k, _, _, _ = _build_agent_capability_context(query_info)
        assert n_k == 2
        assert "2 total" in block

    def test_no_knowledge_no_tools(self):
        from app.api.routes.agent import _build_agent_capability_context
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            block, n_k, _, _, tools = _build_agent_capability_context({})
        assert n_k == 0
        assert "none configured" in block
        assert tools == []

    def test_flat_tools_fallback(self):
        """Lines 687-690 — no toolsets, flat tools list."""
        from app.api.routes.agent import _build_agent_capability_context
        query_info = {"tools": ["web_search", "calculator"]}
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            block, _, _, _, tools = _build_agent_capability_context(query_info)
        assert len(tools) == 2
        assert tools[0]["full_name"] == "web_search"

    def test_tool_with_description(self):
        """Lines 696-697 — tool description appended."""
        from app.api.routes.agent import _build_agent_capability_context
        query_info = {
            "toolsets": [{"tools": [{"fullName": "jira.search", "description": "Search all issues"}]}],
        }
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            block, _, _, _, _ = _build_agent_capability_context(query_info)
        assert "Search all issues" in block

    def test_tool_without_fullname_skipped(self):
        """Lines 682-683 — empty fullName skipped."""
        from app.api.routes.agent import _build_agent_capability_context
        query_info = {
            "toolsets": [{"tools": [{"fullName": "", "description": "bad"}, {"fullName": "ok.tool"}]}],
        }
        with patch("app.modules.agents.capability_summary.classify_knowledge_sources",
                   return_value=([], [])), \
             patch("app.modules.agents.capability_summary.format_connector_filter_lines",
                   return_value=[]):
            _, _, _, _, tools = _build_agent_capability_context(query_info)
        assert len(tools) == 1


# ============================================================================
# _parse_models — lines 845->834, reasoning flag
# ============================================================================


class TestParseModelsReasoningFlag:
    def test_reasoning_model_detected(self):
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models(
            [{"modelKey": "mk1", "modelName": "gpt-4", "isReasoning": True}],
            MagicMock(),
        )
        assert has_reasoning is True
        assert "mk1_gpt-4" in entries

    def test_no_reasoning_model(self):
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models(
            [{"modelKey": "mk1", "isReasoning": False}],
            MagicMock(),
        )
        assert has_reasoning is False

    def test_string_entry(self):
        """Line 845->834 — string model entry."""
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models(["plain_model"], MagicMock())
        assert entries == ["plain_model"]
        assert has_reasoning is False


# ============================================================================
# get_assistant_agent — lines 3881-3882, 3900, 3920-3922, 3930-3947
# ============================================================================


class TestGetAssistantAgent:
    @pytest.mark.asyncio
    async def test_user_not_found(self):
        from app.api.routes.agent import get_assistant_agent
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value=None)
        result = await get_assistant_agent("u1", "o1", AsyncMock(), gp, None, MagicMock())
        assert result == {}

    @pytest.mark.asyncio
    async def test_kb_pagination(self):
        from app.api.routes.agent import get_assistant_agent
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        page1 = ([{"id": "kb1", "name": "KB1"}], 2, None)
        page2 = ([{"id": "kb2", "name": "KB2"}], 2, None)
        gp.list_user_knowledge_bases = AsyncMock(side_effect=[page1, page2])
        gp.get_user_apps = AsyncMock(return_value=[])
        cs = AsyncMock()

        with patch("app.api.routes.toolsets.get_authenticated_toolsets",
                   new_callable=AsyncMock, return_value=[]):
            result = await get_assistant_agent("u1", "o1", cs, gp, None, MagicMock())

        assert len(result.get("knowledge", [])) == 2

    @pytest.mark.asyncio
    async def test_kb_skip_no_id(self):
        """Line 3900 — KB without id is skipped."""
        from app.api.routes.agent import get_assistant_agent
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        gp.list_user_knowledge_bases = AsyncMock(return_value=([{"name": "No ID"}], 1, None))
        gp.get_user_apps = AsyncMock(return_value=[])

        with patch("app.api.routes.toolsets.get_authenticated_toolsets",
                   new_callable=AsyncMock, return_value=[]):
            result = await get_assistant_agent("u1", "o1", AsyncMock(), gp, None, MagicMock())

        kb_entries = [k for k in result.get("knowledge", []) if "knowledgeBase" in k.get("connectorId", "")]
        assert len(kb_entries) == 0

    @pytest.mark.asyncio
    async def test_app_connectors_skip_kb_type(self):
        """Lines 3934-3935 — KB type connectors skipped from apps."""
        from app.api.routes.agent import get_assistant_agent
        from app.config.constants.arangodb import Connectors
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
        gp.list_user_knowledge_bases = AsyncMock(return_value=([], 0, None))
        gp.get_user_apps = AsyncMock(return_value=[
            {"id": "c1", "name": "Slack", "type": "slack"},
            {"id": "c2", "name": "MyKB", "type": Connectors.KNOWLEDGE_BASE.value},
        ])

        with patch("app.api.routes.toolsets.get_authenticated_toolsets",
                   new_callable=AsyncMock, return_value=[]):
            result = await get_assistant_agent("u1", "o1", AsyncMock(), gp, None, MagicMock())

        names = [k["name"] for k in result.get("knowledge", [])]
        assert "Slack" in names
        assert "MyKB" not in names

    @pytest.mark.asyncio
    async def test_knowledge_fetch_exception(self):
        """Lines 3948-3950 — outer exception → empty knowledge."""
        from app.api.routes.agent import get_assistant_agent
        gp = AsyncMock()
        gp.get_user_by_user_id = AsyncMock(side_effect=RuntimeError("fail"))

        with patch("app.api.routes.toolsets.get_authenticated_toolsets",
                   new_callable=AsyncMock, return_value=[]):
            result = await get_assistant_agent("u1", "o1", AsyncMock(), gp, None, MagicMock())

        assert result.get("knowledge", []) == [] or result == {}


# ============================================================================
# update_agent — lines 2621-2622, 2648, 2654, 2680-2700, 2706
# ============================================================================


class TestUpdateAgentServiceAccountGuards:
    @pytest.mark.asyncio
    async def test_no_reasoning_model(self):
        """Line 2621-2622 — missing reasoning model."""
        from app.api.routes.agent import update_agent, InvalidRequestError
        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({
            "models": [{"modelKey": "mk1", "isReasoning": False}],
        }).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(InvalidRequestError, match="reasoning"):
                await update_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_sa_downgrade_blocked(self):
        """Line 2648 — SA → regular blocked."""
        from app.api.routes.agent import update_agent, InvalidRequestError
        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({
            "isServiceAccount": False,
        }).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": True, "shareWithOrg": True}
        )
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(InvalidRequestError, match="cannot be converted"):
                await update_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_sa_upgrade_forces_share(self):
        """Line 2654 — regular → SA forces shareWithOrg."""
        from app.api.routes.agent import update_agent
        request = MagicMock()
        body = {"isServiceAccount": True, "name": "Agent"}
        request.body = AsyncMock(return_value=json.dumps(body).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": False, "shareWithOrg": False}
        )
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
             patch("app.api.routes.agent._format_web_search_for_response", return_value=None), \
             patch("app.api.routes.agent._mark_deprecated_tools", new_callable=AsyncMock):
            result = await update_agent(request, "a1")

        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_update_agent_returns_false(self):
        """Line 2706 — update_agent graph call fails."""
        from app.api.routes.agent import update_agent
        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({"name": "A"}).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": False}
        )
        services["graph_provider"].update_agent = AsyncMock(return_value=False)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await update_agent(request, "a1")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_sa_cannot_unshare(self):
        """Line 2682-2686 — SA agent cannot disable org sharing."""
        from app.api.routes.agent import update_agent, InvalidRequestError
        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps({
            "shareWithOrg": False,
        }).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": True, "shareWithOrg": True}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(InvalidRequestError, match="service account"):
                await update_agent(request, "a1")


# ============================================================================
# delete_agent — lines 3053-3074, 3097-3110
# ============================================================================


class TestDeleteAgentServiceAccountCleanup:
    @pytest.mark.asyncio
    async def test_sa_refresh_task_cancellation(self):
        """Lines 3053-3074 — cancel refresh tasks for SA agent."""
        from app.api.routes.agent import delete_agent
        request = MagicMock()

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_delete": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": True, "_key": "a1"}
        )
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn1")
        services["graph_provider"].delete_agent = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()
        services["config_service"].list_keys_in_directory = AsyncMock(
            return_value=["/services/toolsets/inst1/a1", "/services/toolsets/inst2/other"]
        )

        mock_refresh = MagicMock()
        mock_startup = MagicMock()
        mock_startup.get_toolset_token_refresh_service.return_value = mock_refresh

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}), \
             patch.dict("sys.modules", {
                 "app.connectors.core.base.token_service.startup_service": MagicMock(startup_service=mock_startup),
             }):
            result = await delete_agent(request, "a1")

        assert result.status_code == 200
        mock_refresh.cancel_refresh_task.assert_called_once_with("/services/toolsets/inst1/a1")

    @pytest.mark.asyncio
    async def test_generic_exception_rollback(self):
        """Lines 3101-3110 — generic exception triggers rollback."""
        from app.api.routes.agent import delete_agent
        request = MagicMock()

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_delete": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"isServiceAccount": False, "_key": "a1"}
        )
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn1")
        services["graph_provider"].delete_agent = AsyncMock(side_effect=RuntimeError("db error"))
        services["graph_provider"].rollback_transaction = AsyncMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await delete_agent(request, "a1")
            assert exc_info.value.status_code == 400

        services["graph_provider"].rollback_transaction.assert_called_once()


# ============================================================================
# _build_prior_routing_messages — lines 568, 576, 579, 585, 591, 595
# ============================================================================


class TestBuildPriorRoutingMessages:
    @pytest.mark.asyncio
    async def test_empty_previous_conversations(self):
        from app.api.routes.agent import _build_prior_routing_messages
        result = await _build_prior_routing_messages({})
        assert result == []

    @pytest.mark.asyncio
    async def test_user_query_and_bot_response(self):
        from app.api.routes.agent import _build_prior_routing_messages
        query_info = {
            "previous_conversations": [
                {"role": "user_query", "content": "Hello there"},
                {"role": "bot_response", "content": "Hi! How can I help?"},
            ]
        }
        result = await _build_prior_routing_messages(query_info)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_user_query_with_pdf_attachment(self):
        from app.api.routes.agent import _build_prior_routing_messages
        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": []}})
        query_info = {
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Check this",
                    "attachments": [{"mimeType": "application/pdf", "virtualRecordId": "vr1"}],
                },
            ]
        }
        mock_mod = MagicMock(resolve_pdf_blocks_simple=MagicMock(return_value=[{"type": "text", "text": "pdf content"}]))
        with patch.dict("sys.modules", {"app.utils.attachment_utils": mock_mod}):
            result = await _build_prior_routing_messages(query_info, blob_store, "o1", True)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_user_query_attachment_no_vrid_skipped(self):
        """Line 571-572 — attachment without virtualRecordId skipped."""
        from app.api.routes.agent import _build_prior_routing_messages
        query_info = {
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Check",
                    "attachments": [{"mimeType": "image/png"}],
                },
            ]
        }
        result = await _build_prior_routing_messages(query_info, AsyncMock(), "o1", True)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_user_query_image_attachment_multimodal(self):
        """Lines 579-594 — image attachment added for multimodal."""
        from app.api.routes.agent import _build_prior_routing_messages
        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(return_value={
            "block_containers": {"blocks": [
                {"type": "image", "data": {"uri": "data:image/png;base64,iVBOR"}},
            ]}
        })
        query_info = {
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Look at image",
                    "attachments": [{"mimeType": "image/png", "virtualRecordId": "vr1"}],
                },
            ]
        }
        mock_chat_helpers = MagicMock(is_base64_image=MagicMock(return_value=True))
        mock_attachment_utils = MagicMock(resolve_pdf_blocks_simple=MagicMock(return_value=[]))
        with patch.dict("sys.modules", {
            "app.utils.chat_helpers": mock_chat_helpers,
            "app.utils.attachment_utils": mock_attachment_utils,
        }):
            result = await _build_prior_routing_messages(query_info, blob_store, "o1", True)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_attachment_exception_swallowed(self):
        """Line 595 — exception during attachment fetch doesn't block routing."""
        from app.api.routes.agent import _build_prior_routing_messages
        blob_store = AsyncMock()
        blob_store.get_record_from_storage = AsyncMock(side_effect=RuntimeError("fail"))
        query_info = {
            "previous_conversations": [
                {
                    "role": "user_query",
                    "content": "Content",
                    "attachments": [{"mimeType": "image/png", "virtualRecordId": "vr1"}],
                },
            ]
        }
        result = await _build_prior_routing_messages(query_info, blob_store, "o1", True)
        assert len(result) == 1


# ============================================================================
# _auto_select_graph — lines 355, 485, 493
# ============================================================================


class TestAutoSelectGraph:
    @pytest.mark.asyncio
    async def test_empty_query_returns_modern(self):
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        result = await _auto_select_graph({"query": ""}, MagicMock(), MagicMock())
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_attachment_blocks_in_routing(self):
        """Lines 485-493 — attachment blocks inject multimodal content."""
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        mock_llm = MagicMock()
        structured = AsyncMock()
        mock_llm.with_structured_output = MagicMock(return_value=structured)
        mock_decision = MagicMock()
        mock_decision.chatMode = "react"
        structured.ainvoke = AsyncMock(return_value=mock_decision)
        blob_store = AsyncMock()

        with patch("app.api.routes.agent._build_agent_capability_context",
                   return_value=("cap_block", 0, [], [], [])), \
             patch("app.api.routes.agent._build_prior_routing_messages",
                   new_callable=AsyncMock, return_value=[]), \
             patch("app.api.routes.agent.resolve_attachments",
                   new_callable=AsyncMock, return_value=[{"type": "image_url", "image_url": {"url": "base64..."}}]):
            result = await _auto_select_graph(
                {"query": "analyze this", "attachments": [{"id": "a1"}]},
                MagicMock(), mock_llm,
                is_multimodal_llm=True,
                org_id="o1",
            )
        assert result is modern_agent_graph


# ============================================================================
# _select_agent_graph_for_query — multiple modes
# ============================================================================


class TestSelectAgentGraphForQuery:
    @pytest.mark.asyncio
    async def test_deep_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, deep_agent_graph
        result = await _select_agent_graph_for_query(
            {"chatMode": "deep"}, MagicMock(), MagicMock()
        )
        assert result is deep_agent_graph

    @pytest.mark.asyncio
    async def test_verification_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, modern_agent_graph
        result = await _select_agent_graph_for_query(
            {"chatMode": "verification"}, MagicMock(), MagicMock()
        )
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_quick_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph
        result = await _select_agent_graph_for_query(
            {"chatMode": "quick"}, MagicMock(), MagicMock()
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_auto_mode_delegates(self):
        from app.api.routes.agent import _select_agent_graph_for_query, modern_agent_graph
        with patch("app.api.routes.agent._auto_select_graph",
                   new_callable=AsyncMock, return_value=modern_agent_graph):
            result = await _select_agent_graph_for_query(
                {"chatMode": "auto"}, MagicMock(), MagicMock()
            )
        assert result is modern_agent_graph


# ============================================================================
# share_agent/unshare_agent — lines 3133, 3140, 3148-3150, 3169, 3172, 3176, 3182-3186
# ============================================================================


class TestShareAgentEdgeCases:
    @pytest.mark.asyncio
    async def test_share_fails(self):
        """Line 3140 — share_agent returns falsy."""
        from app.api.routes.agent import share_agent
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].share_agent = AsyncMock(return_value=False)
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await share_agent(request, "a1")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_share_generic_exception(self):
        """Lines 3148-3150 — generic exception."""
        from app.api.routes.agent import share_agent
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].share_agent = AsyncMock(side_effect=RuntimeError("fail"))
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await share_agent(request, "a1")
            assert exc_info.value.status_code == 400


class TestUnshareAgentEdgeCases:
    @pytest.mark.asyncio
    async def test_unshare_not_found(self):
        """Line 3169 — no permission."""
        from app.api.routes.agent import unshare_agent, AgentNotFoundError
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(return_value=None)
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(AgentNotFoundError):
                await unshare_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_unshare_no_share_perm(self):
        """Line 3172 — no can_share."""
        from app.api.routes.agent import unshare_agent, PermissionDeniedError
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": False}
        )
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(PermissionDeniedError):
                await unshare_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_unshare_fails(self):
        """Line 3176 — unshare_agent returns falsy."""
        from app.api.routes.agent import unshare_agent
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].unshare_agent = AsyncMock(return_value=False)
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await unshare_agent(request, "a1")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_unshare_generic_exception(self):
        """Lines 3182-3186 — generic exception."""
        from app.api.routes.agent import unshare_agent
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].unshare_agent = AsyncMock(side_effect=RuntimeError("fail"))
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds": ["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await unshare_agent(request, "a1")
            assert exc_info.value.status_code == 400


# ============================================================================
# update_agent_permission — lines 3211-3215, 3238, 3246-3248
# ============================================================================


class TestUpdateAgentPermissionEdgeCases:
    @pytest.mark.asyncio
    async def test_update_perm_fails(self):
        """Lines 3211-3215 — update returns false."""
        from app.api.routes.agent import update_agent_permission
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].update_agent_permission = AsyncMock(return_value=False)
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userId": "u2", "role": "editor"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await update_agent_permission(request, "a1")
            assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_update_perm_generic_exception(self):
        """Lines 3246-3248 — generic exception."""
        from app.api.routes.agent import update_agent_permission
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_share": True}
        )
        services["graph_provider"].update_agent_permission = AsyncMock(side_effect=RuntimeError("fail"))
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userId": "u2", "role": "editor"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await update_agent_permission(request, "a1")
            assert exc_info.value.status_code == 400


# ============================================================================
# _mark_deprecated_tools — lines 1477, 1480
# ============================================================================


class TestMarkDeprecatedToolsEdgeCases:
    def _mock_registry(self, tool_names):
        mock_reg = MagicMock()
        mock_reg.list_tools = MagicMock(return_value=list(tool_names))
        return MagicMock(_global_tools_registry=mock_reg)

    def test_empty_toolset_skipped(self):
        """Line 1477 — falsy toolset skipped."""
        from app.api.routes.agent import _mark_deprecated_tools
        agent = {"toolsets": [None, {"tools": [{"fullName": "jira.search"}]}]}
        with patch.dict("sys.modules", {"app.agents.tools.registry": self._mock_registry({"jira.search"})}):
            _mark_deprecated_tools(agent, MagicMock())

    def test_empty_tool_skipped(self):
        """Line 1480 — falsy tool skipped."""
        from app.api.routes.agent import _mark_deprecated_tools
        agent = {"toolsets": [{"tools": [None, {"fullName": "jira.search"}]}]}
        with patch.dict("sys.modules", {"app.agents.tools.registry": self._mock_registry({"jira.search"})}):
            _mark_deprecated_tools(agent, MagicMock())

    def test_empty_registry_skips(self):
        """Line 1471 — empty registry skips annotation."""
        from app.api.routes.agent import _mark_deprecated_tools
        agent = {"toolsets": [{"tools": [{"fullName": "jira.search"}]}]}
        with patch.dict("sys.modules", {"app.agents.tools.registry": self._mock_registry(set())}):
            _mark_deprecated_tools(agent, MagicMock())
        assert "deprecated" not in agent["toolsets"][0]["tools"][0]

    def test_deprecated_tool_marked(self):
        from app.api.routes.agent import _mark_deprecated_tools
        agent = {"toolsets": [{"tools": [
            {"fullName": "jira.search"},
            {"fullName": "old.tool"},
        ]}]}
        with patch.dict("sys.modules", {"app.agents.tools.registry": self._mock_registry({"jira.search"})}):
            _mark_deprecated_tools(agent, MagicMock())
        assert agent["toolsets"][0]["tools"][0]["deprecated"] is False
        assert agent["toolsets"][0]["tools"][1]["deprecated"] is True


# ============================================================================
# get_agent_templates — lines 1848-1852
# ============================================================================


class TestGetAgentTemplatesEdgeCases:
    @pytest.mark.asyncio
    async def test_generic_exception(self):
        """Lines 1848-1852 — generic exception wrapped."""
        from app.api.routes.agent import get_agent_templates
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].get_agent_templates = AsyncMock(side_effect=RuntimeError("fail"))
        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc_info:
                await get_agent_templates(request)
            assert exc_info.value.status_code == 400


# ============================================================================
# create_agent — lines 2142, 2144 (instanceId/instanceName)
# ============================================================================


class TestCreateAgentInstanceFields:
    @pytest.mark.asyncio
    async def test_instance_id_stored(self):
        """Lines 2142-2144 — instanceId/instanceName stored in toolset node."""
        from app.api.routes.agent import create_agent
        body = {
            "name": "Agent1",
            "description": "A test agent",
            "type": "agent",
            "models": [{"modelKey": "mk1", "modelName": "gpt-4", "isReasoning": True}],
            "toolsets": [{"name": "jira", "displayName": "Jira", "type": "app",
                          "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}],
                          "instanceId": "inst1", "instanceName": "My Jira"}],
        }
        request = MagicMock()
        request.body = AsyncMock(return_value=json.dumps(body).encode())

        services = {
            "graph_provider": AsyncMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1", "email": "u@co.com"}):
            result = await create_agent(request)

        assert result.status_code == 200
        upsert_calls = services["graph_provider"].batch_upsert_nodes.call_args_list
        toolset_nodes = None
        for call in upsert_calls:
            args, kwargs = call
            if len(args) >= 2 and "TOOLSET" in str(args[1]).upper():
                toolset_nodes = args[0]
                break
        if toolset_nodes:
            assert any(n.get("instanceId") == "inst1" for n in toolset_nodes)


class TestStreamResponse:
    @pytest.mark.asyncio
    async def test_stream_yields_events(self):
        from app.api.routes.agent import stream_response

        mock_llm = MagicMock()
        log = logging.getLogger("test")
        gp = AsyncMock()
        rr = MagicMock()
        rs = MagicMock()
        cs = MagicMock()

        async def mock_astream(*args, **kwargs):
            yield {"event": "token", "data": {"text": "hello"}}

        with patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select:
            mock_graph = MagicMock()
            mock_graph.astream = mock_astream
            mock_select.return_value = mock_graph
            with patch("app.api.routes.agent.build_initial_state", return_value={}):
                chunks = []
                async for chunk in stream_response(
                    {"chatMode": "quick"}, {"userId": "u1", "orgId": "o1"}, mock_llm, log, rs, gp, rr, cs
                ):
                    chunks.append(chunk)
                assert len(chunks) >= 1
                assert "event: token" in chunks[0]

    @pytest.mark.asyncio
    async def test_stream_error(self):
        from app.api.routes.agent import stream_response

        mock_llm = MagicMock()
        log = logging.getLogger("test")

        with patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock, side_effect=Exception("fail")):
            chunks = []
            async for chunk in stream_response(
                {"chatMode": "quick"}, {"userId": "u1", "orgId": "o1"}, mock_llm, log,
                MagicMock(), AsyncMock(), MagicMock(), MagicMock()
            ):
                chunks.append(chunk)
            assert any("error" in c for c in chunks)
