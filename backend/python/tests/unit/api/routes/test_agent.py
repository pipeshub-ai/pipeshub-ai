"""Tests for app.api.routes.agent helper functions and models."""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
import uuid
from fastapi import HTTPException


class TestChatQueryModel:
    def test_defaults(self):
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(query="test")
        assert q.query == "test"
        assert q.limit == 50
        assert q.previousConversations == []
        assert q.quickMode is False
        assert q.chatMode == "auto"

    def test_all_fields(self):
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(
            query="test", limit=10, quickMode=True, chatMode="deep",
            modelKey="mk", modelName="mn", timezone="UTC",
            currentTime="2025-01-01T00:00:00Z", conversationId="c1",
        )
        assert q.chatMode == "deep"
        assert q.conversationId == "c1"

    def test_missing_query_fails(self):
        from app.api.routes.agent import ChatQuery
        with pytest.raises(ValidationError):
            ChatQuery()


class TestRouteDecision:
    def test_valid(self):
        from app.api.routes.agent import RouteDecision
        rd = RouteDecision(reasoning="Simple query", route="quick")
        assert rd.route == "quick"

    def test_invalid_route(self):
        from app.api.routes.agent import RouteDecision
        with pytest.raises(ValidationError):
            RouteDecision(reasoning="x", route="invalid")

    def test_all_routes(self):
        from app.api.routes.agent import RouteDecision
        for r in ["quick", "react", "deep"]:
            rd = RouteDecision(reasoning="x", route=r)
            assert rd.route == r


class TestExceptions:
    def test_agent_error(self):
        from app.api.routes.agent import AgentError
        e = AgentError("fail", 500)
        assert e.status_code == 500
        assert e.detail == "fail"

    def test_agent_not_found(self):
        from app.api.routes.agent import AgentNotFoundError
        e = AgentNotFoundError("a1")
        assert e.status_code == 404

    def test_template_not_found(self):
        from app.api.routes.agent import AgentTemplateNotFoundError
        e = AgentTemplateNotFoundError("t1")
        assert e.status_code == 404
        assert "t1" in e.detail

    def test_permission_denied(self):
        from app.api.routes.agent import PermissionDeniedError
        e = PermissionDeniedError("delete agent")
        assert e.status_code == 403
        assert "delete agent" in e.detail

    def test_invalid_request(self):
        from app.api.routes.agent import InvalidRequestError
        e = InvalidRequestError("missing field")
        assert e.status_code == 400

    def test_llm_init_error(self):
        from app.api.routes.agent import LLMInitializationError
        e = LLMInitializationError()
        assert e.status_code == 500


class TestGetUserContext:
    def test_valid_user(self):
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params = {"sendUserInfo": True}
        ctx = _get_user_context(request)
        assert ctx["userId"] == "u1"
        assert ctx["orgId"] == "o1"

    def test_missing_user_id(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"orgId": "o1"}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401

    def test_missing_org_id(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        with pytest.raises(HTTPException):
            _get_user_context(request)


class TestSelectAgentGraph:
    @pytest.mark.asyncio
    async def test_deep_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, deep_agent_graph
        log = logging.getLogger("test")
        result = await _select_agent_graph_for_query(
            {"chatMode": "deep"}, log, MagicMock()
        )
        assert result is deep_agent_graph

    @pytest.mark.asyncio
    async def test_verification_mode(self):
        from app.api.routes.agent import _select_agent_graph_for_query, modern_agent_graph
        log = logging.getLogger("test")
        result = await _select_agent_graph_for_query(
            {"chatMode": "verification"}, log, MagicMock()
        )
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_unknown_mode_returns_legacy(self):
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph
        log = logging.getLogger("test")
        result = await _select_agent_graph_for_query(
            {"chatMode": "custom"}, log, MagicMock()
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_auto_mode_delegates(self):
        from app.api.routes.agent import _select_agent_graph_for_query
        log = logging.getLogger("test")
        llm = MagicMock()
        with patch("app.api.routes.agent._auto_select_graph", new_callable=AsyncMock) as mock_auto:
            mock_auto.return_value = MagicMock()
            result = await _select_agent_graph_for_query(
                {"chatMode": "auto"}, log, llm
            )
            mock_auto.assert_called_once()


class TestAutoSelectGraph:
    @pytest.mark.asyncio
    async def test_empty_query_returns_modern(self):
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        log = logging.getLogger("test")
        result = await _auto_select_graph({"query": ""}, log, MagicMock())
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_llm_returns_quick(self):
        from app.api.routes.agent import _auto_select_graph, agent_graph
        log = logging.getLogger("test")
        llm = MagicMock()
        mock_decision = MagicMock()
        mock_decision.route = "quick"
        mock_decision.reasoning = "simple"
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=mock_decision)
        llm.with_structured_output.return_value = structured
        result = await _auto_select_graph(
            {"query": "what time is it"}, log, llm
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_llm_returns_deep(self):
        from app.api.routes.agent import _auto_select_graph, deep_agent_graph
        log = logging.getLogger("test")
        llm = MagicMock()
        mock_decision = MagicMock()
        mock_decision.route = "deep"
        mock_decision.reasoning = "complex"
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=mock_decision)
        llm.with_structured_output.return_value = structured
        result = await _auto_select_graph(
            {"query": "analyze all jira tickets and create summary"}, log, llm
        )
        assert result is deep_agent_graph

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self):
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        log = logging.getLogger("test")
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("fail"))
        llm.with_structured_output.return_value = structured
        result = await _auto_select_graph(
            {"query": "test"}, log, llm
        )
        assert result is modern_agent_graph


class TestBuildRoutingContext:
    def test_returns_string(self):
        from app.api.routes.agent import _build_routing_context
        info = {"query": "follow up", "previousConversations": [
            {"role": "user_query", "content": "q1"},
        ]}
        ctx = _build_routing_context(info)
        assert isinstance(ctx, str)

    def test_no_conversations(self):
        from app.api.routes.agent import _build_routing_context
        info = {"query": "hello"}
        ctx = _build_routing_context(info)
        assert isinstance(ctx, str)


class TestParseModels:
    def test_valid_models(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [
            {"modelKey": "mk1", "modelName": "mn1"},
            {"modelKey": "mk2", "modelName": "mn2", "isReasoning": True},
        ]
        entries, has_reasoning = _parse_models(raw, log)
        assert len(entries) == 2
        assert has_reasoning is True

    def test_no_reasoning(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "mn1"}]
        entries, has_reasoning = _parse_models(raw, log)
        assert has_reasoning is False

    def test_empty_models(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models([], log)
        assert entries == []
        assert has_reasoning is False

    def test_none_models(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models(None, log)
        assert entries == []


class TestParseToolsets:
    def test_valid_toolsets(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"displayName": "Jira", "type": "jira", "tools": [{"fullName": "jira.search"}],
             "instanceId": "i1", "instanceName": "My Jira"},
        ]
        result = _parse_toolsets(raw)
        assert isinstance(result, dict)

    def test_empty(self):
        from app.api.routes.agent import _parse_toolsets
        assert _parse_toolsets([]) == {}

    def test_none(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(None)
        assert isinstance(result, dict)


class TestValidateRequiredFields:
    def test_all_present(self):
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"a": 1, "b": 2}, ["a", "b"])

    def test_missing_field(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"a": 1}, ["a", "b"])


class TestEnrichUserInfo:
    @pytest.mark.asyncio
    async def test_enriches(self):
        from app.api.routes.agent import _enrich_user_info
        user_info = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1", "firstName": "A", "lastName": "B"}
        result = await _enrich_user_info(user_info, user_doc)
        assert result["userEmail"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_missing_names(self):
        from app.api.routes.agent import _enrich_user_info
        result = await _enrich_user_info({"userId": "u1"}, {"email": "a@b.com", "_key": "k1"})
        assert result["userEmail"] == "a@b.com"


# ---------------------------------------------------------------------------
# get_services
# ---------------------------------------------------------------------------

class TestGetServices:
    """Tests for the get_services helper that extracts services from container."""

    @pytest.mark.asyncio
    async def test_returns_all_services(self):
        from app.api.routes.agent import get_services

        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()  # LLM is already set
        mock_graph = MagicMock()
        mock_reranker = MagicMock()
        mock_config = MagicMock()
        mock_logger = MagicMock()

        container = MagicMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)
        container.graph_provider = AsyncMock(return_value=mock_graph)
        container.reranker_service.return_value = mock_reranker
        container.config_service.return_value = mock_config
        container.logger.return_value = mock_logger

        request = MagicMock()
        request.app.container = container

        result = await get_services(request)

        assert result["retrieval_service"] is mock_retrieval
        assert result["graph_provider"] is mock_graph
        assert result["reranker_service"] is mock_reranker
        assert result["config_service"] is mock_config
        assert result["logger"] is mock_logger
        assert result["llm"] is mock_retrieval.llm

    @pytest.mark.asyncio
    async def test_llm_none_falls_back_to_get_llm_instance(self):
        from app.api.routes.agent import get_services

        mock_llm = MagicMock()
        mock_retrieval = MagicMock()
        mock_retrieval.llm = None
        mock_retrieval.get_llm_instance = AsyncMock(return_value=mock_llm)

        container = MagicMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.reranker_service.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        container.logger.return_value = MagicMock()

        request = MagicMock()
        request.app.container = container

        result = await get_services(request)
        assert result["llm"] is mock_llm
        mock_retrieval.get_llm_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_none_and_fallback_none_raises(self):
        from app.api.routes.agent import get_services, LLMInitializationError

        mock_retrieval = MagicMock()
        mock_retrieval.llm = None
        mock_retrieval.get_llm_instance = AsyncMock(return_value=None)

        container = MagicMock()
        container.retrieval_service = AsyncMock(return_value=mock_retrieval)
        container.graph_provider = AsyncMock(return_value=MagicMock())
        container.reranker_service.return_value = MagicMock()
        container.config_service.return_value = MagicMock()
        container.logger.return_value = MagicMock()

        request = MagicMock()
        request.app.container = container

        with pytest.raises(LLMInitializationError):
            await get_services(request)


# ---------------------------------------------------------------------------
# _get_user_document
# ---------------------------------------------------------------------------

class TestGetUserDocument:
    """Tests for user document fetching with validation."""

    @pytest.mark.asyncio
    async def test_valid_user(self):
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "email": "test@example.com",
            "_key": "k1",
            "fullName": "Test User",
        })
        log = logging.getLogger("test")

        result = await _get_user_document("user-1", graph_provider, log)
        assert result["email"] == "test@example.com"
        graph_provider.get_user_by_user_id.assert_awaited_once_with("user-1")

    @pytest.mark.asyncio
    async def test_user_not_found_none(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 404
        assert "User not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_user_not_dict(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value="not a dict")
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_user_missing_email(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "email": "",
            "_key": "k1",
        })
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 400
        assert "email" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_user_whitespace_email(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "email": "   ",
            "_key": "k1",
        })
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_user_no_email_field(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value={
            "_key": "k1",
        })
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_graph_provider_exception(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=RuntimeError("db connection failed")
        )
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 500
        assert "Failed to retrieve" in exc.value.detail

    @pytest.mark.asyncio
    async def test_http_exception_reraises(self):
        """HTTPException from inside should be re-raised, not wrapped."""
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Forbidden")
        )
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# _get_org_info
# ---------------------------------------------------------------------------

class TestGetOrgInfo:
    """Tests for organization lookup and validation."""

    @pytest.mark.asyncio
    async def test_enterprise_org(self):
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "enterprise",
            "name": "Acme Corp",
        })
        log = logging.getLogger("test")

        result = await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert result["orgId"] == "org-1"
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_individual_org(self):
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "individual",
        })
        log = logging.getLogger("test")

        result = await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert result["accountType"] == "individual"

    @pytest.mark.asyncio
    async def test_org_not_found_none(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 404
        assert "Organization not found" in exc.value.detail

    @pytest.mark.asyncio
    async def test_org_not_dict(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value="not a dict")
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_account_type(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "free_tier",
        })
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 400
        assert "Invalid organization account type" in exc.value.detail

    @pytest.mark.asyncio
    async def test_missing_account_type(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "name": "Acme Corp",
        })
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_account_type_case_insensitive(self):
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "Enterprise",
        })
        log = logging.getLogger("test")

        result = await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_graph_provider_exception(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            side_effect=RuntimeError("db error")
        )
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 500
        assert "Failed to retrieve organization" in exc.value.detail

    @pytest.mark.asyncio
    async def test_http_exception_reraises(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            side_effect=HTTPException(status_code=403, detail="Forbidden")
        )
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_calls_get_document_with_correct_args(self):
        from app.api.routes.agent import _get_org_info
        from app.config.constants.arangodb import CollectionNames

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "enterprise",
        })
        log = logging.getLogger("test")

        await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        graph_provider.get_document.assert_awaited_once_with("org-1", CollectionNames.ORGS.value)


# ---------------------------------------------------------------------------
# _parse_knowledge_sources (additional coverage)
# ---------------------------------------------------------------------------

class TestParseKnowledgeSources:
    def test_valid_knowledge(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [
            {"connectorId": "google_drive", "filters": {"types": ["doc"]}},
            {"connectorId": "confluence", "filters": {}},
        ]
        result = _parse_knowledge_sources(raw)
        assert "google_drive" in result
        assert "confluence" in result

    def test_empty_list(self):
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources([]) == {}

    def test_none_input(self):
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources(None) == {}

    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = ["not a dict", 42, None]
        assert _parse_knowledge_sources(raw) == {}

    def test_empty_connector_id_skipped(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "", "filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_whitespace_connector_id_skipped(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "   ", "filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_string_filters_parsed_as_json(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "jira", "filters": '{"types": ["bug"]}'}]
        result = _parse_knowledge_sources(raw)
        assert result["jira"]["filters"] == {"types": ["bug"]}

    def test_invalid_json_filters_default_to_empty(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "jira", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["jira"]["filters"] == {}


# ---------------------------------------------------------------------------
# _filter_knowledge_by_enabled_sources (additional coverage)
# ---------------------------------------------------------------------------

class TestFilterKnowledgeByEnabledSources:
    def test_no_filters_returns_all(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "google"}, {"connectorId": "jira"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert result == knowledge

    def test_empty_apps_and_kbs_returns_all(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [], "kb": []})
        assert result == knowledge

    def test_app_filter(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "jira"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "google"

    def test_kb_filter_with_record_groups(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg-1", "rg-2"]}},
            {"connectorId": "knowledgeBase_2", "filters": {"recordGroups": ["rg-3"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "knowledgeBase_1"

    def test_kb_filter_with_string_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": '{"recordGroups": ["rg-1"]}'},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 1

    def test_kb_filter_with_invalid_json_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": "not json"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 0

    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not a dict", None, {"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1

    def test_filtersParsed_fallback(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filtersParsed": {"recordGroups": ["rg-1"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg-1"]})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_routing_context (additional coverage)
# ---------------------------------------------------------------------------

class TestBuildRoutingContextExtended:
    def test_empty_previous_conversations(self):
        from app.api.routes.agent import _build_routing_context
        result = _build_routing_context({"previous_conversations": []})
        assert result == ""

    def test_user_and_bot_turns(self):
        from app.api.routes.agent import _build_routing_context
        info = {
            "previous_conversations": [
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is a thing.\nMore details here."},
            ]
        }
        result = _build_routing_context(info)
        assert "User: What is X?" in result
        assert "Assistant: X is a thing." in result
        # Only first line of bot response
        assert "More details here." not in result

    def test_truncates_to_last_6_entries(self):
        from app.api.routes.agent import _build_routing_context
        entries = [{"role": "user_query", "content": f"q{i}"} for i in range(10)]
        info = {"previous_conversations": entries}
        result = _build_routing_context(info)
        # Should only contain last 6 entries
        assert "q4" in result
        assert "q9" in result

    def test_unknown_role_skipped(self):
        from app.api.routes.agent import _build_routing_context
        info = {
            "previous_conversations": [
                {"role": "system", "content": "System msg"},
            ]
        }
        result = _build_routing_context(info)
        assert result == ""


# ---------------------------------------------------------------------------
# _parse_request_body
# ---------------------------------------------------------------------------


class TestParseRequestBody:
    def test_valid_json(self):
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_body_raises(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError, match="required"):
            _parse_request_body(b"")

    def test_none_body_raises(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError, match="required"):
            _parse_request_body(b"")

    def test_invalid_json_raises(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError, match="Invalid JSON"):
            _parse_request_body(b"not json")

    def test_complex_json(self):
        from app.api.routes.agent import _parse_request_body
        body = b'{"name": "test", "nested": {"key": [1, 2, 3]}}'
        result = _parse_request_body(body)
        assert result["nested"]["key"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _enrich_agent_models
# ---------------------------------------------------------------------------


class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enriches_matching_model(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_gpt-4o"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [{
                "modelKey": "mk1",
                "configuration": {"model": "gpt-4o, gpt-4o-mini"},
                "provider": "openai",
                "isReasoning": False,
                "isMultimodal": True,
                "isDefault": True,
                "modelFriendlyName": "GPT-4o",
            }]
        })
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert len(agent["models"]) == 1
        assert agent["models"][0]["modelKey"] == "mk1"
        assert agent["models"][0]["modelName"] == "gpt-4o"
        assert agent["models"][0]["provider"] == "openai"
        assert agent["models"][0]["isMultimodal"] is True

    @pytest.mark.asyncio
    async def test_no_matching_config_fallback(self):
        from app.api.routes.agent import _enrich_agent_models

        # "unknown_model" splits on first "_" into ("unknown", "model")
        agent = {"models": ["unknown_model"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"][0]["provider"] == "unknown"
        assert agent["models"][0]["modelKey"] == "unknown"
        assert agent["models"][0]["modelName"] == "model"

    @pytest.mark.asyncio
    async def test_no_model_name_uses_config(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1"]}  # no underscore -> no model_name
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [{
                "modelKey": "mk1",
                "configuration": {"model": "gpt-4o, gpt-4o-mini"},
                "provider": "openai",
                "modelFriendlyName": "GPT-4o",
            }]
        })
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"][0]["modelName"] == "gpt-4o"  # first from csv

    @pytest.mark.asyncio
    async def test_empty_models_returns_early(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": []}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        config_service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_models_key_returns_early(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        config_service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_config_exception_swallowed(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_mn1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("config fail"))
        log = logging.getLogger("test")

        # Should not raise
        await _enrich_agent_models(agent, config_service, log)
        # models should remain unchanged
        assert agent["models"] == ["mk1_mn1"]

    @pytest.mark.asyncio
    async def test_none_ai_models(self):
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_mn1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        # With None config, llm_configs = [], model not found -> fallback
        assert agent["models"][0]["provider"] == "unknown"


# ---------------------------------------------------------------------------
# _parse_toolsets (extended)
# ---------------------------------------------------------------------------


class TestParseToolsetsExtended:
    def test_non_dict_entries_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(["not a dict", 42])
        assert result == {}

    def test_empty_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "", "tools": []}])
        assert result == {}

    def test_whitespace_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "   ", "tools": []}])
        assert result == {}

    def test_tools_parsed(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{
            "name": "jira",
            "displayName": "Jira",
            "type": "app",
            "tools": [
                {"name": "search", "fullName": "jira.search", "description": "Search Jira"},
            ],
            "instanceId": "i1",
            "instanceName": "My Jira",
        }])
        assert "jira" in result
        assert len(result["jira"]["tools"]) == 1
        assert result["jira"]["tools"][0]["name"] == "search"
        assert result["jira"]["instanceId"] == "i1"

    def test_duplicate_toolset_merges(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([
            {"name": "jira", "tools": [{"name": "t1", "fullName": "jira.t1", "description": ""}]},
            {"name": "jira", "tools": [{"name": "t2", "fullName": "jira.t2", "description": ""}]},
        ])
        assert len(result["jira"]["tools"]) == 2

    def test_tool_without_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{
            "name": "jira",
            "tools": [
                {"name": "", "description": "no name"},
                {"name": "valid", "description": "has name"},
            ],
        }])
        assert len(result["jira"]["tools"]) == 1
        assert result["jira"]["tools"][0]["name"] == "valid"


# ---------------------------------------------------------------------------
# _validate_required_fields (extended)
# ---------------------------------------------------------------------------


class TestValidateRequiredFieldsExtended:
    def test_empty_string_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": ""}, ["name"])

    def test_whitespace_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "   "}, ["name"])

    def test_none_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])

    def test_zero_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"count": 0}, ["count"])

    def test_valid_numeric_value_passes(self):
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"count": 5}, ["count"])


# ---------------------------------------------------------------------------
# _parse_models (extended)
# ---------------------------------------------------------------------------


class TestParseModelsExtended:
    def test_string_entries(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = ["mk1_mn1", "mk2"]
        entries, has_reasoning = _parse_models(raw, log)
        assert len(entries) == 2
        assert entries[0] == "mk1_mn1"
        assert entries[1] == "mk2"

    def test_dict_without_model_key_skipped(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelName": "mn1"}]  # no modelKey
        entries, _ = _parse_models(raw, log)
        assert entries == []

    def test_dict_with_key_and_name(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "mn1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1_mn1"]

    def test_dict_with_key_only(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1"]

    def test_non_list_input(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models("not a list", log)
        assert entries == []


# ---------------------------------------------------------------------------
# _enrich_user_info (extended)
# ---------------------------------------------------------------------------


class TestEnrichUserInfoExtended:
    @pytest.mark.asyncio
    async def test_adds_all_name_fields(self):
        from app.api.routes.agent import _enrich_user_info
        user_doc = {
            "email": "a@b.com",
            "_key": "k1",
            "fullName": "Full",
            "firstName": "First",
            "lastName": "Last",
            "displayName": "Display",
        }
        result = await _enrich_user_info({"userId": "u1"}, user_doc)
        assert result["fullName"] == "Full"
        assert result["firstName"] == "First"
        assert result["lastName"] == "Last"
        assert result["displayName"] == "Display"

    @pytest.mark.asyncio
    async def test_does_not_mutate_original(self):
        from app.api.routes.agent import _enrich_user_info
        original = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1"}
        await _enrich_user_info(original, user_doc)
        assert "userEmail" not in original  # original unchanged

    @pytest.mark.asyncio
    async def test_empty_email(self):
        from app.api.routes.agent import _enrich_user_info
        result = await _enrich_user_info({"userId": "u1"}, {"email": "  ", "_key": "k1"})
        assert result["userEmail"] == ""


# ---------------------------------------------------------------------------
# _select_agent_graph_for_query (extended)
# ---------------------------------------------------------------------------


class TestSelectAgentGraphExtended:
    @pytest.mark.asyncio
    async def test_explicit_react_falls_to_legacy(self):
        """'react' is not an explicit mode, so it falls to the default (legacy agent_graph)."""
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph
        log = logging.getLogger("test")
        result = await _select_agent_graph_for_query(
            {"chatMode": "react"}, log, MagicMock()
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_quick_mode(self):
        """'quick' is not an explicit mode, so it falls to the default (legacy agent_graph)."""
        from app.api.routes.agent import _select_agent_graph_for_query, agent_graph
        log = logging.getLogger("test")
        result = await _select_agent_graph_for_query(
            {"chatMode": "quick"}, log, MagicMock()
        )
        assert result is agent_graph

    @pytest.mark.asyncio
    async def test_none_chatmode_defaults_to_auto(self):
        from app.api.routes.agent import _select_agent_graph_for_query
        log = logging.getLogger("test")
        llm = MagicMock()
        with patch("app.api.routes.agent._auto_select_graph", new_callable=AsyncMock) as mock_auto:
            mock_auto.return_value = MagicMock()
            await _select_agent_graph_for_query(
                {"chatMode": None}, log, llm
            )
            mock_auto.assert_called_once()


# ---------------------------------------------------------------------------
# _auto_select_graph (extended)
# ---------------------------------------------------------------------------


class TestAutoSelectGraphExtended:
    @pytest.mark.asyncio
    async def test_react_route(self):
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        log = logging.getLogger("test")
        llm = MagicMock()
        mock_decision = MagicMock()
        mock_decision.route = "react"
        mock_decision.reasoning = "needs tools"
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=mock_decision)
        llm.with_structured_output.return_value = structured
        result = await _auto_select_graph(
            {"query": "update the jira ticket"}, log, llm
        )
        assert result is modern_agent_graph

    @pytest.mark.asyncio
    async def test_missing_query_key(self):
        """When query key is missing entirely, defaults to empty string and returns modern_agent_graph."""
        from app.api.routes.agent import _auto_select_graph, modern_agent_graph
        log = logging.getLogger("test")
        result = await _auto_select_graph(
            {}, log, MagicMock()
        )
        assert result is modern_agent_graph


# ---------------------------------------------------------------------------
# _get_user_context (extended)
# ---------------------------------------------------------------------------


class TestGetUserContextExtended:
    def test_no_user_state(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401

    def test_send_user_info_flag(self):
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params = {"sendUserInfo": False}
        ctx = _get_user_context(request)
        assert ctx["userId"] == "u1"


# ---------------------------------------------------------------------------
# _filter_knowledge_by_enabled_sources (extended)
# ---------------------------------------------------------------------------


class TestFilterKnowledgeExtended:
    def test_kb_with_no_record_groups_not_matched(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": {}},
        ]
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {"kb": ["rg-1"]}
        )
        # KB with empty recordGroups should not match
        assert len(result) == 0

    def test_combined_app_and_kb_filter(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "jira"},
            {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg-1"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {"apps": ["google"], "kb": ["rg-1"]}
        )
        assert len(result) == 2
        connectors = [r["connectorId"] for r in result]
        assert "google" in connectors
        assert "knowledgeBase_1" in connectors


# ---------------------------------------------------------------------------
# _parse_knowledge_sources (extended)
# ---------------------------------------------------------------------------


class TestParseKnowledgeSourcesExtended:
    def test_dict_filters_kept(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": {"types": ["doc"]}}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_missing_connector_id(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_duplicate_connector_ids(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [
            {"connectorId": "c1", "filters": {"a": 1}},
            {"connectorId": "c1", "filters": {"b": 2}},
        ]
        result = _parse_knowledge_sources(raw)
        # Last one wins
        assert result["c1"]["filters"] == {"b": 2}


# ---------------------------------------------------------------------------
# _get_org_info
# ---------------------------------------------------------------------------

class TestGetOrgInfo:
    @pytest.mark.asyncio
    async def test_valid_enterprise(self):
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "enterprise"})
        result = await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert result["orgId"] == "o1"
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_valid_individual(self):
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "Individual"})
        result = await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert result["accountType"] == "individual"

    @pytest.mark.asyncio
    async def test_org_not_found(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_account_type(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "invalid"})
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_graph_provider_error(self):
        from fastapi import HTTPException
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(side_effect=RuntimeError("db error"))
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# _parse_request_body
# ---------------------------------------------------------------------------

class TestParseRequestBody:
    def test_valid_json(self):
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_empty_body_raises(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"")

    def test_invalid_json_raises(self):
        from app.api.routes.agent import _parse_request_body, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"not json")

    def test_nested_json(self):
        from app.api.routes.agent import _parse_request_body
        body = b'{"a": {"b": [1, 2, 3]}}'
        result = _parse_request_body(body)
        assert result["a"]["b"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _enrich_agent_models
# ---------------------------------------------------------------------------

class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enriches_models(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["mk1_modelA"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {"modelKey": "mk1", "provider": "openai", "isReasoning": True,
                 "isMultimodal": False, "isDefault": True,
                 "modelFriendlyName": "Model A",
                 "configuration": {"model": "gpt-4"}},
            ]
        })
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert len(agent["models"]) == 1
        assert agent["models"][0]["modelKey"] == "mk1"
        assert agent["models"][0]["provider"] == "openai"
        assert agent["models"][0]["isReasoning"] is True

    @pytest.mark.asyncio
    async def test_enriches_model_no_underscore(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["mk1"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {"modelKey": "mk1", "provider": "anthropic",
                 "configuration": {"model": "claude-3"}},
            ]
        })
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"][0]["modelName"] == "claude-3"

    @pytest.mark.asyncio
    async def test_model_not_found_in_config(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["unknown_key"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_models(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": []}
        config_service = MagicMock()
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_none_models(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {}
        config_service = MagicMock()
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_config_error_handled(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["mk1_modelA"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=Exception("config error"))
        # Should not raise
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_comma_separated_model_name(self):
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["mk1"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [
                {"modelKey": "mk1", "provider": "openai",
                 "configuration": {"model": "gpt-4, gpt-4-turbo"}},
            ]
        })
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"][0]["modelName"] == "gpt-4"


# ---------------------------------------------------------------------------
# _parse_models (extended)
# ---------------------------------------------------------------------------

class TestParseModelsExtended:
    def test_string_entries(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = ["model_key_1", "model_key_2"]
        entries, _ = _parse_models(raw, log)
        assert entries == ["model_key_1", "model_key_2"]

    def test_model_key_only_no_name(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1"]

    def test_model_key_with_name(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "gpt-4"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1_gpt-4"]

    def test_missing_model_key_skipped(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelName": "name_only"}]
        entries, _ = _parse_models(raw, log)
        assert entries == []

    def test_mixed_types(self):
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [
            {"modelKey": "mk1", "modelName": "m1"},
            "plain_string",
            {"modelKey": "mk2", "isReasoning": True},
        ]
        entries, has_reasoning = _parse_models(raw, log)
        assert len(entries) == 3
        assert has_reasoning is True


# ---------------------------------------------------------------------------
# _parse_toolsets (extended)
# ---------------------------------------------------------------------------

class TestParseToolsetsExtended:
    def test_with_tools(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "Jira",
            "displayName": "Jira",
            "type": "app",
            "tools": [
                {"name": "search", "fullName": "jira.search", "description": "Search Jira"},
            ],
        }]
        result = _parse_toolsets(raw)
        assert "jira" in result
        assert len(result["jira"]["tools"]) == 1
        assert result["jira"]["tools"][0]["name"] == "search"

    def test_tool_without_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "test",
            "tools": [{"description": "no name"}],
        }]
        result = _parse_toolsets(raw)
        assert len(result["test"]["tools"]) == 0

    def test_non_dict_toolset_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        raw = ["not_a_dict", {"name": "valid"}]
        result = _parse_toolsets(raw)
        assert "valid" in result

    def test_empty_name_skipped(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "", "tools": []}]
        result = _parse_toolsets(raw)
        assert len(result) == 0

    def test_duplicate_toolset_merges_tools(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "tools": [{"name": "t1", "fullName": "jira.t1", "description": ""}]},
            {"name": "jira", "tools": [{"name": "t2", "fullName": "jira.t2", "description": ""}]},
        ]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 2

    def test_instance_id_set(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "jira",
            "instanceId": "inst-123",
            "instanceName": "My Jira",
            "tools": [],
        }]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-123"
        assert result["jira"]["instanceName"] == "My Jira"

    def test_instance_id_updated_from_second_entry(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "tools": []},
            {"name": "jira", "instanceId": "inst-456", "instanceName": "New Jira", "tools": []},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-456"

    def test_default_display_name(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "my_toolset", "tools": []}]
        result = _parse_toolsets(raw)
        assert result["my_toolset"]["displayName"] == "My Toolset"

    def test_tool_fullname_default(self):
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "jira",
            "tools": [{"name": "search"}],
        }]
        result = _parse_toolsets(raw)
        assert result["jira"]["tools"][0]["fullName"] == "jira.search"


# ---------------------------------------------------------------------------
# _parse_knowledge_sources (extended more)
# ---------------------------------------------------------------------------

class TestParseKnowledgeSourcesFull:
    def test_json_string_filters(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": '{"key": "val"}'}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"key": "val"}

    def test_invalid_json_string_filters(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {}

    def test_non_dict_entry_skipped(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = ["not_a_dict", {"connectorId": "c1"}]
        result = _parse_knowledge_sources(raw)
        assert "c1" in result

    def test_whitespace_connector_id_skipped(self):
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "   "}]
        result = _parse_knowledge_sources(raw)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _filter_knowledge_by_enabled_sources (more branches)
# ---------------------------------------------------------------------------

class TestFilterKnowledgeFull:
    def test_no_filters_returns_all(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}, {"connectorId": "c2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert len(result) == 2

    def test_empty_apps_and_kb_returns_all(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [], "kb": []})
        assert len(result) == 1

    def test_app_filter_only(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "slack"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "google"

    def test_kb_with_string_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": '{"recordGroups": ["rg1"]}'},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1

    def test_kb_with_invalid_json_filters(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": "not json"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 0

    def test_non_dict_entry_skipped(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not_a_dict", {"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1

    def test_kb_no_matching_record_groups(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filters": {"recordGroups": ["rg1"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg_other"]})
        assert len(result) == 0

    def test_kb_with_filtersParsed(self):
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "knowledgeBase_1", "filtersParsed": {"recordGroups": ["rg1"]}},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["rg1"]})
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _build_routing_context (extended)
# ---------------------------------------------------------------------------

class TestBuildRoutingContextExtended:
    def test_with_previous_conversations(self):
        from app.api.routes.agent import _build_routing_context
        info = {
            "previous_conversations": [
                {"role": "user_query", "content": "Hello"},
                {"role": "bot_response", "content": "Hi there!\nMore text"},
            ]
        }
        ctx = _build_routing_context(info)
        assert "User: Hello" in ctx
        assert "Assistant: Hi there!" in ctx

    def test_long_conversations_trimmed(self):
        from app.api.routes.agent import _build_routing_context
        convs = [{"role": "user_query", "content": f"q{i}"} for i in range(10)]
        info = {"previous_conversations": convs}
        ctx = _build_routing_context(info)
        # Should only take last 6
        assert "q4" in ctx

    def test_unknown_role_ignored(self):
        from app.api.routes.agent import _build_routing_context
        info = {
            "previous_conversations": [
                {"role": "system", "content": "System msg"},
            ]
        }
        ctx = _build_routing_context(info)
        assert ctx == ""

    def test_content_truncated(self):
        from app.api.routes.agent import _build_routing_context
        long_content = "a" * 500
        info = {
            "previous_conversations": [
                {"role": "user_query", "content": long_content},
            ]
        }
        ctx = _build_routing_context(info)
        assert len(ctx) < 500


# ---------------------------------------------------------------------------
# _enrich_user_info (extended)
# ---------------------------------------------------------------------------

class TestEnrichUserInfoExtended:
    @pytest.mark.asyncio
    async def test_with_display_name(self):
        from app.api.routes.agent import _enrich_user_info
        user_doc = {"email": "a@b.com", "_key": "k1", "displayName": "Test User"}
        result = await _enrich_user_info({"userId": "u1"}, user_doc)
        assert result["displayName"] == "Test User"

    @pytest.mark.asyncio
    async def test_original_info_not_mutated(self):
        from app.api.routes.agent import _enrich_user_info
        original = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1"}
        result = await _enrich_user_info(original, user_doc)
        assert "userEmail" not in original
        assert "userEmail" in result

    @pytest.mark.asyncio
    async def test_whitespace_email(self):
        from app.api.routes.agent import _enrich_user_info
        user_doc = {"email": "  a@b.com  ", "_key": "k1"}
        result = await _enrich_user_info({"userId": "u1"}, user_doc)
        assert result["userEmail"] == "a@b.com"


# ---------------------------------------------------------------------------
# _validate_required_fields (extended)
# ---------------------------------------------------------------------------

class TestValidateRequiredFieldsExtended:
    def test_whitespace_only_field_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "   "}, ["name"])

    def test_none_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])

    def test_zero_value_fails(self):
        from app.api.routes.agent import _validate_required_fields, InvalidRequestError
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"count": 0}, ["count"])

    def test_empty_list_no_fields(self):
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"a": 1}, [])  # Should not raise


# ---------------------------------------------------------------------------
# _create_toolset_edges
# ---------------------------------------------------------------------------

class TestCreateToolsetEdges:
    @pytest.mark.asyncio
    async def test_empty_toolsets(self):
        from app.api.routes.agent import _create_toolset_edges
        created, failed = await _create_toolset_edges(
            "agent1", {}, {"userId": "u1"}, "uk1", AsyncMock(), logging.getLogger("test")
        )
        assert created == []
        assert failed == []

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self):
        from app.api.routes.agent import _create_toolset_edges
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)
        toolsets = {
            "jira": {
                "displayName": "Jira",
                "type": "app",
                "tools": [],
                "instanceId": None,
                "instanceName": None,
            }
        }
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, failed = await _create_toolset_edges(
                "agent1", toolsets, {"userId": "u1"}, "uk1",
                graph_provider, logging.getLogger("test")
            )
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_toolset_edges
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(side_effect=Exception("db error"))
        toolsets = {
            "jira": {
                "displayName": "Jira",
                "type": "app",
                "tools": [],
                "instanceId": None,
                "instanceName": None,
            }
        }
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, failed = await _create_toolset_edges(
                "agent1", toolsets, {"userId": "u1"}, "uk1",
                graph_provider, logging.getLogger("test")
            )
        assert len(failed) == 1


# ---------------------------------------------------------------------------
# _create_knowledge_edges
# ---------------------------------------------------------------------------

class TestCreateKnowledgeEdges:
    @pytest.mark.asyncio
    async def test_empty_sources(self):
        from app.api.routes.agent import _create_knowledge_edges
        result = await _create_knowledge_edges(
            "agent1", {}, "uk1", AsyncMock(), logging.getLogger("test")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self):
        from app.api.routes.agent import _create_knowledge_edges
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)
        sources = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges(
            "agent1", sources, "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_creation(self):
        from app.api.routes.agent import _create_knowledge_edges
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_create_edges = AsyncMock(return_value=True)
        sources = {"c1": {"connectorId": "c1", "filters": {"key": "val"}}}
        result = await _create_knowledge_edges(
            "agent1", sources, "uk1", graph_provider, logging.getLogger("test")
        )
        assert len(result) == 1
        assert result[0]["connectorId"] == "c1"


# ===========================================================================
# Route handler tests — askAI
# ===========================================================================


class TestAskAI:
    """Tests for the /agent-chat endpoint handler."""

    def _make_services(self, final_state=None):
        """Create a mock services dict suitable for get_services return."""
        mock_retrieval = MagicMock()
        mock_retrieval.llm = MagicMock()
        services = {
            "retrieval_service": mock_retrieval,
            "graph_provider": AsyncMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        return services

    def _make_request(self, services, user=None):
        request = MagicMock()
        request.state.user = user or {"userId": "u1", "orgId": "o1"}
        request.query_params = {}
        request.app.container = MagicMock()
        return request

    @pytest.mark.asyncio
    async def test_askAI_success_dict_response(self):
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        services["graph_provider"].get_user_by_user_id = AsyncMock(
            return_value={"email": "a@b.com", "_key": "k1"}
        )
        services["graph_provider"].get_document = AsyncMock(
            return_value={"accountType": "enterprise"}
        )

        query = ChatQuery(query="hello")
        request = self._make_request(services)

        final_state = {
            "completion_data": {"status": "success", "message": "hi"},
        }

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "userEmail": "a@b.com"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.get_cache_manager") as mock_cache_mgr, \
             patch("app.api.routes.agent.build_initial_state", return_value={"some": "state"}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=final_state), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "healthy"}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph

            cache = MagicMock()
            cache.get_llm_response.return_value = None
            cache.set_llm_response = MagicMock()
            mock_cache_mgr.return_value = cache

            result = await askAI(request, query)
            assert result == {"status": "success", "message": "hi"}

    @pytest.mark.asyncio
    async def test_askAI_cache_hit(self):
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        query = ChatQuery(query="cached query")
        request = self._make_request(services)

        cached = {"status": "success", "message": "cached"}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent.get_cache_manager") as mock_cache_mgr:

            cache = MagicMock()
            cache.get_llm_response.return_value = cached
            mock_cache_mgr.return_value = cache

            result = await askAI(request, query)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_askAI_error_in_final_state(self):
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        query = ChatQuery(query="bad query")
        request = self._make_request(services)

        final_state = {
            "error": {"status_code": 422, "status": "error", "message": "bad input"},
        }

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.get_cache_manager") as mock_cache_mgr, \
             patch("app.api.routes.agent.build_initial_state", return_value={}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=final_state), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "healthy"}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph
            cache = MagicMock()
            cache.get_llm_response.return_value = None
            mock_cache_mgr.return_value = cache

            result = await askAI(request, query)
            assert result.status_code == 422

    @pytest.mark.asyncio
    async def test_askAI_exception_raises_400(self):
        from fastapi import HTTPException
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        query = ChatQuery(query="fail")
        request = self._make_request(services)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", side_effect=RuntimeError("boom")):

            with pytest.raises(HTTPException) as exc:
                await askAI(request, query)
            assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_askAI_http_exception_reraises(self):
        from fastapi import HTTPException
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        query = ChatQuery(query="fail")
        request = self._make_request(services)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", side_effect=HTTPException(status_code=401, detail="Unauthorized")):

            with pytest.raises(HTTPException) as exc:
                await askAI(request, query)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_askAI_deep_graph_selection(self):
        from app.api.routes.agent import askAI, ChatQuery, deep_agent_graph

        services = self._make_services()
        query = ChatQuery(query="analyze", chatMode="deep")
        request = self._make_request(services)

        final_state = {"completion_data": {"status": "success", "message": "deep"}}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock, return_value=deep_agent_graph), \
             patch("app.api.routes.agent.get_cache_manager") as mock_cache_mgr, \
             patch("app.api.routes.agent.build_deep_agent_state", return_value={}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=final_state), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "healthy"}):

            mock_ainvoke = AsyncMock(return_value=final_state)
            cache = MagicMock()
            cache.get_llm_response.return_value = None
            mock_cache_mgr.return_value = cache

            with patch.object(deep_agent_graph, "ainvoke", mock_ainvoke):
                result = await askAI(request, query)
            assert result == {"status": "success", "message": "deep"}

    @pytest.mark.asyncio
    async def test_askAI_memory_unhealthy(self):
        from app.api.routes.agent import askAI, ChatQuery

        services = self._make_services()
        query = ChatQuery(query="hello")
        request = self._make_request(services)

        final_state = {"completion_data": {"status": "success", "message": "ok"}}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.get_cache_manager") as mock_cache_mgr, \
             patch("app.api.routes.agent.build_initial_state", return_value={}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=final_state), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "warning", "memory_info": {"total_mb": 150.0}}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph
            cache = MagicMock()
            cache.get_llm_response.return_value = None
            mock_cache_mgr.return_value = cache

            result = await askAI(request, query)
            assert result == {"status": "success", "message": "ok"}


# ===========================================================================
# Route handler tests — askAIStream
# ===========================================================================


class TestAskAIStream:
    @pytest.mark.asyncio
    async def test_returns_streaming_response(self):
        from fastapi.responses import StreamingResponse
        from app.api.routes.agent import askAIStream, ChatQuery

        services = {
            "retrieval_service": MagicMock(),
            "graph_provider": AsyncMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }

        query = ChatQuery(query="stream me")
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}):

            result = await askAIStream(request, query)
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_http_exception_reraises(self):
        from fastapi import HTTPException
        from app.api.routes.agent import askAIStream, ChatQuery

        services = {
            "retrieval_service": MagicMock(),
            "graph_provider": AsyncMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }

        query = ChatQuery(query="fail")
        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", side_effect=HTTPException(status_code=401)):

            with pytest.raises(HTTPException) as exc:
                await askAIStream(request, query)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_generic_exception_raises_400(self):
        from fastapi import HTTPException
        from app.api.routes.agent import askAIStream, ChatQuery

        services = {
            "retrieval_service": MagicMock(),
            "graph_provider": AsyncMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }

        query = ChatQuery(query="fail")
        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", side_effect=RuntimeError("unexpected")):

            with pytest.raises(HTTPException) as exc:
                await askAIStream(request, query)
            assert exc.value.status_code == 400


# ===========================================================================
# Route handler tests — stream_response
# ===========================================================================


class TestStreamResponse:
    @pytest.mark.asyncio
    async def test_yields_events(self):
        from app.api.routes.agent import stream_response

        mock_graph = AsyncMock()

        async def mock_astream(state, config, stream_mode):
            yield {"event": "token", "data": {"text": "hello"}}
            yield {"event": "done", "data": {}}

        mock_graph.astream = mock_astream

        with patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock, return_value=mock_graph), \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            chunks = []
            async for chunk in stream_response(
                {"chatMode": "quick", "query": "hi"},
                {"userId": "u1"},
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ):
                chunks.append(chunk)

            assert len(chunks) == 2
            assert "event: token" in chunks[0]
            assert "event: done" in chunks[1]

    @pytest.mark.asyncio
    async def test_yields_error_on_exception(self):
        from app.api.routes.agent import stream_response

        with patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock, side_effect=RuntimeError("graph error")):

            chunks = []
            async for chunk in stream_response(
                {"chatMode": "quick", "query": "hi"},
                {"userId": "u1"},
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ):
                chunks.append(chunk)

            assert len(chunks) == 1
            assert "event: error" in chunks[0]

    @pytest.mark.asyncio
    async def test_unexpected_chunk_format(self):
        from app.api.routes.agent import stream_response

        mock_graph = AsyncMock()

        async def mock_astream(state, config, stream_mode):
            yield "not a dict"

        mock_graph.astream = mock_astream

        with patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock, return_value=mock_graph), \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            chunks = []
            async for chunk in stream_response(
                {"chatMode": "quick", "query": "hi"},
                {"userId": "u1"},
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ):
                chunks.append(chunk)

            assert len(chunks) == 0


# ===========================================================================
# Template CRUD endpoint tests
# ===========================================================================


class TestCreateAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import create_agent_template

        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"T1","description":"Desc","systemPrompt":"SP"}')
        request.state.user = {"userId": "u1", "orgId": "o1"}

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await create_agent_template(request)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self):
        from fastapi import HTTPException
        from app.api.routes.agent import create_agent_template

        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=None)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"T1","description":"Desc","systemPrompt":"SP"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await create_agent_template(request)
            assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        from fastapi import HTTPException
        from app.api.routes.agent import create_agent_template

        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db down"))

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"T1","description":"Desc","systemPrompt":"SP"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await create_agent_template(request)
            assert exc.value.status_code == 500


class TestGetAgentTemplates:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import get_agent_templates

        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].get_all_agent_templates = AsyncMock(return_value=[{"name": "T1"}])

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agent_templates(request)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_empty_on_none(self):
        from app.api.routes.agent import get_agent_templates

        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
        }
        services["graph_provider"].get_all_agent_templates = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agent_templates(request)
            assert result.status_code == 200


class TestGetAgentTemplate:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.api.routes.agent import get_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_template = AsyncMock(return_value={"name": "T1"})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agent_template(request, "t1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.agent import get_agent_template, AgentTemplateNotFoundError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_template = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentTemplateNotFoundError):
                await get_agent_template(request, "missing")


class TestCloneAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import clone_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].clone_agent_template = AsyncMock(return_value="cloned-id")

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services):
            result = await clone_agent_template(request, "t1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_failure(self):
        from fastapi import HTTPException
        from app.api.routes.agent import clone_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].clone_agent_template = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services):
            with pytest.raises(HTTPException) as exc:
                await clone_agent_template(request, "t1")
            assert exc.value.status_code == 500


class TestDeleteAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import delete_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].delete_agent_template = AsyncMock(return_value=True)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await delete_agent_template(request, "t1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_failure(self):
        from fastapi import HTTPException
        from app.api.routes.agent import delete_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].delete_agent_template = AsyncMock(return_value=False)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await delete_agent_template(request, "t1")
            assert exc.value.status_code == 500


class TestUpdateAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import update_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].update_agent_template = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent_template(request, "t1")
            assert result.status_code == 200


class TestShareAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import share_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_template = AsyncMock(return_value={"name": "T1"})
        services["graph_provider"].share_agent_template = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await share_agent_template(request, "t1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_template_not_found(self):
        from app.api.routes.agent import share_agent_template, AgentTemplateNotFoundError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_template = AsyncMock(return_value=None)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentTemplateNotFoundError):
                await share_agent_template(request, "missing")


# ===========================================================================
# Agent CRUD endpoint tests
# ===========================================================================


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_found(self):
        from app.api.routes.agent import get_agent

        services = {"graph_provider": AsyncMock(), "config_service": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "models": []})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock):

            result = await get_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.agent import get_agent, AgentNotFoundError

        services = {"graph_provider": AsyncMock(), "config_service": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentNotFoundError):
                await get_agent(request, "missing")


class TestGetAgents:
    @pytest.mark.asyncio
    async def test_list_result(self):
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(return_value=[{"name": "A1"}, {"name": "A2"}])

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agents(request, page=1, limit=20, search=None, sort_by="updatedAtTimestamp", sort_order="desc")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_dict_result(self):
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(return_value={"agents": [{"name": "A1"}], "totalItems": 1})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agents(request, page=1, limit=20, search=None, sort_by="updatedAtTimestamp", sort_order="desc")
            assert result.status_code == 200


class TestDeleteAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": True})
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].hard_delete_agent = AsyncMock(return_value={"agents_deleted": 1, "toolsets_deleted": 0, "tools_deleted": 0, "knowledge_deleted": 0, "edges_deleted": 0})
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await delete_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.agent import delete_agent, AgentNotFoundError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentNotFoundError):
                await delete_agent(request, "missing")

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        from app.api.routes.agent import delete_agent, PermissionDeniedError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": False})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(PermissionDeniedError):
                await delete_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_hard_delete_failure_rolls_back(self):
        from fastapi import HTTPException
        from app.api.routes.agent import delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": True})
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].hard_delete_agent = AsyncMock(return_value={"agents_deleted": 0})
        services["graph_provider"].rollback_transaction = AsyncMock()

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await delete_agent(request, "a1")
            assert exc.value.status_code == 500


# ===========================================================================
# Agent Sharing/Permissions endpoint tests
# ===========================================================================


class TestShareAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import share_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_share": True})
        services["graph_provider"].share_agent = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await share_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        from app.api.routes.agent import share_agent, PermissionDeniedError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_share": False})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(PermissionDeniedError):
                await share_agent(request, "a1")


class TestUnshareAgent:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import unshare_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_share": True})
        services["graph_provider"].unshare_agent = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await unshare_agent(request, "a1")
            assert result.status_code == 200


class TestGetAgentPermissions:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import get_agent_permissions

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent_permissions = AsyncMock(return_value=[{"role": "OWNER"}])

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agent_permissions(request, "a1")
            assert result.status_code == 200


class TestUpdateAgentPermission:
    @pytest.mark.asyncio
    async def test_success(self):
        from app.api.routes.agent import update_agent_permission

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].update_agent_permission = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":["u2"],"role":"EDITOR"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent_permission(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_missing_role(self):
        from app.api.routes.agent import update_agent_permission, InvalidRequestError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"userIds":["u2"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError):
                await update_agent_permission(request, "a1")


# ===========================================================================
# Create Agent endpoint tests
# ===========================================================================


class TestCreateAgent:
    @pytest.mark.asyncio
    async def test_missing_models_raises(self):
        from app.api.routes.agent import create_agent, InvalidRequestError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"A1","models":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError, match="At least one AI model"):
                await create_agent(request)

    @pytest.mark.asyncio
    async def test_no_reasoning_model_raises(self):
        from app.api.routes.agent import create_agent, InvalidRequestError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"A1","models":[{"modelKey":"mk1","modelName":"mn1"}]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError, match="reasoning model"):
                await create_agent(request)

    @pytest.mark.asyncio
    async def test_success_basic(self):
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        body = '{"name":"A1","models":[{"modelKey":"mk1","modelName":"mn1","isReasoning":true}]}'
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await create_agent(request)
            assert result.status_code == 200


# ===========================================================================
# Update Agent endpoint tests
# ===========================================================================


class TestUpdateAgent:
    @pytest.mark.asyncio
    async def test_success_basic(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_not_found(self):
        from app.api.routes.agent import update_agent, AgentNotFoundError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentNotFoundError):
                await update_agent(request, "missing")

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        from app.api.routes.agent import update_agent, PermissionDeniedError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": False})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(PermissionDeniedError):
                await update_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_share_with_org_on(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True, "shareWithOrg": False})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"shareWithOrg":true}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200
            services["graph_provider"].batch_create_edges.assert_awaited()

    @pytest.mark.asyncio
    async def test_share_with_org_off(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True, "shareWithOrg": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].delete_edge = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"shareWithOrg":false}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200
            services["graph_provider"].delete_edge.assert_awaited()

    @pytest.mark.asyncio
    async def test_update_with_toolsets(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].get_edges_from_node = AsyncMock(return_value=[])
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        body = json.dumps({"toolsets": [{"name": "jira", "displayName": "Jira", "type": "app", "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}]}]})
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._create_toolset_edges", new_callable=AsyncMock, return_value=([], [])):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_update_with_empty_toolsets(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].get_edges_from_node = AsyncMock(return_value=[])
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"toolsets":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_update_with_knowledge(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].get_edges_from_node = AsyncMock(return_value=[])
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        body = json.dumps({"knowledge": [{"connectorId": "google_drive", "filters": {}}]})
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._create_knowledge_edges", new_callable=AsyncMock, return_value=[]):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_update_models_validation(self):
        from app.api.routes.agent import update_agent, InvalidRequestError

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"models":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError, match="At least one AI model"):
                await update_agent(request, "a1")


# ===========================================================================
# Agent Chat endpoint tests
# ===========================================================================


class TestAgentChat:
    @pytest.mark.asyncio
    async def test_chat_success(self):
        from app.api.routes.agent import chat, ChatQuery

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_user_by_user_id = AsyncMock(return_value={"email": "a@b.com", "_key": "k1"})
        services["graph_provider"].get_document = AsyncMock(return_value={"accountType": "enterprise"})
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [], "systemPrompt": "SP", "instructions": "I",
        })

        final_state = {"completion_data": {"status": "success", "message": "reply"}, "response": {}}

        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        query = ChatQuery(query="hello")

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph

            result = await chat(request, "a1", query)
            assert result == {"status": "success", "message": "reply"}

    @pytest.mark.asyncio
    async def test_chat_agent_not_found(self):
        from app.api.routes.agent import chat, ChatQuery, AgentNotFoundError

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()
        query = ChatQuery(query="hello")

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}):

            with pytest.raises(AgentNotFoundError):
                await chat(request, "missing", query)

    @pytest.mark.asyncio
    async def test_chat_with_filters(self):
        from app.api.routes.agent import chat, ChatQuery

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [], "connectors": ["c1"],
        })

        final_state = {"completion_data": {"status": "success"}, "response": {}}

        request = MagicMock()
        query = ChatQuery(query="hello", filters={"apps": ["google"], "kb": ["kb1"]})

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph

            result = await chat(request, "a1", query)
            assert result == {"status": "success"}

    @pytest.mark.asyncio
    async def test_chat_with_knowledge_sources(self):
        from app.api.routes.agent import chat, ChatQuery

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
            "knowledge": [
                {"connectorId": "google_drive", "filters": {}},
                {"connectorId": "knowledgeBase_1", "filters": '{"recordGroups":["rg1"]}'},
            ],
            "toolsets": [],
        })

        final_state = {"completion_data": {"status": "success"}, "response": {}}

        request = MagicMock()
        query = ChatQuery(query="search docs")

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph

            result = await chat(request, "a1", query)
            assert result == {"status": "success"}

    @pytest.mark.asyncio
    async def test_chat_error_in_final_state(self):
        from app.api.routes.agent import chat, ChatQuery

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [],
        })

        final_state = {"error": {"status_code": 500, "status": "error", "message": "internal error"}}

        request = MagicMock()
        query = ChatQuery(query="bad")

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as mock_select, \
             patch("app.api.routes.agent.build_initial_state", return_value={}):

            mock_graph = AsyncMock()
            mock_graph.ainvoke = AsyncMock(return_value=final_state)
            mock_select.return_value = mock_graph

            result = await chat(request, "a1", query)
            assert result.status_code == 500


# ===========================================================================
# Create agent with toolsets and knowledge
# ===========================================================================


class TestCreateAgentWithToolsetsAndKnowledge:
    @pytest.mark.asyncio
    async def test_with_toolsets_and_knowledge(self):
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        body = json.dumps({
            "name": "Agent With Tools",
            "models": [{"modelKey": "mk1", "modelName": "mn1", "isReasoning": True}],
            "toolsets": [{"name": "jira", "displayName": "Jira", "type": "app", "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}]}],
            "knowledge": [{"connectorId": "google_drive", "filters": {}}],
            "shareWithOrg": True,
        })
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):

            result = await create_agent(request)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_error(self):
        from fastapi import HTTPException
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("db error"))
        services["graph_provider"].rollback_transaction = AsyncMock()

        request = MagicMock()
        body = json.dumps({
            "name": "Agent",
            "models": [{"modelKey": "mk1", "modelName": "mn1", "isReasoning": True}],
        })
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await create_agent(request)
            assert exc.value.status_code == 500
            services["graph_provider"].rollback_transaction.assert_awaited()


# ===========================================================================
# _create_toolset_edges with successful tool creation
# ===========================================================================


class TestCreateToolsetEdgesSuccess:
    @pytest.mark.asyncio
    async def test_full_toolset_creation(self):
        from app.api.routes.agent import _create_toolset_edges

        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
        graph_provider.batch_create_edges = AsyncMock(return_value=True)

        toolsets = {
            "jira": {
                "displayName": "Jira",
                "type": "app",
                "tools": [
                    {"name": "search", "fullName": "jira.search", "description": "Search issues"},
                    {"name": "create", "fullName": "jira.create", "description": "Create issue"},
                ],
                "instanceId": "inst-1",
                "instanceName": "My Jira",
            }
        }

        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, failed = await _create_toolset_edges(
                "agent-1", toolsets, {"userId": "u1"}, "uk1",
                graph_provider, logging.getLogger("test")
            )

        assert len(created) == 1
        assert len(failed) == 0
        assert len(created[0]["tools"]) == 2


# ===========================================================================
# chat_stream endpoint tests
# ===========================================================================


class TestChatStream:
    """Tests for the /{agent_id}/chat/stream endpoint."""

    @pytest.mark.asyncio
    async def test_chat_stream_agent_not_found(self):
        from fastapi import HTTPException
        from app.api.routes.agent import chat_stream, AgentNotFoundError

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value=None)
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}):

            with pytest.raises(AgentNotFoundError):
                await chat_stream(request, "missing")

    @pytest.mark.asyncio
    async def test_chat_stream_success(self):
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
            "name": "A1", "knowledge": [], "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), None)):

            result = await chat_stream(request, "a1")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_chat_stream_with_toolsets(self):
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
            "knowledge": [
                {"connectorId": "google_drive", "filters": {}},
            ],
            "toolsets": [
                {"name": "jira", "instanceId": "inst-1", "displayName": "Jira",
                 "tools": [{"fullName": "jira.search", "name": "search"}]},
            ],
            "models": [{"modelKey": "mk1", "modelName": "mn1"}],
        })

        async def mock_config(path, *args, **kwargs):
            if "toolsets" in str(path):
                return {"isAuthenticated": True}
            return {"llm": []}

        services["config_service"].get_config = mock_config

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello","tools":["jira.search"]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), None)), \
             patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/services/toolsets/inst-1/u1"):

            result = await chat_stream(request, "a1")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_chat_stream_missing_toolset_config(self):
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
            "toolsets": [
                {"name": "jira", "instanceId": "inst-1", "displayName": "Jira",
                 "tools": [{"fullName": "jira.search"}]},
            ],
            "models": ["mk1_mn1"],
        })

        async def mock_config(path, *args, **kwargs):
            if "toolsets" in str(path):
                return None  # Not configured
            return {"llm": []}

        services["config_service"].get_config = mock_config

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), None)), \
             patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/services/toolsets/inst-1/u1"):

            result = await chat_stream(request, "a1")
            # Should return StreamingResponse with an error event for missing config
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_chat_stream_with_explicit_filters(self):
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
            "knowledge": [{"connectorId": "google_drive", "filters": {}}],
            "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        body = json.dumps({"query": "hello", "filters": {"apps": ["google_drive"], "kb": ["kb1"]}})
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(MagicMock(), None)):

            result = await chat_stream(request, "a1")
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_chat_stream_llm_init_error(self):
        from app.api.routes.agent import chat_stream, LLMInitializationError

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [], "models": ["mk1_mn1"],
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=(None, None)):

            with pytest.raises(LLMInitializationError):
                await chat_stream(request, "a1")


# ===========================================================================
# Update agent — toolsets deletion with existing edges
# ===========================================================================


class TestUpdateAgentToolsetDeletion:
    @pytest.mark.asyncio
    async def test_deletes_existing_toolsets_before_creating_new(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        # Simulate existing toolset edges
        services["graph_provider"].get_edges_from_node = AsyncMock(side_effect=[
            # First call: agent -> toolset edges
            [{"_to": "AgentToolsets/ts-1"}],
            # Second call: toolset -> tool edges
            [{"_to": "AgentTools/tool-1"}],
        ])
        services["graph_provider"].delete_all_edges_for_node = AsyncMock(return_value=1)
        services["graph_provider"].delete_nodes = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        body = json.dumps({"toolsets": []})  # Empty toolsets = delete all
        request.body = AsyncMock(return_value=body.encode())

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_toolset_deletion_transaction_failure_rolls_back(self):
        from fastapi import HTTPException
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].get_edges_from_node = AsyncMock(side_effect=RuntimeError("db error"))
        services["graph_provider"].rollback_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"toolsets":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await update_agent(request, "a1")
            assert exc.value.status_code == 500
            services["graph_provider"].rollback_transaction.assert_awaited()

    @pytest.mark.asyncio
    async def test_knowledge_deletion_with_existing_edges(self):
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].get_edges_from_node = AsyncMock(return_value=[
            {"_to": "AgentKnowledge/k-1"},
        ])
        services["graph_provider"].delete_all_edges_for_node = AsyncMock(return_value=1)
        services["graph_provider"].delete_nodes = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"knowledge":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200


# ===========================================================================
# Additional coverage - error paths, edge failures, rollbacks
# ===========================================================================

class TestToolsetEdgeCreationFailures:
    @pytest.mark.asyncio
    async def test_agent_toolset_edges_exception(self):
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=Exception("edge fail"))
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, _ = await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_tool_nodes_upsert_none(self):
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=[True, None])
        gp.batch_create_edges = AsyncMock(return_value=True)
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, _ = await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_tool_nodes_upsert_exception(self):
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=[True, Exception("fail")])
        gp.batch_create_edges = AsyncMock(return_value=True)
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, _ = await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))
        assert len(created) == 1

    @pytest.mark.asyncio
    async def test_toolset_tool_edges_exception(self):
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=[True, Exception("fail")])
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            created, _ = await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))
        assert len(created) == 1

class TestKnowledgeEdgeFailures2:
    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self):
        from app.api.routes.agent import _create_knowledge_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("fail"))
        result = await _create_knowledge_edges("a1", {"c1": {"connectorId": "c1", "filters": {}}}, "uk1", gp, logging.getLogger("test"))
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_create_edges_exception(self):
        from app.api.routes.agent import _create_knowledge_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=Exception("fail"))
        result = await _create_knowledge_edges("a1", {"c1": {"connectorId": "c1", "filters": {}}}, "uk1", gp, logging.getLogger("test"))
        assert len(result) == 1

class TestAllErrorPaths:
    @pytest.mark.asyncio
    async def test_json_response_cache(self):
        from fastapi.responses import JSONResponse as JR
        from app.api.routes.agent import askAI, ChatQuery
        services = {"retrieval_service": MagicMock(llm=MagicMock()), "graph_provider": AsyncMock(), "reranker_service": MagicMock(), "config_service": AsyncMock(), "logger": MagicMock(), "llm": MagicMock()}
        jr = JR(content={"m": "c"})
        fs = {"completion_data": jr}
        req = MagicMock(); req.state.user = {"userId": "u1", "orgId": "o1"}; req.query_params = {}
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as ms, \
             patch("app.api.routes.agent.get_cache_manager") as mc, \
             patch("app.api.routes.agent.build_initial_state", return_value={}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=fs), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "healthy"}):
            mg = AsyncMock(); mg.ainvoke = AsyncMock(return_value=fs); ms.return_value = mg
            c = MagicMock(); c.get_llm_response.return_value = None; mc.return_value = c
            r = await askAI(req, ChatQuery(query="t"))
            assert isinstance(r, JR)
            c.set_llm_response.assert_called_once()

    @pytest.mark.asyncio
    async def test_perf_tracker(self):
        from app.api.routes.agent import askAI, ChatQuery
        services = {"retrieval_service": MagicMock(llm=MagicMock()), "graph_provider": AsyncMock(), "reranker_service": MagicMock(), "config_service": AsyncMock(), "logger": MagicMock(), "llm": MagicMock()}
        fs = {"completion_data": {"s": "ok"}, "_performance_tracker": True, "performance_summary": {"ms": 1}}
        req = MagicMock(); req.state.user = {"userId": "u1", "orgId": "o1"}; req.query_params = {}
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent._select_agent_graph_for_query", new_callable=AsyncMock) as ms, \
             patch("app.api.routes.agent.get_cache_manager") as mc, \
             patch("app.api.routes.agent.build_initial_state", return_value={}), \
             patch("app.api.routes.agent.auto_optimize_state", return_value=fs), \
             patch("app.api.routes.agent.check_memory_health", return_value={"status": "healthy"}):
            mg = AsyncMock(); mg.ainvoke = AsyncMock(return_value=fs); ms.return_value = mg
            c = MagicMock(); c.get_llm_response.return_value = None; mc.return_value = c
            r = await askAI(req, ChatQuery(query="t"))
            assert r["_performance"] == {"ms": 1}

    @pytest.mark.asyncio
    async def test_template_edge_fail(self):
        from fastapi import HTTPException
        from app.api.routes.agent import create_agent_template
        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=None)
        req = MagicMock(); req.body = AsyncMock(return_value=b'{"name":"T","description":"D","systemPrompt":"SP"}')
        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):
            with pytest.raises(HTTPException) as exc:
                await create_agent_template(req)
            assert exc.value.status_code == 500

# =============================================================================
# Merged from test_agent_full_coverage.py
# =============================================================================

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


class TestFilterKnowledgeByEnabledSourcesFullCoverage:
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


class TestEnrichAgentModelsFullCoverage:
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


class TestParseRequestBodyFullCoverage:
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


class TestCreateToolsetEdgesFullCoverage:
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


class TestCreateKnowledgeEdgesFullCoverage:
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


class TestBuildRoutingContextEdgeCases:
    def test_with_bot_response(self):
        from app.api.routes.agent import _build_routing_context
        info = {
            "query": "follow up",
            "previous_conversations": [
                {"role": "user_query", "content": "What is X?"},
                {"role": "bot_response", "content": "X is...\nMore details here"},
            ],
        }
        ctx = _build_routing_context(info)
        assert "User:" in ctx
        assert "Assistant:" in ctx

    def test_long_conversation_trimmed(self):
        from app.api.routes.agent import _build_routing_context
        convos = [{"role": "user_query", "content": f"msg{i}"} for i in range(20)]
        info = {"query": "test", "previous_conversations": convos}
        ctx = _build_routing_context(info)
        assert isinstance(ctx, str)


class TestStreamResponseFullCoverage:
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
                    {"chatMode": "quick"}, {"userId": "u1"}, mock_llm, log, rs, gp, rr, cs
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
                {"chatMode": "quick"}, {"userId": "u1"}, mock_llm, log,
                MagicMock(), AsyncMock(), MagicMock(), MagicMock()
            ):
                chunks.append(chunk)
            assert any("error" in c for c in chunks)
