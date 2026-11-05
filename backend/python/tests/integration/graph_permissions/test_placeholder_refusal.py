"""A placeholder record is refused like a record the viewer cannot read, wherever a record's content or metadata is served.

Both backends, real containers. Graph (one App with record-group level permissions):

  App ph-app
    -> ph-group  RECORD_GROUP_LEVEL, granted to U
         -> ph-stub  placeholder TICKET (reached by id, as listings show it)
         -> ph-real  real TICKET

W has the app but no grant.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import app.edition_config  # (binds the edition seams before the routers load)
from app.api.routes import records as records_routes
from app.connectors.api import router as connector_router
from app.utils.chat_helpers import accessible_node_ids
from tests.integration.graph_permissions.fixture_graph import (
    ORG,
    _node,
    app,
    bt,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from tests.integration.graph_permissions.loaders import (
    arango_shape,
    load_into_arango,
    load_into_neo4j,
)

pytestmark = pytest.mark.integration

U, W, APP = "ph-u", "ph-w", "ph-app"
STUB, REAL = "ph-stub", "ph-real"


def _graph() -> tuple[list, list]:
    ticket = {"record_type": "TICKET", "connector_id": APP}
    nodes = [
        _node("User", U, userId="ph-u-ext", email="ph-u@example.com", fullName="U", isActive=True),
        _node("User", W, userId="ph-w-ext", email="ph-w@example.com", fullName="W", isActive=True),
        app(APP, "GitHub", connector="GITHUB", app_group="GitHub"),
        rg("ph-group", "Issues", group_type="PROJECT", connector="GITHUB", connectorId=APP,
           permissionModel="RECORD_GROUP_LEVEL"),
        rec(STUB, "A placeholder", isPlaceholder=True, rule="RESTRICTED", indexingStatus="AUTO_INDEX_OFF", **ticket),
        rec(REAL, "A real issue", virtualRecordId="vr-real", indexingStatus="COMPLETED", **ticket),
    ]
    edges = [
        user_app(U, APP), user_app(W, APP),
        nr(APP, "ph-group"), perm(U, "ph-group"),
        nr("ph-group", STUB), bt(STUB, "ph-group"),
        nr("ph-group", REAL), bt(REAL, "ph-group"),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


def _request(user_ext: str) -> SimpleNamespace:
    container = MagicMock()
    container.logger.return_value = MagicMock()
    return SimpleNamespace(
        state=SimpleNamespace(user={"userId": user_ext, "orgId": ORG}),
        app=SimpleNamespace(container=container),
    )


async def _refusal(call) -> tuple[int, object]:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value.status_code, refused.value.detail


async def test_the_stub_is_listed_as_reachable_but_not_as_readable(graph, provider) -> None:
    check = await provider.check_access(U, ORG, node_ids=[STUB, REAL])
    assert check.node_ids == {STUB, REAL} and check.placeholder_ids == {STUB}  # browsing keeps listing it
    assert await accessible_node_ids(provider, {STUB, REAL}, "ph-u-ext", ORG) == {REAL}
    assert await accessible_node_ids(provider, {STUB, REAL}, "ph-u-ext", ORG, with_placeholders=True) == {STUB, REAL}


async def test_the_record_check_refuses_the_stub_and_serves_the_real_record(graph, provider) -> None:
    assert await provider.check_record_access_with_details("ph-u-ext", ORG, STUB) is None
    assert await provider.check_record_access_with_details("ph-w-ext", ORG, STUB) is None
    details = await provider.check_record_access_with_details("ph-u-ext", ORG, REAL)
    assert details and details["record"]["recordName"] == "A real issue"


async def test_the_record_routes_refuse_the_stub_like_an_unreadable_record(graph, provider) -> None:
    # The unreadable record is the real one, asked by the viewer who has no grant.
    for route in (
        lambda who, rid: connector_router.get_record_by_id(rid, _request(who), graph_provider=provider),
        lambda who, rid: connector_router.get_record_content(rid, _request(who), graph_provider=provider),
        lambda who, rid: records_routes._check_access(_request(who), provider, rid),
    ):
        stub_refusal = await _refusal(route("ph-u-ext", STUB))
        unreadable_refusal = await _refusal(route("ph-w-ext", REAL))
        assert stub_refusal == unreadable_refusal
