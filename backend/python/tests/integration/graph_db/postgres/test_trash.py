"""Trash, the knowledge-base push claims and reindex on PostgreSQL, in one org and across two.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed
from app.services.graph_db.postgres.trash import TrashMixin
from app.services.graph_db.push_claim import PushClaim, PushExpect, PushSet

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("trash"), pg_db), org or new_org())


def rec(org: str, rid: str, parent: str | None = None, **kw: object) -> dict:
    return {"_key": rid, "orgId": org, "recordName": rid.upper(), "externalRecordId": f"x-{rid}", "connectorId": "c1", "virtualRecordId": f"v-{rid}",
            "recordType": "FILE", "origin": "CONNECTOR", "connectorName": "GITHUB", **({"externalParentId": f"x-{parent}"} if parent else {}), **kw}


def link(f: str, t: str, rel: str = "PARENT_CHILD") -> dict:
    return {"from_id": f, "from_collection": "records", "to_id": t, "to_collection": "records", "relationshipType": rel}


async def tree(p: OrgBound) -> None:
    """a -> b -> c, a -> m (attachment)."""
    await p.batch_upsert_nodes([rec(p.org, "a"), rec(p.org, "b", "a"), rec(p.org, "c", "b"), rec(p.org, "m", "a"), rec(p.org, "z")], "records")
    await p.batch_create_edges([link("a", "b"), link("b", "c"), link("a", "m", "ATTACHMENT")], "nodeRelations")


async def trash(p: OrgBound, ids: list[str], batch: str = "bt1", **kw: object) -> dict:
    return await p.soft_delete_records(ids, "c1", delete_source="USER", batch_id=batch, deleted_by_user_id="u1", **kw)


NAMES = [n for n, f in vars(TrashMixin).items() if not n.startswith("__") and callable(f) and n not in ("_qrows", "_trash_purge_row", "_create_reindex_event_payload",
                                                                                                           "is_trash_walk_index_ready")]
CALLS = {
    "soft_delete_records": (["a"], "c1"), "restore_records": ([{"id": "a"}], "b"), "get_records_in_delete_batch": ("b", "o"), "list_trashed_records": ("c1", "o"),
    "get_purgeable_trashed_records": ("o", 1), "get_trash_purge_stats": ("o",), "record_purge_failure": (["a"], "o", "e"), "purge_trashed_records": (["a"], "o", 1),
    "purge_trash_kept_record_groups": ("o",), "take_back_kept_record_group": ("g",), "claim_push_record": (), "retag_push_record": (),
    "reindex_single_record": ("a", "u", "o"), "reindex_record_group_records": ("g", 0, "u", "o"),
}
KW = {"soft_delete_records": {"delete_source": "USER", "batch_id": "b"},
      "claim_push_record": {"kb_id": "k", "org_id": "o", "key": "r", "expected": None, "new": PushSet(document_id="d", version=1)},
      "retag_push_record": {"key": "r", "kb_id": "k", "org_id": "o", "expected_revision": "1", "new_revision": "2"}}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    args = CALLS[name]
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*args, **KW.get(name, {}))
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*args, **KW.get(name, {}))


async def test_a_subtree_moves_to_the_trash_as_one_batch_and_a_second_try_finds_nothing(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    for p in (a, b):
        await tree(p)
    out = await trash(a, ["b", "nope"])
    assert sorted(r["record_id"] for r in out["soft_deleted_records"]) == ["b", "c"] and [f["record_id"] for f in out["failed_records"]] == ["nope"]
    doc = await a.get_document("c", "records")
    assert doc["isDeleted"] is True and doc["deleteBatchId"] == "bt1" and doc["deleteSource"] == "USER" and doc["deletedByUserId"] == "u1"
    assert (await a.get_document("a", "records")).get("isDeleted") is not True and (await b.get_document("b", "records")).get("isDeleted") is not True
    again = await trash(a, ["b"], "bt2")
    assert again["soft_deleted_records"] == [] and [f["record_id"] for f in again["failed_records"]] == ["b"]
    assert (await trash(a, []))["soft_deleted_records"] == []


async def test_records_in_a_batch_are_read_with_their_parent_and_a_restore_is_all_or_nothing(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["b"])
    batch = {i["record"]["id"]: i for i in await p.get_records_in_delete_batch("bt1", p.org)}
    assert set(batch) == {"b", "c"} and batch["b"]["parentId"] == "a" and batch["b"]["parentIsDeleted"] is False and batch["c"]["parentBatchId"] == "bt1"
    assert await p.restore_records([{"id": "b"}, {"id": "c"}, {"id": "missing"}], "bt1") == []                    # one is not in the batch: none restored
    assert await p.restore_records([{"id": "b"}], "other-batch") == []
    assert sorted(await p.restore_records([{"id": "b", "set": {"note": "back"}}, {"id": "c"}], "bt1", require_live_parent=True)) == ["b", "c"]
    got = await p.get_document("b", "records")
    assert got["isDeleted"] is False and got["note"] == "back" and "deleteBatchId" not in got and "deletedAtTimestamp" not in got


async def test_a_restore_keeps_the_null_fields_it_was_not_told_about_and_clears_the_ones_it_is(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "n", extra={"w": {"x": None, "y": 1}}, gone="old")], "records")
    await trash(p, ["n"])
    assert await p.restore_records([{"id": "n", "set": {"gone": None, "note": "back"}}], "bt1") == ["n"]
    got = await p.get_document("n", "records")
    assert got["extra"] == {"w": {"x": None, "y": 1}}     # what the caller did not name is as it was, a nested null too
    assert "gone" not in got and got["note"] == "back" and got["isDeleted"] is False                              # a null in ``set`` clears the field


async def test_a_restore_refuses_while_the_parent_is_in_the_trash_and_when_the_external_id_was_taken(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["a"], "bt-a")
    await p.batch_update_nodes([{"id": "c", "deleteBatchId": "bt-c"}], "records")
    assert await p.restore_records([{"id": "c"}], "bt-c", require_live_parent=True) == []                        # b, its parent, is still trashed
    assert await p.restore_records([{"id": "c"}], "bt-c") == ["c"]
    await p.batch_upsert_nodes([rec(p.org, "new", externalRecordId="x-b")], "records")                          # another record took b's external id
    await p.batch_update_nodes([{"id": "b", "deleteBatchId": "bt-b"}], "records")
    assert await p.restore_records([{"id": "b", "reclaimExternalRecordId": "x-b"}], "bt-b", connector_id="c1") == []   # taken by a live record: refused
    with pytest.raises(ValueError):
        await p.restore_records([{"id": "b", "reclaimExternalRecordId": "x-b"}], "bt-b")
    assert await p.restore_records([{"id": "b"}], "bt-b", connector_id="c1") == ["b"]                           # without the reclaim it just comes back


async def test_the_trash_lists_one_row_per_batch_newest_first_and_counts_batches(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["m"], "bt-m")
    await asyncio.sleep(0.01)
    await trash(p, ["b"], "bt-b")
    page = await p.list_trashed_records("c1", p.org)
    assert page["total"] == 2 and [i["record"]["id"] for i in page["items"]] == ["b", "m"]
    first = page["items"][0]
    assert first["parentId"] == "a" and first["parentIsDeleted"] is False and first["batchSize"] == 2 and first["rootCount"] == 1
    assert (await p.list_trashed_records("c1", p.org, skip=1, limit=1))["items"][0]["record"]["id"] == "m"
    assert (await p.list_trashed_records("other", p.org))["total"] == 0 and (await p.list_trashed_records("c1", ""))["items"] == []
    assert (await bound(pg_db).list_trashed_records("c1", p.org))["total"] == 0                                  # another org sees none of it


async def test_the_purge_takes_what_is_due_keeps_what_has_a_child_and_counts_failures(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["c", "m"], "bt1")
    await p.batch_update_nodes([{"id": "c", "deletedAtTimestamp": 10}, {"id": "m", "deletedAtTimestamp": 20}], "records")
    stats = await p.get_trash_purge_stats(p.org)
    assert stats["trashed"] == 2 and stats["stuck"] == 0 and stats["oldestDeletedAt"] == 10
    page = await p.get_purgeable_trashed_records(p.org, 15)
    assert [r["id"] for r in page["records"]] == ["c"] and page["next"] is None
    assert await p.record_purge_failure(["c", "a", "nope"], p.org, "boom") == 1                                   # only a trashed record counts
    out = await p.purge_trashed_records(["c", "m", "a"], p.org, 100)
    assert sorted(r["id"] if "id" in r else r["record_id"] for r in out["purged"]) == ["c", "m"] and out["kept"] == ["a"]
    assert await p.get_existing_record_keys(["a", "b", "c", "m"]) == {"a", "b"}
    assert (await p.get_trash_purge_stats(p.org))["trashed"] == 0
    assert await p.is_trash_walk_index_ready() is True


async def test_a_trashed_record_that_still_has_a_child_is_kept_by_the_purge(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["b"], "bt1")
    await p.batch_update_nodes([{"id": "b", "deletedAtTimestamp": 10}, {"id": "c", "deletedAtTimestamp": 10}], "records")
    out = await p.purge_trashed_records(["b"], p.org, 100)
    assert out["purged"] == [] and out["kept"] == ["b"]                                                          # c is b's child
    assert await p.get_existing_record_keys(["b"]) == {"b"}


async def test_record_groups_marked_deleted_at_source_are_purged_only_when_empty(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "connectorId": "c1", "isDeletedAtSource": True},
                                {"_key": "g2", "orgId": p.org, "connectorId": "c1", "isDeletedAtSource": True},
                                {"_key": "g3", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
    await p.batch_upsert_nodes([rec(p.org, "r1")], "records")
    await p.batch_create_edges([{"from_id": "r1", "from_collection": "records", "to_id": "g2", "to_collection": "recordGroups"}], "belongsTo")
    assert await p.purge_trash_kept_record_groups(p.org) == ["g1"]                                                # g2 has a member, g3 is not marked
    assert await p.take_back_kept_record_group("g2") is True and await p.take_back_kept_record_group("nope") is False
    assert (await p.get_document("g2", "recordGroups"))["isDeletedAtSource"] is False
    assert await p.purge_trash_kept_record_groups(p.org) == []


async def kb(p: OrgBound, status: str = "ACTIVE") -> None:
    await p.batch_upsert_nodes([{"_key": "kb1", "orgId": p.org, "type": "KB", "name": "Docs", "status": status}], "apps")
    await p.batch_upsert_nodes([rec(p.org, "k1", connectorId="kb1", externalRecordId="d1", externalRevisionId="r1", version=1)], "records")


async def test_two_racing_push_creates_both_pass_the_claim_and_a_taken_key_is_refused(pg_db) -> None:
    p = bound(pg_db)
    await kb(p)
    new = PushSet(document_id="d9", version=1)
    claims = await asyncio.gather(*(p.claim_push_record(kb_id="kb1", org_id=p.org, key="fresh", expected=None, new=new) for _ in range(2)))
    assert sorted(c.value for c in claims) == [PushClaim.CLAIMED.value, PushClaim.CLAIMED.value]                  # the claim only checks; the creator inserts after
    assert (await p.claim_push_record(kb_id="kb1", org_id=p.org, key="k1", expected=None, new=new)) is PushClaim.KEY_TAKEN
    assert (await p.claim_push_record(kb_id="nope", org_id=p.org, key="x", expected=None, new=new)) is PushClaim.KB_MISSING
    assert (await p.claim_push_record(kb_id="kb1", org_id="other", key="x", expected=None, new=new)) is PushClaim.KB_MISSING
    await p.batch_update_nodes([{"id": "kb1", "status": "DELETING"}], "apps")
    assert (await p.claim_push_record(kb_id="kb1", org_id=p.org, key="x", expected=None, new=new)) is PushClaim.KB_DELETING


async def test_a_push_update_moves_a_record_only_while_it_holds_the_expected_revision_and_version(pg_db) -> None:
    p = bound(pg_db)
    await kb(p)
    new = PushSet(document_id="d2", version=2)
    assert await p.claim_push_record(kb_id="kb1", org_id=p.org, key="k1", expected=PushExpect(document_id="d1", revision="r1", version=1), new=new) is PushClaim.CLAIMED
    assert (await p.get_document("k1", "records"))["externalRecordId"] == "d2"
    assert await p.claim_push_record(kb_id="kb1", org_id=p.org, key="k1", expected=PushExpect(document_id="d1", revision="r1", version=1), new=new) is PushClaim.LOST
    assert await p.retag_push_record(key="k1", kb_id="kb1", org_id=p.org, expected_revision="r1", new_revision="r2") is True
    assert await p.retag_push_record(key="k1", kb_id="kb1", org_id=p.org, expected_revision="r1", new_revision="r3") is False
    assert await p.retag_push_record(key="k1", kb_id="kb1", org_id="other", expected_revision="r2", new_revision="r3") is False


async def test_reindex_refuses_a_missing_trashed_or_code_record_and_an_unknown_user(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "gone", isDeleted=True), rec(p.org, "code", connectorName="GITHUB", recordType="CODE_FILE"),
                                rec(p.org, "plain", origin="WEIRD")], "records")
    assert (await p.reindex_single_record("nope", "u", p.org))["code"] == 404
    assert (await p.reindex_single_record("gone", "u", p.org))["code"] == 400
    assert (await p.reindex_single_record("code", "u", p.org))["code"] == 400
    assert (await p.reindex_single_record("plain", "u", p.org))["code"] == 404                                   # no such user


async def test_reindex_of_a_collection_record_needs_a_role_and_publishes_by_depth(pg_db) -> None:
    p = bound(pg_db)
    await kb(p)
    await p.batch_update_nodes([{"id": "k1", "origin": "UPLOAD", "connectorName": "KB", "recordType": "FILE", "indexingStatus": "COMPLETED"}], "records")
    await p.batch_create_edges([{"from_id": "k1", "from_collection": "records", "to_id": "kb1", "to_collection": "apps"}], "belongsTo")
    await p.batch_upsert_nodes([{"_key": "me", "userId": "ext-me", "orgId": p.org, "email": "me@x.io"}, {"_key": "other", "userId": "ext-other", "orgId": p.org}], "users")
    await p.batch_create_edges([{"from_id": "me", "from_collection": "users", "to_id": "kb1", "to_collection": "apps", "type": "USER", "role": "WRITER"}], "permission")
    assert (await p.reindex_single_record("k1", "ext-other", p.org))["code"] == 403                              # a user with no role on the collection
    one = await p.reindex_single_record("k1", "ext-me", p.org)
    assert one["success"] and one["userRole"] == "WRITER" and one["eventData"]["topic"] == "record-events" and one["eventData"]["eventType"] == "newRecord"
    assert (await p.get_document("k1", "records"))["indexingStatus"] == "NOT_STARTED"                           # the terminal status is cleared for the consumer
    deep = await p.reindex_single_record("k1", "ext-me", p.org, depth=-1, status_filters=["FAILED"])
    assert deep["eventData"]["topic"] == "sync-events" and deep["eventData"]["payload"]["depth"] == 100 and deep["eventData"]["payload"]["statusFilters"] == ["FAILED"]


async def test_reindex_of_a_record_group_checks_the_group_the_connector_and_the_user(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "connectorId": "c1", "connectorName": "GITHUB"}, {"_key": "g2", "orgId": p.org}], "recordGroups")
    await p.batch_upsert_nodes([{"_key": "me", "userId": "ext-me", "orgId": p.org}], "users")
    assert (await p.reindex_record_group_records("nope", 0, "ext-me", p.org))["code"] == 404
    assert (await p.reindex_record_group_records("g2", 0, "ext-me", p.org))["code"] == 400                       # no connector on the group
    assert (await p.reindex_record_group_records("g1", 0, "ext-nobody", p.org))["code"] == 404
    assert (await p.reindex_record_group_records("g1", 0, "ext-me", p.org))["code"] == 403                       # the user cannot reach the group


async def test_reindex_of_a_connector_record_goes_through_the_access_check_and_the_connector_state(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "c1", "orgId": p.org, "type": "CONNECTOR", "isActive": True, "name": "Git"}], "apps")
    await p.batch_upsert_nodes([{"_key": "me", "userId": "ext-me", "orgId": p.org}, {"_key": "nobody", "userId": "ext-nobody", "orgId": p.org}], "users")
    await p.batch_upsert_nodes([rec(p.org, "open"), rec(p.org, "shut")], "records")
    await p.batch_create_edges([{"from_id": "me", "from_collection": "users", "to_id": "c1", "to_collection": "apps", "type": "USER", "role": "READER"},
                                {"from_id": "me", "from_collection": "users", "to_id": "open", "to_collection": "records", "type": "USER", "role": "READER"}], "permission")
    ok = await p.reindex_single_record("open", "ext-me", p.org, depth=3, status_filters=["FAILED"])
    assert ok["success"] and ok["connector"] == "GITHUB" and ok["useBatchReindex"] is True and ok["userRole"] is None
    assert ok["eventData"]["topic"] == "sync-events" and ok["eventData"]["eventType"] == "github.reindex"
    assert ok["eventData"]["payload"] == {"orgId": p.org, "recordId": "open", "depth": 3, "connectorId": "c1", "connector": "github", "userKey": "me", "statusFilters": ["FAILED"]}
    assert (await p.reindex_single_record("shut", "ext-me", p.org))["code"] == 403                                 # no grant on the record
    assert (await p.reindex_single_record("open", "ext-nobody", p.org))["code"] == 403                             # a user with no path to it
    await p.batch_update_nodes([{"id": "c1", "isActive": False}], "apps")
    off = await p.reindex_single_record("open", "ext-me", p.org)
    assert off["code"] == 409 and "Git" in off["reason"]                                                          # the connector is disabled


async def test_a_purge_page_of_no_rows_is_empty_and_a_restore_of_the_wrong_batch_restores_nothing(pg_db) -> None:
    p = bound(pg_db)
    await tree(p)
    await trash(p, ["m"], "bt1")
    assert await p.get_purgeable_trashed_records(p.org, 10**12, limit=0) == {"records": [], "held": 0, "next": None}
    assert await p.restore_records([{"id": "m"}], None) == []                                   # the batch must match: None means a record with no batch
    assert await p.restore_records([{"id": "m"}, {"id": "m"}], "bt1") == ["m"]                  # an id named twice is restored once


async def test_a_second_push_claim_in_a_shared_transaction_is_lost(pg_db) -> None:
    p = bound(pg_db)
    await kb(p)
    t = await p.begin_transaction([], [])
    try:
        expected = PushExpect(document_id="d1", revision="r1", version=1)
        assert await p.claim_push_record(kb_id="kb1", org_id=p.org, key="k1", expected=expected, new=PushSet(document_id="d2", version=2), transaction=t) is PushClaim.CLAIMED
        assert await p.claim_push_record(kb_id="kb1", org_id=p.org, key="k1", expected=expected, new=PushSet(document_id="d3", version=2), transaction=t) is PushClaim.LOST
    finally:
        await p.rollback_transaction(t)


async def test_reindex_of_a_record_group_returns_the_connector_for_the_caller_to_publish(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "c1", "orgId": p.org, "type": "CONNECTOR", "isActive": True, "name": "Git"}], "apps")
    await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "connectorId": "c1", "connectorName": "GITHUB"}], "recordGroups")
    await p.batch_upsert_nodes([{"_key": "me", "userId": "ext-me", "orgId": p.org}], "users")
    await p.batch_create_edges([{"from_id": "me", "from_collection": "users", "to_id": "c1", "to_collection": "apps", "type": "USER", "role": "READER"},
                                {"from_id": "me", "from_collection": "users", "to_id": "g1", "to_collection": "recordGroups", "type": "USER", "role": "READER"}], "permission")
    ok = await p.reindex_record_group_records("g1", -1, "ext-me", p.org)
    assert ok == {"success": True, "connectorId": "c1", "connectorName": "GITHUB", "depth": 100, "recordGroupId": "g1", "userKey": "me"}
    assert (await p.reindex_record_group_records("g1", -5, "ext-me", p.org))["depth"] == 0                       # any other negative depth is direct records only
    await p.batch_update_nodes([{"id": "c1", "isActive": False}], "apps")
    assert (await p.reindex_record_group_records("g1", 0, "ext-me", p.org))["code"] == 409


async def test_moving_a_record_to_the_trash_changes_only_the_trash_fields(pg_db) -> None:
    """A null inside the record (a nested one) is the record's own: only the trash fields are written, and a delete without a user clears an earlier one."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "n1", meta={"keep": None, "x": 1}), rec(p.org, "n2", deletedByUserId="old")], "records")
    await trash(p, ["n1"])
    doc = await p.get_document("n1", "records")
    assert doc["isDeleted"] is True and doc["deletedByUserId"] == "u1" and doc["meta"] == {"keep": None, "x": 1}
    await p.soft_delete_records(["n2"], "c1", delete_source="USER", batch_id="b2", deleted_by_user_id=None)
    doc = await p.get_document("n2", "records")
    assert doc["isDeleted"] is True and "deletedByUserId" not in doc
