"""A timeline's shared permission walk against a real Neo4j and a real ArangoDB.

A dated read inside a timeline takes its records from one walk over the span,
filtered by ``dated_scope.row_in_time_range`` (``app/services/graph_db/dated_scope.py``).
That filter stands in for each provider's own time conditions, so for every
range inside the span it must return exactly what the provider's live dated
read returns: the same virtual records, each through a record the range admits.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-it-dated-scope"
NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "dated_scope_it"

_DOC_COLLECTIONS = (
    "users", "records", "recordGroups", "groups", "roles", "organizations", "anyone", "apps", "teams",
)
_EDGE_COLLECTIONS = ("permission", "belongsTo", "inheritPermissions", "authenticatedAs", "userAppRelation")
_NEO4J_LABELS = {"users": "User", "records": "Record"}

DAY = 86_400_000
T0 = 1_760_000_000_000  # an arbitrary day; every date below is an offset from it

# (name, created, modified, origin, virtual record id suffix). Two records share
# the virtual record id "dup" with different dates, as copies of one file do.
_RECORDS = (
    ("early", T0, T0 + 1 * DAY, None, None),
    ("mid", T0 + 10 * DAY, T0 + 12 * DAY, None, None),
    ("late", T0 + 20 * DAY, T0 + 25 * DAY, None, None),
    ("edge", T0 + 15 * DAY, T0 + 15 * DAY, None, None),
    ("no_created", None, T0 + 5 * DAY, None, None),
    ("no_dates", None, None, None, None),
    ("zero", 0, 0, None, None),
    ("upload_old", T0 + 2 * DAY, T0 + 2 * DAY, "upload", None),
    ("upload_new", T0 + 30 * DAY, T0 + 30 * DAY, "upload", None),
    ("from_file", T0 + 8 * DAY, T0 + 9 * DAY, "file", None),
    ("dup_a", T0 + 3 * DAY, T0 + 3 * DAY, None, "dup"),
    ("dup_b", T0 + 18 * DAY, T0 + 18 * DAY, None, "dup"),
)

# Ranges within the span {created after T0 - 1 day}, on either date, with and
# without the dated-only flag, and with bounds that sit exactly on a date.
SPAN = {"source_created_after_ms": T0 - DAY}
_RANGES = (
    {"source_created_after_ms": T0 - DAY},
    {"source_created_after_ms": T0 + 5 * DAY, "source_created_before_ms": T0 + 15 * DAY},
    {"source_created_after_ms": T0 + 15 * DAY, "source_created_before_ms": T0 + 15 * DAY},
    {"source_created_after_ms": T0, "source_created_before_ms": T0 + 4 * DAY, "dated_only": 1},
    {"source_created_after_ms": T0 + 16 * DAY, "source_created_before_ms": T0 + 40 * DAY, "dated_only": 1},
    {"source_created_after_ms": T0, "source_updated_before_ms": T0 + 12 * DAY},
    {"source_created_after_ms": T0, "source_updated_after_ms": T0 + 10 * DAY},
)


def _log() -> logging.Logger:
    from app.utils.logger import create_logger

    return create_logger("dated_scope_test")


@pytest.fixture(scope="module")
async def neo4j_provider() -> AsyncIterator[Any]:
    from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    logger = _log()
    client = Neo4jClient(
        uri=NEO4J_URI, username="neo4j", password=NEO4J_PASSWORD, database="neo4j", logger=logger
    )
    # Unreachable is a skip (the unit-test job has no Neo4j; backend-matrix fails on any skip).
    try:
        connected = await client.connect()
    except Exception as exc:
        pytest.skip(f"Neo4j not available at {NEO4J_URI}: {exc}")
    if not connected:
        pytest.skip(f"Neo4j not available at {NEO4J_URI}")
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = logger
    provider.client = client
    yield provider
    await client.disconnect()


@pytest.fixture(scope="module")
async def arango_provider() -> AsyncIterator[Any]:
    import aiohttp

    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    logger = _log()
    try:
        await _ensure_arango_schema()
    except aiohttp.ClientConnectionError as exc:
        pytest.skip(f"ArangoDB not available at {ARANGO_URL}: {exc}")
    client = ArangoHTTPClient(
        base_url=ARANGO_URL, username="root", password=ARANGO_PASSWORD, database=ARANGO_DB, logger=logger
    )
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logger
    provider.http_client = client
    yield provider
    await client.disconnect()


async def _ensure_arango_schema() -> None:
    import aiohttp

    auth = aiohttp.BasicAuth("root", ARANGO_PASSWORD)
    async with aiohttp.ClientSession(auth=auth) as s:
        async with s.post(f"{ARANGO_URL}/_db/_system/_api/database", json={"name": ARANGO_DB}) as r:
            if r.status not in (200, 201, 409):
                raise RuntimeError(f"cannot create database {ARANGO_DB}: {r.status}")
        wanted = [(n, 2) for n in _DOC_COLLECTIONS] + [(n, 3) for n in _EDGE_COLLECTIONS]
        for name, kind in wanted:
            async with s.post(f"{ARANGO_URL}/_db/{ARANGO_DB}/_api/collection", json={"name": name, "type": kind}) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


class _Graph:
    """A user granted every record of one connector directly."""

    def __init__(self) -> None:
        self.run = uuid.uuid4().hex[:8]
        self.user_id = f"{self.run}-uid"
        self.user_key = f"{self.run}-ukey"
        self.app = f"{self.run}-app"
        self.nodes: list[tuple[str, str, dict]] = [("users", self.user_key, {"userId": self.user_id, "orgId": ORG})]
        self.edges: list[tuple[str, str]] = []
        self.dates: dict[str, tuple[Any, Any, Any]] = {}
        from app.models.source_dates import SOURCE_TIMESTAMP_ORIGIN_FIELD

        for name, created, modified, origin, vr in _RECORDS:
            rid = f"{self.run}-{name}"
            props = {
                "orgId": ORG, "connectorId": self.app, "origin": "CONNECTOR", "recordName": rid,
                "isDeleted": False, "indexingStatus": "COMPLETED",
                "virtualRecordId": f"{self.run}-{vr or name}-vr",
            }
            if created is not None:
                props["sourceCreatedAtTimestamp"] = created
            if modified is not None:
                props["sourceLastModifiedTimestamp"] = modified
            if origin is not None:
                props[SOURCE_TIMESTAMP_ORIGIN_FIELD] = origin
            self.nodes.append(("records", rid, props))
            self.edges.append((self.user_key, rid))
            self.dates[rid] = (created, modified, origin)


async def _seed_neo4j(provider: Any, g: _Graph) -> None:  # noqa: ANN401
    for coll, key, props in g.nodes:
        await provider.client.execute_query(
            f"CREATE (n:{_NEO4J_LABELS[coll]}) SET n = $props",
            parameters={"props": {**props, "id": key, "itRun": g.run}},
        )
    for user, rid in g.edges:
        await provider.client.execute_query(
            "MATCH (a:User {id: $f}), (b:Record {id: $t}) CREATE (a)-[r:PERMISSION]->(b) SET r = $props",
            parameters={"f": user, "t": rid, "props": {"type": "USER", "role": "READER"}},
        )


async def _clean_neo4j(provider: Any, g: _Graph) -> None:  # noqa: ANN401
    await provider.client.execute_query("MATCH (n {itRun: $r}) DETACH DELETE n", parameters={"r": g.run})


async def _seed_arango(provider: Any, g: _Graph) -> None:  # noqa: ANN401
    for coll, key, props in g.nodes:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_key: @k, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "k": key, "r": g.run},
        )
    for user, rid in g.edges:
        await provider.http_client.execute_aql(
            "INSERT {_from: @f, _to: @t, type: 'USER', role: 'READER', itRun: @r} INTO permission",
            bind_vars={"f": f"users/{user}", "t": f"records/{rid}", "r": g.run},
        )


async def _clean_arango(provider: Any, g: _Graph) -> None:  # noqa: ANN401
    for coll in _DOC_COLLECTIONS + _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itRun == @r REMOVE d IN {coll}", bind_vars={"r": g.run},
        )


class _Contract:
    seed = None
    clean = None

    @pytest.mark.parametrize("uploads_undated", [False, True])
    async def test_the_shared_walk_reads_what_each_live_dated_read_does(
        self, provider: Any, monkeypatch: pytest.MonkeyPatch, uploads_undated: bool,  # noqa: ANN401
    ) -> None:
        from app.services.graph_db.dated_scope import virtual_ids_in_range

        if uploads_undated:
            monkeypatch.setenv("CONTENT_DOCUMENT_DATES", "true")
        else:
            monkeypatch.delenv("CONTENT_DOCUMENT_DATES", raising=False)
        g = _Graph()
        await type(self).seed(provider, g)
        try:
            rows = await provider._get_virtual_ids_for_connector(
                g.user_id, ORG, g.app, None, time_range=SPAN, raise_on_error=True, with_dates=True,
            )
            for time_range in _RANGES:
                live = await provider._get_virtual_ids_for_connector(
                    g.user_id, ORG, g.app, None, time_range=time_range, raise_on_error=True,
                )
                shared = virtual_ids_in_range(rows, time_range)
                if time_range == _RANGES[0]:
                    # Two empty reads would also match; this one must find a record.
                    assert f"{g.run}-early-vr" in live, time_range
                assert shared.keys() == live.keys(), time_range
                # A virtual record shared by two records may come through
                # either; both reads pick one the range admits.
                allowed = await provider._get_virtual_ids_for_connector(
                    g.user_id, ORG, g.app, None, time_range=time_range, raise_on_error=True, with_dates=True,
                )
                admitted = {(r.virtual_id, r.record_id) for r in allowed}
                assert set(shared.items()) <= admitted, time_range
        finally:
            await type(self).clean(provider, g)


class TestNeo4jDatedScope(_Contract):
    seed = staticmethod(_seed_neo4j)
    clean = staticmethod(_clean_neo4j)

    @pytest.fixture
    def provider(self, neo4j_provider: Any) -> Any:  # noqa: ANN401 - the provider under test
        return neo4j_provider


class TestArangoDatedScope(_Contract):
    seed = staticmethod(_seed_arango)
    clean = staticmethod(_clean_arango)

    @pytest.fixture
    def provider(self, arango_provider: Any) -> Any:  # noqa: ANN401 - the provider under test
        return arango_provider
