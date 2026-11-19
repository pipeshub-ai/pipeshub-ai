"""Deleting one record on PostgreSQL: the permission rules, the attachments and the event, the soft delete, and the unlink of what a full sync missed.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.config.constants.arangodb import DeleteSource
from app.core import org_context
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.postgres.delete import DeleteMixin

from .pg_support import OrgBound, StandInProvider, new_org

pytestmark = pytest.mark.integration


def bound(pg_db: object, org: str | None = None) -> OrgBound:
    return OrgBound(StandInProvider(logging.getLogger("del"), pg_db), org or new_org())


def rec(org: str, rid: str, **kw: object) -> dict:
    return {"_key": rid, "orgId": org, "recordName": rid.upper(), "externalRecordId": f"x-{rid}", "connectorId": "c1", "recordType": "FILE", "version": 1,
            "origin": "CONNECTOR", "connectorName": "GITHUB", "mimeType": "text/plain", "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            "virtualRecordId": f"v-{rid}", **kw}


async def grant(p: OrgBound, record: str, role: str, user: str = "ext-u") -> None:
    """The user ``user`` (created if need be) holds ``role`` on the record."""
    await p.batch_upsert_nodes([{"_key": f"k-{user}", "userId": user, "orgId": p.org}], "users")
    await p.batch_create_edges([{"from_id": f"k-{user}", "from_collection": "users", "to_id": record, "to_collection": "records", "type": "USER", "role": role}], "permission")


def attach(f: str, t: str) -> dict:
    return {"from_id": f, "from_collection": "records", "to_id": t, "to_collection": "records", "relationshipType": "ATTACHMENT"}


NO_ORG = {"delete_connector_instance": ("c", "o"), "delete_record": ("r", "u", "o"), "delete_record_by_external_id": ("c", "e", "u"), "unlink_nodes_missed_by_full_sync": ("c", {}),
          "_direct_attachment_ids": ("r", "o", None), "_attachments_to_delete": ("r", "o", None), "_collect_connector_entities": ("c",),
          "_refuse_without_delete_role": ("r", {}, "u", None), "_node_fields_by_id": ("records", ["a"], ("f",)), "_delete_all_edges_for_nodes": ("", ["records/a"], ["nodeRelations"]),
          "sweep_deleted_connector_writes": ("c",)}
METHODS = {n for n, f in vars(DeleteMixin).items() if not n.startswith("__") and callable(f)} - {"_drows"}


def test_every_public_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert METHODS == set(NO_ORG)


@pytest.mark.parametrize("name", sorted(NO_ORG))
async def test_no_org_is_an_error_even_where_a_failed_delete_answers_a_dictionary(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*NO_ORG[name])
    with org_context.system("test.no-org"), pytest.raises((SystemContextNotAllowed, MissingOrgContext)):
        await getattr(pg_provider, name)(*NO_ORG[name])


async def test_a_record_of_another_org_is_reported_like_a_missing_one(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    await a.batch_upsert_nodes([rec(a.org, "r1")], "records")
    assert (await a.delete_record("r1", "u", b.org))["code"] == 404 and (await a.delete_record("nope", "u", a.org))["code"] == 404
    assert (await a.delete_record("r1", "u", ""))["code"] == 404
    assert await a.get_existing_record_keys(["r1"]) == {"r1"}


async def test_a_collection_record_needs_owner_or_writer_and_goes_with_its_subtree(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "kb1", "orgId": p.org, "type": "KB", "name": "Docs"}], "apps")
    await p.batch_upsert_nodes([rec(p.org, "folder", origin="UPLOAD", connectorName="KB", connectorId="kb1"),
                                rec(p.org, "file", origin="UPLOAD", connectorName="KB", connectorId="kb1", externalParentId="x-folder")], "records")
    await p.batch_create_edges([{"from_id": i, "from_collection": "records", "to_id": "kb1", "to_collection": "apps"} for i in ("folder", "file")], "belongsTo")
    await p.batch_create_edges([{"from_id": "folder", "from_collection": "records", "to_id": "file", "to_collection": "records", "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    await p.batch_upsert_nodes([{"_key": "w", "userId": "ext-w", "orgId": p.org}, {"_key": "r", "userId": "ext-r", "orgId": p.org}, {"_key": "n", "userId": "ext-n", "orgId": p.org}], "users")
    await p.batch_create_edges([{"from_id": "w", "from_collection": "users", "to_id": "kb1", "to_collection": "apps", "type": "USER", "role": "WRITER"},
                                {"from_id": "r", "from_collection": "users", "to_id": "kb1", "to_collection": "apps", "type": "USER", "role": "READER"}], "permission")
    assert (await p.delete_record("folder", "ext-nobody", p.org))["code"] == 404                                   # no such user
    assert (await p.delete_record("folder", "ext-n", p.org))["code"] == 403 and (await p.delete_record("folder", "ext-r", p.org))["code"] == 403
    out = await p.delete_record("folder", "ext-w", p.org)
    assert out["success"] and out["isKb"] is True and out["connectorId"] == "kb1"
    assert await p.get_existing_record_keys(["folder", "file"]) == set()                                           # the contents went with the folder


async def test_a_mail_goes_with_its_attachments_and_one_event_each_and_a_soft_delete_trashes_them_together(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "mail"), rec(p.org, "att1"), rec(p.org, "att2"), rec(p.org, "keep"), rec(p.org, "k_att")], "records")
    await p.batch_create_edges([attach("mail", "att1"), attach("mail", "att2"), attach("keep", "k_att")], "nodeRelations")
    await grant(p, "mail", "OWNER")
    soft = await p.delete_record("keep", "ext-u", p.org, soft_delete=True, delete_source=DeleteSource.CONNECTOR)
    assert soft["success"] and soft["softDeleted"]
    assert (await p.get_document("k_att", "records"))["isDeleted"] is True and (await p.get_document("keep", "records"))["deleteBatchId"] == (await p.get_document("k_att", "records"))["deleteBatchId"]
    res = await p.delete_record("mail", "ext-u", p.org)
    assert res["success"] and res["attachments_deleted"] == 2 and res["eventData"]["eventType"] == "deleteRecord"
    assert {x["recordId"] for x in res["eventData"]["payloads"]} == {"mail", "att1", "att2"}
    assert await p.get_existing_record_keys(["mail", "att1", "att2", "keep"]) == {"keep"}


async def test_a_hard_delete_takes_the_blocks_of_the_record_and_of_its_attachments(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "mail"), rec(p.org, "att"), rec(p.org, "other")], "records")
    await p.batch_create_edges([attach("mail", "att")], "nodeRelations")
    await p.batch_upsert_nodes([{"_key": "b-mail", "orgId": p.org, "recordId": "mail"}, {"_key": "b-att", "orgId": p.org, "recordId": "att"},
                                {"_key": "b-other", "orgId": p.org, "recordId": "other"}], "blocks")
    await grant(p, "mail", "OWNER")
    assert (await p.delete_record("mail", "ext-u", p.org))["success"]
    assert await p.get_document("b-mail", "blocks") is None and await p.get_document("b-att", "blocks") is None
    assert await p.get_document("b-other", "blocks") is not None


@pytest.mark.parametrize("soft", [False, True])
async def test_a_user_who_can_only_view_a_connector_record_cannot_delete_it(pg_db, soft: bool) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "doc"), rec(p.org, "other")], "records")
    await grant(p, "doc", "READER", "ext-viewer")
    await p.batch_upsert_nodes([{"_key": "k-none", "userId": "ext-none", "orgId": p.org}], "users")       # holds nothing on it
    for who in ("ext-viewer", "ext-none"):
        out = await p.delete_record("doc", who, p.org, soft_delete=soft)
        assert out["success"] is False and out["code"] == 403
    assert (await p.delete_record("doc", "ext-nobody", p.org, soft_delete=soft))["code"] == 404
    assert (await p.delete_record("doc", "ext-viewer", p.org, soft_delete=soft))["success"] is False
    assert (await p.get_document("doc", "records")).get("isDeleted") is not True
    await grant(p, "doc", "WRITER", "ext-writer")
    assert (await p.delete_record("doc", "ext-writer", p.org, soft_delete=soft))["success"]


async def test_a_role_given_through_a_group_counts_and_a_connector_s_own_delete_needs_no_user(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "g1"), rec(p.org, "g2")], "records")
    await p.batch_upsert_nodes([{"_key": "k-m", "userId": "ext-m", "orgId": p.org}], "users")
    await p.batch_upsert_nodes([{"_key": "team", "orgId": p.org}], "groups")
    await p.batch_create_edges([{"from_id": "k-m", "from_collection": "users", "to_id": "team", "to_collection": "groups", "type": "USER", "role": "READER"}], "permission")
    await p.batch_create_edges([{"from_id": "team", "from_collection": "groups", "to_id": "g1", "to_collection": "records", "type": "GROUP", "role": "OWNER"}], "permission")
    assert (await p.delete_record("g1", "ext-m", p.org))["success"]
    assert (await p.delete_record("g2", "ext-m", p.org))["code"] == 403
    assert (await p.delete_record("g2", "ext-m", p.org, soft_delete=True, delete_source=DeleteSource.CONNECTOR))["success"]


async def test_a_belongs_to_edge_to_a_group_is_not_membership_for_a_deleting_role(pg_db) -> None:
    """Membership is what the access check reads: a USER permission edge from the user to the group, not a belongsTo edge."""
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "g1")], "records")
    await p.batch_upsert_nodes([{"_key": "k-m", "userId": "ext-m", "orgId": p.org}], "users")
    await p.batch_upsert_nodes([{"_key": "team", "orgId": p.org}], "groups")
    await p.batch_create_edges([{"from_id": "k-m", "from_collection": "users", "to_id": "team", "to_collection": "groups", "entityType": "GROUP"}], "belongsTo")
    await p.batch_create_edges([{"from_id": "team", "from_collection": "groups", "to_id": "g1", "to_collection": "records", "type": "GROUP", "role": "OWNER"}], "permission")
    assert (await p.delete_record("g1", "ext-m", p.org))["code"] == 403


async def test_a_failed_block_delete_leaves_the_record_in_place(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "r1")], "records")
    await p.batch_upsert_nodes([{"_key": "b1", "orgId": p.org, "recordId": "r1"}], "blocks")
    await grant(p, "r1", "OWNER")

    async def boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("block delete failed")
    monkeypatch.setattr(StandInProvider, "delete_blocks_for_records", boom)
    assert (await p.delete_record("r1", "ext-u", p.org))["success"] is False
    assert await p.get_existing_record_keys(["r1"]) == {"r1"} and await p.get_document("b1", "blocks") is not None


async def test_delete_by_external_id_hard_and_soft_and_a_missing_one_is_none(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "a"), rec(p.org, "b")], "records")
    assert await p.delete_record_by_external_id("c1", "nope", "ext-u") is None
    hard = await p.delete_record_by_external_id("c1", "x-a", "ext-u")
    assert hard["success"] and await p.get_existing_record_keys(["a"]) == set()
    soft = await p.delete_record_by_external_id("c1", "x-b", "ext-u", soft_delete=True)
    assert soft["success"] and (await p.get_record_by_external_id("c1", "x-b")) is None
    assert (await p.get_record_by_external_id("c1", "x-b", visibility=RecordVisibility.ALL)).id == "b"


async def test_a_full_sync_unlinks_what_it_missed_and_keeps_what_it_saw(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    for p in (a, b):
        await p.batch_upsert_nodes([rec(p.org, "seen"), rec(p.org, "missed")], "records")
        await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "connectorId": "c1"}, {"_key": "g2", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
        await p.batch_create_edges([{"from_id": i, "from_collection": "records", "to_id": "g1", "to_collection": "recordGroups"} for i in ("seen", "missed")], "belongsTo")
    n, ok = await a.unlink_nodes_missed_by_full_sync("c1", {"records": ["seen"], "recordGroups": ["g1", "g2"]})
    assert ok is True and n >= 1
    assert [e["from_id"] for e in await a.get_edges_to_node("recordGroups/g1", "belongsTo")] == ["seen"]          # the missed record lost its group edge
    assert len(await b.get_edges_to_node("recordGroups/g1", "belongsTo")) == 2                                      # the other org is untouched
    assert (await a.unlink_nodes_missed_by_full_sync("nobody", {}))[1] is True


async def test_a_delete_inside_a_caller_transaction_rolls_back_with_it(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "a")], "records")
    await grant(p, "a", "OWNER")
    t = await p.begin_transaction([], [])
    assert (await p.delete_record("a", "ext-u", p.org, t))["success"]
    await p.rollback_transaction(t)
    assert await p.get_existing_record_keys(["a"]) == {"a"}


async def test_a_full_sync_touches_only_its_own_connector_in_the_org(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "c1r"), rec(p.org, "c2r", connectorId="c2")], "records")
    await p.batch_upsert_nodes([{"_key": "g1", "orgId": p.org, "connectorId": "c1"}, {"_key": "g2", "orgId": p.org, "connectorId": "c2"}], "recordGroups")
    await p.batch_create_edges([{"from_id": "c1r", "from_collection": "records", "to_id": "g1", "to_collection": "recordGroups"},
                                {"from_id": "c2r", "from_collection": "records", "to_id": "g2", "to_collection": "recordGroups"}], "belongsTo")
    n, ok = await p.unlink_nodes_missed_by_full_sync("c1", {})                                                 # c1 saw nothing: all its nodes are unlinked
    assert ok is True and n >= 1
    assert await p.get_edges_to_node("recordGroups/g1", "belongsTo") == []
    assert [e["from_id"] for e in await p.get_edges_to_node("recordGroups/g2", "belongsTo")] == ["c2r"]          # the other connector keeps its edge


async def test_outside_the_window_only_the_grants_go_and_inside_everything_does(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([rec(p.org, "old", sourceCreatedAtTimestamp=10), rec(p.org, "new", sourceCreatedAtTimestamp=900)], "records")
    await p.batch_upsert_nodes([{"_key": "g", "orgId": p.org, "connectorId": "c1"}], "recordGroups")
    await p.batch_upsert_nodes([{"_key": "u", "orgId": p.org, "userId": "ext-u"}], "users")
    await p.batch_create_edges([{"from_id": i, "from_collection": "records", "to_id": "g", "to_collection": "recordGroups"} for i in ("old", "new")], "belongsTo")
    await p.batch_create_edges([{"from_id": "u", "from_collection": "users", "to_id": i, "to_collection": "records", "type": "USER", "role": "READER"} for i in ("old", "new")],
                               "permission")
    n, ok = await p.unlink_nodes_missed_by_full_sync("c1", {"recordGroups": ["g"]}, window_start_ms=500)       # neither record was seen; "old" predates the window
    assert ok is True and n >= 1
    assert [e["from_id"] for e in await p.get_edges_to_node("recordGroups/g", "belongsTo")] == ["old"]          # old keeps its structure, new lost it
    assert await p.get_edges_to_node("records/old", "permission") == [] and await p.get_edges_to_node("records/new", "permission") == []   # both lost their grants


async def test_a_failed_hard_delete_by_external_id_raises_so_the_caller_rolls_back(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "kb1", "orgId": p.org, "type": "KB", "name": "Docs"}], "apps")
    await p.batch_upsert_nodes([rec(p.org, "file", origin="UPLOAD", connectorName="KB", connectorId="kb1")], "records")
    await p.batch_create_edges([{"from_id": "file", "from_collection": "records", "to_id": "kb1", "to_collection": "apps"}], "belongsTo")
    await p.batch_upsert_nodes([{"_key": "n", "userId": "ext-n", "orgId": p.org}], "users")           # a user with no role on the collection
    with pytest.raises(Exception, match="Deletion failed"):
        await p.delete_record_by_external_id("kb1", "x-file", "ext-n")
    assert await p.get_existing_record_keys(["file"]) == {"file"}


async def test_a_soft_delete_of_a_collection_record_trashes_it_with_its_subtree(pg_db) -> None:
    p = bound(pg_db)
    await p.batch_upsert_nodes([{"_key": "kb1", "orgId": p.org, "type": "KB", "name": "Docs"}], "apps")
    await p.batch_upsert_nodes([rec(p.org, "folder", origin="UPLOAD", connectorName="KB", connectorId="kb1"),
                                rec(p.org, "file", origin="UPLOAD", connectorName="KB", connectorId="kb1", externalParentId="x-folder")], "records")
    await p.batch_create_edges([{"from_id": i, "from_collection": "records", "to_id": "kb1", "to_collection": "apps"} for i in ("folder", "file")], "belongsTo")
    await p.batch_create_edges([{"from_id": "folder", "from_collection": "records", "to_id": "file", "to_collection": "records", "relationshipType": "PARENT_CHILD"}], "nodeRelations")
    await p.batch_upsert_nodes([{"_key": "w", "userId": "ext-w", "orgId": p.org}], "users")
    await p.batch_create_edges([{"from_id": "w", "from_collection": "users", "to_id": "kb1", "to_collection": "apps", "type": "USER", "role": "OWNER"}], "permission")
    out = await p.delete_record("folder", "ext-w", p.org, soft_delete=True)
    assert out["success"] and out["softDeleted"]
    assert {i: (await p.get_document(i, "records"))["isDeleted"] for i in ("folder", "file")} == {"folder": True, "file": True}


async def test_delete_by_external_id_never_reaches_another_orgs_record(pg_db) -> None:
    a, b = bound(pg_db), bound(pg_db)
    await b.batch_upsert_nodes([rec(b.org, "same")], "records")
    assert await a.delete_record_by_external_id("c1", "x-same", "ext-u") is None
    assert await a.delete_record_by_external_id("c1", "x-same", "ext-u", soft_delete=True) is None
    assert await b.get_existing_record_keys(["same"]) == {"same"}
