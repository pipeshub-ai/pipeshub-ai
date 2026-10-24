"""Knowledge-base content is stored where the KB tree puts it, and follows it.

A KB is its own connector here: its records' content is filed under
``records/<kbId>/<folders>/<name>``. Each test builds a small tree, changes it
through the public API, and checks MongoDB, blob storage and the graph agree
on the result, with the old place left empty and the content still readable.
Tracker ids (L01, L02...) name the audit finding a test guards.
"""

from __future__ import annotations

import pytest

from helper.storage_layout import (
    MAX_PATH_BYTES,
    MAX_SEGMENT_BYTES,
    connector_root,
    expected_path,
    oversized_segments,
)

pytestmark = [pytest.mark.integration, pytest.mark.pattern_match, pytest.mark.asyncio(loop_scope="session")]


class TestWhereContentIsStored:
    async def test_a_file_at_the_root(self, kb_tree, layout, graph_provider):
        note = await kb_tree.file("note.md")

        await layout.assert_stored_at(note.virtual_record_id, expected_path(kb_tree.kb_id, note.name))
        await kb_tree.assert_readable(note)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_a_file_in_nested_folders(self, kb_tree, layout, graph_provider):
        finance = kb_tree.folder("Finance")
        q3 = kb_tree.folder("Q3 2026", parent=finance)
        plan = await kb_tree.file("plan.md", folder=q3)

        await layout.assert_stored_at(
            plan.virtual_record_id, expected_path(kb_tree.kb_id, "Finance", "Q3 2026", plan.name)
        )
        await kb_tree.assert_readable(plan)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_unsafe_characters_become_underscores(self, kb_tree, layout):
        # "<...>" is stripped as markup by the KB API, so it is not used here.
        name = 'Q1: "plan"? a*b|c\\d/e'
        folder = kb_tree.folder(name)
        doc = await kb_tree.file("what? now*.md", folder=folder)

        path = expected_path(kb_tree.kb_id, name, doc.name)
        assert path == f"{connector_root(kb_tree.kb_id)}/Q1_ _plan__ a_b_c_d_e/what_ now_"
        await layout.assert_stored_at(doc.virtual_record_id, path)

    async def test_long_multibyte_names_fit_the_filesystem(self, kb_tree, layout, graph_provider):
        # L02 (folder) and L29 (file): 120 CJK characters are 360 bytes.
        folder = kb_tree.folder("議事録" * 40)
        doc = await kb_tree.file("報告書" * 40 + ".md", folder=folder)

        copies = await layout.assert_stored_at(
            doc.virtual_record_id, expected_path(kb_tree.kb_id, "議事録" * 40, doc.name)
        )
        assert not oversized_segments(copies[0].path), copies[0].path
        assert all(len(s.encode()) <= MAX_SEGMENT_BYTES for s in copies[0].file.split("/"))
        await kb_tree.assert_readable(doc)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_a_deep_tree_keeps_the_path_under_the_cap(self, kb_tree, layout, graph_provider):
        # L02: twelve 90-character folders would make a 1100-byte path.
        parent = None
        for level in range(12):
            parent = kb_tree.folder(f"{level:02d}-" + "d" * 87, parent=parent)
        doc = await kb_tree.file("deep.md", folder=parent)

        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)
        (record,) = [c for c in await layout.copies_of(doc.virtual_record_id) if c.kind == "record"]
        assert len(record.path.encode()) <= MAX_PATH_BYTES, len(record.path.encode())
        assert record.path.startswith(f"{connector_root(kb_tree.kb_id)}/00-"), record.path
        await kb_tree.assert_readable(doc)


class TestContentFollowsTheTree:
    async def test_renaming_a_file_moves_its_content(self, kb_tree, layout, graph_provider):
        folder = kb_tree.folder("Drafts")
        doc = await kb_tree.file("memo.md", folder=folder)
        old = expected_path(kb_tree.kb_id, "Drafts", doc.name)
        await layout.assert_stored_at(doc.virtual_record_id, old)

        kb_tree.kb_client.rename_record(doc.record_id, "memo final")

        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "Drafts", "memo final"))
        await layout.assert_nothing_under(old)
        await kb_tree.assert_readable(doc)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_renaming_a_folder_takes_everything_below_it(self, kb_tree, layout, graph_provider):
        reports = kb_tree.folder("Reports")
        y2026 = kb_tree.folder("2026", parent=reports)
        top = await kb_tree.file("summary.md", folder=reports)
        deep = await kb_tree.file("q1.md", folder=y2026)

        kb_tree.kb_client.rename_folder(kb_tree.kb_id, reports, "Board Reports")

        await layout.assert_stored_at(top.virtual_record_id, expected_path(kb_tree.kb_id, "Board Reports", top.name))
        await layout.assert_stored_at(
            deep.virtual_record_id, expected_path(kb_tree.kb_id, "Board Reports", "2026", deep.name)
        )
        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "Reports"))
        await kb_tree.assert_readable(deep)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_a_case_only_folder_rename_moves_the_content(self, kb_tree, layout, graph_provider):
        # L19: "reports" -> "Reports" used to be treated as no change.
        folder = kb_tree.folder("reports")
        doc = await kb_tree.file("q3.md", folder=folder)

        kb_tree.kb_client.rename_folder(kb_tree.kb_id, folder, "Reports")

        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "Reports", doc.name))
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_moving_a_file_between_folders_and_to_the_root(self, kb_tree, layout, graph_provider):
        inbox = kb_tree.folder("Inbox")
        archive = kb_tree.folder("Archive")
        doc = await kb_tree.file("receipt.md", folder=inbox)

        kb_tree.kb_client.move_record(kb_tree.kb_id, doc.record_id, archive)
        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "Archive", doc.name))
        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "Inbox", doc.name))

        kb_tree.kb_client.move_record(kb_tree.kb_id, doc.record_id, None)
        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, doc.name))
        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "Archive", doc.name))
        await kb_tree.assert_readable(doc)
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_moving_a_folder_carries_its_subtree(self, kb_tree, layout, graph_provider):
        projects = kb_tree.folder("Projects")
        apollo = kb_tree.folder("Apollo")
        specs = kb_tree.folder("Specs", parent=apollo)
        doc = await kb_tree.file("design.md", folder=specs)

        kb_tree.kb_client.move_record(kb_tree.kb_id, apollo, projects)

        await layout.assert_stored_at(
            doc.virtual_record_id, expected_path(kb_tree.kb_id, "Projects", "Apollo", "Specs", doc.name)
        )
        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "Apollo"))
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_renaming_a_file_leaves_a_same_named_folders_children(self, kb_tree, layout, graph_provider):
        # L01: file "Reports" and folder "Reports" side by side share one path.
        folder = kb_tree.folder("Reports")
        child = await kb_tree.file("plan.md", folder=folder)
        twin = await kb_tree.file("Reports.md")
        assert twin.name == "Reports"

        kb_tree.kb_client.rename_record(twin.record_id, "Q3")

        await layout.assert_stored_at(twin.virtual_record_id, expected_path(kb_tree.kb_id, "Q3"))
        await layout.assert_stored_at(child.virtual_record_id, expected_path(kb_tree.kb_id, "Reports", child.name))
        await kb_tree.assert_readable(child)

        kb_tree.kb_client.rename_folder(kb_tree.kb_id, folder, "Plans")

        await layout.assert_stored_at(child.virtual_record_id, expected_path(kb_tree.kb_id, "Plans", child.name))
        await layout.assert_stored_at(twin.virtual_record_id, expected_path(kb_tree.kb_id, "Q3"))
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_back_to_back_renames_end_where_the_last_one_says(self, kb_tree, layout, graph_provider):
        # L08: moves for one tree must not interleave.
        folder = kb_tree.folder("v1")
        doc = await kb_tree.file("spec.md", folder=folder)

        for name in ("v2", "v3", "v4"):
            kb_tree.kb_client.rename_folder(kb_tree.kb_id, folder, name)

        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "v4", doc.name))
        for stale in ("v1", "v2", "v3"):
            await layout.assert_nothing_under(expected_path(kb_tree.kb_id, stale))
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)

    async def test_renaming_the_knowledge_base_keeps_its_content_in_place(self, kb_tree, layout, graph_provider):
        folder = kb_tree.folder("Team")
        doc = await kb_tree.file("roster.md", folder=folder)
        before = await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "Team", doc.name))

        kb_tree.kb_client.rename_kb(kb_tree.kb_id, "Renamed knowledge base")

        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)
        assert await layout.copies_of(doc.virtual_record_id) == before


class TestDeletesAfterMoves:
    async def test_a_moved_file_leaves_nothing_behind_when_deleted(self, kb_tree, layout):
        a = kb_tree.folder("A")
        b = kb_tree.folder("B")
        doc = await kb_tree.file("temp.md", folder=a)
        kb_tree.kb_client.move_record(kb_tree.kb_id, doc.record_id, b)
        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "B", doc.name))

        kb_tree.kb_client.delete_record(doc.record_id)

        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "A"))
        await layout.assert_nothing_under(expected_path(kb_tree.kb_id, "B"))
        assert await layout.copies_of(doc.virtual_record_id) == []

    async def test_a_renamed_folder_leaves_nothing_behind_when_deleted(self, kb_tree, layout):
        folder = kb_tree.folder("Old name")
        docs = [await kb_tree.file(f"f{i}.md", folder=folder) for i in range(2)]
        kb_tree.kb_client.rename_folder(kb_tree.kb_id, folder, "New name")
        for doc in docs:
            await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "New name", doc.name))

        kb_tree.kb_client.delete_folder(kb_tree.kb_id, folder)

        await layout.assert_nothing_under(connector_root(kb_tree.kb_id))

    async def test_deleting_the_knowledge_base_empties_its_folder(self, kb_tree, layout):
        folder = kb_tree.folder("X")
        doc = await kb_tree.file("x.md", folder=folder)
        kb_tree.kb_client.rename_folder(kb_tree.kb_id, folder, "Y")
        await layout.assert_stored_at(doc.virtual_record_id, expected_path(kb_tree.kb_id, "Y", doc.name))

        kb_tree.kb_client.delete_kb(kb_tree.kb_id)

        await layout.assert_nothing_under(connector_root(kb_tree.kb_id))
