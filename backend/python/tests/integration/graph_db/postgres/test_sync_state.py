"""Sync state, page tokens and the indexing-status writes on PostgreSQL, against a real database.

The answers are those of ``Neo4jProvider``. Every write stays in the org of the call: another org's rows (the same ids) are neither read nor changed.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.core.org_context import MissingOrgContext
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.postgres.sync_state import SyncStateMixin

from .test_identity import _org
from .test_identity import w as _identity_world  # noqa: F401
from .test_kb import other_org, users

if TYPE_CHECKING:
    from .test_access import World

pytestmark = pytest.mark.integration


@pytest.fixture
def w(request: pytest.FixtureRequest) -> World:
    return request.getfixturevalue("_identity_world")


async def record(w: World, key: str, **doc: object) -> None:
    await w.nodes("records", {"_key": key, "recordName": key, "connectorId": "c1", "orgId": w.org, **doc})


async def field(w: World, key: str, name: str) -> object:
    return (await w.p.get_document(key, "records")).get(name)


# ---- a user's sync state -------------------------------------------------------------------------------------------------------------------

async def test_a_users_sync_state_is_the_relation_to_the_app_of_that_name_in_this_org(w: World) -> None:
    await users(w, "u1", email="a@x.io")
    await users(w, "u2", email="b@x.io")
    await w.nodes("apps", {"_key": "drive", "name": "DRIVE", "type": "DRIVE"}, {"_key": "mail", "name": "GMAIL", "type": "GMAIL"})
    await w.edge("userAppRelation", ("users", "u1"), ("apps", "drive"), syncState="NOT_STARTED")
    await w.edge("userAppRelation", ("users", "u1"), ("apps", "mail"), syncState="DONE")
    await w.edge("userAppRelation", ("users", "u2"), ("apps", "drive"), syncState="DONE")
    s = await w.p.get_user_sync_state("a@x.io", "DRIVE")
    assert s == {"syncState": "NOT_STARTED", "from_id": "u1", "from_collection": "users", "to_id": "drive", "to_collection": "apps"}
    assert (await w.p.get_user_sync_state("a@x.io", "GMAIL"))["syncState"] == "DONE"
    assert await w.p.get_user_sync_state("A@x.io", "DRIVE") is None and await w.p.get_user_sync_state("a@x.io", "SLACK") is None
    assert await w.p.get_user_sync_state("nobody@x.io", "DRIVE") is None
    u = await w.p.update_user_sync_state("a@x.io", "IN_PROGRESS", "DRIVE")
    assert u["syncState"] == "IN_PROGRESS" and u["lastSyncUpdate"] > 0 and (u["from_id"], u["to_id"]) == ("u1", "drive")
    assert (await w.p.get_user_sync_state("a@x.io", "DRIVE"))["syncState"] == "IN_PROGRESS"
    assert (await w.p.get_user_sync_state("a@x.io", "GMAIL"))["syncState"] == "DONE"                  # the user's other app
    assert (await w.p.get_user_sync_state("b@x.io", "DRIVE"))["syncState"] == "DONE"                  # another user's relation to the same app
    assert await w.p.update_user_sync_state("a@x.io", "X", "SLACK") is None and await w.p.update_user_sync_state("nobody@x.io", "X", "DRIVE") is None
    with other_org():
        assert await w.p.get_user_sync_state("a@x.io", "DRIVE") is None
        assert await w.p.update_user_sync_state("a@x.io", "HACKED", "DRIVE") is None
    assert (await w.p.get_user_sync_state("a@x.io", "DRIVE"))["syncState"] == "IN_PROGRESS"


async def test_a_drives_sync_state_is_read_and_set_on_its_own_node(w: World) -> None:
    await w.nodes("drives", {"_key": "d1", "name": "one"}, {"_key": "d2", "name": "two"})
    assert (await w.p.get_drive_sync_state("d1"))["_key"] == "d1" and await w.p.get_drive_sync_state("nope") is None
    u = await w.p.update_drive_sync_state("d1", "SYNCING")
    assert u["sync_state"] == "SYNCING" and u["last_sync_update"] > 0 and u["name"] == "one"
    assert "sync_state" not in await w.p.get_drive_sync_state("d2")
    assert await w.p.update_drive_sync_state("nope", "X") is None
    with other_org():
        assert await w.p.get_drive_sync_state("d1") is None and await w.p.update_drive_sync_state("d1", "HACKED") is None
    assert (await w.p.get_drive_sync_state("d1"))["sync_state"] == "SYNCING"


# ---- page tokens ---------------------------------------------------------------------------------------------------------------------------

async def test_a_page_token_is_one_per_user_channel_and_resource_and_the_newest_matching_is_read(w: World) -> None:
    t = await w.p.store_page_token("ch1", "res1", "a@x.io", "tok-1", "2030-01-01")
    assert t["_key"] == "page_token_a@x.io_ch1_res1" and t["token"] == "tok-1" and t["expiration"] == "2030-01-01" and t["createdAtTimestamp"] > 0
    again = await w.p.store_page_token("ch1", "res1", "a@x.io", "tok-2")                   # replaces: one node, no expiry any more
    assert again["token"] == "tok-2" and "expiration" not in again and again["_key"] == t["_key"]
    assert len(await w.p.get_nodes_by_filters("pageTokens", {})) == 1
    await w.p.store_page_token("ch2", "res1", "a@x.io", "tok-3")
    await asyncio.sleep(0.01)                                                               # "newest" needs distinct times
    await w.p.store_page_token("ch1", "res1", "b@x.io", "tok-4")
    assert (await w.p.get_page_token_db(channel_id="ch1", resource_id="res1", user_email="a@x.io"))["token"] == "tok-2"
    assert (await w.p.get_page_token_db(channel_id="ch2"))["token"] == "tok-3"
    assert (await w.p.get_page_token_db(user_email="b@x.io"))["token"] == "tok-4"
    assert (await w.p.get_page_token_db(channel_id="ch1", user_email="b@x.io"))["token"] == "tok-4"
    newest = await w.p.get_page_token_db()                                                  # no filter: the newest of the org
    assert newest["token"] == "tok-4" and (await w.p.get_page_token_db(channel_id="", resource_id=""))["token"] == "tok-4"
    assert await w.p.get_page_token_db(channel_id="nope") is None and await w.p.get_page_token_db(user_email="c@x.io") is None
    with other_org():
        assert await w.p.get_page_token_db() is None and await w.p.get_page_token_db(channel_id="ch1") is None
        other = await w.p.store_page_token("ch1", "res1", "a@x.io", "theirs")               # the same key in another org is another node
        assert other["token"] == "theirs"
    assert (await w.p.get_page_token_db(channel_id="ch1", resource_id="res1", user_email="a@x.io"))["token"] == "tok-2"


async def test_a_failed_page_token_write_answers_none_and_does_not_log_the_token(w: World, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    real = w.p.batch_upsert_nodes

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down tok-secret")
    monkeypatch.setattr(w.p, "batch_upsert_nodes", boom)
    assert await w.p.store_page_token("ch1", "res1", "a@x.io", "tok-secret") is None
    assert "tok-secret" not in caplog.text
    monkeypatch.setattr(w.p, "batch_upsert_nodes", real)
    assert (await w.p.store_page_token("ch1", "res1", "a@x.io", "tok-secret"))["token"] == "tok-secret"


# ---- indexing status -----------------------------------------------------------------------------------------------------------------------

async def test_the_indexed_copy_flag_is_backfilled_for_completed_records_of_the_org_only(w: World) -> None:
    assert await w.p.records_accept_indexed_copy() is True
    await record(w, "done1", indexingStatus="COMPLETED")
    await record(w, "done2", indexingStatus="COMPLETED")
    await record(w, "done3", indexingStatus="COMPLETED")
    await record(w, "kept", indexingStatus="COMPLETED", hasIndexedCopy=False)
    await record(w, "empty", indexingStatus="EMPTY", hasIndexedCopy=False)
    await record(w, "queued", indexingStatus="QUEUED")
    await record(w, "none")
    with other_org():
        await w.nodes("records", {"_key": "done1", "indexingStatus": "COMPLETED", "orgId": "x"})
        assert await w.p.backfill_indexed_copy_flags() == 1
        assert await field(w, "done1", "hasIndexedCopy") is True
    assert await field(w, "done1", "hasIndexedCopy") is None                                  # the other org's run left this org's record alone
    assert await w.p.backfill_indexed_copy_flags(batch_size=2) == 3                           # in batches of two: 2 + 1
    assert [await field(w, k, "hasIndexedCopy") for k in ("done1", "done2", "done3", "kept", "empty", "queued", "none")] == [True, True, True, False, False, None, None]
    assert await w.p.backfill_indexed_copy_flags() == 0                                       # idempotent
    assert await w.p.backfill_indexed_copy_flags(batch_size=0) == 0                           # a size of 0 does not loop


async def test_the_backfill_under_a_system_context_marks_every_org(w: World, pg_db) -> None:
    other = _org()
    await record(w, "mine", indexingStatus="COMPLETED")
    try:
        with other_org(other):
            await w.nodes("organizations", {"_key": other, "isActive": True})
            await w.nodes("records", {"_key": "theirs", "indexingStatus": "COMPLETED", "orgId": other})
        token = org_context._ctx.set(None)
        try:
            with org_context.system("test.backfill"):
                assert await w.p.backfill_indexed_copy_flags() >= 2
        finally:
            org_context._ctx.reset(token)
        assert await field(w, "mine", "hasIndexedCopy") is True
        with other_org(other):
            assert (await w.p.get_document("theirs", "records"))["hasIndexedCopy"] is True
    finally:
        with other_org(other):
            await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", other), write=True)


async def test_a_connectors_records_are_reset_by_the_filters_given_and_nothing_else_changes(w: World) -> None:
    await record(w, "a", indexingStatus="COMPLETED", hasIndexedCopy=True)
    await record(w, "b", indexingStatus="FAILED")
    await record(w, "c", indexingStatus="QUEUED", isDeleted=True)
    await record(w, "d", indexingStatus="FAILED", connectorId="c2")
    await record(w, "e")
    r = w.p.reset_indexing_status_for_connector

    async def status() -> list[object]:
        return [await field(w, k, "indexingStatus") for k in "abcde"]
    await r("c1", "QUEUED", exclude_statuses=["COMPLETED"], only_statuses=["FAILED"], reason="redo")
    assert await status() == ["COMPLETED", "QUEUED", "QUEUED", "FAILED", None] and await field(w, "b", "reason") == "redo" and await field(w, "c", "reason") is None
    await r("c1", "FAILED", only_statuses=[])                                                  # an empty only-list is nothing, not everything
    assert await status() == ["COMPLETED", "QUEUED", "QUEUED", "FAILED", None]
    await r("", "X")
    assert await status() == ["COMPLETED", "QUEUED", "QUEUED", "FAILED", None]
    await r("c1", "PAUSED", visibility=RecordVisibility.LIVE)
    assert await status() == ["PAUSED", "PAUSED", "QUEUED", "FAILED", "PAUSED"] and await field(w, "a", "hasIndexedCopy") is True      # the previous copy stays searchable
    await r("c1", "ALL", exclude_statuses=[])
    assert await status() == ["ALL", "ALL", "ALL", "FAILED", "ALL"]
    await record(w, "f", indexingStatus="KEEP")
    await r("c1", "Z", exclude_statuses=["KEEP"])                                              # an excluded status alone is left as it is
    assert [await field(w, k, "indexingStatus") for k in "af"] == ["Z", "KEEP"]
    with other_org():
        await r("c1", "HACKED")
    assert await field(w, "a", "indexingStatus") == "Z"


async def test_a_failed_reset_raises_and_the_next_call_works(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await record(w, "a", indexingStatus="FAILED")
    real = w.p._q

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "_q", boom)
    with pytest.raises(ConnectionError):                                                       # the rebuild after it must not run on stale statuses
        await w.p.reset_indexing_status_for_connector("c1", "QUEUED")
    monkeypatch.setattr(w.p, "_q", real)
    await w.p.reset_indexing_status_for_connector("c1", "QUEUED")
    assert await field(w, "a", "indexingStatus") == "QUEUED"


async def test_queued_duplicates_of_the_finished_record_are_promoted_within_its_org_only(w: World) -> None:
    same = {"md5Checksum": "m", "sizeInBytes": 5}
    await record(w, "ref", indexingStatus="COMPLETED", extractionStatus="COMPLETED", **same)
    await record(w, "d1", indexingStatus="QUEUED", **same)
    await record(w, "d2", indexingStatus="QUEUED", md5Checksum="m", sizeInBytes=6)           # another size
    await record(w, "d3", indexingStatus="QUEUED", md5Checksum="other", sizeInBytes=5)
    await record(w, "d4", indexingStatus="QUEUED", orgId="another-org", **same)               # a copy that names another org
    await record(w, "d5", indexingStatus="QUEUED", isDeleted=True, **same)
    await record(w, "d6", indexingStatus="COMPLETED", **same)
    await record(w, "d7", indexingStatus="QUEUED", connectorId="c9", **same)                  # another connector's copy of the same file is still a duplicate
    with other_org():
        await w.nodes("records", {"_key": "d8", "indexingStatus": "QUEUED", "orgId": "x", **same})
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 2
    for k in ("d1", "d7"):
        d = await w.p.get_document(k, "records")
        assert (d["indexingStatus"], d["hasIndexedCopy"], d["virtualRecordId"], d["extractionStatus"], d["isDirty"]) == ("COMPLETED", True, "v1", "COMPLETED", False)
        assert d["lastIndexTimestamp"] > 0 and "reason" not in d
    assert [await field(w, k, "indexingStatus") for k in ("d2", "d3", "d4", "d5", "d6")] == ["QUEUED", "QUEUED", "QUEUED", "QUEUED", "COMPLETED"]
    ref = await w.p.get_document("ref", "records")
    assert ref["duplicateReconcilePending"] is True and ref["duplicateReconcileAttempts"] == 0 and ref["duplicateReconcileDueAt"] > 0
    with other_org():
        assert (await w.p.get_document("d8", "records")) is None
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v1") == 0         # nothing queued is left
    await record(w, "d9", indexingStatus="QUEUED", **same)
    assert await w.p.update_queued_duplicates_status("ref", "FAILED", None, reason="boom") == 1
    d = await w.p.get_document("d9", "records")
    assert (d["indexingStatus"], d["extractionStatus"], d["reason"]) == ("FAILED", "FAILED", "boom") and "virtualRecordId" not in d and "hasIndexedCopy" not in d
    assert "duplicateReconcilePending" in await w.p.get_document("ref", "records")


async def test_queued_duplicates_wait_or_are_skipped_when_the_reference_cannot_promote_them(w: World) -> None:
    await record(w, "pending", indexingStatus="COMPLETED", extractionStatus="QUEUED", md5Checksum="m")
    await record(w, "nomd5", indexingStatus="COMPLETED")
    await record(w, "noorg", indexingStatus="COMPLETED", md5Checksum="m", orgId=None)
    await record(w, "dup", indexingStatus="QUEUED", md5Checksum="m")
    for ref in ("pending", "nomd5", "noorg", "missing"):
        assert await w.p.update_queued_duplicates_status(ref, "COMPLETED", "v") == 0
    assert await field(w, "dup", "indexingStatus") == "QUEUED"
    with other_org():
        assert await w.p.update_queued_duplicates_status("pending", "FAILED") == 0
    await w.p.update_node("pending", "records", {"extractionStatus": "COMPLETED"})              # the enrichment ended: now they are promoted
    assert await w.p.update_queued_duplicates_status("pending", "COMPLETED", "v") == 1
    assert await field(w, "dup", "indexingStatus") == "COMPLETED"


async def test_a_failed_promotion_answers_minus_one_and_the_next_call_promotes(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await record(w, "ref", indexingStatus="COMPLETED", extractionStatus="COMPLETED", md5Checksum="m")
    await record(w, "dup", indexingStatus="QUEUED", md5Checksum="m")
    real = w.p.batch_update_nodes

    async def boom(*a: object, **k: object) -> None:
        raise ConnectionError("down")
    monkeypatch.setattr(w.p, "batch_update_nodes", boom)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v") == -1
    assert await field(w, "dup", "indexingStatus") == "QUEUED"
    monkeypatch.setattr(w.p, "batch_update_nodes", real)
    assert await w.p.update_queued_duplicates_status("ref", "COMPLETED", "v") == 1


# ---- no org ----------------------------------------------------------------------------------------------------------------------------------

NAMES = sorted(n for n, f in vars(SyncStateMixin).items() if not n.startswith("__") and callable(f) and n != "records_accept_indexed_copy")
CALLS = {
    "get_user_sync_state": ("e", "s"), "update_user_sync_state": ("e", "st", "s"), "get_drive_sync_state": ("d",), "update_drive_sync_state": ("d", "st"),
    "store_page_token": ("c", "r", "e", "t"), "get_page_token_db": ("c",), "backfill_indexed_copy_flags": (), "reset_indexing_status_for_connector": ("c", "s"),
    "update_queued_duplicates_status": ("r", "s"),
}


def test_every_method_of_the_module_is_in_the_no_org_cases() -> None:
    assert set(NAMES) == set(CALLS)


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_org_is_an_error_even_where_a_failed_read_answers_a_default(pg_provider, name: str) -> None:
    with pytest.raises(MissingOrgContext):
        await getattr(pg_provider, name)(*CALLS[name])
    if name == "backfill_indexed_copy_flags":
        return                                                  # under a system context it is the sweep of every org (tested above)
    with org_context.system("test.no-org"), pytest.raises(Exception, match="(?i)system|org"):
        await getattr(pg_provider, name)(*CALLS[name])
