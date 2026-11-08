"""The storage service's routes for processed records, called as the Python services call them.

``move-tree`` is how a rename or move in the graph reaches stored content; the
listing routes let the indexer find content it stored but never mapped (L05)
and the one-off migration find the flat layout (L06); the connector route
removes a deleted connector's folder. No indexing is involved, so these run
without a model.
"""

from __future__ import annotations

import concurrent.futures
import uuid

import pytest
import requests

from helper.storage_layout import connector_root

pytestmark = [pytest.mark.integration, pytest.mark.pattern_match, pytest.mark.asyncio(loop_scope="session")]


def new_vrid() -> str:
    return uuid.uuid4().hex


def _p(connector: str, path: str) -> str:
    return f"{connector_root(connector)}/{path}"


class TestMoveTree:
    async def test_moves_a_folder_and_everything_below_it(self, storage_client, stored_records, layout, connector_id):
        top, deep, sibling = new_vrid(), new_vrid(), new_vrid()
        stored_records(connector_id, "Reports/q3", top)
        stored_records(connector_id, "Reports/2024/plan", deep)
        stored_records(connector_id, "Reports-old/notes", sibling)

        resp = storage_client.move_tree(_p(connector_id, "Reports"), _p(connector_id, "Archive"))

        assert resp.status_code == 200, resp.text
        assert resp.json()["moved"] == 4
        await layout.assert_stored_at(top, _p(connector_id, "Archive/q3"), timeout=0)
        await layout.assert_stored_at(deep, _p(connector_id, "Archive/2024/plan"), timeout=0)
        await layout.assert_stored_at(sibling, _p(connector_id, "Reports-old/notes"), timeout=0)
        await layout.assert_nothing_under(_p(connector_id, "Reports"), timeout=0)

    async def test_owner_list_leaves_a_same_named_folders_children(self, storage_client, stored_records, layout, connector_id):
        # L01: a file "Reports" next to a folder "Reports" share the path.
        doc, child = new_vrid(), new_vrid()
        stored_records(connector_id, "Reports", doc)
        stored_records(connector_id, "Reports/plan", child)

        resp = storage_client.move_tree(
            _p(connector_id, "Reports"), _p(connector_id, "Q3"), virtual_record_ids=[doc]
        )

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"moved": 2, "collision": True}
        await layout.assert_stored_at(doc, _p(connector_id, "Q3"), timeout=0)
        await layout.assert_stored_at(child, _p(connector_id, "Reports/plan"), timeout=0)

    async def test_single_record_form_leaves_an_exact_path_twin(self, storage_client, stored_records, layout, connector_id):
        # L01: two emails "Invoice"; the one renamed takes its attachment along.
        mine, twin, attachment = new_vrid(), new_vrid(), new_vrid()
        stored_records(connector_id, "Invoice", mine)
        stored_records(connector_id, "Invoice", twin)
        stored_records(connector_id, "Invoice/scan", attachment)

        resp = storage_client.move_tree(
            _p(connector_id, "Invoice"), _p(connector_id, "Invoice March"), virtual_record_id=mine
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["collision"] is True
        await layout.assert_stored_at(mine, _p(connector_id, "Invoice March"), timeout=0)
        await layout.assert_stored_at(attachment, _p(connector_id, "Invoice March/scan"), timeout=0)
        await layout.assert_stored_at(twin, _p(connector_id, "Invoice"), timeout=0)

    async def test_a_soft_deleted_twin_keeps_its_files_where_its_row_says(
        self, storage_client, stored_records, layout, connector_id
    ):
        mine, deleted = new_vrid(), new_vrid()
        stored_records(connector_id, "Reports", mine)
        deleted_ids = stored_records(connector_id, "Reports", deleted)
        for doc_id in deleted_ids.values():
            assert storage_client.delete_document(doc_id).status_code == 200

        resp = storage_client.move_tree(
            _p(connector_id, "Reports"), _p(connector_id, "Q3"), virtual_record_ids=[mine]
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["collision"] is True
        await layout.assert_stored_at(mine, _p(connector_id, "Q3"), timeout=0)
        rows = await layout.copies_of(deleted, include_deleted=True)
        assert {r.path for r in rows} == {_p(connector_id, "Reports")}
        files = await layout.files_under(_p(connector_id, "Reports"))
        assert sorted(r.file for r in rows) == sorted(f for f in files if deleted in f)

    async def test_nothing_to_move_is_not_an_error(self, storage_client, connector_id):
        resp = storage_client.move_tree(_p(connector_id, "missing"), _p(connector_id, "elsewhere"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["moved"] == 0

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("records/c/../other", "records/c/x"),
            ("records/c/a", "records/c/../../etc"),
            ("/records/c/a", "records/c/b"),
            ("records/c/a", "records/c/a/b"),
        ],
        ids=["parent-segment-in-old", "parent-segment-in-new", "absolute", "into-itself"],
    )
    async def test_paths_that_escape_or_nest_are_refused(self, storage_client, old, new):
        resp = storage_client.move_tree(old, new)
        assert resp.status_code == 400, resp.text

    async def test_a_name_with_two_dots_inside_it_is_a_legitimate_folder(
        self, storage_client, stored_records, layout, connector_id
    ):
        vrid = new_vrid()
        stored_records(connector_id, "Q1..Q2", vrid)
        resp = storage_client.move_tree(_p(connector_id, "Q1..Q2"), _p(connector_id, "H1"))
        assert resp.status_code == 200, resp.text
        await layout.assert_stored_at(vrid, _p(connector_id, "H1"), timeout=0)

    async def test_a_user_token_is_refused(self, pipeshub_client, connector_id):
        resp = requests.post(
            pipeshub_client._url("/api/v1/document/internal/move-tree"),
            headers={**pipeshub_client.auth_headers, "Content-Type": "application/json"},
            json={"oldPath": _p(connector_id, "a"), "newPath": _p(connector_id, "b")},
            timeout=30,
        )
        assert resp.status_code in (401, 403), resp.text

    async def test_overlapping_moves_leave_rows_and_files_in_agreement(
        self, storage_client, stored_records, layout, connector_id
    ):
        # L08: a folder rename and a move inside it, at the same time.
        vrids = {name: new_vrid() for name in ("a", "b", "c")}
        stored_records(connector_id, "Top/inner/a", vrids["a"])
        stored_records(connector_id, "Top/inner/b", vrids["b"])
        stored_records(connector_id, "Top/c", vrids["c"])

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            renames = [
                pool.submit(storage_client.move_tree, _p(connector_id, "Top"), _p(connector_id, "Renamed")),
                pool.submit(storage_client.move_tree, _p(connector_id, "Top/inner"), _p(connector_id, "Top/moved")),
            ]
            assert all(f.result().status_code == 200 for f in renames)

        for vrid in vrids.values():
            copies = await layout.copies_of(vrid)
            assert len(copies) == 2, f"{vrid}: {copies}"
            for copy in copies:
                assert copy.file in await layout.files_under(copy.path), f"row without its file: {copy}"
        await layout.assert_nothing_under(_p(connector_id, "Top/inner"), timeout=0)


class TestRecordListings:
    async def test_lists_a_virtual_records_live_documents(self, storage_client, stored_records, connector_id):
        vrid = new_vrid()
        ids = stored_records(connector_id, "Notes", vrid)
        other = stored_records(connector_id, "Notes", new_vrid())
        assert storage_client.delete_document(other["record"]).status_code == 200

        resp = storage_client.virtual_record_documents(vrid)

        assert resp.status_code == 200, resp.text
        listed = {d["_id"]: d for d in resp.json()["documents"]}
        assert set(listed) == set(ids.values())
        assert {d["documentName"] for d in listed.values()} == {f"record_{vrid}", f"metadata_{vrid}"}

    async def test_a_soft_deleted_document_is_not_listed(self, storage_client, stored_records, connector_id):
        vrid = new_vrid()
        ids = stored_records(connector_id, "Notes", vrid)
        assert storage_client.delete_document(ids["record"]).status_code == 200

        listed = storage_client.virtual_record_documents(vrid).json()["documents"]

        assert [d["_id"] for d in listed] == [ids["metadata"]]

    async def test_flat_listing_finds_only_the_flat_layout(self, storage_client, stored_records, test_org_id):
        flat, nested = new_vrid(), new_vrid()
        flat_ids = _store_flat(storage_client, flat)
        try:
            # records/<vrid> nested inside a connector folder named like a vrid is not flat.
            stored_records(f"it-pm-{nested[:12]}", nested, nested)
            found = _all_flat_documents(storage_client)
            assert set(flat_ids.values()) <= {d["_id"] for d in found}
            assert not [d for d in found if d["documentName"].endswith(nested)]
        finally:
            storage_client.purge_virtual_record(flat)

    async def test_flat_listing_pages_by_document_id(self, storage_client):
        vrids = [new_vrid() for _ in range(3)]
        for vrid in vrids:
            _store_flat(storage_client, vrid)
        try:
            first = storage_client.flat_record_documents(limit=1).json()["documents"]
            assert len(first) == 1
            after = storage_client.flat_record_documents(after=first[0]["_id"], limit=1000).json()["documents"]
            assert first[0]["_id"] not in {d["_id"] for d in after}
            assert all(d["_id"] > first[0]["_id"] for d in after)
        finally:
            for vrid in vrids:
                storage_client.purge_virtual_record(vrid)


class TestConnectorStorageDelete:
    async def test_removes_only_that_connectors_folder(self, storage_client, stored_records, layout, connector_id):
        gone, kept = new_vrid(), new_vrid()
        stored_records(connector_id, "A", gone)
        stored_records(f"{connector_id}-2", "A", kept)

        resp = storage_client.delete_connector_storage(connector_id)

        assert resp.status_code == 200, resp.text
        await layout.assert_nothing_under(connector_root(connector_id))
        await layout.assert_stored_at(kept, _p(f"{connector_id}-2", "A"), timeout=0)

    @pytest.mark.parametrize("bad", ["a%2Fb", "..", "x" * 65, "a.b"], ids=["encoded-slash", "dots", "too-long", "dot"])
    async def test_a_connector_id_that_could_name_another_folder_is_refused(self, storage_client, bad):
        # L14: the id becomes a storage prefix.
        assert storage_client.delete_connector_storage(bad).status_code in (400, 404)


def _store_flat(storage_client, vrid: str) -> dict[str, str]:
    ids = {}
    for kind in ("record", "metadata"):
        name = f"{kind}_{vrid}"
        resp = storage_client.upload(b"{}", f"{name}.json", name, is_versioned=False, document_path=f"records/{vrid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        ids[kind] = str(body.get("document", body)["_id"])
    return ids


def _all_flat_documents(storage_client) -> list[dict]:
    found: list[dict] = []
    after = None
    while True:
        page = storage_client.flat_record_documents(after=after, limit=1000).json()["documents"]
        if not page:
            return found
        found.extend(page)
        after = page[-1]["_id"]
