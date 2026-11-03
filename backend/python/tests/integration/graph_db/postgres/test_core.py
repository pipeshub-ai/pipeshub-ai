"""Generic documents and edges of the PostgreSQL graph provider, against a real database: what is written is read back, every call stays in the
ambient org, and an explicit transaction commits or rolls back as a whole.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from asyncpg.exceptions import InsufficientPrivilegeError

from app.core.org_context import MissingOrgContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


def _org() -> str:
    return uuid.uuid4().hex[:24]


@pytest.fixture
async def orgs(pg_provider, pg_db, org_ctx) -> AsyncIterator[tuple[str, str]]:
    a, b = _org(), _org()
    yield a, b
    for o in (a, b):
        with org_ctx(o):
            await pg_db.run(lambda tx, o=o: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", o), write=True)
            await pg_db.run(lambda tx, o=o: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def test_documents_are_written_read_updated_and_deleted(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    with org_ctx(a):
        assert await p.batch_upsert_nodes([{"_key": "r1", "name": "one", "kind": "x", "keep": 1}, {"_key": "r2", "name": "two", "kind": "y"}], "things")
        got = await p.get_document("r1", "things")
        assert got["_key"] == "r1" and got["_id"] == "things/r1" and got["name"] == "one"
        assert await p.get_document("nope", "things") is None
        # an upsert merges; a field set to None is removed
        await p.batch_upsert_nodes([{"_key": "r1", "name": None, "extra": True}], "things")
        merged = await p.get_document("r1", "things")
        assert "name" not in merged and merged["extra"] is True and merged["keep"] == 1
        assert [d["_key"] for d in await p.get_nodes_by_filters("things", {"kind": "y"})] == ["r2"]
        assert {d["_key"] for d in await p.get_nodes_by_field_in("things", "_key", ["r1", "r2", "zz"])} == {"r1", "r2"}
        assert {d["_key"] for d in await p.get_nodes_by_field_in("things", "kind", ["x", "y"])} == {"r1", "r2"}
        assert [d["_key"] for d in await p.get_documents_paginated("things", limit=1)] == ["r1"]
        assert [d["_key"] for d in await p.get_documents_paginated("things", limit=5, after_key="r1")] == ["r2"]
        assert await p.get_existing_keys("things", ["r1", "r9"]) == {"r1"}
        assert await p.create_node_if_absent("r1", "things", {"name": "again"}) is False
        assert await p.create_node_if_absent("r3", "things", {"name": "three"}) is True
        assert await p.update_node("r3", "things", {"name": "3"}) is True
        assert await p.update_node("missing", "things", {"name": "3"}) is False
        assert await p.update_node_fields_if_match("r3", "things", {"name": "x"}, {"name": "wrong"}) is False
        assert await p.update_node_fields_if_match("r3", "things", {"name": "x"}, {"name": "3"}) is True
        assert await p.batch_update_nodes([{"_key": "r1", "kind": "z"}, {"id": "r2", "kind": "z"}, {"_key": "ghost", "kind": "z"}], "things")
        assert {d["kind"] for d in await p.get_nodes_by_filters("things", {"kind": "z"})} == {"z"}
        assert await p.delete_nodes(["r1", "r2", "r3"], "things") is True
        assert await p.get_all_documents("things") == []


async def test_edges_are_written_read_and_deleted_and_a_node_delete_takes_its_edges(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    def edge(f: str, t: str, **kw: str) -> dict:
        return {"from_id": f, "from_collection": "things", "to_id": t, "to_collection": "things", **kw}

    with org_ctx(a):
        await p.batch_create_edges([edge("a", "b"), edge("a", "c")], "belongsTo")
        assert await p.get_edge("a", "things", "b", "things", "belongsTo") is not None
        assert await p.get_edge("b", "things", "a", "things", "belongsTo") is None
        assert {e["to_id"] for e in await p.get_edges_from_node("things/a", "belongsTo")} == {"b", "c"}
        assert [e["from_id"] for e in await p.get_edges_to_node("things/b", "belongsTo")] == ["a"]
        # parallel grants of one pair are two edges; a regrant with another type replaces the old one
        await p.batch_create_edges([edge("u", "k", type="USER", role="READER"), edge("u", "k", type="ORG", role="READER")], "permission")
        assert len(await p.get_edges_from_node("things/u", "permission")) == 2
        await p.batch_create_edges([edge("u", "k", type="USER", role="WRITER")], "permission")
        assert sorted(e["role"] for e in await p.get_edges_from_node("things/u", "permission")) == ["WRITER"]
        # a hierarchy edge and a link of one pair are told apart in nodeRelations
        await p.batch_create_edges([edge("p", "q", relationshipType="PARENT_CHILD"), edge("p", "q", relationshipType="BOOKMARK")], "nodeRelations")
        assert len(await p.get_edges_from_node("things/p", "nodeRelations")) == 2
        assert await p.batch_delete_edges([edge("p", "q")], "nodeRelations") == 1          # the hierarchy edge only; the link stays
        assert [e["relationshipType"] for e in await p.get_edges_from_node("things/p", "nodeRelations")] == ["BOOKMARK"]
        assert await p.delete_edge("a", "things", "b", "things", "belongsTo") is True
        assert await p.delete_edges_from("a", "things", "belongsTo") == 1
        await p.batch_upsert_nodes([{"_key": "gone"}], "things")
        await p.batch_create_edges([edge("gone", "x"), edge("y", "gone")], "belongsTo")
        await p.delete_nodes(["gone"], "things")
        assert await p.get_edges_from_node("things/gone", "belongsTo") == [] and await p.get_edges_to_node("things/gone", "belongsTo") == []
        await p.batch_create_edges([edge("s", "t")], "belongsTo")
        assert await p.delete_edges_to("t", "things", "belongsTo") == 1


async def test_a_record_delete_waits_for_the_records_module_instead_of_leaving_its_documents_behind(pg_provider, orgs, org_ctx) -> None:
    from app.services.graph_db.postgres.base import NotImplementedInPostgres
    with org_ctx(orgs[0]), pytest.raises(NotImplementedInPostgres):
        await pg_provider.delete_nodes_and_edges(["r1"], "records")


async def test_every_call_stays_in_the_ambient_org(pg_provider, orgs, org_ctx) -> None:
    a, b = orgs
    p = pg_provider
    e = {"from_id": "x", "from_collection": "things", "to_id": "y", "to_collection": "things"}
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "same", "v": "a"}, {"_key": "only-a", "v": "a"}], "things")
        await p.batch_create_edges([e], "belongsTo")
    with org_ctx(b):
        assert await p.get_document("same", "things") is None
        assert await p.get_all_documents("things") == []
        assert await p.get_nodes_by_filters("things", {"v": "a"}) == []
        assert await p.get_nodes_by_field_in("things", "_key", ["same"]) == []
        assert await p.get_existing_keys("things", ["same", "only-a"]) == set()
        assert await p.get_edge("x", "things", "y", "things", "belongsTo") is None
        assert await p.get_edges_from_node("things/x", "belongsTo") == []
        # writes under the same ids do not touch org a, and a create finds the id free
        assert await p.update_node("same", "things", {"v": "b"}) is False
        assert await p.batch_update_nodes([{"_key": "same", "v": "b"}], "things")
        assert await p.delete_nodes(["same"], "things") is False
        assert await p.delete_edges_from("x", "things", "belongsTo") == 0
        assert await p.create_node_if_absent("same", "things", {"v": "b"}) is True
    with org_ctx(a):
        assert (await p.get_document("same", "things"))["v"] == "a"
        assert await p.get_edge("x", "things", "y", "things", "belongsTo") is not None
    with org_ctx(b):
        assert (await p.get_document("same", "things"))["v"] == "b"


async def test_a_call_without_an_org_fails_instead_of_reading_every_org(pg_provider) -> None:
    with pytest.raises(MissingOrgContext):
        await pg_provider.get_all_documents("things")
    with pytest.raises(MissingOrgContext):
        await pg_provider.batch_upsert_nodes([{"_key": "x"}], "things")


async def test_a_transaction_commits_or_rolls_back_as_a_whole(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    with org_ctx(a):
        t = await p.begin_transaction([], [])
        await p.batch_upsert_nodes([{"_key": "t1"}], "things", transaction=t)
        await p.batch_create_edges([{"from_id": "t1", "from_collection": "things", "to_id": "t2", "to_collection": "things"}], "belongsTo", transaction=t)
        assert await p.get_document("t1", "things", transaction=t) is not None
        assert await p.get_document("t1", "things") is None                      # not visible outside before the commit
        await p.rollback_transaction(t)
        assert await p.get_document("t1", "things") is None and await p.get_edges_from_node("things/t1", "belongsTo") == []
        t = await p.begin_transaction([], [])
        await p.batch_upsert_nodes([{"_key": "t1"}], "things", transaction=t)
        await p.commit_transaction(t)
        assert await p.get_document("t1", "things") is not None


async def test_a_transaction_stays_in_the_org_it_began_in(pg_provider, orgs, org_ctx) -> None:
    """The unit opens on its first call and row-level security binds it to that call's org: another org's call on it reads nothing and cannot write."""
    a, b = orgs
    p = pg_provider
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "k", "v": "a"}], "things")
        t = await p.begin_transaction([], [])
        assert (await p.get_document("k", "things", transaction=t))["v"] == "a"
    try:
        with org_ctx(b):
            assert await p.get_document("k", "things", transaction=t) is None
            with pytest.raises(InsufficientPrivilegeError):          # row-level security refuses the other org's row
                await p.batch_upsert_nodes([{"_key": "k2", "v": "b"}], "things", transaction=t)
    finally:
        await p.rollback_transaction(t)
    with org_ctx(a):
        assert (await p.get_document("k", "things"))["v"] == "a"
    with org_ctx(b):
        assert await p.get_document("k2", "things") is None
