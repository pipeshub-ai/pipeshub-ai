"""``filter_accessible_record_ids`` against a real Neo4j and a real ArangoDB.

Requires: docker compose -f tests/integration/compose/graph-db.yml up -d
Run: pytest tests/integration/graph_db/ -m integration

This gates what graph enrichment shows the LLM about records linked to a search
hit, so both backends must return the same readable set: a divergence means one
deployment discloses a record the other hides. One graph is seeded per test with
a record for each gate — readable by group inheritance, readable but unindexed,
unreadable, placeholder, other org, soft-deleted, and on a connector the user
cannot reach.
"""

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-it"
OTHER_ORG = "org-it-other"

_DOC_COLLECTIONS = (
    "users", "records", "recordGroups", "apps", "teams", "groups", "roles", "organizations",
)
_EDGE_COLLECTIONS = (
    "permission", "belongsTo", "inheritPermissions", "userAppRelation",
    "authenticatedAs", "recordRelations",
)
_NEO4J_LABELS = {
    "users": "User", "records": "Record", "recordGroups": "RecordGroup",
    "apps": "App", "groups": "Group",
}
_NEO4J_EDGES = {
    "permission": "PERMISSION", "inheritPermissions": "INHERIT_PERMISSIONS",
    "userAppRelation": "USER_APP_RELATION", "recordRelations": "RECORD_RELATION",
}


def _log():
    from app.utils.logger import create_logger
    return create_logger("accessible_record_relations_test")


@pytest.fixture(scope="module")
async def neo4j_provider():
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
async def arango_provider():
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
    """Create the database and every collection the permission AQL reads."""
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
    """One seeded graph: ids by role, plus the nodes and edges to write."""

    def __init__(self) -> None:
        run = uuid.uuid4().hex[:8]
        self.run = run
        self.user_id = f"{run}-uid"
        self.user_key = f"{run}-ukey"
        self.app = f"{run}-app"
        self.lost_app = f"{run}-app-lost"
        self.group = f"{run}-group"
        self.space = f"{run}-space"
        self.ids = {name: f"{run}-{name}" for name in (
            "epic", "parent", "attachment", "child_group", "child_unindexed",
            "child_no_ts", "child_denied", "child_placeholder", "child_other_org",
            "child_deleted", "child_lost_connector",
        )}
        self.nodes: list[tuple[str, str, dict]] = []
        self.edges: list[tuple[str, tuple[str, str], tuple[str, str], dict]] = []

        self.nodes += [
            ("users", self.user_key, {"userId": self.user_id, "orgId": ORG}),
            ("apps", self.app, {"type": "JIRA"}),
            ("apps", self.lost_app, {"type": "JIRA"}),
            ("groups", self.group, {"orgId": ORG}),
            ("recordGroups", self.space, {"orgId": ORG}),
        ]
        self._edge("userAppRelation", ("users", self.user_key), ("apps", self.app))
        self._edge("permission", ("users", self.user_key), ("groups", self.group),
                   type="USER", role="READER")
        self._edge("permission", ("groups", self.group), ("recordGroups", self.space),
                   type="GROUP", role="READER")

        ids = self.ids
        self._record(ids["epic"], 100, direct=True)
        self._record(ids["parent"], 50, direct=True)
        self._record(ids["attachment"], 10, direct=True)
        self._record(ids["child_group"], 300)
        self._edge("inheritPermissions", ("records", ids["child_group"]),
                   ("recordGroups", self.space))
        self._record(ids["child_unindexed"], 200, direct=True, indexingStatus="FAILED")
        self._record(ids["child_no_ts"], None, direct=True)
        # Newest of all: if denial happened after the limit it would take a slot.
        self._record(ids["child_denied"], 500)
        self._record(ids["child_placeholder"], 400, direct=True, isPlaceholder=True)
        self._record(ids["child_other_org"], 450, direct=True, orgId=OTHER_ORG)
        self._record(ids["child_deleted"], 460, direct=True, isDeleted=True)
        self._record(ids["child_lost_connector"], 470, direct=True, connectorId=self.lost_app)

        self._relation(ids["parent"], ids["epic"], "PARENT_CHILD")
        self._relation(ids["epic"], ids["attachment"], "ATTACHMENT")
        for name in ("child_group", "child_unindexed", "child_no_ts", "child_denied",
                     "child_placeholder", "child_other_org", "child_deleted",
                     "child_lost_connector"):
            self._relation(ids["epic"], ids[name], "PARENT_CHILD")

    def _edge(self, coll, frm, to, **props) -> None:
        self.edges.append((coll, frm, to, props))

    def _relation(self, frm, to, relation_type) -> None:
        self._edge("recordRelations", ("records", frm), ("records", to),
                   relationshipType=relation_type)

    def _record(self, rid, modified_at, *, direct=False, **overrides) -> None:
        props = {
            "orgId": ORG, "connectorId": self.app, "origin": "CONNECTOR",
            "recordName": rid, "isDeleted": False, "indexingStatus": "COMPLETED",
        }
        if modified_at is not None:
            props["sourceLastModifiedTimestamp"] = modified_at
        props.update(overrides)
        self.nodes.append(("records", rid, props))
        if direct:
            self._edge("permission", ("users", self.user_key), ("records", rid),
                       type="USER", role="OWNER")


async def _seed_neo4j(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.client.execute_query(
            f"CREATE (n:{_NEO4J_LABELS[coll]}) SET n = $props",
            parameters={"props": {**props, "id": key, "itRun": g.run}},
        )
    for coll, (fc, fk), (tc, tk), props in g.edges:
        await provider.client.execute_query(
            f"MATCH (a:{_NEO4J_LABELS[fc]} {{id: $f}}), (b:{_NEO4J_LABELS[tc]} {{id: $t}}) "
            f"CREATE (a)-[r:{_NEO4J_EDGES[coll]}]->(b) SET r = $props",
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
    for coll, (fc, fk), (tc, tk), props in g.edges:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_from: @f, _to: @t, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "f": f"{fc}/{fk}", "t": f"{tc}/{tk}", "r": g.run},
        )


async def _clean_arango(provider, g: _Graph) -> None:
    for coll in _DOC_COLLECTIONS + _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itRun == @r REMOVE d IN {coll}",
            bind_vars={"r": g.run},
        )


class _Contract:
    """The behaviour both providers owe graph enrichment."""

    seed = None
    clean = None

    async def _with_graph(self, provider, check) -> None:
        g = _Graph()
        await type(self).seed(provider, g)
        try:
            await check(provider, g)
        finally:
            await type(self).clean(provider, g)

    async def test_filter_admits_exactly_the_readable_records(self, provider):
        async def check(provider, g):
            ids = g.ids
            granted = await provider.filter_accessible_record_ids(
                [*ids.values(), f"{g.run}-missing"], g.user_id, ORG,
            )
            assert granted == {
                ids["epic"], ids["parent"], ids["attachment"], ids["child_group"],
                ids["child_unindexed"], ids["child_no_ts"],
            }
        await self._with_graph(provider, check)

    async def test_filter_denies_an_unknown_user(self, provider):
        async def check(provider, g):
            granted = await provider.filter_accessible_record_ids(
                list(g.ids.values()), f"{g.run}-nobody", ORG,
            )
            assert granted == set()
        await self._with_graph(provider, check)


class TestNeo4jAccessibleRecordRelations(_Contract):
    seed = staticmethod(_seed_neo4j)
    clean = staticmethod(_clean_neo4j)

    @pytest.fixture
    def provider(self, neo4j_provider):
        return neo4j_provider


class TestArangoAccessibleRecordRelations(_Contract):
    seed = staticmethod(_seed_arango)
    clean = staticmethod(_clean_arango)

    @pytest.fixture
    def provider(self, arango_provider):
        return arango_provider
