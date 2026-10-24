"""Identical files share one stored copy; it must stay readable to every holder.

Content is deduplicated by its bytes: two knowledge bases holding the same file
share a virtual record id and one ``record_<vrid>`` document, filed under the
first holder's path. Renaming, moving or deleting one holder must never leave
the other reading nothing (L04, L11).
"""

from __future__ import annotations

import uuid

import pytest

from helper.storage_layout import connector_root, expected_path

pytestmark = [pytest.mark.integration, pytest.mark.pattern_match, pytest.mark.asyncio(loop_scope="session")]


async def _shared_pair(first, second):
    token = uuid.uuid4().hex
    body = f"# Shared\n\nThe same file in two places {token}.\n".encode()
    a = await first.file("shared.md", folder=first.folder("Here"), body=body, token=token)
    b = await second.file("copy.md", folder=second.folder("There"), body=body, token=token)
    assert a.virtual_record_id == b.virtual_record_id, "identical bytes should share content"
    return a, b


class TestSharedContent:
    async def test_one_copy_is_stored_under_the_first_holder(self, kb_tree, second_kb, layout, graph_provider):
        a, _ = await _shared_pair(kb_tree, second_kb)

        await layout.assert_stored_at(a.virtual_record_id, expected_path(kb_tree.kb_id, "Here", a.name))
        await layout.assert_nothing_under(connector_root(second_kb.kb_id))
        await layout.assert_connector_consistent(graph_provider, kb_tree.kb_id)
        await layout.assert_connector_consistent(graph_provider, second_kb.kb_id)

    async def test_renaming_the_holder_moves_the_copy_and_both_still_read_it(
        self, kb_tree, second_kb, layout, graph_provider
    ):
        a, b = await _shared_pair(kb_tree, second_kb)

        kb_tree.kb_client.rename_record(a.record_id, "renamed")

        await layout.assert_stored_at(a.virtual_record_id, expected_path(kb_tree.kb_id, "Here", "renamed"))
        await layout.assert_connector_consistent(graph_provider, second_kb.kb_id)
        await kb_tree.assert_readable(a)
        await second_kb.assert_readable(b)

    async def test_renaming_the_other_holder_leaves_the_copy_alone(self, kb_tree, second_kb, layout, graph_provider):
        a, b = await _shared_pair(kb_tree, second_kb)
        before = await layout.assert_stored_at(a.virtual_record_id, expected_path(kb_tree.kb_id, "Here", a.name))

        second_kb.kb_client.rename_record(b.record_id, "renamed copy")

        await layout.assert_connector_consistent(graph_provider, second_kb.kb_id)
        assert await layout.copies_of(a.virtual_record_id) == before
        await second_kb.assert_readable(b)

    async def test_deleting_the_holders_knowledge_base_rebuilds_the_copy_for_the_other(
        self, kb_tree, second_kb, layout, graph_provider
    ):
        a, b = await _shared_pair(kb_tree, second_kb)

        kb_tree.kb_client.delete_kb(kb_tree.kb_id)

        await layout.assert_stored_at(b.virtual_record_id, expected_path(second_kb.kb_id, "There", b.name))
        await layout.assert_nothing_under(connector_root(kb_tree.kb_id))
        await second_kb.assert_readable(b)
        await layout.assert_connector_consistent(graph_provider, second_kb.kb_id)

    async def test_deleting_the_holder_record_keeps_the_content_readable_for_the_other(
        self, kb_tree, second_kb, layout, graph_provider
    ):
        a, b = await _shared_pair(kb_tree, second_kb)

        kb_tree.kb_client.delete_record(a.record_id)

        await layout.assert_connector_consistent(graph_provider, second_kb.kb_id)
        await second_kb.assert_readable(b)
