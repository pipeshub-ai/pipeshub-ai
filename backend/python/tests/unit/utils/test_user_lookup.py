"""A failed read of a user's record answers 503; only a real miss answers 404.

The graph providers answer a failed read with None unless asked to raise, so
the fakes here do the same: ``_read_fails`` returns None without
``raise_on_error``, which is how these routes used to report an outage as
"User not found" (or, for toolsets, as a 401 that signs the person out).
"""

from __future__ import annotations

import base64
import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes, config_node_constants
from app.core.org_context import OrgContext, bind
from app.utils import user_lookup
from app.utils.user_lookup import (
    USER_NODE_ENSURE_PATH,
    connectors_user_node_ensurer,
    get_caller_user,
    get_org_or_503,
    get_user_or_503,
)
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


@pytest.fixture
def ensurer(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    ensure = AsyncMock(return_value=True)
    monkeypatch.setitem(user_lookup._ensurer, "caller_node", ensure)
    return ensure


def _as(user: str | None = "u1", org: str = "org-1", kind: str = "user") -> Any:  # noqa: ANN401
    return bind(OrgContext(org, user, kind, "jwt", "t"))  # type: ignore[arg-type]


class TestGetCallerUser:
    """A caller whose graph user is not written yet (their userAdded event is in flight) is written, then read again;
    only the user the request's verified token names, in that token's org."""

    async def test_a_missing_caller_is_written_in_their_own_org_and_read_again(self, ensurer: AsyncMock) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=[None, {"_key": "k1"}])
        with _as():
            assert await get_caller_user(graph, "u1") == {"_key": "k1"}
        ensurer.assert_awaited_once_with("org-1", "u1")
        assert graph.get_user_by_user_id.await_count == 2

    async def test_a_present_user_is_not_written(self, ensurer: AsyncMock) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "k1"})
        with _as():
            assert await get_caller_user(graph, "u1") == {"_key": "k1"}
        ensurer.assert_not_awaited()

    @pytest.mark.parametrize(
        "context",
        [
            lambda: _as(user="someone-else"),  # the token names another user
            lambda: _as(kind="service"),  # a service token is no one's own request
            lambda: _as(kind="job"),
            contextlib.nullcontext,  # no request bound
        ],
        ids=["another-user", "service-token", "job", "no-request"],
    )
    async def test_only_the_tokens_own_user_is_written(self, ensurer: AsyncMock, context: Any) -> None:  # noqa: ANN401
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        with context():
            assert await get_caller_user(graph, "u1") is None
        ensurer.assert_not_awaited()
        graph.get_user_by_user_id.assert_awaited_once()

    async def test_a_user_the_ensurer_refuses_stays_missing(self, ensurer: AsyncMock) -> None:
        ensurer.return_value = False  # a deleted or disabled account, or a record of another org
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        with _as():
            assert await get_caller_user(graph, "u1") is None
        assert graph.get_user_by_user_id.await_count == 2  # one more read, not a loop

    async def test_a_user_another_writer_wrote_meanwhile_is_found(self, ensurer: AsyncMock) -> None:
        ensurer.return_value = False  # this writer lost, or failed, while the event (or another worker) wrote them
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=[None, {"_key": "k1"}])
        with _as():
            assert await get_caller_user(graph, "u1") == {"_key": "k1"}

    async def test_without_an_ensurer_a_missing_user_stays_missing(self) -> None:
        assert "caller_node" not in user_lookup._ensurer
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value=None)
        with _as():
            assert await get_caller_user(graph, "u1") is None

    async def test_the_routes_lookup_goes_through_it(self, ensurer: AsyncMock) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=[None, {"_key": "k1"}])
        with _as():
            assert await get_user_or_503(graph, "u1") == {"_key": "k1"}
        ensurer.assert_awaited_once_with("org-1", "u1")


_SECRET = "scoped-secret-for-tests-0123456789"


def _query_config(endpoints: dict | None = None) -> AsyncMock:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": _SECRET},
        config_node_constants.ENDPOINTS.value: endpoints if endpoints is not None else {"connectors": {"endpoint": "http://conn:8088/"}},
    }
    config = AsyncMock()
    config.get_config = AsyncMock(side_effect=lambda key, **_: values[key])
    return config


def _connectors(handler: Any) -> Any:  # noqa: ANN401
    real = httpx.AsyncClient
    return patch("app.utils.user_lookup.httpx.AsyncClient", side_effect=lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


class TestConnectorsUserNodeEnsurer:
    """The query service has the connectors service write the caller (it also registers their private knowledge base
    connector, which only it runs)."""

    async def test_asks_for_exactly_that_user_and_org(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"written": True})

        with _connectors(handler):
            assert await connectors_user_node_ensurer(_query_config())("org-1", "u1") is True
        (request,) = seen
        assert (request.method, str(request.url)) == ("POST", f"http://conn:8088{USER_NODE_ENSURE_PATH}")
        claims = jwt.decode(request.headers["authorization"].removeprefix("Bearer "), _SECRET, algorithms=["HS256"])
        assert (claims["orgId"], claims["userId"], claims["scopes"]) == ("org-1", "u1", [TokenScopes.USER_NODE_ENSURE.value])

    @pytest.mark.parametrize("answer", [httpx.Response(200, json={"written": False}), httpx.Response(503), httpx.Response(403)])
    async def test_anything_but_a_write_is_false(self, answer: httpx.Response) -> None:
        with _connectors(lambda _r: answer):
            assert await connectors_user_node_ensurer(_query_config())("org-1", "u1") is False

    async def test_an_unreachable_service_is_false(self) -> None:
        def handler(_r: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with _connectors(handler):
            assert await connectors_user_node_ensurer(_query_config())("org-1", "u1") is False


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


class TestQueryServiceCallerSites:
    """The query service's own caller lookups go through get_caller_user, so a brand-new caller is written (by the
    connectors service) instead of being reported missing."""

    @pytest.mark.parametrize("site", ["access", "gallery"])
    async def test_an_artifact_read_by_a_brand_new_caller_resolves_them(self, ensurer: AsyncMock, site: str) -> None:
        from app.services.artifact_registry.access import AccessPolicy
        from app.services.artifact_registry.gallery import ArtifactGalleryService
        from app.services.artifact_registry.models import Actor

        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(side_effect=[None, {"_key": "k1"}])
        actor = Actor(org_id="org-1", user_id="u1")
        with _as():
            key = await (AccessPolicy(graph).resolve_user_key(actor) if site == "access"
                         else ArtifactGalleryService(graph)._resolve_user_key(actor))
        assert key == "k1"
        ensurer.assert_awaited_once_with("org-1", "u1")
