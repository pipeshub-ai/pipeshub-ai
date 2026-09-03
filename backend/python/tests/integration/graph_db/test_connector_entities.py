"""``_collect_connector_entities`` against a real Neo4j and a real ArangoDB.

Requires: docker compose -f tests/integration/compose/graph-db.yml up -d
Run: pytest tests/integration/graph_db/ -m integration

This is what decides whether a connector delete removes everything it should.
The result feeds both the graph cascade (every node and edge to drop) and, on
the legacy path, the virtualRecordId list shipped to indexing — so a query that
under-reports leaves orphans behind and reports success.

The case worth pinning is a connector that has record groups but **no records**:
on Neo4j the aggregation pipeline starts from ``MATCH (r:Record ...)``, and if
the projection introduces a grouping key there, an empty record match collapses
the whole result to zero rows and the connector's groups, roles and edges
silently survive the delete.
"""

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

CONNECTOR = None  # per-test, assigned in fixtures


def _log():
    from app.utils.logger import create_logger
    return create_logger("graph_db_integration_test")


# ---------------------------------------------------------------------------
# Providers, built directly against a live server (no ConfigurationService)
# ---------------------------------------------------------------------------

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
        await _ensure_arango_schema(client, url, password, db)
    except Exception as exc:
        pytest.skip(f"ArangoDB not available at {url} — {exc}")

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logger
    provider.http_client = client
    yield provider
    await client.disconnect()


async def _ensure_arango_schema(client, url, password, db) -> None:
    """Create the database and the four collections the query reads."""
    import aiohttp

    auth = aiohttp.BasicAuth("root", password)
    async with aiohttp.ClientSession(auth=auth) as s:
        async with s.post(
            f"{url}/_db/_system/_api/database", json={"name": db}
        ) as r:
            if r.status not in (200, 201, 409):
                raise RuntimeError(f"cannot create database {db}: {r.status}")
        for name in ("records", "recordGroups", "roles", "groups"):
            async with s.post(
                f"{url}/_db/{db}/_api/collection", json={"name": name}
            ) as r:
                if r.status not in (200, 201, 409):
                    raise RuntimeError(f"cannot create collection {name}: {r.status}")


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

async def _seed_neo4j(provider, connector_id: str, *, records: list[tuple[str, str]],
                      groups: int) -> None:
    for rec_id, vrid in records:
        await provider.client.execute_query(
            "CREATE (r:Record {id: $id, connectorId: $c, virtualRecordId: $v})",
            parameters={"id": rec_id, "c": connector_id, "v": vrid},
        )
    for i in range(groups):
        await provider.client.execute_query(
            "CREATE (g:RecordGroup {id: $id, connectorId: $c})",
            parameters={"id": f"{connector_id}-grp-{i}", "c": connector_id},
        )


async def _clean_neo4j(provider, connector_id: str) -> None:
    await provider.client.execute_query(
        "MATCH (n) WHERE n.connectorId = $c DETACH DELETE n",
        parameters={"c": connector_id},
    )


async def _seed_arango(provider, connector_id: str, *, records: list[tuple[str, str]],
                       groups: int) -> None:
    for rec_id, vrid in records:
        await provider.http_client.execute_aql(
            "INSERT {_key: @k, connectorId: @c, virtualRecordId: @v} INTO records",
            bind_vars={"k": rec_id, "c": connector_id, "v": vrid},
        )
    for i in range(groups):
        await provider.http_client.execute_aql(
            "INSERT {_key: @k, connectorId: @c} INTO recordGroups",
            bind_vars={"k": f"{connector_id}-grp-{i}", "c": connector_id},
        )


async def _clean_arango(provider, connector_id: str) -> None:
    for coll in ("records", "recordGroups", "roles", "groups"):
        await provider.http_client.execute_aql(
            f"FOR d IN {coll} FILTER d.connectorId == @c REMOVE d IN {coll}",
            bind_vars={"c": connector_id},
        )


# ---------------------------------------------------------------------------
# Shared assertions, run against both providers
# ---------------------------------------------------------------------------

class _Contract:
    """The behaviour both providers owe the delete path."""

    async def check_groups_without_records(self, provider, seed, clean):
        """A connector with groups but no records must still report its groups.

        The regression this exists for: an empty record match collapsing the
        whole aggregation to zero rows, which reads as "nothing to delete".
        """
        cid = f"conn-{uuid.uuid4().hex[:8]}"
        try:
            await seed(provider, cid, records=[], groups=3)
            for flag in (False, True):
                got = await provider._collect_connector_entities(
                    cid, include_virtual_record_ids=flag
                )
                assert got["record_keys"] == [], flag
                assert len(got["record_group_keys"]) == 3, (
                    f"groups lost with include_virtual_record_ids={flag}: {got}"
                )
                assert f"apps/{cid}" in got["all_node_ids"], flag
        finally:
            await clean(provider, cid)

    async def check_vrids_off_by_default(self, provider, seed, clean):
        cid = f"conn-{uuid.uuid4().hex[:8]}"
        try:
            await seed(
                provider, cid,
                records=[("r1", "vr-a"), ("r2", "vr-b")], groups=1,
            )
            got = await provider._collect_connector_entities(cid)
            assert got["virtual_record_ids"] == []
            assert len(got["record_keys"]) == 2
        finally:
            await clean(provider, cid)

    async def check_vrids_deduplicated(self, provider, seed, clean):
        """Records sharing content share a VRID; the list carries it once."""
        cid = f"conn-{uuid.uuid4().hex[:8]}"
        try:
            await seed(
                provider, cid,
                records=[("r1", "vr-a"), ("r2", "vr-a"), ("r3", "vr-b")],
                groups=0,
            )
            got = await provider._collect_connector_entities(
                cid, include_virtual_record_ids=True
            )
            assert len(got["record_keys"]) == 3
            assert sorted(got["virtual_record_ids"]) == ["vr-a", "vr-b"]
        finally:
            await clean(provider, cid)

    async def check_empty_connector(self, provider, seed, clean):
        """An unknown connector yields empty lists, not an exception."""
        cid = f"conn-{uuid.uuid4().hex[:8]}"
        got = await provider._collect_connector_entities(
            cid, include_virtual_record_ids=True
        )
        assert got["record_keys"] == []
        assert got["record_group_keys"] == []
        assert got["virtual_record_ids"] == []

    async def check_other_connectors_untouched(self, provider, seed, clean):
        mine = f"conn-{uuid.uuid4().hex[:8]}"
        theirs = f"conn-{uuid.uuid4().hex[:8]}"
        try:
            await seed(provider, mine, records=[("m1", "vr-m")], groups=1)
            await seed(provider, theirs, records=[("t1", "vr-t")], groups=2)
            got = await provider._collect_connector_entities(
                mine, include_virtual_record_ids=True
            )
            assert len(got["record_keys"]) == 1
            assert len(got["record_group_keys"]) == 1
            assert got["virtual_record_ids"] == ["vr-m"]
            assert not any(theirs in n for n in got["all_node_ids"])
        finally:
            await clean(provider, mine)
            await clean(provider, theirs)


class TestNeo4jConnectorEntities(_Contract):
    async def test_groups_without_records(self, neo4j_provider):
        await self.check_groups_without_records(neo4j_provider, _seed_neo4j, _clean_neo4j)

    async def test_vrids_off_by_default(self, neo4j_provider):
        await self.check_vrids_off_by_default(neo4j_provider, _seed_neo4j, _clean_neo4j)

    async def test_vrids_deduplicated(self, neo4j_provider):
        await self.check_vrids_deduplicated(neo4j_provider, _seed_neo4j, _clean_neo4j)

    async def test_empty_connector(self, neo4j_provider):
        await self.check_empty_connector(neo4j_provider, _seed_neo4j, _clean_neo4j)

    async def test_other_connectors_untouched(self, neo4j_provider):
        await self.check_other_connectors_untouched(neo4j_provider, _seed_neo4j, _clean_neo4j)


class TestArangoConnectorEntities(_Contract):
    async def test_groups_without_records(self, arango_provider):
        await self.check_groups_without_records(arango_provider, _seed_arango, _clean_arango)

    async def test_vrids_off_by_default(self, arango_provider):
        await self.check_vrids_off_by_default(arango_provider, _seed_arango, _clean_arango)

    async def test_vrids_deduplicated(self, arango_provider):
        await self.check_vrids_deduplicated(arango_provider, _seed_arango, _clean_arango)

    async def test_empty_connector(self, arango_provider):
        await self.check_empty_connector(arango_provider, _seed_arango, _clean_arango)

    async def test_other_connectors_untouched(self, arango_provider):
        await self.check_other_connectors_untouched(arango_provider, _seed_arango, _clean_arango)
