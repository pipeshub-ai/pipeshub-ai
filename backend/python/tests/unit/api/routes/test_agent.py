"""Tests for app.api.routes.agent helper functions and models."""
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


async def _drain(response) -> str:
    """Consume an SSE StreamingResponse body.

    `chat_stream` returns as soon as its authorization checks pass and does the
    config fan-out inside the stream, so anything it calls below that point only
    happens once the body is iterated.
    """
    return "".join([chunk async for chunk in response.body_iterator])

def _stub_agent_loop():
    """Patch out the agent loop so draining a `chat_stream` response exercises
    only the setup phase (config/credential fan-out) and not a whole agent run."""
    async def _empty(*_a, **_kw):
        return
        yield  # pragma: no cover - marks this an async generator

    return patch("app.api.routes.agent.run_agent_loop_stream", new=_empty)

class TestChatQueryModel:
    def test_defaults(self) -> None:
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(query="test")
        assert q.query == "test"
        assert q.limit == 50
        assert q.previousConversations == []
        assert q.quickMode is False
        assert q.chatMode == "auto"
        assert q.attachments == []

    def test_all_fields(self) -> None:
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(
            query="test", limit=10, quickMode=True, chatMode="deep",
            modelKey="mk", modelName="mn", timezone="UTC",
            currentTime="2025-01-01T00:00:00Z", conversationId="c1",
        )
        assert q.chatMode == "deep"
        assert q.conversationId == "c1"

    def test_missing_query_fails(self) -> None:
        from app.api.routes.agent import ChatQuery
        with pytest.raises(ValidationError):
            ChatQuery()

    def test_caller_fields_optional(self) -> None:
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(query="hi", callerDisplayName="A", callerEmail="a@b.co")
        assert q.callerDisplayName == "A"
        assert q.callerEmail == "a@b.co"

    def test_attachments_default_empty(self) -> None:
        from app.api.routes.agent import ChatQuery
        q = ChatQuery(query="q")
        assert q.attachments == []

    def test_attachments_round_trip(self) -> None:
        from app.api.routes.agent import ChatQuery
        att = [{"virtualRecordId": "vr-1", "mimeType": "application/pdf"}]
        q = ChatQuery(query="q", attachments=att)
        assert q.attachments == att


class TestMergeEndUserServiceAccountUserInfo:
    def _creator_like(self) -> dict:
        return {
            "userId": "creator-uid",
            "orgId": "org-1",
            "userEmail": "creator@example.com",
            "email": "creator@example.com",
            "fullName": "Creator Person",
            "firstName": "Creator",
        }

    def test_both_overlay(self) -> None:
        from app.api.routes.agent import _merge_end_user_into_service_account_user_info

        out = _merge_end_user_into_service_account_user_info(
            self._creator_like(), "Slack User", "slack@example.com"
        )
        assert out["userId"] == "creator-uid"
        assert out["orgId"] == "org-1"
        assert out["userEmail"] == "slack@example.com"
        assert out["email"] == "slack@example.com"
        assert out["fullName"] == "Slack User"
        assert out["displayName"] == "Slack User"
        assert "firstName" not in out

    def test_name_only(self) -> None:
        from app.api.routes.agent import _merge_end_user_into_service_account_user_info

        out = _merge_end_user_into_service_account_user_info(
            self._creator_like(), "Only Name", None
        )
        assert out["userEmail"] == "creator@example.com"
        assert out["fullName"] == "Only Name"

    def test_email_only(self) -> None:
        from app.api.routes.agent import _merge_end_user_into_service_account_user_info

        out = _merge_end_user_into_service_account_user_info(
            self._creator_like(), None, "only@mail.com"
        )
        assert out["userEmail"] == "only@mail.com"
        assert out["fullName"] == "Creator Person"

    def test_neither_overlay(self) -> None:
        from app.api.routes.agent import _merge_end_user_into_service_account_user_info

        base = self._creator_like()
        out = _merge_end_user_into_service_account_user_info(base, None, None)
        assert out == base


class TestRouteDecision:
    def test_valid(self) -> None:
        from app.api.routes.agent import RouteDecision
        rd = RouteDecision(reasoning="Simple query", route="quick")
        assert rd.route == "quick"

    def test_invalid_route(self) -> None:
        from app.api.routes.agent import RouteDecision
        with pytest.raises(ValidationError):
            RouteDecision(reasoning="x", route="invalid")

    def test_all_routes(self) -> None:
        from app.api.routes.agent import RouteDecision
        for r in ["quick", "react", "deep"]:
            rd = RouteDecision(reasoning="x", route=r)
            assert rd.route == r


class TestExceptions:
    def test_agent_error(self) -> None:
        from app.api.routes.agent import AgentError
        e = AgentError("fail", 500)
        assert e.status_code == 500
        assert e.detail == "fail"

    def test_agent_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError
        e = AgentNotFoundError("a1")
        assert e.status_code == 404

    def test_template_not_found(self) -> None:
        from app.api.routes.agent import AgentTemplateNotFoundError
        e = AgentTemplateNotFoundError("t1")
        assert e.status_code == 404
        assert "t1" in e.detail

    def test_permission_denied(self) -> None:
        from app.api.routes.agent import PermissionDeniedError
        e = PermissionDeniedError("delete agent")
        assert e.status_code == 403
        assert "delete agent" in e.detail

    def test_invalid_request(self) -> None:
        from app.api.routes.agent import InvalidRequestError
        e = InvalidRequestError("missing field")
        assert e.status_code == 400

    def test_llm_init_error(self) -> None:
        from app.api.routes.agent import LLMInitializationError
        e = LLMInitializationError()
        assert e.status_code == 500


class TestGetUserContext:
    def test_valid_user(self) -> None:
        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1", "orgId": "o1"}
        request.query_params = {"sendUserInfo": True}
        ctx = _get_user_context(request)
        assert ctx["userId"] == "u1"
        assert ctx["orgId"] == "o1"

    def test_missing_user_id(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"orgId": "o1"}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401

    def test_missing_org_id(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {"userId": "u1"}
        with pytest.raises(HTTPException):
            _get_user_context(request)


class TestBuildPriorRoutingMessages:
    """Covers _build_prior_routing_messages (replaces legacy _build_routing_context)."""

    @pytest.mark.asyncio
    async def test_empty_previous(self) -> None:
        from app.api.routes.agent import _build_prior_routing_messages
        assert await _build_prior_routing_messages({}) == []

    @pytest.mark.asyncio
    async def test_user_query_string_content_when_no_attachments(self) -> None:
        from langchain_core.messages import HumanMessage

        from app.api.routes.agent import _build_prior_routing_messages
        msgs = await _build_prior_routing_messages({
            "previous_conversations": [{"role": "user_query", "content": "Hello"}],
        })
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_bot_response_first_line_only(self) -> None:
        from langchain_core.messages import AIMessage

        from app.api.routes.agent import _build_prior_routing_messages
        msgs = await _build_prior_routing_messages({
            "previous_conversations": [
                {"role": "bot_response", "content": "Line one\nLine two"},
            ],
        })
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].content == "Line one"

    @pytest.mark.asyncio
    async def test_user_and_bot_turns_in_order(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage

        from app.api.routes.agent import _build_prior_routing_messages
        msgs = await _build_prior_routing_messages({
            "previous_conversations": [
                {"role": "user_query", "content": "Q?"},
                {"role": "bot_response", "content": "A1\nmore"},
            ],
        })
        assert isinstance(msgs[0], HumanMessage)
        assert msgs[0].content == "Q?"
        assert isinstance(msgs[1], AIMessage)
        assert msgs[1].content == "A1"

    @pytest.mark.asyncio
    async def test_only_last_six_messages(self) -> None:
        from app.api.routes.agent import _build_prior_routing_messages
        convs = [{"role": "user_query", "content": f"q{i}"} for i in range(10)]
        msgs = await _build_prior_routing_messages({
            "previous_conversations": convs,
        })
        assert len(msgs) == 6
        assert "q9" in str(msgs[-1].content)
        assert "q3" not in str(msgs[0].content)

    @pytest.mark.asyncio
    async def test_user_content_truncated_to_200(self) -> None:
        from app.api.routes.agent import _build_prior_routing_messages
        long = "z" * 250
        msgs = await _build_prior_routing_messages({
            "previous_conversations": [{"role": "user_query", "content": long}],
        })
        assert msgs[0].content == "z" * 200

    @pytest.mark.asyncio
    async def test_unknown_role_skipped(self) -> None:
        from app.api.routes.agent import _build_prior_routing_messages
        msgs = await _build_prior_routing_messages({
            "previous_conversations": [{"role": "system", "content": "nope"}],
        })
        assert msgs == []

    @pytest.mark.asyncio
    async def test_pdf_attachment_merges_blocks(self) -> None:
        from langchain_core.messages import HumanMessage

        from app.api.routes.agent import _build_prior_routing_messages
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(
            return_value={
                "block_containers": {
                    "blocks": [{"type": "text", "data": " body "}],
                },
            },
        )
        msgs = await _build_prior_routing_messages(
            {
                "previous_conversations": [{
                    "role": "user_query",
                    "content": "read this",
                    "attachments": [{
                        "mimeType": "application/pdf",
                        "virtualRecordId": "vr-pdf",
                    }],
                }],
            },
            blob_store=blob,
            org_id="org-x",
            is_multimodal_llm=False,
        )
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[0].content, list)
        blob.get_record_from_storage.assert_awaited_once_with("vr-pdf", "org-x")

    @pytest.mark.asyncio
    async def test_skips_pdf_row_when_vrid_missing(self) -> None:
        from app.api.routes.agent import _build_prior_routing_messages
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock()
        msgs = await _build_prior_routing_messages(
            {
                "previous_conversations": [{
                    "role": "user_query",
                    "content": "x",
                    "attachments": [{"mimeType": "application/pdf", "virtualRecordId": ""}],
                }],
            },
            blob_store=blob,
            org_id="o",
            is_multimodal_llm=True,
        )
        assert msgs[0].content == "x"
        blob.get_record_from_storage.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_attachment_appends_when_multimodal(self) -> None:
        from langchain_core.messages import HumanMessage

        from app.api.routes.agent import _build_prior_routing_messages
        data_uri = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(
            return_value={
                "block_containers": {
                    "blocks": [{"type": "image", "data": {"uri": data_uri}}],
                },
            },
        )
        msgs = await _build_prior_routing_messages(
            {
                "previous_conversations": [{
                    "role": "user_query",
                    "content": "pic",
                    "attachments": [{
                        "mimeType": "image/png",
                        "virtualRecordId": "vr-img",
                    }],
                }],
            },
            blob_store=blob,
            org_id="org-i",
            is_multimodal_llm=True,
        )
        assert isinstance(msgs[0], HumanMessage)
        parts = msgs[0].content
        assert isinstance(parts, list)
        assert any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for p in parts
        )


class TestParseModels:
    def test_valid_models(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [
            {"modelKey": "mk1", "modelName": "mn1"},
            {"modelKey": "mk2", "modelName": "mn2", "isReasoning": True},
        ]
        entries, has_reasoning = _parse_models(raw, log)
        assert len(entries) == 2
        assert has_reasoning is True

    def test_no_reasoning(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "mn1"}]
        entries, has_reasoning = _parse_models(raw, log)
        assert has_reasoning is False

    def test_empty_models(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models([], log)
        assert entries == []
        assert has_reasoning is False

    def test_none_models(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models(None, log)
        assert entries == []


class TestParseToolsets:
    def test_valid_toolsets(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"displayName": "Jira", "type": "jira", "tools": [{"fullName": "jira.search"}],
             "instanceId": "i1", "instanceName": "My Jira"},
        ]
        result = _parse_toolsets(raw)
        assert isinstance(result, dict)

    def test_empty(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        assert _parse_toolsets([]) == {}

    def test_none(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(None)
        assert isinstance(result, dict)


class TestValidateRequiredFields:
    def test_all_present(self) -> None:
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"a": 1, "b": 2}, ["a", "b"])

    def test_missing_field(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"a": 1}, ["a", "b"])


class TestEnrichUserInfo:
    @pytest.mark.asyncio
    async def test_enriches(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        user_info = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1", "firstName": "A", "lastName": "B"}
        result = await _enrich_user_info(user_info, user_doc)
        assert result["userEmail"] == "a@b.com"

    @pytest.mark.asyncio
    async def test_missing_names(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        result = await _enrich_user_info({"userId": "u1"}, {"email": "a@b.com", "_key": "k1"})
        assert result["userEmail"] == "a@b.com"


# ---------------------------------------------------------------------------
# get_services
# ---------------------------------------------------------------------------

class TestGetServices:
    """Tests for the get_services helper that extracts services from container."""

    @pytest.mark.asyncio
    async def test_returns_all_services(self) -> None:
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
    async def test_llm_none_falls_back_to_get_llm_instance(self) -> None:
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
    async def test_llm_none_and_fallback_none_raises(self) -> None:
        from app.api.routes.agent import LLMInitializationError, get_services

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
    async def test_valid_user(self) -> None:
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
    async def test_user_not_found_none(self) -> None:
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
    async def test_user_not_dict(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_user_document

        graph_provider = AsyncMock()
        graph_provider.get_user_by_user_id = AsyncMock(return_value="not a dict")
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_user_document("user-1", graph_provider, log)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_user_missing_email(self) -> None:
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
    async def test_user_whitespace_email(self) -> None:
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
    async def test_user_no_email_field(self) -> None:
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
    async def test_graph_provider_exception(self) -> None:
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
    async def test_http_exception_reraises(self) -> None:
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
    async def test_enterprise_org(self) -> None:
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
    async def test_individual_org(self) -> None:
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "individual",
        })
        log = logging.getLogger("test")

        result = await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert result["accountType"] == "individual"

    @pytest.mark.asyncio
    async def test_org_not_found_none(self) -> None:
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
    async def test_org_not_dict(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value="not a dict")
        log = logging.getLogger("test")

        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_account_type(self) -> None:
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
    async def test_missing_account_type(self) -> None:
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
    async def test_account_type_case_insensitive(self) -> None:
        from app.api.routes.agent import _get_org_info

        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={
            "accountType": "Enterprise",
        })
        log = logging.getLogger("test")

        result = await _get_org_info({"orgId": "org-1"}, graph_provider, log)
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_graph_provider_exception(self) -> None:
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
    async def test_http_exception_reraises(self) -> None:
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
    async def test_calls_get_document_with_correct_args(self) -> None:
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
    def test_valid_knowledge(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [
            {"connectorId": "google_drive", "filters": {"types": ["doc"]}},
            {"connectorId": "confluence", "filters": {}},
        ]
        result = _parse_knowledge_sources(raw)
        assert "google_drive" in result
        assert "confluence" in result

    def test_empty_list(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources([]) == {}

    def test_none_input(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        assert _parse_knowledge_sources(None) == {}

    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = ["not a dict", 42, None]
        assert _parse_knowledge_sources(raw) == {}

    def test_empty_connector_id_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "", "filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_whitespace_connector_id_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "   ", "filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_string_filters_parsed_as_json(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "jira", "filters": '{"types": ["bug"]}'}]
        result = _parse_knowledge_sources(raw)
        assert result["jira"]["filters"] == {"types": ["bug"]}

    def test_invalid_json_filters_default_to_empty(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "jira", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["jira"]["filters"] == {}


# ---------------------------------------------------------------------------
# _filter_knowledge_by_enabled_sources (additional coverage)
# ---------------------------------------------------------------------------

class TestFilterKnowledgeByEnabledSources:
    def test_no_filters_returns_all(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "google"}, {"connectorId": "jira"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert result == knowledge

    def test_empty_apps_and_kbs_returns_nothing(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [], "kb": []})
        assert result == []

    def test_app_filter(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "jira"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "google"

    def test_kb_filter_with_record_groups(self) -> None:
        """KB entries are UUID connector-ids now, but matched via filters["kb"] — never filters["apps"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid_1 = "550e8400-e29b-41d4-a716-446655440001"
        kb_uuid_2 = "550e8400-e29b-41d4-a716-446655440002"
        knowledge = [
            {"connectorId": kb_uuid_1, "type": "KB"},
            {"connectorId": kb_uuid_2, "type": "KB"},
        ]
        # Filter by including only one KB UUID in the kb list
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid_1]})
        assert len(result) == 1
        assert result[0]["connectorId"] == kb_uuid_1

    def test_kb_filter_with_string_filters(self) -> None:
        """KB entries no longer use string filters - they're regular connectors."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440003"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_filter_with_invalid_json_filters(self) -> None:
        """KB entries are regular connectors - no special invalid filter handling."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440004"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        # KB not in the kb list, so filtered out
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["some-other-kb"]})
        assert len(result) == 0

    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not a dict", None, {"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1

    def test_filtersParsed_fallback(self) -> None:
        """KB entries are regular connectors with UUID connector ids, matched via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440005"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_entry_not_matched_by_apps_filter(self) -> None:
        """Regression: a KB entry's id in filters["apps"] must NOT match it —
        KB entries are only ever enabled via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440006"
        knowledge = [{"connectorId": kb_uuid, "type": "KB"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [kb_uuid]})
        assert len(result) == 0

    def test_mixed_kb_and_app_both_kept(self) -> None:
        """Configuring an app connector must not drop KB entries — each
        entry is checked against the enabled-set matching its own type."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440007"
        knowledge = [
            {"connectorId": "confluence-app", "type": "confluence"},
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {"apps": ["confluence-app"], "kb": [kb_uuid]},
        )
        ids = {k["connectorId"] for k in result}
        assert ids == {"confluence-app", kb_uuid}



class TestParseRequestBody:
    def test_valid_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_body_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError, match="required"):
            _parse_request_body(b"")

    def test_none_body_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError, match="required"):
            _parse_request_body(b"")

    def test_invalid_json_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError, match="Invalid JSON"):
            _parse_request_body(b"not json")

    def test_complex_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        body = b'{"name": "test", "nested": {"key": [1, 2, 3]}}'
        result = _parse_request_body(body)
        assert result["nested"]["key"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _enrich_agent_models
# ---------------------------------------------------------------------------


class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enriches_matching_model(self) -> None:
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
    async def test_no_matching_config_fallback(self) -> None:
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
    async def test_no_model_name_uses_config(self) -> None:
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
    async def test_empty_models_returns_early(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": []}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        config_service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_models_key_returns_early(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        config_service.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_config_exception_swallowed(self) -> None:
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
    async def test_none_ai_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_mn1"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=None)
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        # With None config, llm_configs = [], model not found -> fallback
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_usesOrgDefault_true_when_no_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": []}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert agent["usesOrgDefault"] is True
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_usesOrgDefault_true_when_models_key_missing(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {}
        config_service = AsyncMock()
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert agent["usesOrgDefault"] is True
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_usesOrgDefault_false_when_models_present(self) -> None:
        from app.api.routes.agent import _enrich_agent_models

        agent = {"models": ["mk1_gpt-4o"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={
            "llm": [{
                "modelKey": "mk1",
                "configuration": {"model": "gpt-4o"},
                "provider": "openai",
                "isReasoning": True,
            }]
        })
        log = logging.getLogger("test")

        await _enrich_agent_models(agent, config_service, log)
        assert agent["usesOrgDefault"] is False


# ---------------------------------------------------------------------------
# _parse_toolsets (extended)
# ---------------------------------------------------------------------------


class TestParseToolsetsExtended:
    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(["not a dict", 42])
        assert result == {}

    def test_empty_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "", "tools": []}])
        assert result == {}

    def test_whitespace_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"name": "   ", "tools": []}])
        assert result == {}

    def test_tools_parsed(self) -> None:
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

    def test_duplicate_toolset_merges(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([
            {"name": "jira", "tools": [{"name": "t1", "fullName": "jira.t1", "description": ""}]},
            {"name": "jira", "tools": [{"name": "t2", "fullName": "jira.t2", "description": ""}]},
        ])
        assert len(result["jira"]["tools"]) == 2

    def test_tool_without_name_skipped(self) -> None:
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
    def test_empty_string_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": ""}, ["name"])

    def test_whitespace_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "   "}, ["name"])

    def test_none_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])

    def test_zero_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"count": 0}, ["count"])

    def test_valid_numeric_value_passes(self) -> None:
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"count": 5}, ["count"])


# ---------------------------------------------------------------------------
# _parse_models (extended)
# ---------------------------------------------------------------------------


class TestParseModelsExtended:
    def test_string_entries(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = ["mk1_mn1", "mk2"]
        entries, has_reasoning = _parse_models(raw, log)
        assert len(entries) == 2
        assert entries[0] == "mk1_mn1"
        assert entries[1] == "mk2"

    def test_dict_without_model_key_skipped(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelName": "mn1"}]  # no modelKey
        entries, _ = _parse_models(raw, log)
        assert entries == []

    def test_dict_with_key_and_name(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "mn1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1_mn1"]

    def test_dict_with_key_only(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1"]

    def test_non_list_input(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, has_reasoning = _parse_models("not a list", log)
        assert entries == []


# ---------------------------------------------------------------------------
# _enrich_user_info (extended)
# ---------------------------------------------------------------------------


class TestEnrichUserInfoExtended:
    @pytest.mark.asyncio
    async def test_adds_all_name_fields(self) -> None:
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
    async def test_does_not_mutate_original(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        original = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1"}
        await _enrich_user_info(original, user_doc)
        assert "userEmail" not in original  # original unchanged

    @pytest.mark.asyncio
    async def test_empty_email(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        result = await _enrich_user_info({"userId": "u1"}, {"email": "  ", "_key": "k1"})
        assert result["userEmail"] == ""


# ---------------------------------------------------------------------------
# _get_user_context (extended)
# ---------------------------------------------------------------------------


class TestGetUserContextExtended:
    def test_no_user_state(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_user_context
        request = MagicMock()
        request.state.user = {}
        with pytest.raises(HTTPException) as exc:
            _get_user_context(request)
        assert exc.value.status_code == 401

    def test_send_user_info_flag(self) -> None:
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
    def test_kb_with_no_record_groups_not_matched(self) -> None:
        """KB apps are regular apps now - filtered by UUID in apps list"""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440010"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        # No apps filter provided - returns all
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {}
        )
        assert len(result) == 1

    def test_combined_app_and_kb_filter(self) -> None:
        """App connectors are matched via filters["apps"], KB entries via
        filters["kb"] — each entry checked against the bucket for its own type."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440011"
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "jira"},
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {"apps": ["google"], "kb": [kb_uuid]}
        )
        assert len(result) == 2
        connectors = [r["connectorId"] for r in result]
        assert "google" in connectors
        assert kb_uuid in connectors


# ---------------------------------------------------------------------------
# _parse_knowledge_sources (extended)
# ---------------------------------------------------------------------------


class TestParseKnowledgeSourcesExtended:
    def test_dict_filters_kept(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": {"types": ["doc"]}}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_missing_connector_id(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"filters": {}}]
        assert _parse_knowledge_sources(raw) == {}

    def test_duplicate_connector_ids(self) -> None:
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
    async def test_valid_enterprise(self) -> None:
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "enterprise"})
        result = await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert result["orgId"] == "o1"
        assert result["accountType"] == "enterprise"

    @pytest.mark.asyncio
    async def test_valid_individual(self) -> None:
        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "Individual"})
        result = await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert result["accountType"] == "individual"

    @pytest.mark.asyncio
    async def test_org_not_found(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_account_type(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import _get_org_info
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"accountType": "invalid"})
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "o1"}, graph_provider, logging.getLogger("test"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_graph_provider_error(self) -> None:
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
    def test_valid_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"key": "value"}')
        assert result == {"key": "value"}

    def test_empty_body_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"")

    def test_invalid_json_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"not json")

    def test_nested_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        body = b'{"a": {"b": [1, 2, 3]}}'
        result = _parse_request_body(body)
        assert result["a"]["b"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _enrich_agent_models
# ---------------------------------------------------------------------------

class TestEnrichAgentModels:
    @pytest.mark.asyncio
    async def test_enriches_models(self) -> None:
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
    async def test_enriches_model_no_underscore(self) -> None:
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
    async def test_model_not_found_in_config(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["unknown_key"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": []}
        config_service = MagicMock()
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_none_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {}
        config_service = MagicMock()
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_config_error_handled(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["mk1_modelA"]}
        config_service = MagicMock()
        config_service.get_config = AsyncMock(side_effect=Exception("config error"))
        # Should not raise
        await _enrich_agent_models(agent, config_service, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_comma_separated_model_name(self) -> None:
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
    def test_string_entries(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = ["model_key_1", "model_key_2"]
        entries, _ = _parse_models(raw, log)
        assert entries == ["model_key_1", "model_key_2"]

    def test_model_key_only_no_name(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1"]

    def test_model_key_with_name(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelKey": "mk1", "modelName": "gpt-4"}]
        entries, _ = _parse_models(raw, log)
        assert entries == ["mk1_gpt-4"]

    def test_missing_model_key_skipped(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        raw = [{"modelName": "name_only"}]
        entries, _ = _parse_models(raw, log)
        assert entries == []

    def test_mixed_types(self) -> None:
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
    def test_with_tools(self) -> None:
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

    def test_tool_without_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{
            "name": "test",
            "tools": [{"description": "no name"}],
        }]
        result = _parse_toolsets(raw)
        assert len(result["test"]["tools"]) == 0

    def test_non_dict_toolset_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = ["not_a_dict", {"name": "valid"}]
        result = _parse_toolsets(raw)
        assert "valid" in result

    def test_empty_name_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "", "tools": []}]
        result = _parse_toolsets(raw)
        assert len(result) == 0

    def test_duplicate_toolset_merges_tools(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "tools": [{"name": "t1", "fullName": "jira.t1", "description": ""}]},
            {"name": "jira", "tools": [{"name": "t2", "fullName": "jira.t2", "description": ""}]},
        ]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 2

    def test_instance_id_set(self) -> None:
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

    def test_instance_id_updated_from_second_entry(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "tools": []},
            {"name": "jira", "instanceId": "inst-456", "instanceName": "New Jira", "tools": []},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-456"

    def test_default_display_name(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "my_toolset", "tools": []}]
        result = _parse_toolsets(raw)
        assert result["my_toolset"]["displayName"] == "My Toolset"

    def test_tool_fullname_default(self) -> None:
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
    def test_json_string_filters(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": '{"key": "val"}'}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"key": "val"}

    def test_invalid_json_string_filters(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {}

    def test_non_dict_entry_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = ["not_a_dict", {"connectorId": "c1"}]
        result = _parse_knowledge_sources(raw)
        assert "c1" in result

    def test_whitespace_connector_id_skipped(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "   "}]
        result = _parse_knowledge_sources(raw)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _filter_knowledge_by_enabled_sources (more branches)
# ---------------------------------------------------------------------------

class TestFilterKnowledgeFull:
    def test_no_filters_returns_all(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}, {"connectorId": "c2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert len(result) == 2

    def test_empty_apps_and_kb_returns_nothing(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [], "kb": []})
        assert len(result) == 0

    def test_app_filter_only(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "google"},
            {"connectorId": "slack"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "google"

    def test_kb_with_string_filters(self) -> None:
        """KB entries are regular connectors now, matched via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440020"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_with_invalid_json_filters(self) -> None:
        """KB entries filtered by the kb list, not by invalid filters."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440021"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        # Not in kb list - filtered out
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["other-kb"]})
        assert len(result) == 0

    def test_non_dict_entry_skipped(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not_a_dict", {"connectorId": "google"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["google"]})
        assert len(result) == 1

    def test_kb_no_matching_record_groups(self) -> None:
        """KB entries are filtered by UUID in the kb list, not the apps list."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440022"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        # Not in kb list - filtered out
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["other-kb"]})
        assert len(result) == 0

    def test_kb_with_filtersParsed(self) -> None:
        """KB entries are regular connectors - filtered by UUID via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440023"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_entry_not_matched_by_apps_filter(self) -> None:
        """A KB entry's id living in filters["apps"] must NOT match it."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440024"
        knowledge = [{"connectorId": kb_uuid, "type": "KB"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [kb_uuid]})
        assert len(result) == 0



class TestEnrichUserInfoExtended:
    @pytest.mark.asyncio
    async def test_with_display_name(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        user_doc = {"email": "a@b.com", "_key": "k1", "displayName": "Test User"}
        result = await _enrich_user_info({"userId": "u1"}, user_doc)
        assert result["displayName"] == "Test User"

    @pytest.mark.asyncio
    async def test_original_info_not_mutated(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        original = {"userId": "u1"}
        user_doc = {"email": "a@b.com", "_key": "k1"}
        result = await _enrich_user_info(original, user_doc)
        assert "userEmail" not in original
        assert "userEmail" in result

    @pytest.mark.asyncio
    async def test_whitespace_email(self) -> None:
        from app.api.routes.agent import _enrich_user_info
        user_doc = {"email": "  a@b.com  ", "_key": "k1"}
        result = await _enrich_user_info({"userId": "u1"}, user_doc)
        assert result["userEmail"] == "a@b.com"


# ---------------------------------------------------------------------------
# _validate_required_fields (extended)
# ---------------------------------------------------------------------------

class TestValidateRequiredFieldsExtended:
    def test_whitespace_only_field_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": "   "}, ["name"])

    def test_none_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"name": None}, ["name"])

    def test_zero_value_fails(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _validate_required_fields
        with pytest.raises(InvalidRequestError):
            _validate_required_fields({"count": 0}, ["count"])

    def test_empty_list_no_fields(self) -> None:
        from app.api.routes.agent import _validate_required_fields
        _validate_required_fields({"a": 1}, [])  # Should not raise


# ---------------------------------------------------------------------------
# _create_toolset_edges
# ---------------------------------------------------------------------------

class TestCreateToolsetEdges:
    @pytest.mark.asyncio
    async def test_empty_toolsets(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        created, failed = await _create_toolset_edges(
            "agent1", {}, {"userId": "u1"}, "uk1", AsyncMock(), logging.getLogger("test")
        )
        assert created == []
        assert failed == []

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self) -> None:
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
    async def test_batch_upsert_exception(self) -> None:
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
    async def test_empty_sources(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        result = await _create_knowledge_edges(
            "agent1", {}, "uk1", AsyncMock(), logging.getLogger("test")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_failure(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        graph_provider = AsyncMock()
        graph_provider.batch_upsert_nodes = AsyncMock(return_value=None)
        sources = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges(
            "agent1", sources, "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_creation(self) -> None:
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


# ---------------------------------------------------------------------------
# _parse_skills
# ---------------------------------------------------------------------------

class TestParseSkills:
    def test_none_returns_empty(self) -> None:
        from app.api.routes.agent import _parse_skills
        assert _parse_skills(None) == []

    def test_empty_list_returns_empty(self) -> None:
        from app.api.routes.agent import _parse_skills
        assert _parse_skills([]) == []

    def test_not_a_list_returns_empty(self) -> None:
        from app.api.routes.agent import _parse_skills
        assert _parse_skills("pdf-extractor") == []

    def test_list_of_dicts_with_name(self) -> None:
        from app.api.routes.agent import _parse_skills
        raw = [{"name": "pdf-extractor"}, {"name": "csv-summarizer"}]
        assert _parse_skills(raw) == ["pdf-extractor", "csv-summarizer"]

    def test_list_of_plain_strings(self) -> None:
        from app.api.routes.agent import _parse_skills
        assert _parse_skills(["pdf-extractor", "csv-summarizer"]) == ["pdf-extractor", "csv-summarizer"]

    def test_deduplicates_preserving_order(self) -> None:
        from app.api.routes.agent import _parse_skills
        raw = [{"name": "pdf-extractor"}, "csv-summarizer", {"name": "pdf-extractor"}]
        assert _parse_skills(raw) == ["pdf-extractor", "csv-summarizer"]

    def test_strips_whitespace(self) -> None:
        from app.api.routes.agent import _parse_skills
        assert _parse_skills(["  pdf-extractor  "]) == ["pdf-extractor"]

    def test_skips_blank_and_malformed_entries(self) -> None:
        from app.api.routes.agent import _parse_skills
        raw = [{"name": ""}, {"name": "   "}, {"notname": "x"}, 42, None, {"name": "valid"}]
        assert _parse_skills(raw) == ["valid"]


# ---------------------------------------------------------------------------
# _create_skill_edges
# ---------------------------------------------------------------------------

class TestCreateSkillEdges:
    @pytest.mark.asyncio
    async def test_empty_names_returns_empty_without_db_calls(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        result = await _create_skill_edges(
            "agent1", [], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []
        graph_provider.get_document.assert_not_called()
        graph_provider.batch_create_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_links_owned_skill(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"orgId": "org1", "createdBy": "uk1", "source": "custom"}
        )
        graph_provider.batch_create_edges = AsyncMock(return_value=True)
        result = await _create_skill_edges(
            "agent1", ["pdf-extractor"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == ["pdf-extractor"]
        graph_provider.batch_create_edges.assert_awaited_once()
        edges = graph_provider.batch_create_edges.await_args.args[0]
        assert edges[0]["skillName"] == "pdf-extractor"
        assert edges[0]["_from"] == "agentInstances/agent1"

    @pytest.mark.asyncio
    async def test_links_builtin_skill_regardless_of_creator(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"orgId": "org1", "createdBy": "someone-else", "source": "builtin"}
        )
        graph_provider.batch_create_edges = AsyncMock(return_value=True)
        result = await _create_skill_edges(
            "agent1", ["web-search"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == ["web-search"]

    @pytest.mark.asyncio
    async def test_skips_skill_not_found(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)
        result = await _create_skill_edges(
            "agent1", ["missing-skill"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []
        graph_provider.batch_create_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_skill_from_other_org(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"orgId": "org-other", "createdBy": "uk1"})
        result = await _create_skill_edges(
            "agent1", ["cross-org-skill"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_skill_not_owned_by_user(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(
            return_value={"orgId": "org1", "createdBy": "someone-else", "source": "custom"}
        )
        result = await _create_skill_edges(
            "agent1", ["colleagues-skill"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == []
        graph_provider.batch_create_edges.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_valid_and_invalid_only_links_valid(self) -> None:
        from app.api.routes.agent import _create_skill_edges
        graph_provider = AsyncMock()

        async def fake_get_document(key: str, *_args: object, **_kwargs: object) -> dict | None:
            if key == "org1_good-skill":
                return {"orgId": "org1", "createdBy": "uk1", "source": "custom"}
            return None

        graph_provider.get_document = AsyncMock(side_effect=fake_get_document)
        graph_provider.batch_create_edges = AsyncMock(return_value=True)
        result = await _create_skill_edges(
            "agent1", ["good-skill", "bad-skill"], "org1", "uk1", graph_provider, logging.getLogger("test")
        )
        assert result == ["good-skill"]
        edges = graph_provider.batch_create_edges.await_args.args[0]
        assert len(edges) == 1


# ===========================================================================
# Template CRUD endpoint tests
# ===========================================================================


class TestCreateAgentTemplate:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
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
    async def test_batch_upsert_failure(self) -> None:
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
    async def test_generic_exception(self) -> None:
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
    async def test_success(self) -> None:
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
    async def test_returns_empty_on_none(self) -> None:
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
    async def test_found(self) -> None:
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
    async def test_not_found(self) -> None:
        from app.api.routes.agent import AgentTemplateNotFoundError, get_agent_template

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
    async def test_success(self) -> None:
        from app.api.routes.agent import clone_agent_template

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].clone_agent_template = AsyncMock(return_value="cloned-id")

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services):
            result = await clone_agent_template(request, "t1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_failure(self) -> None:
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
    async def test_success(self) -> None:
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
    async def test_failure(self) -> None:
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
    async def test_success(self) -> None:
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


# ===========================================================================
# Agent CRUD endpoint tests
# ===========================================================================


class TestMarkDeprecatedTools:
    """Unit tests for `_mark_deprecated_tools`.

    The old global tools registry was removed; the function is currently a
    no-op pending a replacement registry. Tests confirm it does not mutate
    the agent and does not raise.
    """

    def test_is_noop_does_not_mutate_agent(self) -> None:
        """Function must not add or modify any keys on the agent dict."""
        from app.api.routes.agent import _mark_deprecated_tools

        agent = {
            "_key": "a1",
            "toolsets": [
                {"name": "ts", "tools": [
                    {"name": "Search", "fullName": "google.search"},
                    {"name": "Gone", "fullName": "google.removed_tool"},
                ]},
            ],
        }
        import copy
        original = copy.deepcopy(agent)
        logger = MagicMock()

        _mark_deprecated_tools(agent, logger)

        assert agent == original, "no-op must not mutate the agent"
        logger.info.assert_not_called()
        logger.warning.assert_not_called()

    def test_no_toolsets_or_tools_is_noop(self) -> None:
        """Edge-case agent shapes must not cause AttributeError."""
        from app.api.routes.agent import _mark_deprecated_tools

        for agent in (
            {"_key": "a1"},
            {"_key": "a2", "toolsets": []},
            {"_key": "a3", "toolsets": None},
            {"_key": "a4", "toolsets": [{"name": "ts"}]},
            {"_key": "a5", "toolsets": [{"name": "ts", "tools": None}]},
        ):
            logger = MagicMock()
            _mark_deprecated_tools(agent, logger)
            logger.info.assert_not_called()


class TestGetAgent:
    @pytest.mark.asyncio
    async def test_found(self) -> None:
        from app.api.routes.agent import get_agent

        services = {"graph_provider": AsyncMock(), "config_service": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "models": []})
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_read": True})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock), \
             patch("app.api.routes.agent._mark_deprecated_tools") as mark_dep:

            result = await get_agent(request, "a1")
            assert result.status_code == 200
            mark_dep.assert_called_once()

    @pytest.mark.asyncio
    async def test_found_returns_agent_toolsets_unchanged(self) -> None:
        """GET /agents/{id} must return the agent doc without mutating its tools.
        _mark_deprecated_tools is currently a no-op (registry removed)."""
        from app.api.routes.agent import get_agent

        agent_doc = {
            "_key": "a1",
            "name": "A1",
            "models": [],
            "toolsets": [
                {
                    "name": "google_drive",
                    "tools": [
                        {"name": "Search", "fullName": "google.search"},
                        {"name": "Gone", "fullName": "google.removed_tool"},
                    ],
                }
            ],
        }
        services = {"graph_provider": AsyncMock(), "config_service": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value=agent_doc)
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_read": True})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_agent_models", new_callable=AsyncMock):

            result = await get_agent(request, "a1")

        assert result.status_code == 200
        body = json.loads(result.body)
        tools = body["agent"]["toolsets"][0]["tools"]
        # No deprecated key should be injected while _mark_deprecated_tools is a no-op.
        assert "deprecated" not in tools[0]
        assert "deprecated" not in tools[1]

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, get_agent

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
    async def test_list_result(self) -> None:
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(return_value=[{"name": "A1"}, {"name": "A2"}])

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agents(
                request,
                page=1,
                limit=20,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=False,
            )
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_dict_result(self) -> None:
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(return_value={"agents": [{"name": "A1"}], "totalItems": 1})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agents(
                request,
                page=1,
                limit=20,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=False,
            )
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_passes_is_deleted_to_graph_provider(self) -> None:
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(
            return_value={"agents": [], "totalItems": 0}
        )

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            await get_agents(
                request,
                page=1,
                limit=10,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=True,
            )

        services["graph_provider"].get_all_agents.assert_awaited_once()
        call = services["graph_provider"].get_all_agents.await_args
        assert call is not None
        assert call.kwargs.get("is_deleted") is True

    @pytest.mark.asyncio
    async def test_usesOrgDefault_flag_derived_per_agent(self) -> None:
        """Each listed agent gets a cheap `usesOrgDefault` flag derived from
        whether its `models` array is empty — no etcd lookup involved."""
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(return_value=[
            {"name": "HasModel", "models": ["mk1_mn1"]},
            {"name": "NoModel", "models": []},
            {"name": "MissingKey"},
        ])

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await get_agents(
                request,
                page=1,
                limit=20,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=False,
            )
            body = json.loads(result.body)
            agents_by_name = {a["name"]: a for a in body["agents"]}
            assert agents_by_name["HasModel"]["usesOrgDefault"] is False
            assert agents_by_name["NoModel"]["usesOrgDefault"] is True
            assert agents_by_name["MissingKey"]["usesOrgDefault"] is True


class TestDeleteAgent:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        from app.api.routes.agent import delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": True})
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_delete": True})
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].delete_agent = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await delete_agent(request, "a1")
            assert result.status_code == 200
            services["graph_provider"].delete_agent.assert_awaited_once_with(
                "a1", "k1", "o1", transaction="txn-1"
            )

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value=None)
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentNotFoundError):
                await delete_agent(request, "missing")

    @pytest.mark.asyncio
    async def test_permission_denied(self) -> None:
        from app.api.routes.agent import PermissionDeniedError, delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_delete": False})
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": False})

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(PermissionDeniedError):
                await delete_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_soft_delete_failure_rolls_back(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import delete_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_delete": True})
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_delete": True})
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].delete_agent = AsyncMock(return_value=False)
        services["graph_provider"].rollback_transaction = AsyncMock()

        request = MagicMock()

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(HTTPException) as exc:
                await delete_agent(request, "a1")
            assert exc.value.status_code == 500


# ===========================================================================
# Create Agent endpoint tests
# ===========================================================================


class TestCreateAgent:
    @pytest.mark.asyncio
    async def test_empty_models_allowed_falls_back_to_org_default(self) -> None:
        """An agent created with no models is valid — it uses the
        organization's default LLM at chat time (see `get_llm_for_chat`)."""
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"A1","models":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await create_agent(request)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_omitted_models_allowed_falls_back_to_org_default(self) -> None:
        """Omitting `models` entirely from the create payload is equivalent
        to sending an empty array."""
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"A1"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await create_agent(request)
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_no_reasoning_model_raises(self) -> None:
        from app.api.routes.agent import InvalidRequestError, create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"A1","models":[{"modelKey":"mk1","modelName":"mn1"}]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError, match="reasoning model"):
                await create_agent(request)

    @pytest.mark.asyncio
    async def test_success_basic(self) -> None:
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
    async def test_success_basic(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(AgentNotFoundError):
                await update_agent(request, "missing")

    @pytest.mark.asyncio
    async def test_permission_denied(self) -> None:
        from app.api.routes.agent import PermissionDeniedError, update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": False})
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": False})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name":"Updated"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(PermissionDeniedError):
                await update_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_share_with_org_on(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_share_with_org_off(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_update_with_toolsets(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_update_with_empty_toolsets(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_update_with_knowledge(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_update_empty_models_clears_agent_models(self) -> None:
        """An empty `models` array on update is valid — it clears the
        agent's models, reverting it to the organization's default LLM."""
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(return_value={"name": "A1", "can_edit": True})
        services["graph_provider"].update_agent = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"models":[]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            result = await update_agent(request, "a1")
            assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_update_models_without_reasoning_still_rejected(self) -> None:
        """When `models` is provided and non-empty, at least one entry must
        still be flagged `isReasoning: true`."""
        from app.api.routes.agent import InvalidRequestError, update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"models":[{"modelKey":"mk1","modelName":"mn1"}]}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):

            with pytest.raises(InvalidRequestError, match="reasoning model"):
                await update_agent(request, "a1")


# ===========================================================================
# Agent Chat endpoint tests
# ===========================================================================


class TestAgentChat:
    """`chat()` drains `chat_stream()`'s SSE body_iterator and returns the
    final `complete`/`error` event as JSON -- see `chat()`'s docstring in
    `app.api.routes.agent`. These tests mock `chat_stream()` itself rather
    than the (now-removed) LangGraph plumbing it used to have its own copy
    of."""

    @staticmethod
    def _sse_streaming_response(frames: list[str]):
        from fastapi.responses import StreamingResponse

        async def _gen():
            for frame in frames:
                yield frame

        return StreamingResponse(_gen(), media_type="text/event-stream")

    @pytest.mark.asyncio
    async def test_chat_success_returns_complete_payload(self) -> None:
        from fastapi.responses import JSONResponse

        from app.api.routes.agent import chat

        completion_data = {"status": "success", "message": "reply", "searchResults": [], "records": []}
        streaming_response = self._sse_streaming_response([
            f"event: complete\ndata: {json.dumps(completion_data)}\n\n",
        ])

        request = MagicMock()
        with patch("app.api.routes.agent.chat_stream", new_callable=AsyncMock, return_value=streaming_response) as mock_chat_stream:
            result = await chat(request, "a1")

        mock_chat_stream.assert_awaited_once_with(request, "a1")
        assert isinstance(result, JSONResponse)
        assert json.loads(result.body) == completion_data

    @pytest.mark.asyncio
    async def test_chat_error_event_returns_error_response(self) -> None:
        from fastapi.responses import JSONResponse

        from app.api.routes.agent import chat

        error_payload = {"status_code": 422, "status": "error", "message": "bad input"}
        streaming_response = self._sse_streaming_response([
            f"event: error\ndata: {json.dumps(error_payload)}\n\n",
        ])

        request = MagicMock()
        with patch("app.api.routes.agent.chat_stream", new_callable=AsyncMock, return_value=streaming_response):
            result = await chat(request, "a1")

        assert isinstance(result, JSONResponse)
        assert result.status_code == 422
        body = json.loads(result.body)
        assert body["status"] == "error"
        assert body["message"] == "bad input"

    @pytest.mark.asyncio
    async def test_chat_no_events_returns_500(self) -> None:
        from fastapi.responses import JSONResponse

        from app.api.routes.agent import chat

        streaming_response = self._sse_streaming_response([])

        request = MagicMock()
        with patch("app.api.routes.agent.chat_stream", new_callable=AsyncMock, return_value=streaming_response):
            result = await chat(request, "a1")

        assert isinstance(result, JSONResponse)
        assert result.status_code == 500
        body = json.loads(result.body)
        assert body["status"] == "error"

    @pytest.mark.asyncio
    async def test_chat_non_streaming_response_passthrough(self) -> None:
        from fastapi.responses import JSONResponse

        from app.api.routes.agent import chat

        passthrough = JSONResponse(status_code=400, content={"status": "error", "message": "bad"})

        request = MagicMock()
        with patch("app.api.routes.agent.chat_stream", new_callable=AsyncMock, return_value=passthrough):
            result = await chat(request, "a1")

        assert result is passthrough


# ===========================================================================
# Create agent with toolsets and knowledge
# ===========================================================================


class TestCreateAgentWithToolsetsAndKnowledge:
    @pytest.mark.asyncio
    async def test_with_toolsets_and_knowledge(self) -> None:
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
    async def test_transaction_rollback_on_error(self) -> None:
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
    async def test_full_toolset_creation(self) -> None:
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
    async def test_chat_stream_agent_not_found(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(return_value=None)
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}):

            with pytest.raises(AgentNotFoundError):
                await chat_stream(request, "missing")

    @pytest.mark.asyncio
    async def test_chat_stream_success(self) -> None:
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
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ):

            with _stub_agent_loop():
                result = await chat_stream(request, "a1")
                assert isinstance(result, StreamingResponse)
                await _drain(result)

    @pytest.mark.asyncio
    async def test_chat_stream_no_agent_models_falls_back_to_org_default(self) -> None:
        """An agent with no configured models resolves modelKey/modelName as
        None, which `get_llm_for_chat` interprets as "use the organization's
        default LLM" (see `get_model_config`'s `isDefault` lookup)."""
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
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [],
            "models": [],
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        mock_get_llm_for_chat = AsyncMock(return_value=(MagicMock(), {"isReasoning": True}, {}))

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", mock_get_llm_for_chat):

            with _stub_agent_loop():
                result = await chat_stream(request, "a1")
                assert isinstance(result, StreamingResponse)
                await _drain(result)

        mock_get_llm_for_chat.assert_awaited_once()
        call_args = mock_get_llm_for_chat.await_args
        assert call_args.args[1] is None  # model_key
        assert call_args.args[2] is None  # model_name

    @pytest.mark.asyncio
    async def test_chat_stream_with_toolsets(self) -> None:
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
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ), \
             patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/services/toolsets/inst-1/u1"):

            with _stub_agent_loop():
                result = await chat_stream(request, "a1")
                assert isinstance(result, StreamingResponse)
                await _drain(result)

    @pytest.mark.asyncio
    async def test_chat_stream_missing_toolset_config(self) -> None:
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
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ), \
             patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/services/toolsets/inst-1/u1"):

            result = await chat_stream(request, "a1")
            # Should return StreamingResponse with an error event for missing config
            assert isinstance(result, StreamingResponse)

    @pytest.mark.asyncio
    async def test_chat_stream_with_explicit_filters(self) -> None:
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
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ):

            with _stub_agent_loop():
                result = await chat_stream(request, "a1")
                assert isinstance(result, StreamingResponse)
                await _drain(result)

    @pytest.mark.asyncio
    async def test_chat_stream_llm_init_error(self) -> None:
        from app.api.routes.agent import LLMInitializationError, chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "name": "A1", "knowledge": [], "toolsets": [], "models": ["mk1_mn1"],
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}), \
             patch("app.api.routes.agent._enrich_user_info", new_callable=AsyncMock, return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch("app.api.routes.agent.get_llm_for_chat", new_callable=AsyncMock, return_value=None):

            # Once the headers are out there is no HTTP status left to carry
            # the failure — it arrives as a terminal SSE error frame instead,
            # matching what `/chat/stream` already does for this same case.
            body = await _drain(await chat_stream(request, "a1"))

        assert "RUN_ERROR" in body or "event: error" in body
        assert "Failed to initialize LLM service" in body


# ===========================================================================
# Update agent — toolsets deletion with existing edges
# ===========================================================================


class TestUpdateAgentToolsetDeletion:
    @pytest.mark.asyncio
    async def test_deletes_existing_toolsets_before_creating_new(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_toolset_deletion_transaction_failure_rolls_back(self) -> None:
        from fastapi import HTTPException

        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_knowledge_deletion_with_existing_edges(self) -> None:
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
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
    async def test_agent_toolset_edges_exception(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=Exception("edge fail"))
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            with pytest.raises(Exception, match="edge fail"):
                await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_tool_nodes_upsert_none(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=[True, None])
        gp.batch_create_edges = AsyncMock(return_value=True)
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            with pytest.raises(RuntimeError, match="Failed to create tool nodes"):
                await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_tool_nodes_upsert_exception(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=[True, Exception("fail")])
        gp.batch_create_edges = AsyncMock(return_value=True)
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            with pytest.raises(Exception, match="fail"):
                await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))

    @pytest.mark.asyncio
    async def test_toolset_tool_edges_exception(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=[True, Exception("fail")])
        toolsets = {"jira": {"displayName": "J", "type": "app", "tools": [{"name": "s", "fullName": "jira.s", "description": ""}], "instanceId": None, "instanceName": None}}
        with patch("app.agents.constants.toolset_constants.normalize_app_name", return_value="jira"):
            with pytest.raises(Exception, match="fail"):
                await _create_toolset_edges("a1", toolsets, {"userId": "u1"}, "uk1", gp, logging.getLogger("test"))

class TestKnowledgeEdgeFailures2:
    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("fail"))
        result = await _create_knowledge_edges("a1", {"c1": {"connectorId": "c1", "filters": {}}}, "uk1", gp, logging.getLogger("test"))
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_create_edges_exception(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=True)
        gp.batch_create_edges = AsyncMock(side_effect=Exception("fail"))
        result = await _create_knowledge_edges("a1", {"c1": {"connectorId": "c1", "filters": {}}}, "uk1", gp, logging.getLogger("test"))
        assert len(result) == 1

class TestAllErrorPaths:
    @pytest.mark.asyncio
    async def test_template_edge_fail(self) -> None:
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
    def test_filters_as_json_string(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": '{"types": ["doc"]}'}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {"types": ["doc"]}

    def test_filters_as_invalid_json_string(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "c1", "filters": "not json"}]
        result = _parse_knowledge_sources(raw)
        assert result["c1"]["filters"] == {}

    def test_missing_connector_id(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"filters": {}}]
        result = _parse_knowledge_sources(raw)
        assert result == {}

    def test_empty_connector_id(self) -> None:
        from app.api.routes.agent import _parse_knowledge_sources
        raw = [{"connectorId": "  ", "filters": {}}]
        result = _parse_knowledge_sources(raw)
        assert result == {}


class TestFilterKnowledgeByEnabledSourcesFullCoverage:
    def test_no_filters(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [{"connectorId": "c1"}, {"connectorId": "c2"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {})
        assert len(result) == 2

    def test_app_filter(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = [
            {"connectorId": "app1"},
            {"connectorId": "app2"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["app1"]})
        assert len(result) == 1
        assert result[0]["connectorId"] == "app1"

    def test_kb_filter_with_matching_record_groups(self) -> None:
        """KB entries are regular connectors - filtered by UUID via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440030"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_filter_no_matching_record_groups(self) -> None:
        """KB entries filtered out when not in the kb list"""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440031"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["other-kb"]})
        assert len(result) == 0

    def test_kb_filter_with_json_string_filters(self) -> None:
        """KB entries filtered by UUID via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440032"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": [kb_uuid]})
        assert len(result) == 1

    def test_kb_filter_invalid_json_filters(self) -> None:
        """KB entries filtered by UUID, not by invalid filters"""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440033"
        knowledge = [
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"kb": ["other-kb"]})
        assert len(result) == 0

    def test_non_dict_skipped(self) -> None:
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        knowledge = ["not a dict", None, 42]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": ["a1"]})
        assert len(result) == 0

    def test_kb_entry_not_matched_by_apps_filter(self) -> None:
        """A KB entry's id living in filters["apps"] must NOT match it —
        it is only ever enabled via filters["kb"]."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440034"
        knowledge = [{"connectorId": kb_uuid, "type": "KB"}]
        result = _filter_knowledge_by_enabled_sources(knowledge, {"apps": [kb_uuid]})
        assert len(result) == 0

    def test_mixed_kb_and_app_both_kept(self) -> None:
        """Regression: configuring an app connector must not drop KB entries."""
        from app.api.routes.agent import _filter_knowledge_by_enabled_sources
        kb_uuid = "550e8400-e29b-41d4-a716-446655440035"
        knowledge = [
            {"connectorId": "app1", "type": "confluence"},
            {"connectorId": kb_uuid, "type": "KB"},
        ]
        result = _filter_knowledge_by_enabled_sources(
            knowledge, {"apps": ["app1"], "kb": [kb_uuid]},
        )
        ids = {k["connectorId"] for k in result}
        assert ids == {"app1", kb_uuid}


class TestParseToolsetsEdgeCases:
    def test_non_dict_entries_skipped(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets(["not dict", 42, None])
        assert result == {}

    def test_missing_name(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        result = _parse_toolsets([{"type": "app"}])
        assert result == {}

    def test_duplicate_toolset_updates_instance_id(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [
            {"name": "jira", "displayName": "Jira", "type": "app", "tools": []},
            {"name": "jira", "displayName": "Jira", "type": "app", "tools": [], "instanceId": "inst-1", "instanceName": "My Jira"},
        ]
        result = _parse_toolsets(raw)
        assert result["jira"]["instanceId"] == "inst-1"

    def test_tool_dict_with_name(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "jira", "tools": [{"name": "search", "fullName": "jira.search", "description": "Search"}]}]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 1

    def test_tool_dict_without_name(self) -> None:
        from app.api.routes.agent import _parse_toolsets
        raw = [{"name": "jira", "tools": [{"description": "No name"}]}]
        result = _parse_toolsets(raw)
        assert len(result["jira"]["tools"]) == 0


class TestParseModelsEdgeCases:
    def test_string_model(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models(["model_key_1"], log)
        assert "model_key_1" in entries

    def test_dict_without_model_key(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models([{"modelName": "name"}], log)
        assert entries == []

    def test_dict_with_key_no_name(self) -> None:
        from app.api.routes.agent import _parse_models
        log = logging.getLogger("test")
        entries, _ = _parse_models([{"modelKey": "mk1"}], log)
        assert entries == ["mk1"]


class TestEnrichAgentModelsFullCoverage:
    @pytest.mark.asyncio
    async def test_enriches_with_matching_config(self) -> None:
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
    async def test_no_matching_config(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["unknown_model"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"][0]["provider"] == "unknown"

    @pytest.mark.asyncio
    async def test_empty_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": []}
        config_service = AsyncMock()
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)
        assert agent["models"] == []

    @pytest.mark.asyncio
    async def test_none_models(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {}
        config_service = AsyncMock()
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)

    @pytest.mark.asyncio
    async def test_exception_caught(self) -> None:
        from app.api.routes.agent import _enrich_agent_models
        agent = {"models": ["key_name"]}
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("fail"))
        log = logging.getLogger("test")
        await _enrich_agent_models(agent, config_service, log)

    @pytest.mark.asyncio
    async def test_comma_separated_model_name(self) -> None:
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
    def test_valid_json(self) -> None:
        from app.api.routes.agent import _parse_request_body
        result = _parse_request_body(b'{"name": "test"}')
        assert result == {"name": "test"}

    def test_empty_body(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"")

    def test_invalid_json(self) -> None:
        from app.api.routes.agent import InvalidRequestError, _parse_request_body
        with pytest.raises(InvalidRequestError):
            _parse_request_body(b"not json")


class TestCreateToolsetEdgesFullCoverage:
    @pytest.mark.asyncio
    async def test_empty_toolsets(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        log = logging.getLogger("test")
        created, failed = await _create_toolset_edges("ak1", {}, {}, "uk1", AsyncMock(), log)
        assert created == []
        assert failed == []

    @pytest.mark.asyncio
    async def test_batch_upsert_fails(self) -> None:
        from app.api.routes.agent import _create_toolset_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=False)
        toolsets = {"jira": {"displayName": "Jira", "type": "app", "tools": [], "instanceId": None, "instanceName": None}}
        user_info = {"userId": "u1"}
        created, failed = await _create_toolset_edges("ak1", toolsets, user_info, "uk1", gp, log)
        assert len(failed) == 1

    @pytest.mark.asyncio
    async def test_batch_upsert_exception(self) -> None:
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
    async def test_empty_knowledge(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        result = await _create_knowledge_edges("ak1", {}, "uk1", AsyncMock(), log)
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_upsert_fails(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(return_value=False)
        knowledge = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges("ak1", knowledge, "uk1", gp, log)
        assert result == []

    @pytest.mark.asyncio
    async def test_success(self) -> None:
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
    async def test_batch_upsert_exception(self) -> None:
        from app.api.routes.agent import _create_knowledge_edges
        log = logging.getLogger("test")
        gp = AsyncMock()
        gp.batch_upsert_nodes = AsyncMock(side_effect=Exception("err"))
        knowledge = {"c1": {"connectorId": "c1", "filters": {}}}
        result = await _create_knowledge_edges("ak1", knowledge, "uk1", gp, log)
        assert result == []


class TestServiceAccountAgentRoutes:
    @pytest.mark.asyncio
    async def test_create_agent_service_account_forces_org_permission(self) -> None:
        from app.api.routes.agent import create_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].begin_transaction = AsyncMock(return_value="txn-1")
        services["graph_provider"].batch_upsert_nodes = AsyncMock(return_value=True)
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)
        services["graph_provider"].commit_transaction = AsyncMock()

        request = MagicMock()
        request.body = AsyncMock(
            return_value=b'{"name":"SA","isServiceAccount":true,"shareWithOrg":false,'
            b'"models":[{"modelKey":"mk1","modelName":"mn1","isReasoning":true}]}'
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):
            result = await create_agent(request)

        assert result.status_code == 200
        agent_nodes = services["graph_provider"].batch_upsert_nodes.await_args_list[0].args[0]
        assert agent_nodes[0]["isServiceAccount"] is True
        permission_edges = services["graph_provider"].batch_create_edges.await_args_list[0].args[0]
        assert any(edge.get("type") == "ORG" for edge in permission_edges)

    @pytest.mark.asyncio
    async def test_get_agent_internal_requires_service_account_agent(self) -> None:
        from app.api.routes.agent import get_agent_internal

        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": AsyncMock()}
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"_key": "a1", "isServiceAccount": False, "createdBy": "ck1"}
        )
        services["graph_provider"].get_document = AsyncMock(
            return_value={"userId": "u1", "orgId": "o1"}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}):
            with pytest.raises(HTTPException) as exc:
                await get_agent_internal(MagicMock(), "a1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_agent_rejects_disabling_org_sharing_for_service_account(self) -> None:
        from app.api.routes.agent import InvalidRequestError, update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value={"can_edit": True})
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"name": "A1", "isServiceAccount": True, "shareWithOrg": True}
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"shareWithOrg":false}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"email": "a@b.com", "_key": "k1"}):
            with pytest.raises(InvalidRequestError, match="Cannot disable org-wide sharing"):
                await update_agent(request, "a1")

    @pytest.mark.asyncio
    async def test_chat_stream_service_account_uses_agent_credentials_lookup(self) -> None:
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
            "isServiceAccount": True,
            "createdBy": "creator-key-1",
            "knowledge": [],
            "toolsets": [{"name": "jira", "instanceId": "inst-1", "displayName": "Jira", "tools": []}],
            "models": ["mk1_mn1"],
        })
        services["graph_provider"].get_document = AsyncMock(return_value={
            "_key": "creator-key-1",
            "userId": "creator-user-1",
            "orgId": "o1",
            "email": "creator@example.com",
        })

        async def mock_config(path, *args, **kwargs):
            if "toolsets" in str(path):
                return {"isAuthenticated": True}
            return {"llm": []}

        services["config_service"].get_config = mock_config

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch(
                 "app.api.routes.agent._get_user_context",
                 return_value={"userId": "slack-bot@x", "orgId": "o1", "isServiceAccount": True, "email": "slack-bot@x"},
             ), \
             patch("app.api.routes.agent._get_org_info", new_callable=AsyncMock, return_value={"orgId": "o1", "accountType": "enterprise"}), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ), \
             patch("app.agents.constants.toolset_constants.get_toolset_config_path", return_value="/services/toolsets/inst-1/a1") as mock_cfg_path, \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, side_effect=HTTPException(status_code=404, detail="User not found")) as mock_user_doc:
            with _stub_agent_loop():
                result = await chat_stream(request, "a1")
                assert isinstance(result, StreamingResponse)
                # The credential lookup happens inside the stream now.
                await _drain(result)

        mock_user_doc.assert_awaited_once()
        services["graph_provider"].get_document.assert_awaited_once()
        mock_cfg_path.assert_called_with("inst-1", "a1")

    @pytest.mark.asyncio
    async def test_chat_stream_service_account_rejects_other_org_caller(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, chat_stream

        services = {
            "graph_provider": AsyncMock(),
            "retrieval_service": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "logger": MagicMock(),
            "llm": MagicMock(),
        }
        services["graph_provider"].get_agent = AsyncMock(return_value={
            "_key": "sa-org-a",
            "name": "A1",
            "isServiceAccount": True,
            "createdBy": "creator-key-1",
            "knowledge": [],
            "toolsets": [],
            "models": ["mk1_mn1"],
        })
        services["graph_provider"].get_document = AsyncMock(return_value={
            "_key": "creator-key-1",
            "userId": "creator-user-1",
            "orgId": "org-a",
            "email": "creator@example.com",
        })
        services["config_service"].get_config = AsyncMock(return_value={"llm": []})

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"query":"hello"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch(
                 "app.api.routes.agent._get_user_context",
                 return_value={"userId": "org-b-user", "orgId": "org-b", "email": "b@example.com"},
             ), \
             patch(
                 "app.api.routes.agent._get_org_info",
                 new_callable=AsyncMock,
                 return_value={"orgId": "org-b", "accountType": "enterprise"},
             ), \
             patch(
                 "app.api.routes.agent.get_llm_for_chat",
                 new_callable=AsyncMock,
                 return_value=(MagicMock(), {"isReasoning": True}, {}),
             ):
            with pytest.raises(AgentNotFoundError):
                await chat_stream(request, "sa-org-a")

        services["graph_provider"].check_agent_permission.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_agent_internal_success(self) -> None:
        """get_agent_internal returns 200 with isServiceAccount=True for a SA agent."""
        from app.api.routes.agent import get_agent_internal

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
            "config_service": config_service,
        }
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"_key": "a1", "isServiceAccount": True, "models": [], "createdBy": "ck1"}
        )
        services["graph_provider"].get_document = AsyncMock(
            return_value={"userId": "u1", "orgId": "o1"}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}):
            result = await get_agent_internal(MagicMock(), "a1")

        assert result.status_code == 200
        body = json.loads(result.body)
        assert body["isServiceAccount"] is True
        assert body["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_agent_internal_agent_not_found(self) -> None:
        """get_agent_internal raises when agent does not exist."""
        from app.api.routes.agent import get_agent_internal

        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": AsyncMock()}
        services["graph_provider"].get_agent = AsyncMock(return_value=None)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services):
            with pytest.raises(HTTPException) as exc:
                await get_agent_internal(MagicMock(), "missing-agent")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_agent_internal_other_org_service_account_is_404(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, get_agent_internal

        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": AsyncMock()}
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"_key": "sa-org-a", "isServiceAccount": True, "createdBy": "ck1"}
        )
        services["graph_provider"].get_document = AsyncMock(
            return_value={"userId": "creator", "orgId": "org-a"}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch(
                 "app.api.routes.agent._get_user_context",
                 return_value={"userId": "org-b-user", "orgId": "org-b"},
             ):
            with pytest.raises(AgentNotFoundError):
                await get_agent_internal(MagicMock(), "sa-org-a")

    @pytest.mark.asyncio
    async def test_get_agent_internal_other_org_user_agent_is_404_not_403(self) -> None:
        from app.api.routes.agent import AgentNotFoundError, get_agent_internal

        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": AsyncMock()}
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"_key": "user-org-a", "isServiceAccount": False, "createdBy": "ck1"}
        )
        services["graph_provider"].get_document = AsyncMock(
            return_value={"userId": "creator", "orgId": "org-a"}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch(
                 "app.api.routes.agent._get_user_context",
                 return_value={"userId": "org-b-user", "orgId": "org-b"},
             ):
            with pytest.raises(AgentNotFoundError):
                await get_agent_internal(MagicMock(), "user-org-a")

    @pytest.mark.asyncio
    async def test_get_agent_no_permission_raises_404(self) -> None:
        """get_agent raises AgentNotFoundError when user has no permission."""
        from app.api.routes.agent import get_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": AsyncMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value=None)

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc:
                await get_agent(MagicMock(), "a1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_agent_success_returns_enriched_agent(self) -> None:
        """get_agent returns 200 with the enriched agent dict."""
        from app.api.routes.agent import get_agent

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"llm": []})
        services = {
            "graph_provider": AsyncMock(),
            "logger": MagicMock(),
            "config_service": config_service,
        }
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True, "can_delete": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"_key": "a1", "name": "MyAgent", "models": []}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            result = await get_agent(MagicMock(), "a1")

        assert result.status_code == 200
        body = json.loads(result.body)
        assert body["status"] == "success"
        assert body["agent"]["name"] == "MyAgent"
        assert body["agent"]["can_edit"] is True

    @pytest.mark.asyncio
    async def test_update_agent_no_permission_raises(self) -> None:
        """update_agent raises when check_agent_permission returns None."""
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(return_value=None)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name": "New Name"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc:
                await update_agent(request, "a1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_agent_no_edit_permission_raises_403(self) -> None:
        """update_agent raises 403 when user has view-only access."""
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": False}
        )

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"name": "New Name"}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            with pytest.raises(HTTPException) as exc:
                await update_agent(request, "a1")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_update_agent_converting_to_sa_forces_org_share(self) -> None:
        """update_agent sets shareWithOrg=True when upgrading a regular agent to SA."""
        from app.api.routes.agent import update_agent

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].check_agent_permission = AsyncMock(
            return_value={"can_edit": True}
        )
        services["graph_provider"].get_agent = AsyncMock(
            return_value={"name": "A1", "isServiceAccount": False, "shareWithOrg": False}
        )
        services["graph_provider"].update_agent = AsyncMock(return_value={"_key": "a1"})
        services["graph_provider"].batch_create_edges = AsyncMock(return_value=True)

        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"isServiceAccount":true}')

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            result = await update_agent(request, "a1")

        assert result.status_code == 200
        # The body passed to update_agent should have shareWithOrg forced True
        update_call_body = services["graph_provider"].update_agent.await_args[0][1]
        assert update_call_body.get("shareWithOrg") is True

    @pytest.mark.asyncio
    async def test_get_agents_returns_paginated_response(self) -> None:
        """get_agents returns success with pagination envelope."""
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(
            return_value={"agents": [{"name": "A1"}], "totalItems": 1}
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            result = await get_agents(
                MagicMock(),
                page=1,
                limit=20,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=False,
            )

        assert result.status_code == 200
        body = json.loads(result.body)
        assert body["success"] is True
        assert len(body["agents"]) == 1
        assert body["pagination"]["totalItems"] == 1
        assert body["pagination"]["currentPage"] == 1

    @pytest.mark.asyncio
    async def test_get_agents_returns_list_backward_compat(self) -> None:
        """get_agents handles plain list response from older graph providers."""
        from app.api.routes.agent import get_agents

        services = {"graph_provider": AsyncMock(), "logger": MagicMock()}
        services["graph_provider"].get_all_agents = AsyncMock(
            return_value=[{"name": "A"}, {"name": "B"}]
        )

        with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
             patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "uk1"}):
            result = await get_agents(
                MagicMock(),
                page=1,
                limit=20,
                search=None,
                sort_by="updatedAtTimestamp",
                sort_order="desc",
                is_deleted=False,
            )

        body = json.loads(result.body)
        assert body["pagination"]["totalItems"] == 2
