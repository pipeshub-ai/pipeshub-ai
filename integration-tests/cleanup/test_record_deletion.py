"""Deleting a record has to clear all four stores.

A record's data lives in four places — the graph, the vector database, blob
storage and MongoDB — and until now only the graph was ever checked after a
delete. These tests check each store separately, so a failure names the store
that kept the data rather than reporting one undifferentiated "cleanup failed".

Two of the four currently fail, and are marked as expected failures with the
reason recorded against each. They are written as assertions of correct
behaviour rather than of today's behaviour: pinning the current result would
make the bug permanent, and the strict marker means that whoever fixes it is
told to remove the marker rather than left wondering.

The markers are deliberately on separate one-store tests. A strict expected
failure applies to a whole test, so combining stores into a single test would
let a regression in the working stores hide behind the known failure in the
broken ones.
"""

from __future__ import annotations

import logging

import pytest

logger = logging.getLogger("cleanup-record-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]

BLOB_ISSUE = (
    "Deleting a record does not reach blob storage. The delete path publishes a "
    "deleteRecord event whose scope is the graph and the vector database "
    "(kb_service.py:1178 says so in as many words), and never calls the storage "
    "service. The files are left behind and are not flagged either, so the "
    "soft-delete flag that a scheduled clean-up would collect on is never set."
)

MONGO_ISSUE = (
    "Deleting a record leaves its storage documents in MongoDB with "
    "isDeleted still false. The soft-delete path exists "
    "(storage.controller.ts:317) but only the storage API's own delete endpoint "
    "reaches it, and the record delete does not call that endpoint."
)


class TestDeletingOneRecord:
    """The test list's 'Delete a record in collection' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_embeddings_are_removed(
        self, indexed_record, kb_client, vector_store
    ) -> None:
        """The one store the delete path does reach."""
        virtual_id = indexed_record["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        kb_client.delete_record(indexed_record["record_id"])

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=BLOB_ISSUE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        vendor = indexed_record["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        kb_client.delete_record(indexed_record["record_id"])

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.xfail(strict=True, raises=AssertionError, reason=MONGO_ISSUE)
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_storage_documents_are_removed_from_mongodb(
        self, indexed_record, kb_client, mongo_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        before = await mongo_store.count_documents_under_path(prefix)
        assert before > 0, "No storage documents existed before the delete."

        kb_client.delete_record(indexed_record["record_id"])

        await mongo_store.assert_documents_under_path_gone(prefix, timeout=120)


class TestWhatSurvivesADelete:
    """Deleting one record must not disturb another."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_second_records_embeddings_are_untouched(
        self, indexed_record, second_indexed_record, kb_client, vector_store
    ) -> None:
        """Over-deletion is the quieter failure and needs its own guard.

        A delete that removes too much leaves the other record present in the
        graph and silently unsearchable. Nothing looks broken until someone
        searches for it and it is not there, which is why this is checked
        directly rather than inferred from a total count.
        """
        survivor = second_indexed_record["virtual_record_id"]
        before = await vector_store.assert_embeddings_present(survivor)

        kb_client.delete_record(indexed_record["record_id"])
        await vector_store.assert_embeddings_gone(
            indexed_record["virtual_record_id"], timeout=120
        )

        after = await vector_store.count_for_virtual_record(survivor)
        assert after == before, (
            f"Deleting one record changed another record's embeddings from "
            f"{before} to {after}. The surviving record is still in the graph "
            "but has lost the content that makes it findable."
        )
