"""The vector membership backfill's reads and the knowledge base upload's name check, on PostgreSQL against a real database.

Both ways: the backfill pages every record of a connector exactly once and nothing of another connector or org, never returns an app that is done or being
deleted, and a page that fails raises (an empty page would read as "finished") and reads the same page again once the store is back. The name check
returns the live files of its folder or knowledge base root and nothing of another folder, base or org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid

import pytest

from app.config.constants.arangodb import FOLDER_MIME_TYPES
from app.core import org_context
from app.services.graph_db.vector_membership_queries import PAGE_RECORD_FIELDS

from .test_access import World, _org
from .test_access import w as w
from .test_grants import failing, in_org

pytestmark = pytest.mark.integration


async def walk(w: World, connector: str, size: int) -> list[list[str]]:
    """The pages a backfill reads: from no cursor, each continuing after the last key, until a short page."""
    pages, after = [], None
    while True:
        page = await w.p.page_records_for_vector_membership_backfill(connector, after, size)
        pages.append([r["_key"] for r in page])
        if len(page) < size:
            return pages
        after = page[-1]["_key"]


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


async def test_a_system_scope_pages_the_records_of_the_org_that_holds_the_connector(pg_provider, org_ctx) -> None:
    tag, org = uuid.uuid4().hex[:8], _org()
    with org_ctx(org):
        await pg_provider.batch_upsert_nodes([{"_key": f"vm-{tag}", "type": "DRIVE", "orgId": org}], "apps")
        await pg_provider.batch_upsert_nodes([{"_key": k, "connectorId": c, "orgId": org} for k, c in (("r1", f"vm-{tag}"), ("r2", f"vm-{tag}"), ("r3", "elsewhere"))], "records")
    try:
        with org_context.system("test.vector-membership"):
            page = await pg_provider.page_records_for_vector_membership_backfill(f"vm-{tag}", "r1", 5)
            none = await pg_provider.page_records_for_vector_membership_backfill("no-such-app", None, 5)
            app = await pg_provider.get_app_needing_vector_membership_backfill()
        assert [r["_key"] for r in page] == ["r2"] and page[0]["orgId"] == org and none == []
        assert app is None or (app.get("vectorMembershipBackfilled") in (None, False) and app.get("status") != "DELETING")
    finally:
        with org_ctx(org):
            await pg_provider._db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def tree(w: World) -> None:
    folder = FOLDER_MIME_TYPES[0]
    await w.nodes("apps", {"_key": "kb", "type": "KB"}, {"_key": "kb2", "type": "KB"}, {"_key": "drive", "type": "DRIVE"})
    await w.nodes("records", {"_key": "fold", "recordName": "Docs", "mimeType": folder},
                  {"_key": "root1", "recordName": "Report.TXT", "mimeType": "text/plain"}, {"_key": "root2", "recordName": "Viafile", "mimeType": None},
                  {"_key": "root_gone", "recordName": "Gone", "mimeType": "text/plain", "isDeleted": True}, {"_key": "root_nomime", "recordName": "Nomime"},
                  {"_key": "root_noname", "mimeType": "text/plain"}, {"_key": "nested", "recordName": "Nested.txt", "mimeType": "text/plain"},
                  {"_key": "kb2file", "recordName": "Other.txt", "mimeType": "text/plain"}, {"_key": "drivefile", "recordName": "Drive.txt", "mimeType": "text/plain"},
                  {"_key": "c1", "recordName": "Child1", "mimeType": "text/csv"}, {"_key": "c2", "recordName": "Child2"}, {"_key": "c_nofile", "recordName": "Nofile", "mimeType": "text/csv"},
                  {"_key": "c_notfile", "recordName": "Notfile", "mimeType": "text/csv"}, {"_key": "c_gone", "recordName": "Cgone", "mimeType": "text/csv", "isDeleted": True},
                  {"_key": "c_attach", "recordName": "Attach", "mimeType": "text/csv"})
    await w.nodes("files", {"_key": "root1", "isFile": True}, {"_key": "root2", "isFile": True, "mimeType": "image/png"}, {"_key": "nested", "isFile": True},
                  {"_key": "c1", "isFile": True}, {"_key": "c2", "isFile": True, "mimeType": "application/pdf"}, {"_key": "c_notfile", "isFile": False},
                  {"_key": "c_gone", "isFile": True}, {"_key": "c_attach", "isFile": True}, {"_key": "fold", "isFile": False})
    for r in ("root1", "root2", "root_gone", "root_nomime", "root_noname", "nested", "fold"):
        await w.edge("belongsTo", ("records", r), ("apps", "kb"))
    await w.edge("belongsTo", ("records", "kb2file"), ("apps", "kb2"))
    await w.edge("belongsTo", ("records", "drivefile"), ("apps", "drive"))
    for r in ("root1", "root2", "nested", "c1", "c2", "c_notfile", "c_gone", "c_attach", "fold"):
        await w.edge("isOfType", ("records", r), ("files", r))
    await w.edge("nodeRelations", ("records", "fold"), ("records", "nested"), relationshipType="PARENT_CHILD")
    for c in ("c1", "c2", "c_nofile", "c_notfile", "c_gone"):
        await w.edge("nodeRelations", ("records", "fold"), ("records", c), relationshipType="PARENT_CHILD")
    await w.edge("nodeRelations", ("records", "fold"), ("records", "c_attach"), relationshipType="ATTACHMENT")


async def test_the_names_at_the_root_of_a_knowledge_base_are_its_live_top_level_files(w: World) -> None:
    await tree(w)
    got = await w.p._fetch_existing_file_names_in_parent("kb", None)
    assert got == {("report.txt", "text/plain"), ("viafile", "image/png")}      # the mime type of the record, else of its file; not a folder, a nested file, a deleted one or one without a type
    assert await w.p._fetch_existing_file_names_in_parent("kb2", None) == {("other.txt", "text/plain")} and await w.p._fetch_existing_file_names_in_parent("drive", None) == set()
    assert await w.p._fetch_existing_file_names_in_parent("none", None) == set()
    with in_org(_org()):
        assert await w.p._fetch_existing_file_names_in_parent("kb", None) == set()
    await w.p.update_node("root1", "records", {"isDeleted": True})
    assert await w.p._fetch_existing_file_names_in_parent("kb", None) == {("viafile", "image/png")}


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
