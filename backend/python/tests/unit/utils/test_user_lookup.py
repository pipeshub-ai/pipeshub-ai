"""A failed read of a user's record answers 503; only a real miss answers 404.

The graph providers answer a failed read with None unless asked to raise, so
the fakes here do the same: ``_read_fails`` returns None without
``raise_on_error``, which is how these routes used to report an outage as
"User not found" (or, for toolsets, as a 401 that signs the person out).
"""

from __future__ import annotations

import base64
import contextlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt
import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import TokenScopes, config_node_constants
from app.core.org_context import OrgContext, bind
from app.utils import error_class, user_lookup
from app.utils.logger import log_record_fields
from app.utils.user_lookup import (
    USER_NODE_ENSURE_PATH,
    check_record_access_or_503,
    connectors_user_node_ensurer,
    get_caller_user,
    get_document_or_503,
    get_org_or_503,
    get_record_or_503,
    get_user_or_503,
)
from app.utils.user_messages import DATABASE_UNREACHABLE
from tests.support.graph_read_errors import (
    GRAPH_KEY,
    SENTINELS,
    marked_internal,
    unmarked,
)
from tests.support.log_capture import assert_text_free
from tests.support.record_access import InMemoryRecordAccess


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


@pytest.mark.parametrize("call", ["user", "document", "record"])
async def test_a_failed_read_is_logged_without_the_drivers_text(call: str, caplog: pytest.LogCaptureFixture) -> None:
    """The 503's cause is stamped: a driver's text can quote what was sent (an email, a value)."""
    seeded = "SENTINEL-driver-text alice@" + "example.com"
    graph = AsyncMock()
    graph.get_user_by_user_id = graph.get_document = graph.get_record_by_id = AsyncMock(side_effect=ConnectionError(seeded))
    read = {
        "user": lambda: get_user_or_503(graph, "u1"),
        "document": lambda: get_document_or_503(graph, "k1", CollectionNames.APPS.value),
        "record": lambda: get_record_or_503(graph, "r1"),
    }[call]
    with pytest.raises(HTTPException) as exc:
        await read()
    _assert_outage(exc.value)
    error_class.log_failure(logging.getLogger("test.user_lookup"), "x.end", exc.value)
    assert [r.getMessage() for r in caplog.records] == ["x.end"]
    assert_text_free(caplog.records, seeded)


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


class TestGetDocumentOr503:
    async def test_a_failed_read_is_503(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=_document_read_fails)
        with pytest.raises(HTTPException) as exc:
            await get_document_or_503(graph, "conn-1", CollectionNames.APPS.value)
        _assert_outage(exc.value)
        assert isinstance(exc.value.__cause__, RuntimeError)
        graph.get_document.assert_awaited_once_with("conn-1", CollectionNames.APPS.value, raise_on_error=True)

    async def test_a_missing_document_is_none(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value=None)
        assert await get_document_or_503(graph, "conn-1", CollectionNames.APPS.value) is None

    async def test_a_document_is_returned_as_read(self) -> None:
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"_key": "conn-1"})
        assert await get_document_or_503(graph, "conn-1", CollectionNames.APPS.value) == {"_key": "conn-1"}


async def _record_read_fails(record_id: str, transaction: str | None = None, *, raise_on_error: bool = False) -> None:
    if raise_on_error:
        raise RuntimeError(f"connection refused reading {record_id}")


class TestGetRecordOr503:
    async def test_a_failed_read_is_503(self) -> None:
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(side_effect=_record_read_fails)
        with pytest.raises(HTTPException) as exc:
            await get_record_or_503(graph, "rec-1")
        _assert_outage(exc.value)
        assert isinstance(exc.value.__cause__, RuntimeError)
        graph.get_record_by_id.assert_awaited_once_with("rec-1", raise_on_error=True)

    async def test_a_missing_record_is_none(self) -> None:
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(return_value=None)
        assert await get_record_or_503(graph, "rec-1") is None

    async def test_a_record_is_returned_as_read(self) -> None:
        record = MagicMock()
        graph = AsyncMock()
        graph.get_record_by_id = AsyncMock(return_value=record)
        assert await get_record_or_503(graph, "rec-1") is record


class TestCheckRecordAccessOr503:
    async def test_a_failed_check_is_503(self) -> None:
        failure = ConnectionError("graph down")
        access = InMemoryRecordAccess().fail_with(failure)
        graph = access.serve(AsyncMock())
        with pytest.raises(HTTPException) as exc:
            await check_record_access_or_503(graph, "user-1", "org-1", "rec-1")
        _assert_outage(exc.value)
        assert exc.value.__cause__ is failure
        assert access.asked_about == [("user-1", "org-1", "rec-1")]

    async def test_no_access_is_none(self) -> None:
        graph = InMemoryRecordAccess().user("user-1", "org-1").record("rec-1", "org-1").serve(AsyncMock())
        assert await check_record_access_or_503(graph, "user-1", "org-1", "rec-1") is None

    async def test_the_details_are_returned_as_read(self) -> None:
        graph = InMemoryRecordAccess().readable("user-1", "org-1", "rec-1").serve(AsyncMock())
        details = await check_record_access_or_503(graph, "user-1", "org-1", "rec-1")
        assert details is not None and details["record"]["id"] == "rec-1"


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


# ---- The request log of a 503: the graph cause's class and type, never its text ------------------------------------

def _helper_app(read: Any) -> Any:  # noqa: ANN401
    from fastapi import FastAPI

    from app.api.middlewares.request_log import install_request_logging

    api = FastAPI()
    install_request_logging(api)

    @api.get("/api/v1/records/{record_id}")
    async def record(record_id: str) -> dict:
        return await read(record_id) or {}

    return api


async def _request_end(api: Any, caplog: pytest.LogCaptureFixture) -> tuple[int, Any]:  # noqa: ANN401
    import asyncio
    import logging

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=api, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/api/v1/records/rec-1")

    with caplog.at_level(logging.DEBUG):
        response = await asyncio.create_task(call())
    (end,) = [r for r in caplog.records if r.getMessage() == "http.request.end"]
    return response.status_code, end


def _failing_graph(error: Exception) -> MagicMock:
    graph = MagicMock()
    for name in ("get_user_by_user_id", "get_document", "get_record_by_id", "check_record_access_with_details"):
        setattr(graph, name, AsyncMock(side_effect=error))
    return graph


HELPERS = {
    "get_user_or_503": lambda g: lambda rid: get_user_or_503(g, "u1"),
    "get_org_or_503": lambda g: lambda rid: get_org_or_503(g, "org-1"),
    "get_document_or_503": lambda g: lambda rid: get_document_or_503(g, rid, CollectionNames.RECORDS.value),
    "get_record_or_503": lambda g: lambda rid: get_record_or_503(g, rid),
    "check_record_access_or_503": lambda g: lambda rid: check_record_access_or_503(g, "u1", "org-1", rid),
}


class TestTheRequestLogOfA503:
    # Only the unrecorded arm guards the helper's stamp: a client-recorded error is text-hidden already.
    @pytest.mark.parametrize(("error", "error_class"), [(unmarked, "unavailable"), (marked_internal, "internal")],
                             ids=["unrecorded", "client_recorded"])
    @pytest.mark.parametrize("helper", HELPERS)
    async def test_carries_the_class_and_type_never_the_text(
        self, helper: str, error: Any, error_class: str, caplog: pytest.LogCaptureFixture,  # noqa: ANN401
    ) -> None:
        cause = error()
        status, end = await _request_end(_helper_app(HELPERS[helper](_failing_graph(cause))), caplog)
        assert status == 503
        fields = log_record_fields(end)
        assert (fields["error_class"], fields["cause_type"]) == (error_class, type(cause).__name__)
        assert "error_message" not in fields
        assert_text_free([end], *SENTINELS)

    async def test_an_arango_strict_read_that_failed_names_no_graph_key(self, caplog: pytest.LogCaptureFixture) -> None:
        """The real client's own GraphQueryError ("Could not read records/<key>: …") is raised after its record."""
        import logging

        from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient

        client = ArangoHTTPClient("http://localhost:8529", "root", "pw", "db", logging.getLogger("test.user_lookup.arango"))
        resp = MagicMock(status=503)
        resp.text = AsyncMock(return_value="restarting")
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = ctx
        client._get_session = AsyncMock(return_value=session)
        graph = MagicMock()

        async def get_document(
            key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
        ) -> dict | None:
            return await client.get_document(collection, GRAPH_KEY, transaction, raise_on_error=raise_on_error)

        graph.get_document = get_document
        status, end = await _request_end(_helper_app(HELPERS["get_document_or_503"](graph)), caplog)
        assert status == 503
        assert log_record_fields(end)["cause_type"] == "GraphQueryError"
        assert_text_free([end], GRAPH_KEY)

    async def test_the_503_keeps_its_cause_and_headers(self) -> None:
        cause = unmarked()
        with pytest.raises(HTTPException) as raised:
            await get_document_or_503(_failing_graph(cause), "rec-1", CollectionNames.RECORDS.value)
        assert raised.value.__cause__ is cause
        assert (raised.value.status_code, raised.value.detail) == (503, DATABASE_UNREACHABLE)
        assert raised.value.headers["Retry-After"] == "5"
