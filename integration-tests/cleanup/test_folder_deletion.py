"""Deleting a folder has to take the records inside it with it.

The cascade works. Deleting a folder removes the folder and the records it
contains, from the graph and from the vector database — the first two tests
guard that. Blob storage and MongoDB are left behind, the same way they are on
the record and collection paths, which is the third and fourth.

These tests deliberately go one level deep. Nesting is possible: the gateway
creates a subfolder when a `?folderId=` query parameter is present, routing it
to the connector's `/folder/{parent}/subfolder`, and `kb_client.create_folder`
uses that. A `parentId` in the request body, by contrast, is ignored and the
folder is created at the root — an earlier version of this file passed the
parent that way, got two sibling root folders, deleted an empty one, and read
the untouched record as a broken cascade. One level is enough to prove the
cascade reaches a folder's contents and that blob storage and MongoDB are left
behind.
"""

from __future__ import annotations

import logging

import pytest

from helper.cleanup_errors import StoreNotEmptied
import requests

logger = logging.getLogger("cleanup-folder-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

STORAGE_GAP = (
    "The delete path's scope is the graph and the vector database "
    "(kb_service.py:1178). Neither blob storage nor the storage documents in "
    "MongoDB are touched, and the documents are not flagged either, so nothing "
    "will collect them later. The same on all three delete paths."
)


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

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Folder delete: {STORAGE_GAP}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_files_are_removed(
        self, record_in_a_folder, kb_client, blob_store
    ) -> None:
        prefix = record_in_a_folder["storage_prefix"]
        vendor = record_in_a_folder["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        _delete_folder(kb_client, record_in_a_folder)

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Folder delete: {STORAGE_GAP}")
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
