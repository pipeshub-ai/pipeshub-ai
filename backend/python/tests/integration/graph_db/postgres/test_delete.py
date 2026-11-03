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


def attach(f: str, t: str) -> dict:
    return {"from_id": f, "from_collection": "records", "to_id": t, "to_collection": "records", "relationshipType": "ATTACHMENT"}


NO_ORG = {"delete_record": ("r", "u", "o"), "delete_record_by_external_id": ("c", "e", "u"), "unlink_nodes_missed_by_full_sync": ("c", {}),
          "_direct_attachment_ids": ("r", "o", None), "_attachments_to_delete": ("r", "o", None), "_collect_connector_entities": ("c",),
          "_node_fields_by_id": ("records", ["a"], ("f",)), "_delete_all_edges_for_nodes": ("", ["records/a"], ["nodeRelations"])}
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
    soft = await p.delete_record("keep", "ext-u", p.org, soft_delete=True, delete_source=DeleteSource.CONNECTOR)
    assert soft["success"] and soft["softDeleted"]
    assert (await p.get_document("k_att", "records"))["isDeleted"] is True and (await p.get_document("keep", "records"))["deleteBatchId"] == (await p.get_document("k_att", "records"))["deleteBatchId"]
    res = await p.delete_record("mail", "ext-u", p.org)
    assert res["success"] and res["attachments_deleted"] == 2 and res["eventData"]["eventType"] == "deleteRecord"
    assert {x["recordId"] for x in res["eventData"]["payloads"]} == {"mail", "att1", "att2"}
    assert await p.get_existing_record_keys(["mail", "att1", "att2", "keep"]) == {"keep"}


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
    t = await p.begin_transaction([], [])
    assert (await p.delete_record("a", "ext-u", p.org, t))["success"]
    await p.rollback_transaction(t)
    assert await p.get_existing_record_keys(["a"]) == {"a"}
