"""The vector membership backfill's reads and the knowledge base upload's name check, on PostgreSQL against a real database.

Both ways: the backfill pages every record of a connector exactly once and nothing of another connector or org, never returns an app that is done or being
deleted, and a page that fails raises (an empty page would read as "finished") and reads the same page again once the store is back. The name check
returns the live files of its folder or knowledge base root and nothing of another folder, base or org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.core import org_context
from app.modules.indexing.vector_membership_backfill import (
    run_vector_membership_backfill_tick,
)
from app.services.graph_db.vector_membership_queries import PAGE_RECORD_FIELDS

from .test_access import World, _org
from .test_access import w as w
from .test_grants import failing, in_org

pytestmark = pytest.mark.integration


async def walk(w: World, connector: str, size: int) -> list[list[str]]:
    """The pages a backfill reads: from no cursor, each continuing after the last key, until a short page."""
    pages, after = [], None
    while len(pages) < 50:
        page = await w.p.page_records_for_vector_membership_backfill(connector, after, size)
        pages.append([r["_key"] for r in page])
        if len(page) < size:
            return pages
        after = page[-1]["_key"]
    raise AssertionError("the walk does not end")


async def test_the_next_app_is_the_first_that_is_neither_done_nor_being_deleted(w: World) -> None:
    other = _org()
    with in_org(other):
        await w.p.batch_upsert_nodes([{"_key": "a0-elsewhere", "orgId": other, "type": "DRIVE"}], "apps")
    await w.nodes("apps", {"_key": "a_done", "vectorMembershipBackfilled": True}, {"_key": "a_del", "status": "DELETING"}, {"_key": "a_todo2"},
                  {"_key": "a_todo1", "vectorMembershipBackfilled": False}, {"_key": "a_null", "vectorMembershipBackfilled": None, "status": "ACTIVE"})
    seen = []
    for _ in range(6):
        app = await w.p.get_app_needing_vector_membership_backfill()
        if not app:
            break
        seen.append(app["_key"])
        await w.p.update_node(app["_key"], "apps", {"vectorMembershipBackfilled": True})        # what the job records when it is done
    assert seen == ["a_null", "a_todo1", "a_todo2", "app1"] and await w.p.get_app_needing_vector_membership_backfill() is None        # another org's app never comes up
    assert (await w.p.get_app_needing_vector_membership_backfill()) is None
    with in_org(other):
        assert (await w.p.get_app_needing_vector_membership_backfill())["_key"] == "a0-elsewhere"


async def test_every_record_of_the_connector_is_paged_once_and_nothing_else(w: World) -> None:
    keys = [f"r{i:02d}" for i in range(7)]
    await w.nodes("records", *[{"_key": k, "connectorId": "c", "virtualRecordId": f"v{k}", "indexingStatus": "COMPLETED", **({"isDeleted": True} if k == "r03" else {})} for k in keys],
                  {"_key": "x1", "connectorId": "other"}, {"_key": "x2"})
    other = _org()
    with in_org(other):
        await w.p.batch_upsert_nodes([{"_key": "r99", "orgId": other, "connectorId": "c"}], "records")
    for size in (1, 3, 7, 50):
        pages = await walk(w, "c", size)
        assert [k for p in pages for k in p] == keys                      # all of them, once, in key order, the deleted one too
    assert [len(p) for p in await walk(w, "c", 3)] == [3, 3, 1] and [len(p) for p in await walk(w, "c", 7)] == [7, 0]
    page = await w.p.page_records_for_vector_membership_backfill("c", None, 2)
    assert set(page[0]) == set(PAGE_RECORD_FIELDS) and page[0]["_key"] == "r00" and page[0]["virtualRecordId"] == "vr00" and page[0]["orgId"] == w.org
    assert page[0]["isDeleted"] is None and [r["isDeleted"] for r in await w.p.page_records_for_vector_membership_backfill("c", "r02", 1)] == [True]
    assert [r["_key"] for r in await w.p.page_records_for_vector_membership_backfill("c", "", 2)] == ["r00", "r01"]       # an empty cursor is no cursor
    assert [r["_key"] for r in await w.p.page_records_for_vector_membership_backfill("c", None, 0)] == ["r00"]          # a limit below one is one
    assert await w.p.page_records_for_vector_membership_backfill("c", "r06", 5) == [] and await w.p.page_records_for_vector_membership_backfill("nope", None, 5) == []
    with in_org(other):
        assert [r["_key"] for r in await w.p.page_records_for_vector_membership_backfill("c", None, 5)] == ["r99"]


async def test_the_cursor_is_in_byte_order(w: World) -> None:
    await w.nodes("records", *[{"_key": k, "connectorId": "c"} for k in ("B1", "a1", "_x", "a2", "10", "9")])
    assert [k for p in await walk(w, "c", 2) for k in p] == ["10", "9", "B1", "_x", "a1", "a2"]


async def test_a_page_that_fails_raises_and_reads_the_same_page_again(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await w.nodes("records", *[{"_key": f"r{i}", "connectorId": "c"} for i in range(4)])
    first = await w.p.page_records_for_vector_membership_backfill("c", None, 2)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError):
        await w.p.page_records_for_vector_membership_backfill("c", first[-1]["_key"], 2)
    monkeypatch.setattr(w.p, "_q", real)
    assert [r["_key"] for r in await w.p.page_records_for_vector_membership_backfill("c", first[-1]["_key"], 2)] == ["r2", "r3"]
    assert await w.p.page_records_for_vector_membership_backfill("c", None, 2) == first


async def test_a_system_scope_cannot_page_records_it_names_no_org_for(pg_provider) -> None:
    with org_context.system("test.vector-membership"), pytest.raises(org_context.SystemContextNotAllowed):
        await pg_provider.page_records_for_vector_membership_backfill("c", None, 5)


async def test_two_orgs_sharing_a_connector_id_are_paged_and_marked_done_each_on_its_own(pg_provider, org_ctx) -> None:
    tag, orgs = uuid.uuid4().hex[:8], [_org(), _org()]
    cid = f"shared-{tag}"
    for o in orgs:
        with org_ctx(o):
            await pg_provider.batch_upsert_nodes([{"_key": cid, "type": "DRIVE", "orgId": o}], "apps")
            await pg_provider.batch_upsert_nodes([{"_key": f"{o[:6]}-{i}", "connectorId": cid, "orgId": o} for i in range(3)], "records")
    try:
        for o in orgs:
            with org_ctx(o):
                assert (await pg_provider.get_app_needing_vector_membership_backfill())["_key"] == cid
                got = [r["_key"] for r in await pg_provider.page_records_for_vector_membership_backfill(cid, None, 10)]
                assert got == [f"{o[:6]}-{i}" for i in range(3)]          # its own records, none of the other org's
                await pg_provider.update_node(cid, "apps", {"vectorMembershipBackfilled": True})
                assert await pg_provider.get_app_needing_vector_membership_backfill() is None
    finally:
        for o in orgs:
            with org_ctx(o):
                await pg_provider._db.run(lambda tx, o=o: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


class _Lock:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True


async def test_one_tick_of_the_job_pages_syncs_and_advances_in_each_apps_own_org(pg_provider, org_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real context binding: the loop has no org of its own, two orgs hold a connector of the same id (one a legacy instance whose document
    names no org), and every tick must move the cursor. The cross-org listing is narrowed to the test's orgs, so no other app is touched."""
    tag, orgs, outsider = uuid.uuid4().hex[:8], [_org(), _org()], _org()
    cid = f"0000-tick-{tag}"
    with org_ctx(outsider):                                     # sorts before the test's apps: left alone only because the listing is narrowed
        await pg_provider.batch_upsert_nodes([{"_key": f"0000-early-{tag}", "type": "DRIVE", "orgId": outsider}], "apps")
        before = await pg_provider.get_document(f"0000-early-{tag}", "apps")
    real = pg_provider._system_apps_page

    async def narrowed(*a: object, **k: object) -> list[dict]:
        return [d for d in await real(*a, **k) if d.get("orgId") in orgs]
    monkeypatch.setattr(pg_provider, "_system_apps_page", narrowed)
    for o in orgs:
        with org_ctx(o):
            await pg_provider.batch_upsert_nodes([{"_key": cid, "type": "DRIVE", **({"orgId": o} if o == orgs[0] else {})}], "apps")
            await pg_provider.batch_upsert_nodes([{"_key": f"{o[:6]}-{i}", "connectorId": cid, "orgId": o, "virtualRecordId": f"v-{o[:6]}-{i}"} for i in range(3)], "records")
    with org_ctx(orgs[1]):
        assert "orgId" not in await pg_provider.get_document(cid, "apps")
    synced: list[tuple[str, str]] = []

    async def sync(vrid: str) -> None:
        synced.append((org_context.current_org_id(), vrid))
    pipeline = SimpleNamespace(sync_vector_membership=sync, orgs_holding_points=lambda _v: [])
    token = org_context.push(None)
    try:
        for _ in range(50):
            await run_vector_membership_backfill_tick(logger=logging.getLogger("t"), graph_provider=pg_provider, pipeline=pipeline, lock=_Lock(), page_size=2, vrid_pause_ms=0)
            if len(synced) == 6:
                break
        assert sorted(synced) == sorted((o, f"v-{o[:6]}-{i}") for o in orgs for i in range(3))
        for o in orgs:
            with org_ctx(o):
                assert await pg_provider.get_app_needing_vector_membership_backfill() is None
                assert (await pg_provider.get_document(cid, "apps"))["vectorMembershipBackfilled"] is True
        with org_ctx(outsider):
            assert await pg_provider.get_document(f"0000-early-{tag}", "apps") == before
    finally:
        org_context.pop(token)
        for o in (*orgs, outsider):
            with org_ctx(o):
                await pg_provider._db.run(lambda tx, o=o: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", o), write=True)


async def test_a_system_scope_skips_the_apps_that_are_done_or_being_deleted(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    apps = [{"_key": "a", "vectorMembershipBackfilled": True}, {"_key": "b", "status": "DELETING"}, {"_key": "c", "vectorMembershipBackfilled": False, "status": "ACTIVE"}, {"_key": "d"}]

    async def listed(*a: object, **k: object) -> list[dict]:
        return apps
    monkeypatch.setattr(pg_provider, "_system_apps_page", listed)
    with org_context.system("test.vector-membership"):
        assert (await pg_provider.get_app_needing_vector_membership_backfill())["_key"] == "c"
        apps[2]["vectorMembershipBackfilled"] = True
        assert (await pg_provider.get_app_needing_vector_membership_backfill())["_key"] == "d"
        apps.pop()
        assert await pg_provider.get_app_needing_vector_membership_backfill() is None


async def test_a_system_scope_picks_the_lowest_id_in_byte_order(pg_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    """As under an org context (ORDER BY id COLLATE "C"): the database collation's order of the cross-org read does not decide the walk. Apps
    sharing an id keep the read's org order."""
    apps = [{"_key": "a", "orgId": "o2"}, {"_key": "b", "orgId": "o1"}, {"_key": "B", "orgId": "o0"}, {"_key": "B", "orgId": "o3"}]

    async def listed(*a: object, **k: object) -> list[dict]:
        return apps
    monkeypatch.setattr(pg_provider, "_system_apps_page", listed)
    with org_context.system("test.vector-membership"):
        got = await pg_provider.get_app_needing_vector_membership_backfill()
    assert (got["_key"], got["orgId"]) == ("B", "o0")


async def tree(w: World) -> None:
    folder = FOLDER_MIME_TYPES[0]
    await w.nodes("apps", {"_key": "kb", "type": "KB"}, {"_key": "kb2", "type": "KB"}, {"_key": "drive", "type": "DRIVE"})
    await w.nodes("records", {"_key": "fold", "recordName": "Docs", "mimeType": folder},
                  {"_key": "root1", "recordName": "Report.TXT", "mimeType": "text/plain"}, {"_key": "root2", "recordName": "Viafile", "mimeType": None},
                  {"_key": "root_gone", "recordName": "Gone", "mimeType": "text/plain", "isDeleted": True}, {"_key": "root_nomime", "recordName": "Nomime"},
                  {"_key": "root_noname", "mimeType": "text/plain"}, {"_key": "root_umlaut", "recordName": "Ärger.txt", "mimeType": "text/plain"}, {"_key": "root_dotted", "recordName": "İstanbul.txt", "mimeType": "text/plain"}, {"_key": "nested", "recordName": "Nested.txt", "mimeType": "text/plain"},
                  {"_key": "kb2file", "recordName": "Other.txt", "mimeType": "text/plain"}, {"_key": "drivefile", "recordName": "Drive.txt", "mimeType": "text/plain"},
                  {"_key": "c1", "recordName": "Child1", "mimeType": "text/csv"}, {"_key": "c2", "recordName": "Child2"}, {"_key": "c_nofile", "recordName": "Nofile", "mimeType": "text/csv"},
                  {"_key": "c_notfile", "recordName": "Notfile", "mimeType": "text/csv"}, {"_key": "c_gone", "recordName": "Cgone", "mimeType": "text/csv", "isDeleted": True},
                  {"_key": "c_attach", "recordName": "Attach", "mimeType": "text/csv"}, {"_key": "root_notfile", "recordName": "Notfile2"})
    await w.nodes("files", {"_key": "root1", "isFile": True}, {"_key": "root2", "isFile": True, "mimeType": "image/png"}, {"_key": "nested", "isFile": True},
                  {"_key": "c1", "isFile": True}, {"_key": "c2", "isFile": True, "mimeType": "application/pdf"}, {"_key": "c_notfile", "isFile": False},
                  {"_key": "c_gone", "isFile": True}, {"_key": "c_attach", "isFile": True}, {"_key": "fold", "isFile": False}, {"_key": "root_notfile", "isFile": False, "mimeType": "text/plain"})
    for r in ("root1", "root2", "root_gone", "root_nomime", "root_noname", "root_umlaut", "root_dotted", "nested", "fold", "root_notfile"):
        await w.edge("belongsTo", ("records", r), ("apps", "kb"))
    await w.edge("belongsTo", ("records", "kb2file"), ("apps", "kb2"))
    await w.edge("belongsTo", ("records", "drivefile"), ("apps", "drive"))
    for r in ("root1", "root2", "nested", "c1", "c2", "c_notfile", "c_gone", "c_attach", "fold", "root_notfile"):
        await w.edge("isOfType", ("records", r), ("files", r))
    await w.edge("nodeRelations", ("records", "fold"), ("records", "nested"), relationshipType="PARENT_CHILD")
    for c in ("c1", "c2", "c_nofile", "c_notfile", "c_gone"):
        await w.edge("nodeRelations", ("records", "fold"), ("records", c), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "fold"), ("records", "c_attach"), relationshipType="ATTACHMENT")


async def test_the_names_at_the_root_of_a_knowledge_base_are_its_live_top_level_files(w: World) -> None:
    await tree(w)
    got = await w.p._fetch_existing_file_names_in_parent("kb", None)
    assert got == {("report.txt", "text/plain"), ("viafile", "image/png"), ("ärger.txt", "text/plain"), ("İstanbul.txt".lower(), "text/plain")}      # the mime type of the record, else of its file; not a folder, a nested file, a deleted one or one without a type
    assert await w.p._fetch_existing_file_names_in_parent("kb2", None) == {("other.txt", "text/plain")} and await w.p._fetch_existing_file_names_in_parent("drive", None) == set()
    assert await w.p._fetch_existing_file_names_in_parent("none", None) == set()
    with in_org(_org()):
        assert await w.p._fetch_existing_file_names_in_parent("kb", None) == set()
    await w.p.update_node("root1", "records", {"isDeleted": True})
    assert await w.p._fetch_existing_file_names_in_parent("kb", None) == {("viafile", "image/png"), ("ärger.txt", "text/plain"), ("İstanbul.txt".lower(), "text/plain")}


async def test_the_names_in_a_folder_are_its_live_children_that_are_files(w: World) -> None:
    await tree(w)
    got = await w.p._fetch_existing_file_names_in_parent("kb", "fold")
    assert got == {("nested.txt", "text/plain"), ("child1", "text/csv"), ("child2", "application/pdf")}
    assert await w.p._fetch_existing_file_names_in_parent("kb", "root1") == set() and await w.p._fetch_existing_file_names_in_parent("kb", "missing") == set()
    with in_org(_org()):
        assert await w.p._fetch_existing_file_names_in_parent("kb", "fold") == set()


async def test_a_failed_name_check_is_empty_unless_asked_to_raise_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await tree(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p._fetch_existing_file_names_in_parent("kb", None) == set() and await w.p._fetch_existing_file_names_in_parent("kb", "fold") == set()
    with pytest.raises(RuntimeError):
        await w.p._fetch_existing_file_names_in_parent("kb", "fold", raise_on_error=True)
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p._fetch_existing_file_names_in_parent("kb", "fold")) == 3
