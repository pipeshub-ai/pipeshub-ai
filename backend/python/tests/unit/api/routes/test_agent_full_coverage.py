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
