"""Record groups, the hierarchy reads and the subtree delete on PostgreSQL, in one org and across two.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed
from app.models.entities import RecordGroup
from app.services.graph_db.postgres.hierarchy import HierarchyMixin

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("hier"), pg_db), org or new_org())


def rec(org: str, rid: str, parent: str | None = None, **kw: object) -> dict:
    return {"_key": rid, "orgId": org, "recordName": rid.upper(), "externalRecordId": f"x-{rid}", "connectorId": "c1", "virtualRecordId": f"v-{rid}",
            **({"externalParentId": f"x-{parent}"} if parent else {}), **kw}


def link(f: str, t: str, rel: str = "PARENT_CHILD", fc: str = "records", tc: str = "records") -> dict:
    return {"from_id": f, "from_collection": fc, "to_id": t, "to_collection": tc, "relationshipType": rel}


async def tree(p: OrgBound) -> None:
    """a -> b -> c (PARENT_CHILD), a -> m (ATTACHMENT), and a link b ~ z that is not hierarchy."""
    await p.batch_upsert_nodes([rec(p.org, "a"), rec(p.org, "b", "a"), rec(p.org, "c", "b"), rec(p.org, "m", "a"), rec(p.org, "z")], "records")
    await p.batch_create_edges([link("a", "b"), link("b", "c"), link("a", "m", "ATTACHMENT"), link("b", "z", "BOOKMARK")], "nodeRelations")


NAMES = [n for n, f in vars(HierarchyMixin).items() if not n.startswith("__") and callable(f) and n != "_rows"]
CALLS = {
    "create_edges_if_absent": ([link("a", "b")], "nodeRelations"), "copy_document_relationships": ("a", "b"), "delete_edges_between_collections": ("a", "records", "nodeRelations", "records"),
    "delete_edges_by_relationship_types": ("a", "records", "nodeRelations", ["BOOKMARK"]), "delete_parent_child_edge_to_record": ("a",),
    "get_record_group_by_external_id": ("c1", "g"), "batch_upsert_record_groups": ([],), "create_record_group_relation": ("a", "g"), "create_record_groups_relation": ("g", "h"),
    "get_record_group_by_id": ("g",), "_ancestor_chains": ("a", None), "get_record_path": ("a",), "get_record_path_segments": ("a",),
    "get_record_group_virtual_record_ids": ("g",), "get_record_group_path": ("g",), "get_descendant_virtual_record_ids": ("a",), "get_record_parent_info": ("a",),
    "get_node_depths_batch": ("a", ["b"]), "delete_records_and_relations": ("a",), "_walk_subtree": (None, ["a"], None, [], None),
    "_inventory": (["a"], None, [], None), "_delete_events": ([], {}), "delete_records_recursive": (["a"], "c1"), "delete_single_record": ("a",),
    "_create_deleted_record_event_payload": ({"id": "a"},),
}
KW = {"_inventory": {"include_trashed": False, "live_only": True, "transaction": None}, "_walk_subtree": {"include_trashed": False, "live_only": True}}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS) | {"_create_deleted_record_event_payload"}


@pytest.mark.parametrize("name", sorted(n for n in CALLS if n not in ("_walk_subtree", "_delete_events", "_create_deleted_record_event_payload")))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*CALLS[name], **KW.get(name, {}))
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*CALLS[name], **KW.get(name, {}))


async def test_edges_are_created_only_between_existing_ends_and_an_existing_edge_is_kept(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "a"), rec(p.org, "b")], "records")
    await p.create_edges_if_absent([{**link("a", "b"), "note": "first"}, link("a", "ghost")], "nodeRelations")
    await p.create_edges_if_absent([{**link("a", "b"), "note": "second"}], "nodeRelations")
    [e] = await p.get_edges_from_node("records/a", "nodeRelations")
    assert e["note"] == "first" and e["to_id"] == "b"


async def test_deleting_edges_by_collection_type_and_the_parent_edge_only(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org}], "recordGroups")
    await p.batch_create_edges([link("g", "b", fc="recordGroups")], "nodeRelations")
    assert await p.delete_edges_by_relationship_types("b", "records", "nodeRelations", ["BOOKMARK", "NONE"]) == 1
    assert await p.get_edges_from_node("records/b", "recordLinks") == []
    assert await p.delete_parent_child_edge_to_record("b") is True                                  # the record's parent edge; the group's own stays
    assert [e["from_id"] for e in await p.get_edges_to_node("records/b", "nodeRelations")] == ["g"]
    assert await p.delete_parent_child_edge_to_record("b") is False
    assert await p.delete_edges_between_collections("a", "records", "nodeRelations", "records") == 1       # a -> m (a -> b went above)


async def test_relationships_copy_to_another_record_without_overwriting(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "s"), rec(p.org, "t"), {"_key": "d1"}, {"_key": "d2"}], "records")
    await p.batch_upsert_nodes([{"_key": "d1"}, {"_key": "d2"}], "departments")
    await p.batch_create_edges([{"from_id": "s", "from_collection": "records", "to_id": "d1", "to_collection": "departments", "extractedName": "one"},
                                {"from_id": "s", "from_collection": "records", "to_id": "d2", "to_collection": "departments"},
                                {"from_id": "t", "from_collection": "records", "to_id": "d2", "to_collection": "departments", "extractedName": "mine"}], "belongsToDepartment")
    assert await p.copy_document_relationships("s", "t") is True
    got = {e["to_id"]: e.get("extractedName") for e in await p.get_edges_from_node("records/t", "belongsToDepartment")}
    assert got == {"d1": "one", "d2": "mine"}


async def test_record_groups_are_written_read_nested_and_pathed(pg_db) -> None:
    p, other = bound(pg_db), bound(pg_db)
    groups = [RecordGroup(id=i, org_id=p.org, name=i, short_name=i, external_group_id=f"e-{i}", connector_name="GITHUB", connector_id="c1", group_type="PROJECT")
              for i in ("top", "mid", "leaf")]
    await p.batch_upsert_record_groups(groups)
    await p.create_record_groups_relation("mid", "top")
    await p.create_record_groups_relation("leaf", "mid")
    await p.batch_upsert_nodes([rec(p.org, "r1"), rec(p.org, "r2"), rec(p.org, "r3", virtualRecordId=None)], "records")
    await p.create_record_group_relation("r1", "leaf")
    await p.create_record_group_relation("r2", "mid")
    await p.create_record_group_relation("r3", "top")
    assert (await p.get_record_group_by_external_id("c1", "e-mid")).id == "mid" and await p.get_record_group_by_external_id("c1", "nope") is None
    assert (await p.get_record_group_by_id("leaf"))["_key"] == "leaf" and await other.get_record_group_by_id("leaf") is None
    assert sorted(await p.get_record_group_virtual_record_ids("mid")) == ["v-r1", "v-r2"]               # the group and what is nested beneath it
    assert sorted(await p.get_record_group_virtual_record_ids("top")) == ["v-r1", "v-r2"]               # r3 has no virtual record id
    assert await p.get_record_group_path("leaf") == ["top", "mid", "leaf"] and await p.get_record_group_path("nope") == []


async def test_a_record_path_follows_the_hierarchy_by_external_parent(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    assert await p.get_record_path("c") == "A/B/C" and await p.get_record_path_segments("c") == ["A", "B", "C"]
    assert await p.get_record_path("a") == "A" and await p.get_record_path("nope") is None
    assert await p.get_record_path_segments("z") == ["Z"]                                             # a link is not a parent
    await p.batch_create_edges([link("c", "a")], "nodeRelations")                                    # a cycle ends
    assert (await p.get_record_path("c")).endswith("C")


async def test_parent_info_depths_and_descendants(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org}], "recordGroups")
    await p.batch_create_edges([link("g", "a", fc="recordGroups")], "nodeRelations")
    assert await p.get_record_parent_info("b") == {"id": "a", "type": "record"}
    assert await p.get_record_parent_info("a") == {"id": "g", "type": "recordGroup"}
    assert await p.get_record_parent_info("z") is None                                               # a link is not a parent
    assert await p.get_node_depths_batch("a", ["b", "c", "m", "z", "a"], 3) == {"b": 1, "c": 2, "m": 1}
    assert await p.get_node_depths_batch("a", ["c"], 1) == {} and await p.get_node_depths_batch("a", []) == {}
    assert sorted(await p.get_descendant_virtual_record_ids("a")) == ["v-b", "v-c", "v-m"]           # children and attachments whose external parent is their parent


async def test_a_subtree_goes_with_its_type_documents_blocks_and_edges_and_the_report_says_what(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    for p in (a, b):
        await tree(p)
        await p.batch_upsert_nodes([{"_key": "blk", "recordId": "c", "name": "n"}], "blocks")
    out = await a.delete_records_recursive(["b", "nope"], "c1")
    assert out["success"] and sorted(r["record_id"] for r in out["deleted_records"]) == ["b", "c"]
    assert out["failed_records"] == [{"record_id": "nope", "reason": "Validation failed"}]
    assert out["total_requested"] == 2 and out["successfully_deleted"] == 1 and out["failed_count"] == 1
    assert sorted(p["recordId"] for p in out["eventData"]["payloads"]) == ["b", "c"] and out["eventData"]["topic"] == "record-events"
    assert await a.get_existing_record_keys(["a", "b", "c", "m", "z"]) == {"a", "m", "z"}            # a sibling, the parent and the linked one stay
    assert await a.get_document("blk", "blocks") is None
    assert await b.get_existing_record_keys(["a", "b", "c"]) == {"a", "b", "c"}                       # the other org untouched


async def test_a_delete_is_scoped_to_the_connector_and_to_a_folder_and_skips_the_trash(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_upsert_nodes([rec(p.org, "x", "b", connectorId="other")], "records")
    await p.batch_create_edges([link("b", "x")], "nodeRelations")
    assert (await p.delete_records_recursive(["a"], "other"))["failed_records"] == [{"record_id": "a", "reason": "Validation failed"}]
    out = await p.delete_records_recursive(["b", "a"], "c1", within_folder_id="a")                    # a is not below a, b is
    assert sorted(r["record_id"] for r in out["deleted_records"]) == ["b", "c"]
    assert await p.get_existing_record_keys(["x"]) == {"x"}                                           # another connector's child is not followed
    await p.batch_update_nodes([{"id": "a", "isDeleted": True}], "records")
    assert (await p.delete_records_recursive(["a"], "c1"))["successfully_deleted"] == 0
    assert (await p.delete_records_recursive(["a"], "c1", include_trashed_roots=True))["successfully_deleted"] == 1


async def test_without_cascade_only_attachments_go_and_survivors_lose_the_parent(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
    await p.batch_create_edges([{"from_id": "b", "from_collection": "records", "to_id": "g", "to_collection": "recordGroups"}], "belongsTo")
    out = await p.delete_records_recursive(["a"], "c1", cascade_children=False)
    assert sorted(r["record_id"] for r in out["deleted_records"]) == ["a", "m"]
    assert await p.get_existing_record_keys(["b", "c"]) == {"b", "c"}
    assert out["reparented"] == [{"record_id": "b", "record_group_id": "g", "inherits": False}]
    assert "externalParentId" not in await p.get_document("b", "records")


async def test_a_survivor_with_no_group_keeps_its_parent_id_as_on_neo4j(pg_db) -> None:
    """Neo4j matches the survivors through ``BELONGS_TO`` a record group (its docstring: cleared "only when they already BELONGS_TO a RecordGroup"), so a
    survivor with no group is neither reported for re-parenting nor cleared."""
    p = bound(pg_db)
    await tree(p)
    await p.batch_upsert_nodes([rec(p.org, "s", "a")], "records")
    await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
    await p.batch_create_edges([{"from_id": "b", "from_collection": "records", "to_id": "g", "to_collection": "recordGroups"}], "belongsTo")      # s has none
    out = await p.delete_records_recursive(["a"], "c1", cascade_children=False)
    assert [r["record_id"] for r in out["reparented"]] == ["b"]
    assert "externalParentId" not in await p.get_document("b", "records")
    assert (await p.get_document("s", "records"))["externalParentId"] == "x-a"


async def test_a_single_record_and_a_raw_delete(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    assert (await p.delete_single_record(""))["deleted_records"] == []
    out = await p.delete_single_record("c")
    assert out["success"] and out["successfully_deleted"] == 1 and out["eventData"]["payloads"][0]["recordId"] == "c"
    assert await p.delete_records_and_relations("b") is True and await p.delete_records_and_relations("b") is False
    assert await p.get_existing_record_keys(["a", "b", "c"]) == {"a"}
    assert {e["to_id"] for e in await p.get_edges_from_node("records/a", "nodeRelations")} == {"m"}


async def test_a_delete_inside_a_caller_transaction_rolls_back_with_the_caller(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    t = await p.begin_transaction([], [])
    out = await p.delete_records_recursive(["b"], "c1", transaction=t)
    assert out["success"]
    await p.rollback_transaction(t)
    assert await p.get_existing_record_keys(["b", "c"]) == {"b", "c"}


async def test_overlapping_concurrent_deletes_do_not_deadlock(pg_db) -> None:
    p = bound(pg_db)
    for _ in range(12):
        await tree(p)
        outs = await asyncio.gather(*(p.delete_records_recursive([r], "c1") for r in ("a", "b", "c", "m")))
        assert all(o["success"] for o in outs), [o for o in outs if not o["success"]]
        assert await p.get_existing_record_keys(["a", "b", "c", "m"]) == set()


async def test_deleting_a_cycle_and_a_self_loop_ends_and_reports_each_record_once(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_create_edges([link("c", "a"), link("z", "z")], "nodeRelations")
    out = await asyncio.wait_for(p.delete_records_recursive(["a", "a", "z"], "c1"), 20)           # a -> b -> c -> a; z -> z; "a" named twice
    assert out["success"] and sorted(r["record_id"] for r in out["deleted_records"]) == ["a", "b", "c", "m", "z"]
    assert out["failed_records"] == [] and len(out["eventData"]["payloads"]) == 5


async def test_stored_nulls_and_a_repeated_constraint_name_do_not_break_a_delete_or_a_walk(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    with org_context.bind(org_context.OrgContext(p.org, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("""UPDATE graph.nodes SET doc = doc || '{"isDeleted": null, "externalParentId": null, "version": null}'::jsonb
            WHERE org_id = $1 AND coll = 'records' AND id IN ('b', 'c')""", p.org), write=True)
    await p.batch_create_edges([{**link("a", "z", "FOREIGN_KEY"), "constraintName": "fk"}, {**link("a", "z", "FOREIGN_KEY"), "constraintName": "fk"}], "nodeRelations")
    assert await p.get_node_depths_batch("a", ["b", "c"], 3) == {"b": 1, "c": 2}
    assert await p.get_record_parent_info("b") == {"id": "a", "type": "record"}
    out = await p.delete_records_recursive(["a"], "c1")
    assert out["success"] and sorted(r["record_id"] for r in out["deleted_records"]) == ["a", "b", "c", "m"]


async def test_recordlinks_deletes_remove_links_and_noderelations_deletes_follow_the_neo4j_scope(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await p.batch_create_edges([link("b", "z", "BOOKMARK"), link("b", "m", "REFERENCE")], "nodeRelations")
    assert await p.delete_edges_by_relationship_types("b", "records", "recordLinks", ["BOOKMARK"]) == 1          # a link stored in nodeRelations
    assert await p.delete_edges_by_relationship_types("b", "records", "recordLinks", ["PARENT_CHILD"]) == 0     # never a hierarchy edge
    assert await p.delete_edges_between_collections("b", "records", "nodeRelations", "records") == 1             # the hierarchy edge b -> c only
    assert [e["relationshipType"] for e in await p.get_edges_from_node("records/b", "recordLinks")] == ["REFERENCE"]
    assert await p.delete_edges_between_collections("b", "records", "recordLinks", "records") == 1


async def test_copying_relationships_to_a_missing_record_creates_nothing(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "s"), {"_key": "d1"}], "records")
    await p.batch_upsert_nodes([{"_key": "d1"}], "departments")
    await p.batch_create_edges([{"from_id": "s", "from_collection": "records", "to_id": "d1", "to_collection": "departments"}], "belongsToDepartment")
    assert await p.copy_document_relationships("s", "ghost") is False
    assert await p.get_edges_from_node("records/ghost", "belongsToDepartment") == []
    assert await p.copy_document_relationships("ghost", "s") is True                                           # nothing to copy is not an error


async def test_the_first_of_two_duplicate_edges_in_a_batch_wins(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "a"), rec(p.org, "b")], "records")
    await p.create_edges_if_absent([{**link("a", "b", "BOOKMARK"), "note": "first"}, {**link("a", "b", "BOOKMARK"), "note": "second"},
                                    link("a", "b", "PARENT_CHILD"), link("a", "b", "ATTACHMENT")], "nodeRelations")
    edges = {e["relationshipType"]: e for e in await p.get_edges_from_node("records/a", "nodeRelations")}
    assert edges["BOOKMARK"]["note"] == "first" and edges["PARENT_CHILD"]["to_id"] == "b" and "ATTACHMENT" not in edges      # one hierarchy edge per pair: the first


async def a_backend_waits_on_a_lock(pg_db, seconds: float = 15) -> None:
    """Wait, from a connection of its own, until a backend is waiting for a lock: the interleaving the test is about, seen and not timed (pg_locks is readable by any role;
    pg_stat_activity shows the waits of other roles only to a monitoring role)."""
    async def waiting() -> bool:
        async with pg_db.runtime_uow() as tx:
            return bool(await tx.fetchval("SELECT 1 FROM pg_locks WHERE NOT granted AND locktype IN ('transactionid', 'tuple') LIMIT 1"))
    for _ in range(int(seconds / 0.05)):
        if await waiting():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no backend waited for a lock")


async def test_a_recursive_delete_waits_for_a_writer_that_is_attaching_a_type_document_with_another_id(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "w")], "records")
    txn = await p.begin_transaction(read=[], write=[])
    deleting: asyncio.Task | None = None
    try:
        await p.batch_update_nodes([{"id": "w", "recordName": "W2"}], "records", transaction=txn)               # the writer holds the record's row
        await p.batch_upsert_nodes([{"_key": "typedoc", "name": "t"}], "files", transaction=txn)
        await p.batch_create_edges([{"from_id": "w", "from_collection": "records", "to_id": "typedoc", "to_collection": "files"}], "isOfType", transaction=txn)
        deleting = asyncio.create_task(p.delete_records_recursive(["w"], "c1"))
        await a_backend_waits_on_a_lock(pg_db)                                                              # it waits on the record's row before it reads what hangs on it
        assert not deleting.done()
        await p.commit_transaction(txn)
        out = await asyncio.wait_for(deleting, 20)
        assert out["success"] and out["successfully_deleted"] == 1
        assert await p.get_document("typedoc", "files") is None and await p.get_edges_from_node("records/w", "isOfType") == []
    finally:
        await p.rollback_transaction(txn)                                                                    # a no-op once committed: a failed test must not keep the connection
        if deleting is not None:
            deleting.cancel()
            await asyncio.gather(deleting, return_exceptions=True)
