"""Queued-duplicate queries against a real Neo4j and a real ArangoDB.

Requires: docker compose -f tests/integration/compose/graph-db.yml up -d
Run: pytest tests/integration/graph_db/ -m integration

A record parked QUEUED behind an in-flight duplicate found it with its *own*
recordType and size, each applied only when set. Releasing it has to ask the
same question from the other side, and the two backends answer null
comparisons differently: ``x = null`` is null in Cypher and ``null == null`` is
true in AQL. These pin that both land on the same set, that "holds no VRID" is
a condition either can express, and that copying edges onto a record twice —
which a retried attach now does — leaves one edge per target.
"""

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_EDGE_COLLECTIONS = (
    "belongsToDepartment", "belongsToCategory", "belongsToLanguage", "belongsToTopic",
)


def _log():
    from app.utils.logger import create_logger
    return create_logger("queued_duplicate_release_test")


@pytest.fixture(scope="module")
async def neo4j_provider():
    pytest.importorskip("neo4j", reason="neo4j driver not installed")
    from app.schema.node_validator import NodeSchemaValidator
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
    provider.validator = NodeSchemaValidator()
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
    import aiohttp

    auth = aiohttp.BasicAuth("root", password)
    async with aiohttp.ClientSession(auth=auth) as s:
        async with s.post(f"{url}/_db/_system/_api/database", json={"name": db}) as r:
            if r.status not in (200, 201, 409):
                raise RuntimeError(f"cannot create database {db}: {r.status}")
        collections = [("records", 2), ("departments", 2)]
        collections += [(name, 3) for name in _EDGE_COLLECTIONS]
        for name, kind in collections:
            async with s.post(
                f"{url}/_db/{db}/_api/collection", json={"name": name, "type": kind}
            ) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


# ---------------------------------------------------------------------------
# Seeding. Every node carries the test's tag as connectorId, for cleanup.
# ---------------------------------------------------------------------------

async def _seed_neo4j(provider, tag, records):
    for rec in records:
        await provider.client.execute_query(
            "CREATE (r:Record) SET r = $props",
            parameters={"props": {**rec, "connectorId": tag}},
        )


async def _seed_neo4j_edge(provider, tag, record_id, department_id):
    await provider.client.execute_query(
        "MERGE (d:Departments {id: $d}) SET d.connectorId = $tag "
        "WITH d MATCH (r:Record {id: $r}) CREATE (r)-[:BELONGS_TO_DEPARTMENT]->(d)",
        parameters={"d": department_id, "r": record_id, "tag": tag},
    )


async def _count_neo4j_edges(provider, record_id):
    rows = await provider.client.execute_query(
        "MATCH (:Record {id: $r})-[e:BELONGS_TO_DEPARTMENT]->(d) RETURN d.id AS d, count(e) AS n",
        parameters={"r": record_id},
    )
    return {row["d"]: row["n"] for row in rows}


async def _clean_neo4j(provider, tag):
    await provider.client.execute_query(
        "MATCH (n) WHERE n.connectorId = $c DETACH DELETE n", parameters={"c": tag}
    )


async def _seed_arango(provider, tag, records):
    for rec in records:
        doc = {k: v for k, v in rec.items() if k != "id"}
        await provider.http_client.execute_aql(
            "INSERT MERGE(@doc, {_key: @k, connectorId: @c}) INTO records",
            bind_vars={"doc": doc, "k": rec["id"], "c": tag},
        )


async def _seed_arango_edge(provider, tag, record_id, department_id):
    await provider.http_client.execute_aql(
        "UPSERT {_key: @d} INSERT {_key: @d, connectorId: @c} UPDATE {} IN departments",
        bind_vars={"d": department_id, "c": tag},
    )
    await provider.http_client.execute_aql(
        "INSERT {_from: @f, _to: @t} INTO belongsToDepartment",
        bind_vars={"f": f"records/{record_id}", "t": f"departments/{department_id}"},
    )


async def _count_arango_edges(provider, record_id):
    rows = await provider.http_client.execute_aql(
        "FOR e IN belongsToDepartment FILTER e._from == @f "
        "COLLECT d = e._to WITH COUNT INTO n RETURN {d: SPLIT(d, '/')[1], n: n}",
        bind_vars={"f": f"records/{record_id}"},
    )
    return {row["d"]: row["n"] for row in rows}


async def _clean_arango(provider, tag):
    await provider.http_client.execute_aql(
        "FOR d IN records FILTER d.connectorId == @c "
        "LET f = CONCAT('records/', d._key) "
        "FOR e IN belongsToDepartment FILTER e._from == f REMOVE e IN belongsToDepartment",
        bind_vars={"c": tag},
    )
    for coll in ("records", "departments"):
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.connectorId == @c REMOVE d IN {coll}",
            bind_vars={"c": tag},
        )


# ---------------------------------------------------------------------------
# Shared contract, run against both providers
# ---------------------------------------------------------------------------

def _record(tag, suffix, **props):
    base = {
        "id": f"{tag}-{suffix}",
        "orgId": f"{tag}-org",
        "md5Checksum": f"{tag}-md5",
        "indexingStatus": "QUEUED",
        "isDeleted": False,
    }
    base.update(props)
    return {k: v for k, v in base.items() if v is not None}


class _Contract:
    async def check_queued_duplicates_mirror_the_waiting_side(self, provider, seed, clean):
        tag = f"qd-{uuid.uuid4().hex[:8]}"
        org = f"{tag}-org"
        records = [
            _record(tag, "primary", indexingStatus="COMPLETED", recordType="FILE", sizeInBytes=10),
            _record(tag, "same", recordType="FILE", sizeInBytes=10),
            _record(tag, "nulls"),
            _record(tag, "other-org", orgId=f"{tag}-elsewhere", recordType="FILE", sizeInBytes=10),
            _record(tag, "other-type", recordType="TICKET", sizeInBytes=10),
            _record(tag, "other-size", recordType="FILE", sizeInBytes=20),
            _record(tag, "deleted", recordType="FILE", sizeInBytes=10, isDeleted=True),
            _record(tag, "in-flight", recordType="FILE", sizeInBytes=10, indexingStatus="IN_PROGRESS"),
        ]
        try:
            await seed(provider, tag, records)
            found = await provider.find_queued_duplicates(
                f"{tag}-primary", f"{tag}-md5", org, "FILE", 10, raise_on_error=True
            )
            assert sorted(r["_key"] for r in found) == [f"{tag}-nulls", f"{tag}-same"]

            # A reference with no type or size could only have been matched by
            # records that had none either.
            found = await provider.find_queued_duplicates(
                f"{tag}-primary", f"{tag}-md5", org, None, None, raise_on_error=True
            )
            assert [r["_key"] for r in found] == [f"{tag}-nulls"]

            # The status-only path reads the same set off the reference record.
            assert await provider.update_queued_duplicates_status(f"{tag}-primary", "FAILED") == 2
        finally:
            await clean(provider, tag)

    async def check_update_record_if_conditions(self, provider, seed, clean):
        tag = f"ui-{uuid.uuid4().hex[:8]}"
        bare, held = f"{tag}-bare", f"{tag}-held"
        try:
            await seed(provider, tag, [
                _record(tag, "bare"),
                _record(tag, "held", virtualRecordId="vr-1"),
            ])

            def update(record_id, **conditions):
                return provider.update_record_if(record_id, {"reason": "touched"}, **conditions)

            # "Holds no VRID" is expressible: Cypher needs its own IS NULL branch.
            assert await update(bare, match_virtual_record_id=True, expected_virtual_record_id=None)
            assert not await update(bare, match_virtual_record_id=True, expected_virtual_record_id="vr-1")
            assert not await update(held, match_virtual_record_id=True, expected_virtual_record_id=None)
            assert await update(held, match_virtual_record_id=True, expected_virtual_record_id="vr-1")
            assert not await update(held, expected_statuses=["COMPLETED"])
            assert await update(held, expected_statuses=["QUEUED", "COMPLETED"])
            assert not await update(f"{tag}-missing", expected_statuses=["QUEUED"])

            # Writing None clears the field, and the cleared field then matches
            # "holds none" — the rollback relies on both.
            assert await provider.update_record_if(
                held, {"virtualRecordId": None}, expected_statuses=["QUEUED"]
            )
            assert await update(held, match_virtual_record_id=True, expected_virtual_record_id=None)
        finally:
            await clean(provider, tag)

    async def check_copying_edges_twice_leaves_one_per_target(
        self, provider, seed, clean, seed_edge, count_edges
    ):
        tag = f"ce-{uuid.uuid4().hex[:8]}"
        src, dst = f"{tag}-src", f"{tag}-dst"
        try:
            await seed(provider, tag, [_record(tag, "src"), _record(tag, "dst")])
            await seed_edge(provider, tag, src, f"{tag}-dep-a")
            await seed_edge(provider, tag, src, f"{tag}-dep-b")

            assert await provider.copy_document_relationships(src, dst)
            assert await provider.copy_document_relationships(src, dst)

            assert await count_edges(provider, dst) == {f"{tag}-dep-a": 1, f"{tag}-dep-b": 1}
        finally:
            await clean(provider, tag)


class TestQueuedDuplicateQueriesNeo4j(_Contract):
    async def test_queued_duplicates_mirror_the_waiting_side(self, neo4j_provider):
        await self.check_queued_duplicates_mirror_the_waiting_side(
            neo4j_provider, _seed_neo4j, _clean_neo4j
        )

    async def test_update_record_if_conditions(self, neo4j_provider):
        await self.check_update_record_if_conditions(neo4j_provider, _seed_neo4j, _clean_neo4j)

    async def test_copying_edges_twice_leaves_one_per_target(self, neo4j_provider):
        await self.check_copying_edges_twice_leaves_one_per_target(
            neo4j_provider, _seed_neo4j, _clean_neo4j, _seed_neo4j_edge, _count_neo4j_edges
        )


class TestQueuedDuplicateQueriesArango(_Contract):
    async def test_queued_duplicates_mirror_the_waiting_side(self, arango_provider):
        await self.check_queued_duplicates_mirror_the_waiting_side(
            arango_provider, _seed_arango, _clean_arango
        )

    async def test_update_record_if_conditions(self, arango_provider):
        await self.check_update_record_if_conditions(arango_provider, _seed_arango, _clean_arango)

    async def test_copying_edges_twice_leaves_one_per_target(self, arango_provider):
        await self.check_copying_edges_twice_leaves_one_per_target(
            arango_provider, _seed_arango, _clean_arango, _seed_arango_edge, _count_arango_edges
        )
