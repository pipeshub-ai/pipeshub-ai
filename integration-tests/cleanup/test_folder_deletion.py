"""Deleting a folder has to remove what is inside it.

The test list asks for this explicitly — "including children/sub-folder
records" — and the code intends it. `kb_service.delete_folder` routes through
`on_records_deleted_cascade` with a comment describing exactly that:

    the folder id is one root, which cascades to remove the folder + all
    descendants (records/subfolders + edges + files docs) and publishes a
    deleteRecord event per contained file

On a running instance it does not happen. The folder goes, the API reports
success, and the sub-folder and the record inside it stay — in the graph, in
the vector database, in blob storage and in MongoDB. The record is still
retrievable by id and still searchable.

So these are expected failures for a different reason from the record and
collection suites. There the cascade works and only blob storage and MongoDB
are missed; here nothing below the folder is touched at all.

The record is deliberately two levels deep. A record sitting directly in the
deleted folder would not distinguish "the cascade does not recurse" from "the
cascade does not run".
"""

from __future__ import annotations

import logging

import pytest
import requests

logger = logging.getLogger("cleanup-folder-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

NO_CASCADE = (
    "Deleting a folder does not delete anything inside it. The folder is "
    "removed and success is reported, while the sub-folder and its record stay "
    "in every store and the record is still retrievable. kb_service.py:991 "
    "describes the cascade this path is supposed to perform."
)


def _delete_outer_folder(kb_client, nested) -> None:
    kb_client.delete_folder(nested["kb_id"], nested["outer_folder_id"])


class TestDeletingAFolder:
    """The test list's 'Delete a folder in collection' scenario."""

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=NO_CASCADE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_nested_record_stops_being_retrievable(
        self, record_in_a_nested_folder, kb_client, pipeshub_client
    ) -> None:
        """The failure a person would actually notice.

        Someone deleting a folder to remove documents is told it worked. The
        documents remain, by id and in search. Checked first because the three
        store-level tests below are consequences of this one.
        """
        nested = record_in_a_nested_folder
        _delete_outer_folder(kb_client, nested)

        pipeshub_client._ensure_access_token()
        response = requests.get(
            f"{pipeshub_client.base_url}/api/v1/knowledgeBase/record/{nested['record_id']}",
            headers={"Authorization": f"Bearer {pipeshub_client._access_token}"},
            timeout=30,
        )
        assert response.status_code != 200, (
            f"The record inside the deleted folder is still retrievable "
            f"(HTTP {response.status_code}). Deleting the folder reported "
            "success and left its contents in place."
        )

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=NO_CASCADE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_nested_records_embeddings_are_removed(
        self, record_in_a_nested_folder, kb_client, vector_store
    ) -> None:
        """Unlike record and collection deletion, which do clear these."""
        virtual_id = record_in_a_nested_folder["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        _delete_outer_folder(kb_client, record_in_a_nested_folder)

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=NO_CASCADE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_nested_records_files_are_removed(
        self, record_in_a_nested_folder, kb_client, blob_store
    ) -> None:
        prefix = record_in_a_nested_folder["storage_prefix"]
        vendor = record_in_a_nested_folder["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        _delete_outer_folder(kb_client, record_in_a_nested_folder)

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=NO_CASCADE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_nested_records_storage_documents_are_removed(
        self, record_in_a_nested_folder, kb_client, mongo_store
    ) -> None:
        prefix = record_in_a_nested_folder["storage_prefix"]
        assert await mongo_store.count_documents_under_path(prefix) > 0, (
            "No storage documents existed before the delete."
        )

        _delete_outer_folder(kb_client, record_in_a_nested_folder)

        await mongo_store.assert_documents_under_path_gone(prefix, timeout=120)
