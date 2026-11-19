"""The graph-view routes: scope per source, no-store on every answer, and failures that are never an empty graph."""
import logging

import pytest

from app.api import connection_limit
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.entity_permissions import EntityAccessError
from app.modules.retrieval.selection_scope import SelectionTooLargeError
from app.telemetry.backend import METRICS_BACKEND
from tests.unit.agents.actions.code_graph.conftest import ORG
from tests.unit.modules.graph_view.world import (
    World,
    client,
    expand,
    personas,
    revalidate,
)

NO_STORE = "private, no-store"
BODY = {"source": "code", "nodeIds": ["block:main"]}


def post(world: World, path: str, body: dict, user: dict | None = None):  # noqa: ANN201
    return client(world, user or {"orgId": ORG, "userId": "bob"}).post(f"/api/v1/graph/{path}", json=body)


@pytest.mark.parametrize("path", ["expand", "revalidate"])
@pytest.mark.parametrize(("user", "status"), [
    ({"orgId": ORG, "userId": "bob"}, 200),                                                    # a dashboard session
    ({"orgId": ORG, "userId": "bob", "isOAuth": True, "oauthScopes": ["code:read"]}, 200),
    ({"orgId": ORG, "userId": "bob", "isOAuth": True, "oauthScopes": ["semantic:read", "connector:read"]}, 403),
    ({"orgId": ORG, "userId": "bob", "isOAuth": True, "oauthScopes": []}, 403),
])
def test_code_needs_code_read_on_an_oauth_token_and_nothing_new(path, user, status) -> None:
    assert post(World(personas()), path, BODY, user).status_code == status


def test_a_service_token_is_refused() -> None:
    user = {"orgId": ORG, "userId": "bob", "token_type": "scoped", "scopes": ["conversation:create"]}
    res = post(World(personas()), "expand", BODY, user)
    assert res.status_code == 403 and res.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize(("path", "body", "status"), [
    ("expand", BODY, 200),
    ("expand", {**BODY, "nodeIds": ["block:nope"]}, 404),                      # hidden or missing
    ("expand", {**BODY, "cursor": "!!"}, 400),
    ("expand", {**BODY, "limit": 0}, 422),
    ("expand", {**BODY, "limit": 101}, 422),
    ("expand", {**BODY, "nodeIds": [f"block:{i}" for i in range(21)]}, 422),   # 20 anchors at most
    ("expand", {**BODY, "nodeIds": []}, 422),
    ("expand", {**BODY, "source": "files"}, 422),
    ("expand", {**BODY, "direction": "sideways"}, 422),
    ("revalidate", BODY, 200),
    ("revalidate", {**BODY, "nodeIds": [f"block:{i}" for i in range(5000)]}, 200),
    ("revalidate", {**BODY, "nodeIds": [f"block:{i}" for i in range(5001)]}, 422),
    ("revalidate", {"source": "code"}, 422),
])
def test_every_answer_carries_cache_control_no_store(path, body, status) -> None:
    res = post(World(personas()), path, body)
    assert res.status_code == status, res.text
    assert res.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize("path", ["expand", "revalidate"])
@pytest.mark.parametrize("failure", [
    PermissionVerificationUnavailableError("graph down: secret-record-id"),
    EntityAccessError("could not resolve secret-record-id"),
    RuntimeError("store exploded secret-record-id"),
])
def test_a_failed_permission_check_is_a_503_never_an_empty_graph(path, failure, caplog) -> None:
    world = World(personas())
    world.fail_access = failure
    with caplog.at_level(logging.DEBUG):
        res = post(world, path, BODY)
    assert res.status_code == 503 and res.json() == {"detail": "Graph unavailable."}
    assert res.headers["cache-control"] == NO_STORE
    assert "secret-record-id" not in caplog.text, "a failure must not put what it was reading into a log"


def test_a_store_error_during_the_walk_is_a_503_too() -> None:
    world = World(personas())
    world.fail_store = RuntimeError("neighbour query failed for block main")
    res = expand(world, "bob", ["main"])
    assert res.status_code == 503 and res.json() == {"detail": "Graph unavailable."}


def test_an_unreadable_build_state_does_not_fail_the_read() -> None:
    world = World(personas())

    async def broken(*a, **k) -> None:
        raise RuntimeError("sync points down")

    world.get_nodes_by_filters = broken
    # The anchors' blocks load through get_nodes_by_field_in, so only the status read fails.
    res = expand(world, "bob", ["main"])
    assert res.status_code == 200 and res.json()["graphStatus"] == "unknown"


def test_a_knowledge_limit_too_large_to_resolve_is_refused_not_ignored(monkeypatch) -> None:
    async def too_large(*a, **k) -> None:
        raise SelectionTooLargeError

    monkeypatch.setattr(connection_limit, "outside_connection", too_large)
    monkeypatch.setattr(connection_limit, "groups_outside_connection", too_large)
    user = {"orgId": ORG, "userId": "bob", "isOAuth": True, "oauthScopes": ["code:read"],
            "knowledgeLimit": {"apps": ["x"]}}
    res = post(World(personas()), "expand", BODY, user)
    assert res.status_code == 422 and res.headers["cache-control"] == NO_STORE


def test_metrics_count_requests_by_outcome_without_naming_anything() -> None:
    METRICS_BACKEND.reset()
    world = World(personas())
    expand(world, "bob", ["main"])
    expand(world, "bob", ["nope"])
    revalidate(world, "bob", ["block:main"])
    out = METRICS_BACKEND.serialize()
    for line in ('pipeshub_graph_view_requests_total{operation="expand",outcome="ok"} 1.0',
                 'pipeshub_graph_view_requests_total{operation="expand",outcome="not_found"} 1.0',
                 'pipeshub_graph_view_requests_total{operation="revalidate",outcome="ok"} 1.0',
                 'pipeshub_graph_view_items_total{kind="ids",operation="revalidate"} 1.0',
                 'pipeshub_graph_view_duration_seconds_count{operation="expand"} 2.0'):
        assert line in out, line
    ours = "\n".join(line for line in out.splitlines() if line.startswith("pipeshub_graph_view"))
    assert "main" not in ours and "bob" not in ours and ORG not in ours
