"""Comprehensive coverage tests for app.api.routes.agent.

Targets every uncovered branch and missing line to achieve 95%+ coverage.
Covers helper functions, exception classes, parsers, route endpoints, and
streaming logic.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# =============================================================================
# Exception classes
# =============================================================================


class TestExceptionClasses:
    def test_agent_error(self) -> None:
        from app.api.routes.agent import AgentError
        err = AgentError("something went wrong", status_code=503)
        assert err.status_code == 503
        assert err.detail == "something went wrong"

    def test_agent_error_default_status(self) -> None:
        from app.api.routes.agent import AgentError
        err = AgentError("generic error")
        assert err.status_code == 500

    def test_agent_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError
        err = AgentNotFoundError("agent-123")
        assert err.status_code == 404
        assert "not found" in err.detail.lower()

    def test_agent_template_not_found(self) -> None:
        from app.api.routes.agent import AgentTemplateNotFoundError
        err = AgentTemplateNotFoundError("tmpl-456")
        assert err.status_code == 404
        assert "tmpl-456" in err.detail

    def test_permission_denied(self) -> None:
        from app.api.routes.agent import PermissionDeniedError
        err = PermissionDeniedError("delete this agent")
        assert err.status_code == 403
        assert "delete this agent" in err.detail

    def test_invalid_request_error(self) -> None:
        from app.api.routes.agent import InvalidRequestError
        err = InvalidRequestError("field missing")
        assert err.status_code == 400
        assert "field missing" in err.detail

    def test_llm_initialization_error(self) -> None:
        from app.api.routes.agent import LLMInitializationError
        err = LLMInitializationError()
        assert err.status_code == 500
        assert "LLM" in err.detail


# =============================================================================
# _get_user_context
# =============================================================================


class TestGetUserContext:
    def test_valid_user_context(self) -> None:
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params.get.return_value = True

        result = _get_user_context(request)
        assert result["userId"] == "u1"
        assert result["orgId"] == "o1"
        assert result["sendUserInfo"] is True

    def test_missing_user_id_raises(self) -> None:
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"orgId": "o1"}

        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == 401

    def test_missing_org_id_raises(self) -> None:
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1"}

        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == 401

    def test_no_user_attr(self) -> None:
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state = MagicMock(spec=[])

        with pytest.raises(HTTPException) as exc_info:
            _get_user_context(request)
        assert exc_info.value.status_code == 401


# =============================================================================
# _validate_required_fields
# =============================================================================


class TestValidateRequiredFields:
    def test_passes_with_all_present(self) -> None:
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"name": "test", "desc": "d"}, ["name", "desc"])

    def test_fails_on_missing_field(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "x"}, ["name", "missing_field"])

    def test_fails_on_empty_string(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "  "}, ["name"])

    def test_fails_on_none_value(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])


# =============================================================================
# _parse_models
# =============================================================================


class TestParseModels:
    def test_dict_models_with_key_and_name(self) -> None:
        from app.api.routes.agent import _parse_models
        raw = [
            {"modelKey": "k1", "modelName": "m1", "isReasoning": True},
            {"modelKey": "k2"},
        ]
        entries, has_reasoning = _parse_models(raw, MagicMock())
        assert entries == ["k1_m1", "k2"]
        assert has_reasoning is True

    def test_string_models(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models(["model1", "model2"], MagicMock())
        assert entries == ["model1", "model2"]
        assert has_reasoning is False

    def test_empty_list(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models([], MagicMock())
        assert entries == []
        assert has_reasoning is False

    def test_none_input(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models(None, MagicMock())
        assert entries == []
        assert has_reasoning is False

    def test_non_list_input(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, has_reasoning = _parse_models("not-a-list", MagicMock())
        assert entries == []

    def test_dict_without_model_key(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, _ = _parse_models([{"modelName": "n"}], MagicMock())
        assert entries == []

    def test_dict_with_empty_model_name(self) -> None:
        from app.api.routes.agent import _parse_models
        entries, _ = _parse_models([{"modelKey": "k", "modelName": ""}], MagicMock())
        assert entries == ["k"]


# =============================================================================
# _parse_toolsets
# =============================================================================


class TestParseToolsets:
    def test_basic_toolset(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "Jira",
            "displayName": "Jira App",
            "type": "app",
            "instanceId": "inst-1",
            "instanceName": "My Jira",
            "tools": [{"name": "search", "fullName": "jira.search", "description": "Search issues"}],
        }]
        result = _parse_toolsets(raw)
        assert "jira" in result
        assert result["jira"]["displayName"] == "Jira App"
        assert result["jira"]["instanceId"] == "inst-1"
        assert len(result["jira"]["tools"]) == 1

    def test_empty_input(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        assert _parse_toolsets([]) == {}
        assert _parse_toolsets(None) == {}

    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(["not-a-dict", 123])
        assert result == {}

    def test_missing_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"displayName": "x"}])
        assert result == {}

    def test_empty_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "  "}])
        assert result == {}

    def test_default_display_name(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "my_tool", "tools": []}])
        assert result["my_tool"]["displayName"] == "My Tool"

    def test_duplicate_toolset_updates_instance_id(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "tools": []},
            {"name": "jira", "instanceId": "inst-2", "instanceName": "Jira #2", "tools": []},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-2"

    def test_duplicate_toolset_does_not_overwrite_existing_instance_id(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "instanceId": "inst-1", "tools": []},
            {"name": "jira", "instanceId": "inst-2", "tools": []},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-1"


# =============================================================================
# _parse_knowledge_sources
# =============================================================================


class TestParseKnowledgeSources:
    def test_valid_knowledge(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": {"types": ["doc"]}}]
        result = _parse_knowledge_sources(raw)
        assert "c1" in result
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_json_string_filters(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": '{"types": ["doc"]}'}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_invalid_json_filters(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {}

    def test_empty_and_none_input(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources([]) == {}
        assert _parse_knowledge_sources(None) == {}

    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources(["not-dict"]) == {}

    def test_empty_connector_id_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources([{"connectorId": "  "}]) == {}


# =============================================================================
# _filter_knowledge_by_enabled_sources
# =============================================================================


class TestFilterKnowledgeByEnabledSources:
    def test_no_filters_returns_all(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}, {"connectorId": "c2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert len(result) == 2

    def test_app_filter(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "app1"}, {"connectorId": "app2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["app1"]})
        assert len(result) == 1

    def test_kb_filter_matching(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg1"]}}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_kb_filter_no_match(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg3"]}}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0

    def test_kb_filter_with_json_string(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": '{"recordGroups": ["rg1"]}'}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_kb_filter_invalid_json_string(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": "not json"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0

    def test_kb_filter_with_filtersParsed_key(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filtersParsed": {"recordGroups": ["rg1"]}}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        result = _filter_knowledge_by_enabled_sources(["not-dict"], {"apps": ["a"]})
        assert len(result) == 0

    def test_kb_connector_not_matching_no_kb_filter(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg1"]}}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["other"]})
        assert len(result) == 0

    def test_non_list_filters_data(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "knowledgeBase_1", "filters": 42}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0


# =============================================================================
# _parse_request_body
# =============================================================================


class TestParseRequestBody:
    def test_valid_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(json.dumps({"key": "val"}).encode("utf-8"))
        assert result == {"key": "val"}

    def test_empty_body(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"")

    def test_invalid_json(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"not json")


# =============================================================================
# _build_routing_context
# =============================================================================


class TestBuildRoutingContext:
    def test_no_previous_conversations(self) -> None:
        from app.api.routes.agent import _build_routing_context
        result = _build_routing_context({})
        assert result == ""

    def test_with_conversations(self) -> None:
        from app.api.routes.agent import _build_routing_context
        convs = [
            {"role": "user_query", "content": "What is Python?"},
            {"role": "bot_response", "content": "Python is a programming language.\nMore details here."},
        ]
        result = _build_routing_context({"previous_conversations": convs})
        assert "User:" in result
        assert "Assistant:" in result
        assert "Prior conversation" in result

    def test_only_last_6_turns(self) -> None:
        from app.api.routes.agent import _build_routing_context
        convs = [{"role": "user_query", "content": f"msg {i}"} for i in range(10)]
        result = _build_routing_context({"previous_conversations": convs})
        # Should only include last 6
        assert "msg 4" in result
        assert "msg 0" not in result

    def test_unknown_role_not_included(self) -> None:
        from app.api.routes.agent import _build_routing_context
        convs = [{"role": "system", "content": "ignored"}]
        result = _build_routing_context({"previous_conversations": convs})
        # No turns generated, returns empty
        assert result == ""


# =============================================================================
# _get_user_document
# =============================================================================


class TestGetUserDocument:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        from app.api.routes.agent import _get_user_document
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = {"email": "a@b.com", "_key": "k1"}
        result = await _get_user_document("u1", gp, MagicMock())
        assert result["email"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_user_not_found(self) -> None:
        from app.api.routes.agent import _get_user_document
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await _get_user_document("u1", gp, MagicMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_user_not_dict(self) -> None:
        from app.api.routes.agent import _get_user_document
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = "not-a-dict"
        with pytest.raises(HTTPException) as exc_info:
            await _get_user_document("u1", gp, MagicMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_email(self) -> None:
        from app.api.routes.agent import _get_user_document
        gp = AsyncMock()
        gp.get_user_by_user_id.return_value = {"email": "  ", "_key": "k1"}
        with pytest.raises(HTTPException) as exc_info:
            await _get_user_document("u1", gp, MagicMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        from app.api.routes.agent import _get_user_document
        gp = AsyncMock()
        gp.get_user_by_user_id.side_effect = RuntimeError("db error")
        with pytest.raises(HTTPException) as exc_info:
            await _get_user_document("u1", gp, MagicMock())
        assert exc_info.value.status_code == 500


# =============================================================================
# _get_org_info
# =============================================================================


class TestGetOrgInfo:
    @pytest.mark.asyncio
    async def test_success_enterprise(self) -> None:
        from app.api.routes.agent import _get_org_info
        gp = AsyncMock()
        gp.get_document.return_value = {"accountType": "Enterprise"}
        result = await _get_org_info({"orgId": "o1"}, gp, MagicMock())
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_success_individual(self) -> None:
        from app.api.routes.agent import _get_org_info
        gp = AsyncMock()
        gp.get_document.return_value = {"accountType": "individual"}
        result = await _get_org_info({"orgId": "o1"}, gp, MagicMock())
        assert result["accountType"] == "individual"

    @pytest.mark.asyncio
    async def test_org_not_found(self) -> None:
        from app.api.routes.agent import _get_org_info
        gp = AsyncMock()
        gp.get_document.return_value = None
        with pytest.raises(HTTPException) as exc_info:
            await _get_org_info({"orgId": "o1"}, gp, MagicMock())
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_account_type(self) -> None:
        from app.api.routes.agent import _get_org_info
        gp = AsyncMock()
        gp.get_document.return_value = {"accountType": "free"}
        with pytest.raises(HTTPException) as exc_info:
            await _get_org_info({"orgId": "o1"}, gp, MagicMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        from app.api.routes.agent import _get_org_info
        gp = AsyncMock()
        gp.get_document.side_effect = RuntimeError("db error")
        with pytest.raises(HTTPException) as exc_info:
            await _get_org_info({"orgId": "o1"}, gp, MagicMock())
        assert exc_info.value.status_code == 500


# =============================================================================
# _enrich_user_info
# =============================================================================


class TestEnrichUserInfo:
    @pytest.mark.asyncio
    async def test_enrich_with_all_fields(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        user_info = {"userId": "u1", "orgId": "o1"}
        user_doc = {
            "email": "a@b.com", "_key": "k1",
            "fullName": "John Doe", "firstName": "John",
            "lastName": "Doe", "displayName": "Johnny",
        }
        result = await _enrich_user_info(user_info, user_doc)
        assert result["userEmail"] == "a@b.com"
        assert result["_key"] == "k1"
        assert result["fullName"] == "John Doe"
        assert result["firstName"] == "John"
        assert result["lastName"] == "Doe"
        assert result["displayName"] == "Johnny"
        # Original fields preserved
        assert result["userId"] == "u1"

    @pytest.mark.asyncio
    async def test_enrich_without_optional_fields(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        result = await _enrich_user_info({"userId": "u1"}, {"email": "a@b.com", "_key": "k1"})
        assert "fullName" not in result


# =============================================================================
# _enrich_agent_models
# =============================================================================


class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enrich_matching_model(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key1_gpt-4"]}
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "llm": [{"modelKey": "key1", "provider": "openai", "isReasoning": True,
                      "isMultimodal": False, "isDefault": True,
                      "modelFriendlyName": "GPT-4",
                      "configuration": {"model": "gpt-4"}}]
        }
        await _enrich_agent_models(agent, config_service, MagicMock())
        assert len(agent["models"]) == 1
        assert agent["models"][0]["modelKey"] == "key1"
        assert agent["models"][0]["modelName"] == "gpt-4"
        assert agent["models"][0]["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_enrich_no_matching_config(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key2_unknown"]}
        config_service = AsyncMock()
        config_service.get_config.return_value = {"llm": []}
        await _enrich_agent_models(agent, config_service, MagicMock())
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_enrich_model_key_only(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key1"]}
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "llm": [{"modelKey": "key1", "provider": "openai",
                      "configuration": {"model": "gpt-4,gpt-4-turbo"}}]
        }
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Comma-separated: should take first
        assert agent["models"][0]["modelName"] == "gpt-4"

    @pytest.mark.asyncio
    async def test_enrich_empty_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": []}
        await _enrich_agent_models(agent, AsyncMock(), MagicMock())
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_enrich_none_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {}
        await _enrich_agent_models(agent, AsyncMock(), MagicMock())

    @pytest.mark.asyncio
    async def test_enrich_exception_swallowed(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["k1"]}
        config_service = AsyncMock()
        config_service.get_config.side_effect = Exception("etcd down")
        await _enrich_agent_models(agent, config_service, MagicMock())
        # Should not raise

    @pytest.mark.asyncio
    async def test_enrich_model_key_no_underscore_with_matching_config_no_model_name(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key1"]}
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "llm": [{"modelKey": "key1", "provider": "openai", "modelName": "MyModel",
                      "configuration": {}}]
        }
        await _enrich_agent_models(agent, config_service, MagicMock())
        assert agent["models"][0]["modelName"] == "MyModel"


# =============================================================================
# _select_agent_graph_for_query
# =============================================================================


class TestSelectAgentGraphForQuery:
    @pytest.mark.asyncio
    async def test_deep_mode(self) -> None:
        from app.api.routes.agent import _select_agent_graph_for_query, deep_agent_graph
        result = await _select_agent_graph_for_query(
            {"chatMode": "deep"}, MagicMock(), MagicMock()
        )
        assert result is deep_agent_graph

    @pytest.mark.asyncio
    async def test_verification_mode(self) -> None:
        from app.api.routes.agent import (
            _select_agent_graph_for_query,
            modern_agent_graph,
        )
        result = await _select_agent_graph_for_query(
            {"chatMode": "verification"}, MagicMock(), MagicMock()
        )
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_explicit_quick_mode(self) -> None:
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph
        result = await _select_agent_graph_for_query(
            {"chatMode": "quick"}, MagicMock(), MagicMock()
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_auto_mode_calls_auto_select(self) -> None:
        from app.api.routes.agent import (
            _select_agent_graph_for_query,
            modern_agent_graph,
        )
        with patch("app.api.routes.agent._auto_select_graph", new_callable=AsyncMock) as mock_auto:
            mock_auto.return_value = modern_agent_graph
            result = await _select_agent_graph_for_query(
                {"chatMode": "auto"}, MagicMock(), MagicMock()
            )
            assert result is modern_agent_graph
            mock_auto.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_mode_defaults_to_auto(self) -> None:
        from app.api.routes.agent import (
            _select_agent_graph_for_query,
            modern_agent_graph,
        )
        with patch("app.api.routes.agent._auto_select_graph", new_callable=AsyncMock) as mock_auto:
            mock_auto.return_value = modern_agent_graph
            result = await _select_agent_graph_for_query(
                {"chatMode": None}, MagicMock(), MagicMock()
            )
            assert result is modern_agent_graph


# =============================================================================
# _auto_select_graph
# =============================================================================


class TestAutoSelectGraph:
    @pytest.mark.asyncio
    async def test_empty_query_returns_modern(self) -> None:
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        result = await _auto_select_graph({"query": ""}, MagicMock(), MagicMock())
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_llm_returns_quick(self) -> None:
        from app.api.routes.agent import RouteDecision, _auto_select_graph, agent_graph
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        decision = RouteDecision(reasoning="simple query", route="quick")
        mock_structured.ainvoke.return_value = decision
        mock_llm.with_structured_output.return_value = mock_structured

        result = await _auto_select_graph({"query": "hello"}, MagicMock(), mock_llm)
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_llm_returns_deep(self) -> None:
        from app.api.routes.agent import (
            RouteDecision,
            _auto_select_graph,
            deep_agent_graph,
        )
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        decision = RouteDecision(reasoning="complex", route="deep")
        mock_structured.ainvoke.return_value = decision
        mock_llm.with_structured_output.return_value = mock_structured

        result = await _auto_select_graph({"query": "analyze everything"}, MagicMock(), mock_llm)
        assert result is deep_agent_graph

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back(self) -> None:
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        mock_llm = MagicMock()
        mock_structured = AsyncMock()
        mock_structured.ainvoke.side_effect = Exception("LLM error")
        mock_llm.with_structured_output.return_value = mock_structured

        result = await _auto_select_graph({"query": "test"}, MagicMock(), mock_llm)
        assert result is modern_agent_graph


# =============================================================================
# get_services
# =============================================================================


class TestGetServices:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        from app.api.routes.agent import get_services
        request = MagicMock()
        container = MagicMock()
        retrieval = AsyncMock()
        retrieval.llm = MagicMock()
        container.retrieval_service = AsyncMock(return_value=retrieval)
        container.graph_provider = AsyncMock(return_value=AsyncMock())
        container.reranker_service.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        container.logger.return_value = MagicMock()
        request.app.container = container

        result = await get_services(request)
        assert "llm" in result
        assert "retrieval_service" in result

    @pytest.mark.asyncio
    async def test_llm_none_tries_get_instance(self) -> None:
        from app.api.routes.agent import get_services
        request = MagicMock()
        container = MagicMock()
        retrieval = AsyncMock()
        retrieval.llm = None
        retrieval.get_llm_instance = AsyncMock(return_value=MagicMock())
        container.retrieval_service = AsyncMock(return_value=retrieval)
        container.graph_provider = AsyncMock(return_value=AsyncMock())
        container.reranker_service.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        container.logger.return_value = MagicMock()
        request.app.container = container

        result = await get_services(request)
        assert result["llm"] is not None

    @pytest.mark.asyncio
    async def test_llm_none_both_fail(self) -> None:
        from app.api.routes.agent import LLMInitializationError, get_services
        request = MagicMock()
        container = MagicMock()
        retrieval = AsyncMock()
        retrieval.llm = None
        retrieval.get_llm_instance = AsyncMock(return_value=None)
        container.retrieval_service = AsyncMock(return_value=retrieval)
        container.graph_provider = AsyncMock(return_value=AsyncMock())
        container.reranker_service.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        container.logger.return_value = MagicMock()
        request.app.container = container

        with pytest.raises(LLMInitializationError):
            await get_services(request)
class TestChatStreamWithPlaceholder:
    """Tests for chat_stream endpoint when using agentIdPlaceholder."""

    @pytest.mark.asyncio
    async def test_placeholder_calls_get_assistant_agent(self) -> None:
        """Should call get_assistant_agent when agent_id is 'agentIdPlaceholder'."""
        from fastapi.responses import StreamingResponse

        from app.api.routes.agent import chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test query"}')
        request.app.state.toolset_registry = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_assistant_agent", new_callable=AsyncMock) as mock_get_assistant, \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), {"isReasoning": True}, {})):

            mock_get_assistant.return_value = {
                "name": "assistant",
                "knowledge": [],
                "toolsets": [],
                "models": [],
                "systemPrompt": "Test"
            }

            result = await chat_stream(request, "agentIdPlaceholder")

            mock_get_assistant.assert_called_once()
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_non_placeholder_uses_get_agent(self) -> None:
        """Should use graph_provider.get_agent for non-placeholder agent_id."""
        from fastapi.responses import StreamingResponse

        from app.api.routes.agent import chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "my-agent",
            "knowledge": [],
            "toolsets": [],
            "models": [],
        })
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_share": True, "role": "editor"},
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_assistant_agent", new_callable=AsyncMock) as mock_get_assistant, \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), {"isReasoning": True}, {})):

            result = await chat_stream(request, "real-agent-123")

            mock_get_assistant.assert_not_called()
            services["graph_provider"].get_agent.assert_called_once()
            assert isinstance(result, StreamingResponse)
class TestGetAssistantAgentHelper:
    """Tests for get_assistant_agent helper method."""

    @pytest.mark.asyncio
    async def test_returns_correct_structure(self) -> None:
        """Should return assistant agent with correct structure."""
        config_service = AsyncMock()
        graph_provider = AsyncMock()
        toolset_registry = MagicMock()
        logger = MagicMock()
        with patch("app.api.routes.toolsets.get_authenticated_toolsets", new_callable=AsyncMock) as mock_get_toolsets:
            mock_get_toolsets.return_value = []
            graph_provider.get_user_apps.return_value = []

            import app.api.routes.agent as agent_module
            result = await agent_module.get_assistant_agent("u1", "o1", config_service, graph_provider, toolset_registry, logger)

            assert result["name"] == "assistant"
            assert result["isActive"] is True
            assert "systemPrompt" in result
            assert "models" in result
            assert "toolsets" in result
            assert "knowledge" in result

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(self) -> None:
        """Should handle errors and return empty lists."""
        config_service = AsyncMock()
        graph_provider = AsyncMock()
        toolset_registry = MagicMock()
        logger = MagicMock()

        with patch("app.api.routes.toolsets.get_authenticated_toolsets", new_callable=AsyncMock) as mock_get_toolsets:
            mock_get_toolsets.side_effect = Exception("Error")
            graph_provider.get_user_apps.side_effect = Exception("Error")

            import app.api.routes.agent as agent_module
            result = await agent_module.get_assistant_agent("u1", "o1", config_service, graph_provider, toolset_registry, logger)

            assert result["toolsets"] == []
            assert result["knowledge"] == []

class TestReasoningModelValidation:
    """Tests for reasoning model validation."""

    def test_reasoning_model_required_error(self) -> None:
        """Should create ReasoningModelRequiredError with correct details."""
        from app.api.routes.agent import ReasoningModelRequiredError

        error = ReasoningModelRequiredError()
        assert error.status_code == 400
        assert "reasoning model" in error.detail.lower()

    def test_parse_models_detects_reasoning_model(self) -> None:
        """Should detect reasoning models."""
        from app.api.routes.agent import _parse_models

        models = [
            {"modelKey": "k1", "modelName": "gpt-4", "isReasoning": True},
            {"modelKey": "k2", "modelName": "claude-3"},
        ]

        entries, has_reasoning = _parse_models(models, MagicMock())

        assert has_reasoning is True
        assert len(entries) == 2

    def test_parse_models_no_reasoning_model(self) -> None:
        """Should return False when no reasoning models."""
        from app.api.routes.agent import _parse_models

        models = [
            {"modelKey": "k1", "modelName": "gpt-4", "isReasoning": False},
        ]

        entries, has_reasoning = _parse_models(models, MagicMock())

        assert has_reasoning is False

    @pytest.mark.asyncio
    async def test_chat_stream_requires_reasoning_model(self) -> None:
        """Should raise ReasoningModelRequiredError when LLM is not reasoning."""
        from app.api.routes.agent import ReasoningModelRequiredError, chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1",
            "knowledge": [],
            "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_share": True, "role": "editor"},
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test"}')

        llm_config = {"isReasoning": False}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), llm_config, {})):

            with pytest.raises(ReasoningModelRequiredError):
                await chat_stream(request, "agent-123")

    @pytest.mark.asyncio
    async def test_chat_stream_succeeds_with_reasoning_model(self) -> None:
        """Should succeed when LLM is a reasoning model."""
        from fastapi.responses import StreamingResponse

        from app.api.routes.agent import chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1",
            "knowledge": [],
            "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_share": True, "role": "editor"},
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test"}')

        llm_config = {"isReasoning": True}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), llm_config, {})):

            result = await chat_stream(request, "agent-123")
            assert isinstance(result, StreamingResponse)


# =============================================================================
# KB Filter Tests
# =============================================================================


class TestKBFilterHandling:
    """Tests for kbId filter handling."""
    @pytest.mark.asyncio
    async def test_no_kb_selected_filter_added_for_regular_agent(self) -> None:
        """Should add NO_KB_SELECTED_FILTER for regular agents without kb."""
        from fastapi.responses import StreamingResponse

        from app.api.routes.agent import chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1",
            "knowledge": [],
            "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_share": True, "role": "editor"},
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test", "filters":{}}')

        llm_config = {"isReasoning": True}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), llm_config, {})):

            result = await chat_stream(request, "regular-agent-123")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_no_kb_filter_not_added_for_placeholder(self) -> None:
        """Should NOT add NO_KB_SELECTED_FILTER for placeholder agent."""
        from fastapi.responses import StreamingResponse

        from app.api.routes.agent import chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"test", "filters":{}}')
        request.app.state.toolset_registry = MagicMock()

        llm_config = {"isReasoning": True}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_assistant_agent", new_callable=AsyncMock) as mock_get_assistant, \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), llm_config, {})):

            mock_get_assistant.return_value = {
                "name": "assistant",
                "knowledge": [],
                "toolsets": [],
                "models": [],
                "systemPrompt": "Test"
            }

            result = await chat_stream(request, "agentIdPlaceholder")
            assert isinstance(result, StreamingResponse)
