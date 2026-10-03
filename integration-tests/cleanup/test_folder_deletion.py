"""Deleting a folder has to take the records inside it with it.

Deleting a folder removes the folder and the records it contains from the
graph, the vector database, blob storage and MongoDB; the first four tests
check one store each.

The first four tests use a one-level folder with one record. The scenario at
the end of the file nests a sub-folder (created with the ``?folderId=`` query
parameter, which is how the gateway makes a sub-folder; a ``parentId`` in the
body is ignored), puts content inside it that a file outside also holds, and
checks the whole subtree leaves the graph while everything outside it, shared
content included, is untouched.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio
import requests

from helper import cleanup_sources as src
from helper import delete_footprint as fp
from helper.mongo_store import records_folder

logger = logging.getLogger("cleanup-folder-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]



def _delete_folder(kb_client, fixture) -> None:
    kb_client.delete_folder(fixture["kb_id"], fixture["folder_id"])


class TestDeletingAFolder:
    """The test list's 'Delete a folder in collection' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_record_inside_stops_being_retrievable(
        self, record_in_a_folder, kb_client, pipeshub_client
    ) -> None:
        """The cascade, from the outside: the contents go with the folder."""
        _delete_folder(kb_client, record_in_a_folder)

        pipeshub_client._ensure_access_token()
        response = requests.get(
            f"{pipeshub_client.base_url}/api/v1/knowledgeBase/record/"
            f"{record_in_a_folder['record_id']}",
            headers={"Authorization": f"Bearer {pipeshub_client._access_token}"},
            timeout=30,
        )
        assert response.status_code == 404, (
            f"The record inside the deleted folder is still retrievable "
            f"(HTTP {response.status_code}); a cascade delete should leave it "
            "returning 404. Deleting a folder has to take its contents with it."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_embeddings_are_removed(
        self, record_in_a_folder, kb_client, vector_store
    ) -> None:
        virtual_id = record_in_a_folder["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        _delete_folder(kb_client, record_in_a_folder)

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_files_are_removed(
        self, record_in_a_folder, kb_client, blob_store
    ) -> None:
        prefix = record_in_a_folder["storage_prefix"]
        vendor = record_in_a_folder["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        _delete_folder(kb_client, record_in_a_folder)

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_storage_documents_are_removed(
        self, record_in_a_folder, kb_client, mongo_store
    ) -> None:
        prefix = record_in_a_folder["storage_prefix"]
        assert await mongo_store.count_documents_under_path(prefix) > 0, (
            "No storage documents existed before the delete."
        )

        _delete_folder(kb_client, record_in_a_folder)

        await mongo_store.assert_documents_under_path_gone(prefix, timeout=120)


SHARED = b"# Creance Handling\n\nThe creance is paid out through the glove, never wrapped round it.\n"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def folder_tree_delete(
    kb_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, Any], None]:
    """
    kb/
      root-copy.md         same bytes as sub/shared.md, uploaded after it
      target/              <- deleted
        own.md
        sub/
          shared.md        uploaded first, so the shared content starts inside the folder
      sibling/
        sibling.md
    """
    tag = uuid.uuid4().hex[:6]
    kb_id = kb_client.create_kb(f"cleanup-tree-{tag}")["id"]
    try:
        target = src.folder_id_of(kb_client.create_folder(kb_id, f"target-{tag}"))
        sub = src.folder_id_of(kb_client.create_folder(kb_id, f"sub-{tag}", parent_id=target))
        sibling = src.folder_id_of(kb_client.create_folder(kb_id, f"sibling-{tag}"))
        shared = await src.upload_to_kb(
            kb_client, vector_store, kb_id, f"shared-{tag}.md", SHARED, folder_id=sub
        )
        own = await src.upload_to_kb(
            kb_client, vector_store, kb_id, f"own-{tag}.md", src.unique_text("own"), folder_id=target
        )
        sibling_file = await src.upload_to_kb(
            kb_client, vector_store, kb_id, f"sibling-{tag}.md", src.unique_text("sibling"), folder_id=sibling
        )
        root_copy = await src.upload_to_kb(kb_client, vector_store, kb_id, f"root-copy-{tag}.md", SHARED)
        assert root_copy.virtual_record_id == shared.virtual_record_id, (
            "The copy outside the folder did not reuse the shared file's virtual record id "
            f"({root_copy.virtual_record_id} vs {shared.virtual_record_id}); this scenario "
            "cannot test shared content surviving a folder delete."
        )
        inside, outside = [own, shared], [sibling_file, root_copy]
        await fp.wait_for_connector_records(graph_provider, kb_id, [r.name for r in inside + outside])
        within = records_folder(test_org_id, kb_id)
        vendor = await fp.storage_vendor(mongo_store, test_org_id, shared.virtual_record_id, within=within)

        inside_graph = await fp.graph_footprint_of_records(
            graph_provider, [target, sub] + [r.record_id for r in inside]
        )
        before = await fp.capture_when_stable(
            inside_graph, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=inside, within=within, vendor=vendor,
        )
        fp.assert_every_store_holds_it(before)
        shared_envelope = {shared.virtual_record_id: before.envelope_paths[shared.virtual_record_id]}
        outside_before = await fp.capture_when_stable(
            await fp.graph_footprint_of_records(graph_provider, [sibling] + [r.record_id for r in outside]),
            vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=outside, within=within, vendor=vendor, envelope_paths=shared_envelope,
        )
        fp.assert_shared_envelope_counted(outside_before, shared.virtual_record_id)

        kb_client.delete_folder(kb_id, target)
        await fp.settle(
            fp.assert_embeddings_gone(vector_store, before, [own.virtual_record_id]), "the folder"
        )
        yield {
            "before": before,
            "own": own,
            "shared": shared,
            "outside": outside,
            "outside_before": outside_before,
            "vendor": vendor,
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


class TestDeletingAFolderWithASubFolderAndSharedContent:
    """'Delete a folder in collection', including children and sub-folder records."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_folder_its_sub_folder_their_records_and_all_their_edges_leave_the_graph(
        self, folder_tree_delete, graph_provider
    ) -> None:
        await fp.assert_graph_gone(graph_provider, folder_tree_delete["before"].graph)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_embeddings_only_it_used_are_removed(self, folder_tree_delete, vector_store) -> None:
        await fp.assert_embeddings_gone(
            vector_store, folder_tree_delete["before"], [folder_tree_delete["own"].virtual_record_id],
            timeout=30,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, folder_tree_delete, blob_store
    ) -> None:
        """The unshared record's envelope, and the original upload of every record inside."""
        before = folder_tree_delete["before"]
        paths = fp.blob_keys_for(before, [folder_tree_delete["own"]])
        paths.append(before.upload_paths[folder_tree_delete["shared"].upload_document_id])
        await fp.assert_blobs_gone(blob_store, before, paths, vendor=folder_tree_delete["vendor"])

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_storage_documents_are_removed_from_mongodb(
        self, folder_tree_delete, mongo_store
    ) -> None:
        keys = fp.document_keys_for(folder_tree_delete["before"], [folder_tree_delete["own"]])
        keys.append(f"id:{folder_tree_delete['shared'].upload_document_id}")
        await fp.assert_documents_gone(mongo_store, folder_tree_delete["before"], keys)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_sibling_folder_and_the_copy_outside_are_untouched(
        self, folder_tree_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        """Including the embeddings, envelope and documents the copy shares with a deleted record."""
        await fp.assert_unchanged(
            folder_tree_delete["outside_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=folder_tree_delete["outside"],
            vendor=folder_tree_delete["vendor"], what="the records outside the folder",
        )
