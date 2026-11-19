"""The record groups a user reaches through inheritance stay inside the connector of the group they were granted, on Neo4j and ArangoDB.

The connectors record a group's inheritance only within the record's own connector, so a cross-connector edge is a shape no writer
emits; the walk must still not follow one. (PostgreSQL: ``graph_db/postgres/test_entity_access.py``.)
"""

from __future__ import annotations

import pytest

from .fixture_graph import CONFLUENCE, DRIVE, ORG, _node, app, ip, perm, rg, user_app
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

USER, MEMBER = "eaw-u", "eaw-m"


def _graph() -> tuple[list[dict], list[dict]]:
    """A grant on ``seed`` (connector a); ``same`` (a) inherits from it, ``other`` (connector b, which the user also reaches) too,
    and ``beyond`` (a) inherits from ``other``."""
    group = {"group_type": "DRIVE", "connector": DRIVE}
    nodes = [
        _node("User", USER, userId=MEMBER, email="eaw@example.com", fullName="EAW", isActive=True),
        app("eaw-a", "Drive", connector=DRIVE, app_group="Google Workspace"),
        app("eaw-b", "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        rg("eaw-seed", "seed", connectorId="eaw-a", **group),
        rg("eaw-same", "same", connectorId="eaw-a", **group),
        rg("eaw-other", "other", connectorId="eaw-b", **group),
        rg("eaw-beyond", "beyond", connectorId="eaw-a", **group),
    ]
    edges = [
        user_app(USER, "eaw-a"), user_app(USER, "eaw-b"), perm(USER, "eaw-seed"),
        ip("eaw-same", "eaw-seed"), ip("eaw-other", "eaw-seed"), ip("eaw-beyond", "eaw-other"),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def loaded(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, nodes, edges)
    return {"neo4j": neo4j_provider, "arango": arango_provider}


@pytest.mark.parametrize("backend", ["neo4j", "arango"])
async def test_the_walk_stays_inside_the_seeds_connector(loaded, backend) -> None:
    context = await loaded[backend].get_entity_access_context(MEMBER, ORG)
    assert {a["id"] for a in context["apps"]} == {"eaw-a", "eaw-b"}
    assert sorted(context["record_group_ids"]) == ["eaw-same", "eaw-seed"]
