"""Deleting a whole collection has to clear all four stores too.

Deleting a collection is a different code path from deleting a record, and it
behaves the same way: the graph and the vector database are cleared, blob
storage and MongoDB are not. Testing both is what shows the gap is in the
delete design rather than in one endpoint — a distinction that decides whether
the fix is one call or a shared step every delete path needs.
"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger("cleanup-collection-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

SHARED_CAUSE = (
    "The delete path's scope is the graph and the vector database "
    "(kb_service.py:1178). Neither blob storage nor the storage documents in "
    "MongoDB are touched, and the documents are not flagged either, so nothing "
    "will collect them later."
)


class TestDeletingACollection:
    """The test list's 'Deleting collection' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_embeddings_are_removed(
        self, indexed_record, kb_client, vector_store
    ) -> None:
        """The one store collection deletion does reach."""
        virtual_id = indexed_record["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        kb_client.delete_kb(indexed_record["kb_id"])

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.xfail(strict=True, reason=f"Collection delete: {SHARED_CAUSE}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        await blob_store.assert_blobs_present(prefix)

        kb_client.delete_kb(indexed_record["kb_id"])

        await blob_store.assert_blobs_gone(prefix, timeout=120)

    @pytest.mark.xfail(strict=True, reason=f"Collection delete: {SHARED_CAUSE}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_storage_documents_are_removed_from_mongodb(
        self, indexed_record, kb_client, mongo_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        assert await mongo_store.count_documents_under_path(prefix) > 0, (
            "No storage documents existed before the delete."
        )

        kb_client.delete_kb(indexed_record["kb_id"])

        await mongo_store.assert_documents_under_path_gone(prefix, timeout=120)
