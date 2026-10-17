"""An edited record stays searchable with its previous indexed copy, on a real Neo4j and ArangoDB.

Requires the graph stack from deployment/docker-compose/docker-compose.integration.graph-db.yml.
Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD (the
backend-matrix workflow sets these and finds this file by them).

A content change queues a COMPLETED record again as NOT_STARTED. Search used to keep
only COMPLETED records, so an edited document left search until it was indexed again,
which can take hours behind a backlog. ``hasIndexedCopy`` keeps it in while it waits.
"""

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ORG = "org-it-indexed-copy"

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "search_keeps_indexed_copy_it"

_DOC_COLLECTIONS = (
    "users", "records", "recordGroups", "groups", "roles", "organizations", "anyone", "apps", "teams",
)
_EDGE_COLLECTIONS = ("permission", "belongsTo", "inheritPermissions", "authenticatedAs")
_NEO4J_LABELS = {"users": "User", "records": "Record", "apps": "App"}
_NEO4J_EDGES = {"permission": "PERMISSION", "belongsTo": "BELONGS_TO"}

# name -> (indexingStatus, hasIndexedCopy or None for absent, isDeleted)
_CONNECTOR_RECORDS = {
    "indexed": ("COMPLETED", True, False),
    "indexed_before_the_flag": ("COMPLETED", None, False),
    "edited_and_queued": ("NOT_STARTED", True, False),
    "being_reindexed": ("IN_PROGRESS", True, False),
    "reindex_failed": ("FAILED", True, False),
    "never_indexed": ("NOT_STARTED", None, False),
    "never_indexed_failed": ("FAILED", None, False),
    "now_empty": ("EMPTY", False, False),
    "edited_then_trashed": ("NOT_STARTED", True, True),
}
_SEARCHABLE = {"indexed", "indexed_before_the_flag", "edited_and_queued", "being_reindexed", "reindex_failed"}


def _log() -> logging.Logger:
    from app.utils.logger import create_logger
    return create_logger("search_keeps_indexed_copy_test")


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
    from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    logger = _log()
    client = ArangoHTTPClient(
        base_url=ARANGO_URL, username="root", password=ARANGO_PASSWORD, database=ARANGO_DB, logger=logger
    )
    # Unreachable is a skip, as for Neo4j; a bad status while creating the collections fails.
    import aiohttp

    try:
        await _ensure_arango_collections()
    except aiohttp.ClientConnectionError as exc:
        pytest.skip(f"ArangoDB not available at {ARANGO_URL}: {exc}")

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logger
    provider.http_client = client
    yield provider
    await client.disconnect()


async def _ensure_arango_collections() -> None:
    import aiohttp

    auth = aiohttp.BasicAuth("root", ARANGO_PASSWORD)
    async with aiohttp.ClientSession(auth=auth) as s:
        async with s.post(f"{ARANGO_URL}/_db/_system/_api/database", json={"name": ARANGO_DB}) as r:
            if r.status not in (200, 201, 409):
                raise RuntimeError(f"cannot create database {ARANGO_DB}: {r.status}")
        wanted = [(n, 2) for n in _DOC_COLLECTIONS] + [(n, 3) for n in _EDGE_COLLECTIONS]
        for name, kind in wanted:
            async with s.post(
                f"{ARANGO_URL}/_db/{ARANGO_DB}/_api/collection", json={"name": name, "type": kind}
            ) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


class _Graph:
    """One user with direct access to a record in each indexing state, plus the same for a KB."""

    def __init__(self) -> None:
        self.run = uuid.uuid4().hex[:8]
        self.user_id = f"{self.run}-uid"
        self.user_key = f"{self.run}-ukey"
        self.app = f"{self.run}-app"
        self.kb = f"{self.run}-kb"
        self.nodes: list[tuple[str, str, dict]] = [
            ("users", self.user_key, {"userId": self.user_id, "orgId": ORG}),
            ("apps", self.kb, {"type": "KB", "orgId": ORG, "isHidden": False}),
        ]
        self.edges: list[tuple[str, tuple[str, str], tuple[str, str], dict]] = [
            ("permission", ("users", self.user_key), ("apps", self.kb), {"type": "USER", "role": "READER"}),
        ]
        for name, state in _CONNECTOR_RECORDS.items():
            self._record(self.rid(name), "CONNECTOR", state)
            self.edges.append(
                ("permission", ("users", self.user_key), ("records", self.rid(name)), {"type": "USER", "role": "READER"})
            )
        for name in ("kb_edited_and_queued", "kb_never_indexed"):
            state = ("NOT_STARTED", True, False) if name == "kb_edited_and_queued" else ("NOT_STARTED", None, False)
            self._record(self.rid(name), "UPLOAD", state)
            self.edges.append(("belongsTo", ("records", self.rid(name)), ("apps", self.kb), {}))

    def rid(self, name: str) -> str:
        return f"{self.run}-{name}"

    def found(self, *names: str) -> dict[str, str]:
        return {f"{self.rid(n)}-vr": self.rid(n) for n in names}

    def _record(self, rid: str, origin: str, state: tuple) -> None:
        status, has_copy, deleted = state
        props = {
            "orgId": ORG, "connectorId": self.app if origin == "CONNECTOR" else self.kb,
            "origin": origin, "recordName": rid, "isDeleted": deleted,
            "indexingStatus": status, "virtualRecordId": f"{rid}-vr",
        }
        if has_copy is not None:
            props["hasIndexedCopy"] = has_copy
        self.nodes.append(("records", rid, props))


async def _seed_neo4j(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.client.execute_query(
            f"CREATE (n:{_NEO4J_LABELS[coll]}) SET n = $props",
            parameters={"props": {**props, "id": key, "itRun": g.run}},
        )
    for kind, (fc, fk), (tc, tk), props in g.edges:
        await provider.client.execute_query(
            f"MATCH (a:{_NEO4J_LABELS[fc]} {{id: $f}}), (b:{_NEO4J_LABELS[tc]} {{id: $t}}) "
            f"CREATE (a)-[r:{_NEO4J_EDGES[kind]}]->(b) SET r = $props",
            parameters={"f": fk, "t": tk, "props": props},
        )


async def _clean_neo4j(provider, g: _Graph) -> None:
    await provider.client.execute_query("MATCH (n {itRun: $r}) DETACH DELETE n", parameters={"r": g.run})


async def _seed_arango(provider, g: _Graph) -> None:
    for coll, key, props in g.nodes:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_key: @k, itRun: @r}}) INTO {coll}",
            bind_vars={"props": props, "k": key, "r": g.run},
        )
    for kind, (fc, fk), (tc, tk), props in g.edges:
        await provider.http_client.execute_aql(
            f"INSERT MERGE(@props, {{_from: @f, _to: @t, itRun: @r}}) INTO {kind}",
            bind_vars={"props": props, "f": f"{fc}/{fk}", "t": f"{tc}/{tk}", "r": g.run},
        )


async def _clean_arango(provider, g: _Graph) -> None:
    for coll in _DOC_COLLECTIONS + _EDGE_COLLECTIONS:
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.itRun == @r REMOVE d IN {coll}", bind_vars={"r": g.run}
        )


async def _set_status(provider, rid: str, status: str) -> None:
    if hasattr(provider, "client"):
        await provider.client.execute_query(
            "MATCH (r:Record {id: $id}) SET r.indexingStatus = $s", parameters={"id": rid, "s": status}
        )
    else:
        await provider.http_client.execute_aql(
            "UPDATE @k WITH {indexingStatus: @s} IN records", bind_vars={"k": rid, "s": status}
        )


class _Contract:
    seed = None
    clean = None

    async def _with_graph(self, provider, check) -> None:
        g = _Graph()
        await type(self).seed(provider, g)
        try:
            await check(provider, g)
        finally:
            await type(self).clean(provider, g)

    async def test_an_edited_record_waiting_to_be_indexed_is_still_found(self, provider) -> None:
        async def check(provider, g: _Graph) -> None:
            found = await provider._get_virtual_ids_for_connector(g.user_id, ORG, g.app, None, raise_on_error=True)
            assert found == g.found(*sorted(_SEARCHABLE))

        await self._with_graph(provider, check)

    async def test_an_edited_upload_waiting_to_be_indexed_is_still_found(self, provider) -> None:
        async def check(provider, g: _Graph) -> None:
            found = await provider._get_kb_virtual_ids(g.user_id, ORG, [g.kb], None, raise_on_error=True)
            assert found == g.found("kb_edited_and_queued")

        await self._with_graph(provider, check)

    async def test_the_backfill_marks_records_indexed_before_the_flag_existed(self, provider) -> None:
        async def check(provider, g: _Graph) -> None:
            assert await provider.backfill_indexed_copy_flags(batch_size=1) >= 1
            assert await provider.backfill_indexed_copy_flags() == 0
            await _set_status(provider, g.rid("indexed_before_the_flag"), "NOT_STARTED")
            await _set_status(provider, g.rid("never_indexed"), "NOT_STARTED")

            found = await provider._get_virtual_ids_for_connector(g.user_id, ORG, g.app, None, raise_on_error=True)
            assert g.rid("indexed_before_the_flag") in found.values()
            assert g.rid("never_indexed") not in found.values()

        await self._with_graph(provider, check)


class TestNeo4jSearchKeepsIndexedCopy(_Contract):
    seed = staticmethod(_seed_neo4j)
    clean = staticmethod(_clean_neo4j)

    @pytest.fixture
    def provider(self, neo4j_provider: Any) -> Any:  # noqa: ANN401 - the provider under test
        return neo4j_provider


class TestArangoSearchKeepsIndexedCopy(_Contract):
    seed = staticmethod(_seed_arango)
    clean = staticmethod(_clean_arango)

    @pytest.fixture
    def provider(self, arango_provider: Any) -> Any:  # noqa: ANN401 - the provider under test
        return arango_provider
