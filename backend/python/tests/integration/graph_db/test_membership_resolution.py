"""``resolve_virtual_record_state`` against a real Neo4j and a real ArangoDB.

Requires: docker compose -f tests/integration/compose/graph-db.yml up -d
Run: pytest tests/integration/graph_db/ -m integration

A record can belong to more than one record group at once. Drive team and Box
attach a ``belongsTo`` edge for the primary group *and* one per entry in
``shared_with_me_record_group_ids``, so the chunk's ``recordGroupIds`` has to be
the union of the scalar ``recordGroupId`` and every ``belongsTo`` edge — reading
the scalar alone silently drops every shared-with-me group.

Collections are the deliberate exception: their ``belongsTo`` points at
``apps/<kbId>``, not a record group, so they resolve to an empty list.
"""

import os
import uuid

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


def _log():
    from app.utils.logger import create_logger
    return create_logger("membership_resolution_test")


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


async def _seed_neo4j(provider, *, record_id, vrid, connector_id,
                      primary_group=None, extra_groups=(), app_id=None):
    """One record, its scalar group, and a belongsTo edge per group.

    ``app_id`` seeds the Collection shape instead: belongsTo -> apps/<id>.
    """
    await provider.client.execute_query(
        "CREATE (r:Record {id: $id, connectorId: $c, virtualRecordId: $v, "
        "recordGroupId: $rg, isDeleted: false})",
        parameters={"id": record_id, "c": connector_id, "v": vrid,
                    "rg": primary_group},
    )
    for gid in ([primary_group] if primary_group else []) + list(extra_groups):
        await provider.client.execute_query(
            "MERGE (g:RecordGroup {id: $gid}) "
            "WITH g MATCH (r:Record {id: $rid}) MERGE (r)-[:BELONGS_TO]->(g)",
            parameters={"gid": gid, "rid": record_id},
        )
    if app_id:
        await provider.client.execute_query(
            "MERGE (a:App {id: $aid}) "
            "WITH a MATCH (r:Record {id: $rid}) MERGE (r)-[:BELONGS_TO]->(a)",
            parameters={"aid": app_id, "rid": record_id},
        )


async def _clean_neo4j(provider, connector_id):
    await provider.client.execute_query(
        "MATCH (n) WHERE n.connectorId = $c DETACH DELETE n",
        parameters={"c": connector_id},
    )


class TestMultiGroupMembershipNeo4j:
    async def test_shared_with_me_groups_are_all_resolved(self, neo4j_provider):
        """Primary + every shared-with-me group, not just the scalar."""
        from app.services.vector_db.membership import resolve_virtual_record_state

        cid = f"conn-{uuid.uuid4().hex[:8]}"
        vrid = f"vr-{uuid.uuid4().hex[:8]}"
        try:
            await _seed_neo4j(
                neo4j_provider,
                record_id=f"{cid}-r1", vrid=vrid, connector_id=cid,
                primary_group=f"{cid}-primary",
                extra_groups=(f"{cid}-shared-a", f"{cid}-shared-b"),
            )
            state = await resolve_virtual_record_state(neo4j_provider, vrid)
            assert state.connector_ids == [cid]
            assert sorted(state.record_group_ids) == sorted(
                [f"{cid}-primary", f"{cid}-shared-a", f"{cid}-shared-b"]
            ), state.record_group_ids
            assert state.complete is True
        finally:
            await _clean_neo4j(neo4j_provider, cid)

    async def test_groups_are_unioned_across_a_deduplicated_vrid(self, neo4j_provider):
        """Two records sharing content contribute both their groups, once each."""
        from app.services.vector_db.membership import resolve_virtual_record_state

        cid_a = f"conn-{uuid.uuid4().hex[:8]}"
        cid_b = f"conn-{uuid.uuid4().hex[:8]}"
        vrid = f"vr-{uuid.uuid4().hex[:8]}"
        shared_group = f"{cid_a}-both"
        try:
            await _seed_neo4j(
                neo4j_provider, record_id=f"{cid_a}-r", vrid=vrid,
                connector_id=cid_a, primary_group=f"{cid_a}-g",
                extra_groups=(shared_group,),
            )
            await _seed_neo4j(
                neo4j_provider, record_id=f"{cid_b}-r", vrid=vrid,
                connector_id=cid_b, primary_group=f"{cid_b}-g",
                extra_groups=(shared_group,),
            )
            state = await resolve_virtual_record_state(neo4j_provider, vrid)
            assert sorted(state.connector_ids) == sorted([cid_a, cid_b])
            assert sorted(state.record_group_ids) == sorted(
                [f"{cid_a}-g", f"{cid_b}-g", shared_group]
            ), state.record_group_ids
            # Deduplicated, not repeated once per contributing record.
            assert len(state.record_group_ids) == len(set(state.record_group_ids))
        finally:
            await _clean_neo4j(neo4j_provider, cid_a)
            await _clean_neo4j(neo4j_provider, cid_b)

    async def test_a_collection_record_resolves_to_no_groups(self, neo4j_provider):
        """belongsTo -> apps/<kbId> is not a record group, by contract."""
        from app.services.vector_db.membership import resolve_virtual_record_state

        cid = f"conn-{uuid.uuid4().hex[:8]}"
        vrid = f"vr-{uuid.uuid4().hex[:8]}"
        try:
            await _seed_neo4j(
                neo4j_provider, record_id=f"{cid}-r1", vrid=vrid,
                connector_id=cid, primary_group=None, app_id=cid,
            )
            state = await resolve_virtual_record_state(neo4j_provider, vrid)
            assert state.connector_ids == [cid]
            assert state.record_group_ids == []
        finally:
            await _clean_neo4j(neo4j_provider, cid)
