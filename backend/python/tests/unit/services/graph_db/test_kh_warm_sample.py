"""The plan keeper's samples on Neo4j: per App, a user and a place that user may browse (the interface's contract).

A sample the user may not open is refused as "Node not found" (knowledge_hub_service.py) and plans nothing, so the
keeper would warm no group or record browse at all. The access check decides, as it decides every browse; the
browse's own navigation rule (hidden groups) keeps out places the check admits but a browse never lists.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from app.core import org_context
from app.services.graph_db.neo4j import kh_scope
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _provider(rows: list[dict[str, Any]], admitted: dict[str, set[str]]) -> tuple[Neo4jProvider, list[tuple[str, set[str]]]]:
    """``admitted``: user key -> the node ids the access check admits for that user."""
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_warm_sample")
    asked: list[tuple[str, set[str]]] = []
    statements: list[tuple[str, dict]] = []

    async def execute_query(query: str, parameters: dict | None = None, **_k: Any) -> list[dict]:  # noqa: ANN401
        statements.append((query, parameters or {}))
        return rows

    async def check_access(user_key: str, org_id: str, *, node_ids: Any = (), **_k: Any) -> SimpleNamespace:  # noqa: ANN401
        ids = set(node_ids)
        asked.append((user_key, ids))
        return SimpleNamespace(node_ids=frozenset(ids & admitted.get(user_key, set())))

    p.client = SimpleNamespace(execute_query=execute_query)
    p.check_access = check_access  # type: ignore[method-assign]
    p._statements = statements  # type: ignore[attr-defined]
    return p, asked


def _row(app: str, users: list[str], places: list[tuple[str, str]]) -> dict[str, Any]:
    return {"appId": app, "appOrg": "org-1", "users": [{"userId": f"uid-{u}", "key": u} for u in users],
            "places": [{"groupId": g, "recordId": r} for g, r in places]}


@pytest.mark.asyncio
async def test_each_sample_is_a_place_its_user_may_open() -> None:
    p, asked = _provider([_row("jira", ["u1"], [("g1", "r1"), ("g2", "r2")])], {"u1": {"g2", "r2", "g1"}})
    assert await p.get_knowledge_hub_warm_samples() == [
        {"userId": "uid-u1", "orgId": "org-1", "appId": "jira", "groupId": "g2", "recordId": "r2"}]
    assert asked == [("u1", {"g1", "r1", "g2", "r2"})]      # one check covers all of an App's places


@pytest.mark.asyncio
async def test_a_user_who_may_open_none_of_the_places_is_passed_over() -> None:
    p, asked = _provider([_row("jira", ["u1", "u2", "u3"], [("g1", "r1")])], {"u2": {"g1", "r1"}, "u3": {"g1", "r1"}})
    assert [s["userId"] for s in await p.get_knowledge_hub_warm_samples()] == ["uid-u2"]
    assert [user for user, _ in asked] == ["u1", "u2"]      # stops at the first user admitted


@pytest.mark.asyncio
async def test_one_sample_per_app_in_order_and_none_for_an_app_nobody_may_open() -> None:
    rows = [_row("confluence", ["u1"], [("g1", "r1")]), _row("jira", ["u1"], [("g9", "r9")]),
            _row("notion", ["u2"], [("g5", "r5")])]
    p, _ = _provider(rows, {"u1": {"g9", "r9"}, "u2": {"g5", "r5"}})
    assert [s["appId"] for s in await p.get_knowledge_hub_warm_samples()] == ["jira", "notion"]


@pytest.mark.asyncio
async def test_no_samples_when_no_user_may_open_any_place() -> None:
    p, _ = _provider([_row("jira", ["u1"], [("g1", "r1")])], {})
    assert await p.get_knowledge_hub_warm_samples() == []


@pytest.mark.asyncio
async def test_the_candidates_are_real_users_and_places_a_browse_lists() -> None:
    p, _ = _provider([], {})
    assert await p.get_knowledge_hub_warm_samples() == []
    (query, params), = p._statements  # type: ignore[attr-defined]
    # A source's synced accounts are org members marked inactive, and never sign in.
    assert "coalesce(u.isActive, true)" in query and "(u)-[:BELONGS_TO]->(:Organization {id: a.orgId})" in query
    # Only hierarchy edges make a browse place; a group that carries access only is never one.
    assert query.count("relationshipType = 'PARENT_CHILD'") == 2 and "NOT coalesce(g.accessOnly, false)" in query
    # The browse's navigation rule, as the browse spells it: no hiding group holds either node or lies above it.
    for node, tag in (("g", "g"), ("r", "r")):
        assert Neo4jProvider._kh_v3_hidden_above(node, tag) in query
        assert Neo4jProvider._kh_v3_in_hidden_group(node, f"{tag}m") in query
    # An App with no candidate spends none of the App budget: the limit comes after that test.
    assert query.index("size(users) > 0 AND size(places) > 0") < query.index("LIMIT $apps")
    assert params == {"apps": Neo4jProvider._KH_WARM_APPS, "users": Neo4jProvider._KH_WARM_USERS,
                      "places": Neo4jProvider._KH_WARM_PLACES}


@pytest.mark.asyncio
async def test_each_access_check_runs_in_its_apps_org() -> None:
    rows = [{**_row("jira", ["u1"], [("g1", "r1")]), "appOrg": org} for org in ("org-1", "org-2")]
    p, _ = _provider(rows, {"u1": {"g1", "r1"}})
    bound: list[tuple[str, str]] = []
    real = p.check_access

    async def check_access(user_key: str, org_id: str, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        bound.append((org_id, org_context.current_org_id()))
        return await real(user_key, org_id, **kwargs)

    p.check_access = check_access  # type: ignore[method-assign]
    assert len(await p.get_knowledge_hub_warm_samples()) == 2
    assert bound == [("org-1", "org-1"), ("org-2", "org-2")]
    assert org_context.peek() is None


@pytest.mark.asyncio
async def test_the_stale_scopes_of_one_org_only_are_listed() -> None:
    asked: list[tuple[str, dict]] = []

    async def execute_query(query: str, parameters: dict | None = None, **_k: Any) -> list[dict]:  # noqa: ANN401
        asked.append((query, parameters or {}))
        return [{"id": "a1"}]

    assert await kh_scope.stale_connectors(SimpleNamespace(execute_query=execute_query), "org-1") == ["a1"]
    (query, params), = asked
    assert "coalesce(a.orgId, $org) = $org" in query and params["org"] == "org-1"
