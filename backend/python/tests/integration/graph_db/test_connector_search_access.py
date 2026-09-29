"""``_get_virtual_ids_for_connector`` against a real Neo4j and a real ArangoDB.

Requires: docker compose -f tests/integration/compose/graph-db.yml up -d
Run: pytest tests/integration/graph_db/ -m integration

This is the set of records a user's search may return from one connector, so
both backends must agree on it. BookStack shares a page with a role as
``User -PERMISSION-> Role -PERMISSION-> Record``; the record page accepted that
on both backends, but Neo4j search followed only a Group there, so the role's
users could open the page and never find it.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-it-search"

_DOC_COLLECTIONS = (
    "users", "records", "recordGroups", "groups", "roles", "organizations", "anyone",
)
_EDGE_COLLECTIONS = ("permission", "belongsTo", "inheritPermissions", "authenticatedAs")
_NEO4J_LABELS = {"users": "User", "records": "Record", "groups": "Group", "roles": "Role"}


def _log() -> logging.Logger:
    from app.utils.logger import create_logger
    return create_logger("connector_search_access_test")


@pytest.fixture(scope="module")
async def neo4j_provider() -> AsyncIterator[Any]:
    pytest.importorskip("neo4j", reason="neo4j driver not installed")
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    uri = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7699")
    password = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")
    logger = _log()
    client = Neo4jClient(
        uri=uri, username="neo4j", password=password, database="neo4j", logger=logger
    )
    try:
        if not await client.connect():
            pytest.skip(f"Neo4j not available at {uri}")
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {uri} — {exc}")

    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = logger
    provider.client = client
    yield provider
    await client.disconnect()


@pytest.fixture(scope="module")
async def arango_provider() -> AsyncIterator[Any]:
    pytest.importorskip("aiohttp", reason="aiohttp not installed")
    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    url = os.environ.get("ARANGO_TEST_URL", "http://localhost:8539")
    password = os.environ.get("ARANGO_TEST_PASSWORD", "testpassword")
    db = os.environ.get("ARANGO_TEST_DB", "es")
    logger = _log()
    client = ArangoHTTPClient(
        base_url=url, username="root", password=password, database=db, logger=logger
    )
    try:
        await _ensure_arango_schema(url, password, db)
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {url} — {exc}")

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logger
    provider.http_client = client
    yield provider
    await client.disconnect()


async def _ensure_arango_schema(url, password, db) -> None:
    """Create the database and every collection the search AQL reads."""
    import aiohttp

    auth = aiohttp.BasicAuth("root", password)
    async with aiohttp.ClientSession(auth=auth) as s:
        async with s.post(f"{url}/_db/_system/_api/database", json={"name": db}) as r:
            if r.status not in (200, 201, 409):
                raise RuntimeError(f"cannot create database {db}: {r.status}")
        wanted = [(n, 2) for n in _DOC_COLLECTIONS] + [(n, 3) for n in _EDGE_COLLECTIONS]
        for name, kind in wanted:
            async with s.post(
                f"{url}/_db/{db}/_api/collection", json={"name": name, "type": kind}
            ) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


class _Graph:
    """A user who reaches one record through each principal, plus records they can't reach."""

    def __init__(self) -> None:
        run = uuid.uuid4().hex[:8]
        self.run = run
        self.user_id = f"{run}-uid"
        self.user_key = f"{run}-ukey"
        self.other_key = f"{run}-other"
        self.app = f"{run}-app"
        self.role = f"{run}-role"
        self.other_role = f"{run}-role-other"
        self.group = f"{run}-group"
        self.ids = {name: f"{run}-{name}" for name in (
            "via_role", "via_group", "direct", "unshared", "other_role", "role_unindexed",
        )}
        self.nodes: list[tuple[str, str, dict]] = [
            ("users", self.user_key, {"userId": self.user_id, "orgId": ORG}),
            ("users", self.other_key, {"userId": f"{run}-uid-other", "orgId": ORG}),
            ("roles", self.role, {"orgId": ORG, "connectorId": self.app}),
            ("roles", self.other_role, {"orgId": ORG, "connectorId": self.app}),
            ("groups", self.group, {"orgId": ORG, "connectorId": self.app}),
        ]
        self.edges: list[tuple[tuple[str, str], tuple[str, str], dict]] = []
        self._grant(("users", self.user_key), ("roles", self.role), "USER")
        self._grant(("users", self.user_key), ("groups", self.group), "USER")
        self._grant(("users", self.other_key), ("roles", self.other_role), "USER")

        ids = self.ids
        for name in ids:
            self._record(ids[name], "FAILED" if name == "role_unindexed" else "COMPLETED")
        self._grant(("roles", self.role), ("records", ids["via_role"]), "ROLE")
        self._grant(("groups", self.group), ("records", ids["via_group"]), "GROUP")
        self._grant(("users", self.user_key), ("records", ids["direct"]), "USER")
        self._grant(("roles", self.other_role), ("records", ids["other_role"]), "ROLE")
        self._grant(("roles", self.role), ("records", ids["role_unindexed"]), "ROLE")

    def vrid(self, name: str) -> str:
        return f"{self.ids[name]}-vr"

    def _grant(self, frm, to, kind) -> None:
        self.edges.append((frm, to, {"type": kind, "role": "READER"}))

    def _record(self, rid, status) -> None:
        self.nodes.append(("records", rid, {
            "orgId": ORG, "connectorId": self.app, "origin": "CONNECTOR",
            "recordName": rid, "isDeleted": False, "indexingStatus": status,
            "virtualRecordId": f"{rid}-vr",
        }))


async def _seed_neo4j(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.client.execute_query(
            f"CREATE (n:{_NEO4J_LABELS[coll]}) SET n = $props",
            parameters={"props": {**props, "id": key, "itRun": g.run}},
        )
    for (fc, fk), (tc, tk), props in g.edges:
        await provider.client.execute_query(
            f"MATCH (a:{_NEO4J_LABELS[fc]} {{id: $f}}), (b:{_NEO4J_LABELS[tc]} {{id: $t}}) "
            "CREATE (a)-[r:PERMISSION]->(b) SET r = $props",
            parameters={"f": fk, "t": tk, "props": props},
        )


async def _clean_neo4j(provider, g: _Graph) -> None:
    await provider.client.execute_query(
        "MATCH (n {itRun: $r}) DETACH DELETE n", parameters={"r": g.run}
    )


async def _seed_arango(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_key: @k, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "k": key, "r": g.run},
        )
    for (fc, fk), (tc, tk), props in g.edges:
        await provider.http_client.execute_aql(
            "INSERT MERGE(@props, {_from: @f, _to: @t, itRun: @r}) INTO permission",
            bind_vars={"props": props, "f": f"{fc}/{fk}", "t": f"{tc}/{tk}", "r": g.run},
        )


async def _clean_arango(provider, g: _Graph) -> None:
    for coll in _DOC_COLLECTIONS + _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itRun == @r REMOVE d IN {coll}",
            bind_vars={"r": g.run},
        )


class _Contract:
    """What one connector's search scope must hold on either backend."""

    seed = None
    clean = None

    async def _with_graph(self, provider, check) -> None:
        g = _Graph()
        await type(self).seed(provider, g)
        try:
            await check(provider, g)
        finally:
            await type(self).clean(provider, g)

    async def test_search_reaches_a_record_shared_with_the_users_role(self, provider) -> None:
        async def check(provider, g) -> None:
            found = await provider._get_virtual_ids_for_connector(
                g.user_id, ORG, g.app, None, raise_on_error=True
            )
            assert found == {
                g.vrid(name): g.ids[name] for name in ("via_role", "via_group", "direct")
            }
        await self._with_graph(provider, check)

    async def test_search_does_not_reach_another_users_role(self, provider) -> None:
        async def check(provider, g) -> None:
            found = await provider._get_virtual_ids_for_connector(
                f"{g.run}-uid-other", ORG, g.app, None, raise_on_error=True
            )
            assert found == {g.vrid("other_role"): g.ids["other_role"]}
        await self._with_graph(provider, check)


class TestNeo4jConnectorSearchAccess(_Contract):
    seed = staticmethod(_seed_neo4j)
    clean = staticmethod(_clean_neo4j)

    @pytest.fixture
    def provider(self, neo4j_provider) -> Any:  # noqa: ANN401 - the provider under test
        return neo4j_provider


class TestArangoConnectorSearchAccess(_Contract):
    seed = staticmethod(_seed_arango)
    clean = staticmethod(_clean_arango)

    @pytest.fixture
    def provider(self, arango_provider) -> Any:  # noqa: ANN401 - the provider under test
        return arango_provider
