"""A store outage behind a route's catch-all is answered 503 + Retry-After; a bug of ours there stays the route's 500.

The real route runs with its collaborators stubbed. Whatever it raises is answered by an app carrying the real
``install_request_logging``, whose hand-over maps a raised 500 whose cause is retry-later (request_log.py). So each row
measures the status the client gets for one representative catch-all of each route family.
"""

import json
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.middlewares.request_log import install_request_logging

OUTAGE = ConnectionError("store down")
BUG = KeyError("orgId")

Call = Callable[[BaseException, pytest.MonkeyPatch], Awaitable[object]]


async def _answer(call: Awaitable[object]) -> tuple[int, dict[str, str], object]:
    """The status, headers and body the client gets for a route call, through the real request layer."""
    try:
        result = await call
    except Exception as exc:
        raised = exc
    else:
        return 200, {}, result

    app = FastAPI()
    install_request_logging(app)

    @app.get("/probe")
    async def probe() -> None:
        raise raised

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = response.text
    return response.status_code, dict(response.headers), body


def _raising(exc: BaseException) -> AsyncMock:
    return AsyncMock(side_effect=exc)


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request.json = AsyncMock(return_value={})
    request.scope = {}
    return request


async def _list_connectors(failure: BaseException, _: pytest.MonkeyPatch) -> object:
    from app.connectors.api import router

    request = _request()
    request.app.state.connector_registry.get_all_connector_instances = _raising(failure)
    return await router.get_connector_instances(
        request, scope=None, page=1, limit=20, search=None, is_authenticated=None, is_active=None, connector_type=None,
    )


async def _reindex(failure: BaseException, _: pytest.MonkeyPatch) -> object:
    from app.connectors.api import router

    request = _request()
    request.app.state.connector_registry.get_connector_instance = AsyncMock(
        return_value={"type": "Google Drive", "isActive": True, "name": "Drive"},
    )
    kafka = MagicMock()
    kafka.publish_event = _raising(failure)
    return await router.reindex_connector("c1", request, kafka)


async def _delete_connector(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.connectors.api import router

    request = _request()
    request.app.state.connector_registry.get_connector_instance_for_deletion = AsyncMock(
        return_value={"type": "Google Drive", "createdBy": "u1", "scope": "team", "status": "ACTIVE"},
    )
    monkeypatch.setattr(router, "check_beta_connector_access", AsyncMock())
    monkeypatch.setattr(router, "_validate_connector_deletion_permissions", MagicMock())
    graph = MagicMock()
    graph.check_connector_in_use = _raising(failure)
    return await router.delete_connector_instance("c1", request, graph)


async def _agent_templates(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.api.routes import agent

    monkeypatch.setattr(agent, "get_services", _raising(failure))
    return await agent.get_agent_templates(_request())


async def _agent_versions(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.api.routes import agent

    monkeypatch.setattr(agent, "_agent_access", _raising(failure))
    return await agent.list_agent_versions(_request(), "a1")


async def _record_access(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.api.routes import records

    monkeypatch.setattr(records, "accessible_node_ids", _raising(failure))
    return await records._check_access(_request(), MagicMock(), "r1")


async def _read_thread(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.api.routes import records

    request = _request()
    request.app.container.graph_provider = AsyncMock(return_value=MagicMock())
    request.app.container.blob_store = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(records, "_check_access", AsyncMock(return_value={"r1"}))
    monkeypatch.setattr(records, "_resolve_thread_record_group", _raising(failure))
    return await records.read_record_content(
        "r1", request, mode="thread", query=None, start_block=None, start_record=0, max_chars=8000, extract=False,
    )


async def _get_team(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    """``get_team`` raises its 500 inside ``except`` without ``from``: the implicit ``__context__`` is read too."""
    from app.api.routes import entity

    request = _request()
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"_key": "u1", "userId": "u1"})
    graph.get_team_with_users = _raising(failure)
    request.app.state.graph_provider = graph
    monkeypatch.setattr(entity, "_caller_is_an_administrator", AsyncMock(return_value=True))
    return await entity.get_team(request, "t1")


async def _grep(failure: BaseException, monkeypatch: pytest.MonkeyPatch) -> object:
    from app.api.routes import knowledge_search

    monkeypatch.setattr(knowledge_search, "_user", lambda _request: ("o1", "u1"))
    monkeypatch.setattr(knowledge_search, "check_pattern_match_eligible", AsyncMock(return_value=True))
    monkeypatch.setattr(knowledge_search, "_reachable_scope", _raising(failure))
    return await knowledge_search.grep(_request(), knowledge_search.GrepQuery(pattern="needle"), MagicMock(), MagicMock())


# (route call, a phrase of its catch-all's detail)
ROUTES: dict[str, tuple[Call, str]] = {
    "connectors list": (_list_connectors, "load your connectors"),
    "connector reindex, event bus down": (_reindex, "start reindexing this connector"),
    "connector delete, in-use check": (_delete_connector, "Unable to verify"),
    "agent templates (_route_failure)": (_agent_templates, "load agent templates"),
    "agent versions": (_agent_versions, "load this agent's versions"),
    "record access check": (_record_access, "Failed to check record access"),
    "record thread read": (_read_thread, "Failed to read the thread"),
    "team read (implicit chain)": (_get_team, "Failed to fetch team"),
    "grep": (_grep, "run this search"),
}


@pytest.mark.parametrize("row", sorted(ROUTES))
@pytest.mark.parametrize("outage", [OUTAGE, TimeoutError()], ids=["connection", "timeout"])
async def test_an_outage_behind_the_catch_all_is_answered_503_with_the_routes_detail(
        row: str, outage: BaseException, monkeypatch: pytest.MonkeyPatch) -> None:
    call, detail = ROUTES[row]

    status, headers, body = await _answer(call(outage, monkeypatch))

    assert (status, headers.get("retry-after")) == (503, "5"), body
    assert detail in str(body)


@pytest.mark.parametrize("row", sorted(ROUTES))
async def test_a_bug_behind_the_same_catch_all_stays_its_500(row: str, monkeypatch: pytest.MonkeyPatch) -> None:
    call, detail = ROUTES[row]

    status, headers, body = await _answer(call(BUG, monkeypatch))

    assert status == 500, body
    assert "retry-after" not in headers
    assert detail in str(body), f"the bug reached another branch: {body!r}"
