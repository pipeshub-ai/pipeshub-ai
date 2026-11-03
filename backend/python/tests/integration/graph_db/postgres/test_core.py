"""Generic documents and edges of the PostgreSQL graph provider, against a real database: what is written is read back, every call stays in the
ambient org, and an explicit transaction commits or rolls back as a whole.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import MissingOrgContext, SystemContextNotAllowed
from app.services.graph_db.postgres.base import (
    TransactionClosed,
    TransactionOrgMismatch,
)

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


_EDGE = {"from_id": "x", "from_collection": "things", "to_id": "y", "to_collection": "things"}
# every implemented read and write method, as (name, args, kwargs)
CALLS = [
    ("get_document", ("k", "things"), {}),
    ("get_all_documents", ("things",), {}),
    ("get_documents_paginated", ("things",), {}),
    ("get_nodes_by_filters", ("things", {"v": "a"}), {}),
    ("get_nodes_by_field_in", ("things", "v", ["a"]), {}),
    ("get_existing_keys", ("things", ["k"]), {}),
    ("batch_upsert_nodes", ([{"_key": "k"}], "things"), {}),
    ("create_node_if_absent", ("k", "things", {}), {}),
    ("update_node", ("k", "things", {"v": 1}), {}),
    ("batch_update_nodes", ([{"_key": "k", "v": 1}], "things"), {}),
    ("update_node_fields_if_match", ("k", "things", {"v": 1}, {"v": 0}), {}),
    ("delete_nodes", (["k"], "things"), {}),
    ("delete_nodes_and_edges", (["k"], "things"), {}),
    ("batch_create_edges", ([_EDGE], "belongsTo"), {}),
    ("get_edge", ("x", "things", "y", "things", "belongsTo"), {}),
    ("get_edges_to_node", ("things/y", "belongsTo"), {}),
    ("get_edges_from_node", ("things/x", "belongsTo"), {}),
    ("batch_delete_edges", ([_EDGE], "belongsTo"), {}),
    ("delete_edge", ("x", "things", "y", "things", "belongsTo"), {}),
    ("delete_edges_from", ("x", "things", "belongsTo"), {}),
    ("delete_edges_to", ("y", "things", "belongsTo"), {}),
]


def test_the_no_org_cases_cover_every_method_the_module_implements() -> None:
    from app.services.graph_db.postgres.core import CoreMixin
    public = {n for n, f in vars(CoreMixin).items() if not n.startswith("_") and callable(f)}
    assert public == {c[0] for c in CALLS}


@pytest.mark.parametrize(("name", "args", "kwargs"), CALLS, ids=[c[0] for c in CALLS])
async def test_a_call_without_an_org_fails_instead_of_answering_empty(pg_provider, name, args, kwargs) -> None:
    """Also for the methods that swallow a database error and answer None or []: a missing org is a programming error, not an empty graph."""
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*args, **kwargs)


@pytest.mark.parametrize(("name", "args"), [("get_document", ("k", "things")), ("get_nodes_by_filters", ("things", {"v": "a"})),
                                           ("get_edges_from_node", ("things/x", "belongsTo")), ("get_edges_to_node", ("things/y", "belongsTo"))])
async def test_a_system_scope_cannot_read_org_data_through_the_swallowing_readers(pg_provider, name, args) -> None:
    from app.core import org_context
    with org_context.system("test.system"), pytest.raises(SystemContextNotAllowed):
        await getattr(pg_provider, name)(*args)


async def test_a_repeated_id_in_one_batch_keeps_the_last(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    with org_ctx(a):
        assert await pg_provider.batch_upsert_nodes([{"_key": "d", "v": 1, "keep": 1}, {"_key": "d", "v": 2}, {"_key": "e"}], "things")
        got = await pg_provider.get_document("d", "things")
        assert got["v"] == 2 and "keep" not in got


async def test_a_keyset_page_needs_a_key_order(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    with org_ctx(a):
        await pg_provider.batch_upsert_nodes([{"_key": "k1", "v": 2}, {"_key": "k2", "v": 1}], "things")
        with pytest.raises(ValueError, match="after_key"):
            await pg_provider.get_documents_paginated("things", sort_field="v", after_key="k1", raise_on_error=True)
        assert [d["_key"] for d in await pg_provider.get_documents_paginated("things", sort_field="v")] == ["k2", "k1"]


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


async def test_a_transaction_refuses_a_call_of_another_org(pg_provider, orgs, org_ctx) -> None:
    """The org is fixed when the transaction begins: a call under another org (first or later) raises instead of binding to it or reading nothing."""
    a, b = orgs
    p = pg_provider
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "k", "v": "a"}], "things")
        t = await p.begin_transaction([], [])
        assert (await p.get_document("k", "things", transaction=t))["v"] == "a"
        t2 = await p.begin_transaction([], [])
    try:
        with org_ctx(b):
            with pytest.raises(TransactionOrgMismatch):
                await p.get_document("k", "things", transaction=t, raise_on_error=True)
            with pytest.raises(TransactionOrgMismatch):
                await p.batch_upsert_nodes([{"_key": "k2", "v": "b"}], "things", transaction=t)
            with pytest.raises(TransactionOrgMismatch):      # the first use is under another org
                await p.batch_upsert_nodes([{"_key": "k3"}], "things", transaction=t2)
    finally:
        await p.rollback_transaction(t)
        await p.rollback_transaction(t2)
    with org_ctx(b):
        assert await p.get_all_documents("things") == []


async def test_an_unknown_or_finished_transaction_is_a_clear_error(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    with org_ctx(a):
        with pytest.raises(TransactionClosed, match="nope"):
            await pg_provider.commit_transaction("nope")
        await pg_provider.rollback_transaction("nope")                                  # tolerated, as on the other stores
        with pytest.raises(TransactionClosed, match="nope"):
            await pg_provider.batch_upsert_nodes([{"_key": "k"}], "things", transaction="nope")
        t = await pg_provider.begin_transaction([], [])
        await pg_provider.commit_transaction(t)
        with pytest.raises(TransactionClosed):
            await pg_provider.commit_transaction(t)


async def test_a_null_filter_means_absent_in_get_nodes_by_filters_and_nothing_in_a_page(pg_provider, orgs, org_ctx) -> None:
    """As on the Neo4j provider: ``get_nodes_by_filters`` reads None as 'absent or null'; ``get_documents_paginated`` compares with equality, so a None matches nothing."""
    a, _ = orgs
    p = pg_provider
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "has", "v": "x"}, {"_key": "none"}], "things")
        assert [d["_key"] for d in await p.get_nodes_by_filters("things", {"v": None})] == ["none"]
        assert [d["_key"] for d in await p.get_nodes_by_filters("things", {"v": "x"})] == ["has"]
        assert await p.get_documents_paginated("things", filters={"v": None}) == []


async def test_a_conditional_update_removes_a_field_set_to_none_and_needs_an_expectation(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "c", "v": "x", "w": "y"}], "things")
        assert await p.update_node_fields_if_match("c", "things", {"w": None, "z": 1}, {"v": "x"}) is True
        got = await p.get_document("c", "things")
        assert "w" not in got and got["z"] == 1
        assert await p.update_node_fields_if_match("c", "things", {"z": 2}, {"missing": None}) is True     # None expects the field absent
        assert await p.update_node_fields_if_match("c", "things", {"z": 3}, {"v": None}) is False
        with pytest.raises(ValueError, match="expectation"):
            await p.update_node_fields_if_match("c", "things", {"z": 4}, {})
        assert (await p.get_document("c", "things"))["z"] == 2


async def test_a_collection_prefix_must_name_the_collection_asked(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    with org_ctx(a):
        await p.batch_upsert_nodes([{"_key": "x"}], "users")
        await p.batch_upsert_nodes([{"_key": "x"}], "things")
        assert [d["_id"] for d in await p.get_nodes_by_field_in("things", "_id", ["things/x"])] == ["things/x"]
        assert await p.get_nodes_by_field_in("things", "_id", ["users/x"]) == []             # another collection's id is not this one's
        assert [d["_key"] for d in await p.get_nodes_by_field_in("things", "_key", ["x"])] == ["x"]
        assert await p.get_nodes_by_field_in("things", "_key", ["users/x"]) == []            # a key has no collection prefix


async def test_deleting_records_by_key_waits_for_the_records_module_too(pg_provider, orgs, org_ctx) -> None:
    from app.services.graph_db.postgres.base import NotImplementedInPostgres
    a, _ = orgs
    with org_ctx(a):
        await pg_provider.batch_upsert_nodes([{"_key": "r1"}], "records")
        with pytest.raises(NotImplementedInPostgres):
            await pg_provider.delete_nodes(["r1"], "records")
        assert await pg_provider.get_document("r1", "records") is not None                   # nothing was deleted


async def test_a_link_stored_with_no_key_is_found_and_deleted_under_its_own_collection(pg_provider, orgs, org_ctx) -> None:
    """``recordLinks`` rows carry no edge key; they are links all the same (a ``nodeRelations`` link is the one with a key)."""
    a, _ = orgs
    p = pg_provider
    link = {"from_id": "x", "from_collection": "records", "to_id": "y", "to_collection": "records"}
    with org_ctx(a):
        await p.batch_create_edges([link], "recordLinks")
        assert await p.get_edge("x", "records", "y", "records", "recordLinks") is not None
        assert await p.get_edge("x", "records", "y", "records", "nodeRelations") is None            # not the hierarchy edge of the pair
        assert await p.batch_delete_edges([link], "recordLinks") == 1
        assert await p.get_edge("x", "records", "y", "records", "recordLinks") is None


async def _links_and_a_hierarchy_edge(p, org_ctx, org: str) -> None:
    """Between x and y: the hierarchy edge, a keyed link in nodeRelations and a keyless row of recordLinks; and from x to z another keyed link."""
    def edge(f: str, t: str, **kw: str) -> dict:
        return {"from_id": f, "from_collection": "records", "to_id": t, "to_collection": "records", **kw}
    with org_ctx(org):
        await p.batch_create_edges([edge("x", "y", relationshipType="PARENT_CHILD"), edge("x", "y", relationshipType="BOOKMARK"), edge("x", "z", relationshipType="BOOKMARK")],
                                   "nodeRelations")
        await p.batch_create_edges([edge("x", "y")], "recordLinks")


@pytest.mark.parametrize(("direction", "node"), [("from", "x"), ("to", "y")])
async def test_a_directional_delete_over_record_links_covers_both_physical_link_collections(pg_provider, orgs, org_ctx, direction: str, node: str) -> None:
    a, _ = orgs
    p = pg_provider
    await _links_and_a_hierarchy_edge(p, org_ctx, a)
    with org_ctx(a):
        delete = p.delete_edges_from if direction == "from" else p.delete_edges_to
        n = await delete(node, "records", "recordLinks")
        assert n == (3 if direction == "from" else 2)                                                  # the keyed links and the keyless row, never the hierarchy edge
        assert await p.get_edge("x", "records", "y", "records", "nodeRelations") is not None
        assert await p.get_edge("x", "records", "y", "records", "recordLinks") is None


async def test_a_node_wide_read_or_delete_over_node_relations_covers_hierarchy_and_links_and_over_record_links_only_links(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    await _links_and_a_hierarchy_edge(p, org_ctx, a)
    with org_ctx(a):
        assert len(await p.get_edges_from_node("records/x", "recordLinks")) == 3
        assert len(await p.get_edges_from_node("records/x", "nodeRelations")) == 4
        assert await p.delete_edges_from("x", "records", "recordLinks") == 3
        assert len(await p.get_edges_from_node("records/x", "nodeRelations")) == 1
        assert await p.delete_edges_from("x", "records", "nodeRelations") == 1


async def test_hierarchy_edges_are_never_links_for_any_reader_or_deleter_of_record_links(pg_provider, orgs, org_ctx) -> None:
    a, _ = orgs
    p = pg_provider
    e = {"from_id": "x", "from_collection": "records", "to_id": "y", "to_collection": "records"}
    with org_ctx(a):
        await p.batch_create_edges([{**e, "relationshipType": "PARENT_CHILD"}, {**e, "to_id": "z", "relationshipType": "ATTACHMENT"}, {**e, "to_id": "w"}], "nodeRelations")
        assert await p.get_edge("x", "records", "y", "records", "recordLinks") is None
        assert await p.get_edges_from_node("records/x", "recordLinks") == []
        assert await p.get_edges_to_node("records/y", "recordLinks") == []
        assert await p.batch_delete_edges([e], "recordLinks") == 0
        assert await p.delete_edges_from("x", "records", "recordLinks") == 0
        assert await p.delete_edges_to("y", "records", "recordLinks") == 0
        assert len(await p.get_edges_from_node("records/x", "nodeRelations")) == 3
