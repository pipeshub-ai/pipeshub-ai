"""A failed read of a user's record answers 503; only a real miss answers 404.

The graph providers answer a failed read with None unless asked to raise, so
the fakes here do the same: ``_read_fails`` returns None without
``raise_on_error``, which is how these routes used to report an outage as
"User not found" (or, for toolsets, as a 401 that signs the person out).
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.utils.user_lookup import get_org_or_503, get_user_or_503
from app.utils.user_messages import DATABASE_UNREACHABLE


async def _read_fails(user_id: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
    if raise_on_error:
        raise RuntimeError(f"connection refused reading {user_id}")
    return None


async def _document_read_fails(
    key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
) -> dict[str, Any] | None:
    if raise_on_error:
        raise RuntimeError(f"connection refused reading {collection}/{key}")
    return None


def _assert_outage(exc: HTTPException) -> None:
    assert exc.status_code == 503
    assert exc.detail == DATABASE_UNREACHABLE
    assert exc.headers == {"Retry-After": "5"}


class TestGetUserOr503:
    async def test_a_failed_read_is_503(self) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=_read_fails)
        with pytest.raises(HTTPException) as exc:
            await get_user_or_503(graph, "u1")
        _assert_outage(exc.value)
        graph.get_user_by_user_id.assert_awaited_once_with("u1", raise_on_error=True)

    async def test_a_missing_user_is_none(self) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        assert await get_user_or_503(graph, "u1") is None

    async def test_a_found_user_is_returned(self) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "k1"})
        assert await get_user_or_503(graph, "u1") == {"_key": "k1"}


class TestGetOrgOr503:
    async def test_a_failed_read_is_503(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=_document_read_fails)
        with pytest.raises(HTTPException) as exc:
            await get_org_or_503(graph, "org-1")
        _assert_outage(exc.value)
        graph.get_document.assert_awaited_once_with("org-1", CollectionNames.ORGS.value, raise_on_error=True)

    async def test_a_missing_org_is_none(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        assert await get_org_or_503(graph, "org-1") is None


def _team_request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "user-1", "orgId": "org-1"}
    request.app.container.logger.return_value = MagicMock()
    request.app.state.graph_provider = AsyncMock()
    request.app.state.graph_provider.get_user_by_user_id = AsyncMock(side_effect=_read_fails)
    request.app.state.graph_provider.is_write_conflict = MagicMock(return_value=False)
    request.body = AsyncMock(return_value=b'{"name": "Team"}')
    return request


class TestTeamRoutes:
    @pytest.mark.parametrize("route", ["get_team", "update_team", "delete_team", "get_team_users"])
    async def test_a_failed_user_read_is_503(self, route: str) -> None:
        from app.api.routes import entity

        with pytest.raises(HTTPException) as exc:
            await getattr(entity, route)(_team_request(), "team-1")
        _assert_outage(exc.value)

    async def test_create_team(self) -> None:
        from app.api.routes.entity import create_team

        request = _team_request()
        with pytest.raises(HTTPException) as exc:
            await create_team(request)
        _assert_outage(exc.value)
        request.app.state.graph_provider.batch_upsert_nodes.assert_not_called()

    async def test_list_teams(self) -> None:
        from app.api.routes.entity import get_user_teams

        with pytest.raises(HTTPException) as exc:
            await get_user_teams(_team_request(), None, 1, 100, None, None, None)
        _assert_outage(exc.value)

    async def test_a_failed_read_of_the_creator_filter_is_not_a_gone_person(self) -> None:
        """Before, the creator filter's failed read answered 400 "This person was removed"."""
        from app.api.routes.entity import get_user_teams

        async def caller_found(user_id: str, *, raise_on_error: bool = False) -> dict[str, Any] | None:
            if user_id == "user-1":
                return {"_key": "uk1", "orgId": "org-1"}
            return await _read_fails(user_id, raise_on_error=raise_on_error)

        request = _team_request()
        request.app.state.graph_provider.get_user_by_user_id = AsyncMock(side_effect=caller_found)
        with pytest.raises(HTTPException) as exc:
            await get_user_teams(request, None, 1, 100, "creator-1", None, None)
        _assert_outage(exc.value)


class TestAgentAndSkillRoutes:
    async def test_agent_user_document(self) -> None:
        from app.api.routes.agent import _get_user_document

        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=_read_fails)
        with pytest.raises(HTTPException) as exc:
            await _get_user_document("u1", graph, MagicMock())
        _assert_outage(exc.value)

    async def test_agent_org_info(self) -> None:
        from app.api.routes.agent import _get_org_info

        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=_document_read_fails)
        with pytest.raises(HTTPException) as exc:
            await _get_org_info({"orgId": "org-1"}, graph, MagicMock())
        _assert_outage(exc.value)

    async def test_skills_user_key(self) -> None:
        from app.api.routes.skills import _get_user_key

        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=_read_fails)
        with pytest.raises(HTTPException) as exc:
            await _get_user_key("u1", graph, MagicMock())
        _assert_outage(exc.value)


class TestChatAttachmentUpload:
    async def test_a_failed_user_read_is_503(self) -> None:
        from app.api.routes.chatbot import upload_chat_attachments

        request = MagicMock()
        request.json = AsyncMock(return_value={"attachments": [{
            "fileName": "notes.txt",
            "mimeType": "text/plain",
            "size": 5,
            "contentBase64": base64.b64encode(b"hello").decode("ascii"),
        }]})
        request.state.user = {"orgId": "org-1", "userId": "user-1", "isServiceAccount": False}
        request.app.container.logger.return_value = MagicMock()
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=_read_fails)

        with patch("app.api.routes.chatbot.BlobStorage"), \
             patch("app.api.routes.chatbot.PDFPlumberOpenCVProcessor"), \
             pytest.raises(HTTPException) as exc:
            await upload_chat_attachments(request, graph, AsyncMock())
        _assert_outage(exc.value)


class TestConnectorRoutes:
    async def test_knowledge_base_connector_stats(self) -> None:
        from app.connectors.api.connector_resolvers import authorize_connector_stats

        request = MagicMock()
        request.state.user = {"userId": "u1"}
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"_key": "kb-1", "type": "KB", "orgId": "org-1"})
        graph.get_user_by_user_id = AsyncMock(side_effect=_read_fails)
        registry = MagicMock()
        registry.belongs_to_org = AsyncMock(return_value=True)

        with patch("app.connectors.api.connector_resolvers.is_request_admin", return_value=False), \
             pytest.raises(HTTPException) as exc:
            await authorize_connector_stats(request, graph, registry, "kb-1", "org-1")
        _assert_outage(exc.value)
