"""A graph read that failed is a 503 on the record-content routes, not a 404.

The record and its connector are read by id. Read without ``raise_on_error``, every provider
answers a failed read with None, which these routes turned into 404 "not found": indexing then
marked the file FAILED for good (a 4xx is terminal there) and the UI said the file was gone.
The graph is the real Neo4jProvider with its client failing per query, so one read can fail
while the one before it worked, as a query timeout or a dropped connection does.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import Connectors
from app.connectors.api import router as router_mod
from app.core.org_context import OrgContext, bind
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import RecordType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.user_messages import DATABASE_UNREACHABLE
from tests.support.log_capture import assert_text_free
from tests.support.record_access import InMemoryRecordAccess
from tests.unit.services.graph_db import test_record_access_check_raise as access_stores

RECORD_ID = "rec-1"
ORG_ID = "org-1"
CONNECTOR_ID = "conn-1"
SENTINEL = object()

Route = Callable[[Neo4jProvider], Awaitable[object]]

# A record row without its type document: the provider will not build it and answers None.
_UNBUILDABLE_ROW = [{"record": {"id": RECORD_ID, "recordType": "FILE", "orgId": ORG_ID}, "typeDoc": None}]


class _Down(ConnectionError):
    pass


def _neo4j(*, failing: set[str], record_rows: list | None = None, app_exists: bool = True) -> Neo4jProvider:
    """The real provider; its client fails the reads named in ``failing`` ("record", "org", "app")."""

    async def execute_query(_query: str, parameters: dict[str, Any], **_k: object) -> list:
        read = "record" if "record_id" in parameters else {ORG_ID: "org", CONNECTOR_ID: "app"}[parameters["key"]]
        if read in failing:
            raise _Down("graph down")
        if read == "record":
            return record_rows or []
        if read == "app":
            return [{"n": {"id": CONNECTOR_ID, "type": "DRIVE", "isActive": True}}] if app_exists else []
        return [{"n": {"id": ORG_ID, "accountType": "enterprise"}}]

    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=execute_query)
    InMemoryRecordAccess().readable("u1", ORG_ID, RECORD_ID).serve(p)
    return p


def _with_record(graph: Neo4jProvider) -> Neo4jProvider:
    """The record read works and answers a connector file, so the route goes on to read its connector."""
    record = MagicMock()
    record.id = RECORD_ID
    record.org_id = ORG_ID
    record.connector_id = CONNECTOR_ID
    record.connector_name = Connectors.GOOGLE_DRIVE
    record.record_type = RecordType.FILE
    record.is_placeholder = False
    graph.get_record_by_id = AsyncMock(return_value=record)
    return graph


def _request(user: dict[str, Any]) -> SimpleNamespace:
    container = SimpleNamespace(logger=lambda: MagicMock(), connectors_map={}, config_service=lambda: MagicMock())
    app_ = SimpleNamespace(container=container, state=SimpleNamespace(connector_registry=MagicMock()))
    return SimpleNamespace(state=SimpleNamespace(user=user), app=app_, headers={})


_USER = {"orgId": ORG_ID, "userId": "u1", "token_type": "regular"}
_SERVICE = {"orgId": ORG_ID, "token_type": "scoped", "scopes": ["connector:signedUrl"]}


async def _internal_content(graph: Neo4jProvider) -> object:
    claims = {"orgId": ORG_ID, "userId": "u1"}
    return await router_mod.get_record_content_internal(
        _request(claims), RECORD_ID, version=None, graph_provider=graph, config_service=MagicMock(), claims=claims
    )


async def _signed_url(graph: Neo4jProvider) -> object:
    return await router_mod.get_signed_url(
        _request(_SERVICE), ORG_ID, "u1", "drive", RECORD_ID, signed_url_handler=MagicMock(), graph_provider=graph
    )


async def _stream_internal(graph: Neo4jProvider) -> object:
    return await router_mod.stream_record_internal(
        _request(_SERVICE), RECORD_ID, graph_provider=graph, config_service=MagicMock(), claims=_SERVICE
    )


async def _download(graph: Neo4jProvider) -> object:
    handler = MagicMock()
    handler.validate_token = MagicMock(
        return_value=SimpleNamespace(user_id="u1", record_id=RECORD_ID, additional_claims={"org_id": ORG_ID})
    )
    return await router_mod.download_file(
        _request(_SERVICE), ORG_ID, RECORD_ID, "drive", "tok", signed_url_handler=handler, graph_provider=graph
    )


async def _stream(graph: Neo4jProvider) -> object:
    return await router_mod.stream_record(
        _request(_USER), RECORD_ID, convertTo=None, version=None, graph_provider=graph, config_service=MagicMock()
    )


RECORD_ROUTES = {
    "internal_content": _internal_content,
    "signed_url": _signed_url,
    "stream_internal": _stream_internal,
    "download_file": _download,
    "stream_record": _stream,
}
# The routes that read the record's connector after the record; route names as above.
CONNECTOR_ROUTES = {name: RECORD_ROUTES[name] for name in ("internal_content", "stream_internal", "download_file")}


async def _call(route: Route, graph: Neo4jProvider, *, stop_at_content: bool) -> HTTPException | None:
    with patch.object(router_mod, "refuse_hidden_demo_record", new=AsyncMock(return_value=None)):
        if stop_at_content:
            with patch.object(router_mod, "_resolve_record_content_response", new=AsyncMock(return_value=SENTINEL)):
                return await _refusal(route, graph)
        return await _refusal(route, graph)


async def _refusal(route: Route, graph: Neo4jProvider) -> HTTPException | None:
    try:
        await route(graph)
    except HTTPException as exc:
        return exc
    return None


def _assert_outage(exc: HTTPException | None) -> None:
    assert exc is not None, "the route answered"
    assert (exc.status_code, exc.detail) == (503, DATABASE_UNREACHABLE)
    assert exc.headers["Retry-After"] == "5"
    assert isinstance(exc.__cause__, _Down), "the graph error is kept as the cause"


class TestTheRecordRead:
    @pytest.mark.parametrize("route", RECORD_ROUTES.values(), ids=RECORD_ROUTES.keys())
    async def test_a_failed_record_read_is_503(self, route: Route) -> None:
        """The org read worked and the record read did not."""
        _assert_outage(await _call(route, _neo4j(failing={"record"}), stop_at_content=True))

    @pytest.mark.parametrize("route", RECORD_ROUTES.values(), ids=RECORD_ROUTES.keys())
    async def test_a_full_outage_is_503(self, route: Route) -> None:
        exc = await _call(route, _neo4j(failing={"record", "org", "app"}), stop_at_content=True)
        assert exc is not None
        assert (exc.status_code, exc.detail) == (503, DATABASE_UNREACHABLE)

    @pytest.mark.parametrize("route", RECORD_ROUTES.values(), ids=RECORD_ROUTES.keys())
    async def test_a_missing_record_stays_404(self, route: Route) -> None:
        exc = await _call(route, _neo4j(failing=set()), stop_at_content=True)
        assert exc is not None
        assert (exc.status_code, exc.detail) == (404, "Record not found")

    @pytest.mark.parametrize("route", RECORD_ROUTES.values(), ids=RECORD_ROUTES.keys())
    async def test_a_record_that_will_not_build_stays_404(self, route: Route) -> None:
        """Not an outage: the provider read it and could not make a typed record of it."""
        exc = await _call(route, _neo4j(failing=set(), record_rows=_UNBUILDABLE_ROW), stop_at_content=True)
        assert exc is not None
        assert (exc.status_code, exc.detail) == (404, "Record not found")

    async def test_the_stream_route_keeps_retry_after_and_is_not_cached(self) -> None:
        exc = await _call(_stream, _neo4j(failing={"record"}), stop_at_content=True)
        _assert_outage(exc)
        assert exc.headers["Cache-Control"] == "no-store"


class TestTheConnectorRead:
    @pytest.mark.parametrize("route", CONNECTOR_ROUTES.values(), ids=CONNECTOR_ROUTES.keys())
    async def test_a_failed_connector_read_is_503(self, route: Route) -> None:
        """The record was read; reading its connector failed. Indexing must retry, not fail the file."""
        _assert_outage(await _call(route, _with_record(_neo4j(failing={"app"})), stop_at_content=False))

    @pytest.mark.parametrize("route", CONNECTOR_ROUTES.values(), ids=CONNECTOR_ROUTES.keys())
    async def test_a_deleted_connector_stays_404(self, route: Route) -> None:
        exc = await _call(route, _with_record(_neo4j(failing=set(), app_exists=False)), stop_at_content=False)
        assert exc is not None
        assert exc.status_code == 404
        assert "no longer exists" in exc.detail


class TestTheOpenDetailsCall:
    """``GET /api/v1/records/{id}`` with ``X-Record-Content-Open: 1`` refuses an open the stream would refuse."""

    _DOC = {"origin": "CONNECTOR", "recordType": "FILE", "connectorName": "DRIVE", "connectorId": CONNECTOR_ID}

    async def test_a_failed_connector_read_is_503(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await router_mod._refuse_connector_state_for_open(_neo4j(failing={"app"}), self._DOC)
        _assert_outage(exc.value)

    async def test_a_deleted_connector_stays_404(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await router_mod._refuse_connector_state_for_open(_neo4j(failing=set(), app_exists=False), self._DOC)
        assert exc.value.status_code == 404


# ---- The access check ------------------------------------------------------------------------------------------------
# ``check_record_access_with_details`` answers None for "may not open" and raises a read it could not make. Each route
# that calls it answers the raise with the same 503 as a failed record read, and keeps its 403/404 for None.


async def _signed_url_as_user(graph: Neo4jProvider) -> object:
    """A session caller: unlike the indexing service token, it goes through the access check."""
    return await router_mod.get_signed_url(
        _request(_USER), ORG_ID, "u1", "drive", RECORD_ID, signed_url_handler=MagicMock(), graph_provider=graph
    )


async def _download_as_user(graph: Neo4jProvider) -> object:
    handler = MagicMock()
    handler.validate_token = MagicMock(
        return_value=SimpleNamespace(user_id="u1", record_id=RECORD_ID, additional_claims={"org_id": ORG_ID})
    )
    return await router_mod.download_file(
        _request(_USER), ORG_ID, RECORD_ID, "drive", "tok", signed_url_handler=handler, graph_provider=graph
    )


async def _record_details(graph: Neo4jProvider) -> object:
    return await router_mod.get_record_by_id(RECORD_ID, _request(_USER), graph_provider=graph, x_record_content_open=None)


async def _delete(graph: Neo4jProvider) -> object:
    return await router_mod.delete_record(RECORD_ID, _request(_USER), graph_provider=graph, kafka_service=AsyncMock())


# Route name -> (route, the status a refused check answers).
ACL_ROUTES: dict[str, tuple[Route, int]] = {
    "internal_content": (_internal_content, 403),
    "signed_url": (_signed_url_as_user, 404),
    "download_file": (_download_as_user, 404),
    "stream_record": (_stream, 403),
    "record_details": (_record_details, 404),
    "delete_record": (_delete, 404),
}


def _capture_check() -> None:
    """One known line, so a route that rightly logs nothing still gives ``assert_text_free`` records to search."""
    logging.getLogger(__name__).debug("log capture is on")


def _checked(check: object) -> Neo4jProvider:
    access = InMemoryRecordAccess().user("u1", ORG_ID).record(RECORD_ID, ORG_ID)
    if isinstance(check, Exception):
        access.fail_with(check)
    elif check is not None:
        access.grant(RECORD_ID, user="u1", role="OWNER")
    return access.serve(_with_record(_neo4j(failing=set())))


class TestTheAccessCheck:
    @pytest.mark.parametrize("error", [_Down("graph down"), PermissionVerificationUnavailableError()],
                             ids=["read_failed", "check_unavailable"])
    @pytest.mark.parametrize("route", [r for r, _ in ACL_ROUTES.values()], ids=ACL_ROUTES.keys())
    async def test_a_check_that_could_not_be_made_is_503(
        self, route: Route, error: Exception, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            _capture_check()
            exc = await _call(route, _checked(error), stop_at_content=True)
        assert_text_free(caplog.records, "graph down")
        assert exc is not None, "the route answered"
        assert (exc.status_code, exc.detail) == (503, DATABASE_UNREACHABLE)
        assert exc.headers["Retry-After"] == "5"
        assert exc.__cause__ is error, "the graph error is kept as the cause"

    @pytest.mark.parametrize(("route", "status"), ACL_ROUTES.values(), ids=ACL_ROUTES.keys())
    async def test_a_refused_check_keeps_its_answer(self, route: Route, status: int) -> None:
        exc = await _call(route, _checked(None), stop_at_content=True)
        assert exc is not None
        assert exc.status_code == status

    async def test_the_stream_route_is_not_cached(self) -> None:
        exc = await _call(_stream, _checked(_Down("graph down")), stop_at_content=True)
        assert exc is not None
        assert exc.status_code == 503
        assert exc.headers["Cache-Control"] == "no-store"


class TestTheWholeChain:
    """The real provider of each store, one read inside the access check failing, through the route."""

    @pytest.mark.parametrize("route", [_stream, _record_details], ids=["stream_record", "record_details"])
    @pytest.mark.parametrize("store", ["arango", "neo4j", "postgres"])
    async def test_a_failed_read_in_the_access_check_is_503(
        self, store: str, route: Route, caplog: pytest.LogCaptureFixture,
    ) -> None:
        graph = access_stores.STORES[store](access_stores._Graph({"user"}))
        graph.get_record_by_id = AsyncMock(return_value=_with_record(MagicMock()).get_record_by_id.return_value)
        graph.get_document = AsyncMock(return_value={"id": ORG_ID})       # the route's own org read
        with bind(OrgContext(ORG_ID, "u1", "user", "user:u1", "t")), caplog.at_level(logging.DEBUG):
            _capture_check()
            exc = await _call(route, graph, stop_at_content=True)
        assert_text_free(caplog.records, "10.0.0.7", "graph down")
        assert exc is not None, "the route answered"
        assert (exc.status_code, exc.detail) == (503, DATABASE_UNREACHABLE)
        assert isinstance(exc.__cause__, access_stores._Down)
