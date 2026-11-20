"""A record's pending virtual record on the PostgreSQL graph provider: the holder lookups, the conditional commit and the index plans.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.graph_db.conditions import EQ, ONE_OF, Cond

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

RUNNING = ["IN_PROGRESS", "QUEUED", "NOT_STARTED"]


@pytest.fixture
async def org(pg_provider, pg_db, org_ctx) -> AsyncIterator[str]:
    o = uuid.uuid4().hex[:24]
    yield o
    with org_ctx(o):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def _put(p, org: str, key: str, vrid: str, status: str, **more: object) -> None:
    assert await p.batch_upsert_nodes(
        [{"_key": key, "orgId": org, "virtualRecordId": vrid, "indexingStatus": status, "md5Checksum": "m", "recordName": key, **more}], "records"
    )


async def test_a_running_record_holds_its_pending_vrid_under_that_id(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await _put(pg_provider, org, "a", "x", "IN_PROGRESS", pendingVirtualRecordId="y")
        await _put(pg_provider, org, "b", "x", "COMPLETED")
        assert await pg_provider.get_records_by_virtual_record_id("y") == ["a"]
        assert await pg_provider.get_virtual_record_ids_in_use(["y", "none"]) == {"y"}
        holders = await pg_provider.get_virtual_record_holders(["x", "y"], org)
        assert [h["id"] for h in holders["y"]] == ["a"]  # reported under the pending id, not the committed one
        assert sorted(h["id"] for h in holders["x"]) == ["a", "b"]


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "EMPTY"])
async def test_any_other_status_releases_the_pending_vrid(pg_provider, org, org_ctx, status) -> None:
    with org_ctx(org):
        await _put(pg_provider, org, "a", "x", status, pendingVirtualRecordId="y")
        assert await pg_provider.get_records_by_virtual_record_id("y") == []
        assert await pg_provider.get_virtual_record_ids_in_use(["y"]) == set()
        assert "y" not in await pg_provider.get_virtual_record_holders(["y"], org)


async def test_the_commit_is_one_conditional_write_that_removes_the_field(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await _put(pg_provider, org, "a", "x", "NOT_STARTED", pendingVirtualRecordId="y")
        conditions = [Cond("virtualRecordId", EQ, "x"), Cond("pendingVirtualRecordId", EQ, "y"), Cond("indexingStatus", ONE_OF, RUNNING)]
        assert not await pg_provider.update_node_fields_if(
            "a", "records", {"virtualRecordId": "y"}, [*conditions[:2], Cond("indexingStatus", ONE_OF, ["COMPLETED"])], unset=["pendingVirtualRecordId"]
        )
        assert await pg_provider.update_node_fields_if(
            "a", "records", {"virtualRecordId": "y", "indexingStatus": "COMPLETED"}, conditions, unset=["pendingVirtualRecordId"]
        )
        doc = await pg_provider.get_document("a", "records")
        assert doc["virtualRecordId"] == "y" and "pendingVirtualRecordId" not in doc
        assert not await pg_provider.update_node_fields_if("a", "records", {"virtualRecordId": "z"}, conditions, unset=["pendingVirtualRecordId"])


async def test_a_record_with_no_status_never_matches_a_one_of_condition(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        assert await pg_provider.batch_upsert_nodes([{"_key": "a", "orgId": org, "virtualRecordId": "x", "pendingVirtualRecordId": "y"}], "records")
        assert not await pg_provider.update_node_fields_if("a", "records", {"virtualRecordId": "y"}, [Cond("indexingStatus", ONE_OF, RUNNING)])


async def test_one_of_matches_a_string_only(pg_provider, org, org_ctx) -> None:
    """A number or a boolean that prints like a listed string is not a match (the other stores compare typed values)."""
    with org_ctx(org):
        assert await pg_provider.batch_upsert_nodes([{"_key": "a", "orgId": org, "version": 1, "flag": True}], "records")
        assert not await pg_provider.update_node_fields_if("a", "records", {"x": 1}, [Cond("version", ONE_OF, ["1"])])
        assert not await pg_provider.update_node_fields_if("a", "records", {"x": 1}, [Cond("flag", ONE_OF, ["true"])])


async def test_the_pending_write_is_a_compare_and_set(pg_provider, org, org_ctx) -> None:
    with org_ctx(org):
        await _put(pg_provider, org, "a", "x", "COMPLETED")
        assert await pg_provider.update_node_fields_if_match("a", "records", {"pendingVirtualRecordId": "p1"}, {"virtualRecordId": "x", "pendingVirtualRecordId": None})
        assert not await pg_provider.update_node_fields_if_match("a", "records", {"pendingVirtualRecordId": "p2"}, {"virtualRecordId": "x", "pendingVirtualRecordId": None})
        assert await pg_provider.update_node_fields_if_match("a", "records", {}, {"pendingVirtualRecordId": "p1"}, unset=["pendingVirtualRecordId"])
        assert "pendingVirtualRecordId" not in await pg_provider.get_document("a", "records")


async def test_the_holder_lookups_use_an_index_on_each_arm(org) -> None:
    """With enough rows that a scan would lose, the single and the batch lookup plan to index scans on both arms, never a sequential scan.

    Seeded and planned as the administrator, on the driver the other PostgreSQL tests use: the table's owner is the only role that can
    ANALYZE it, and a plan made on stale statistics proves nothing."""
    import os

    import asyncpg

    from app.services.graph_db.common.record_visibility import sql_holds_vrid

    admin_url = os.environ.get("PG_ADMIN_URL")
    if not admin_url:
        pytest.fail("PG_ADMIN_URL is not set")
    lit = chr(39)
    single = f"SELECT n.id FROM graph.nodes n WHERE n.org_id = {lit}{org}{lit} AND n.coll = 'records' AND {sql_holds_vrid('n', lit + 'v77' + lit)}"
    batch = (f"SELECT DISTINCT w.v FROM unnest(ARRAY[{lit}v1{lit},{lit}v2{lit}]) AS w(v) JOIN graph.nodes n "
             f"ON n.org_id = {lit}{org}{lit} AND n.coll = 'records' AND {sql_holds_vrid('n', 'w.v')}")

    async def explain(con: asyncpg.Connection) -> list[str]:
        return ["\n".join(r[0] for r in await con.fetch("EXPLAIN " + q)) for q in (single, batch)]

    con = await asyncpg.connect(admin_url)
    try:
        await con.execute("DELETE FROM graph.nodes WHERE org_id = $1", org)
        await con.execute(
            """INSERT INTO graph.nodes (org_id, coll, id, doc)
               SELECT $1, 'records', 'r' || g, jsonb_build_object('virtualRecordId', 'v' || g, 'orgId', $1::text, 'indexingStatus', 'COMPLETED')
               FROM generate_series(1, 30000) g""", org)
        await con.execute("ANALYZE graph.nodes")
        for text in await explain(con):
            assert "Seq Scan" not in text, text
            assert "pending_vrid" in text or "BitmapOr" in text, text
        # the control: with the index dropped the plans no longer use it, so the assertion above catches its absence
        await con.execute("DROP INDEX graph.nodes_record_pending_vrid")
        try:
            await con.execute("ANALYZE graph.nodes")
            for text in await explain(con):
                assert "pending_vrid" not in text, text
        finally:
            await con.execute(
                "CREATE INDEX nodes_record_pending_vrid ON graph.nodes (org_id, (doc->>'pendingVirtualRecordId')) WHERE coll = 'records' AND doc ? 'pendingVirtualRecordId'"
            )
    finally:
        await con.close()
