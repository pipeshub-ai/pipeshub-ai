"""Deleting a record has to clear all four stores.

A record's data lives in four places — the graph, the vector database, blob
storage and MongoDB — and until now only the graph was ever checked after a
delete. These tests check each store separately, so a failure names the store
that kept the data rather than reporting one undifferentiated "cleanup failed".

Records with identical content share one virtual record id, and with it the
embeddings and the processed-record envelope; those stay until the last copy is
deleted. Each record's own uploaded file goes with that record.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper import cleanup_sources as src
from helper import delete_footprint as fp

logger = logging.getLogger("cleanup-record-deletion")

pytestmark = [pytest.mark.integration, pytest.mark.cleanup]




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

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_node_type_node_and_edges_leave_the_graph(
        self, indexed_record, kb_client, graph_provider
    ) -> None:
        graph_fp = await fp.graph_footprint_of_records(graph_provider, [indexed_record["record_id"]])
        assert len(graph_fp.handles) >= 2 and graph_fp.edges > 0, (
            f"Expected the record, its File node and their edges before the delete; found {graph_fp}."
        )

        kb_client.delete_record(indexed_record["record_id"])

        await fp.assert_graph_gone(graph_provider, graph_fp, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        vendor = indexed_record["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        kb_client.delete_record(indexed_record["record_id"])

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

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


COPIED = b"# Hood Fitting Notes\n\nA hood is fitted to the bird, never the bird to the hood.\n"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def two_copies(
    kb_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, Any], None]:
    """Two records with identical bytes, so one virtual record id, and an unrelated neighbour."""
    tag = uuid.uuid4().hex[:6]
    kb_id = kb_client.create_kb(f"cleanup-copies-{tag}")["id"]
    try:
        first = await src.upload_to_kb(kb_client, vector_store, kb_id, f"copy-1-{tag}.md", COPIED)
        second = await src.upload_to_kb(kb_client, vector_store, kb_id, f"copy-2-{tag}.md", COPIED)
        neighbour = await src.upload_to_kb(
            kb_client, vector_store, kb_id, f"neighbour-{tag}.md", src.unique_text("neighbour")
        )
        assert second.virtual_record_id == first.virtual_record_id, (
            f"Identical files got different virtual record ids ({first.virtual_record_id}, "
            f"{second.virtual_record_id}): MD5 dedup did not happen, so there is no shared content to test."
        )
        await fp.wait_for_connector_records(graph_provider, kb_id, [first.name, second.name, neighbour.name])
        vendor = await mongo_store.storage_vendor_under_path(
            fp.envelope_prefix(test_org_id, first.virtual_record_id)
        ) or "local"

        def graph_of(record):
            return fp.graph_footprint_of_records(graph_provider, [record.record_id])

        copies = await fp.capture_when_stable(
            await graph_of(first), vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[first, second], vendor=vendor,
        )
        fp.assert_every_store_holds_it(copies)
        yield {
            "kb_id": kb_id,
            "first": first,
            "second": second,
            "copies": copies,
            "second_graph": await graph_of(second),
            "neighbour": neighbour,
            "neighbour_before": await fp.capture_when_stable(
                await graph_of(neighbour), vector_store, blob_store, mongo_store,
                org_id=test_org_id, records=[neighbour], vendor=vendor,
            ),
            "vendor": vendor,
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def one_copy_deleted(two_copies, kb_client, graph_provider) -> dict[str, Any]:
    kb_client.delete_record(two_copies["first"].record_id)
    await fp.settle(fp.assert_graph_gone(graph_provider, two_copies["copies"].graph), "the first copy")
    return two_copies


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def last_copy_deleted(one_copy_deleted, kb_client, vector_store) -> dict[str, Any]:
    kb_client.delete_record(one_copy_deleted["second"].record_id)
    await fp.settle(
        fp.assert_embeddings_gone(
            vector_store, one_copy_deleted["copies"], [one_copy_deleted["second"].virtual_record_id]
        ),
        "the last copy",
    )
    return one_copy_deleted


class TestDeletingOneOfTwoIdenticalRecords:
    """'VectorDB embeddings / blob deleted if record doesn't have any duplicates', first half."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copy_leaves_the_graph(self, one_copy_deleted, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, one_copy_deleted["copies"].graph, timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_shared_embeddings_envelope_and_documents_stay(
        self, one_copy_deleted, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        """Checked store by store against the counts from before; only the deleted copy's upload may go."""
        copies = one_copy_deleted["copies"]
        vrid = one_copy_deleted["second"].virtual_record_id
        prefix = fp.envelope_prefix(test_org_id, vrid)
        assert await vector_store.count_for_virtual_record(vrid) == copies.points[vrid], (
            "Deleting one copy changed the embeddings the other copy still uses."
        )
        assert await blob_store.count_under(prefix, one_copy_deleted["vendor"]) == copies.blobs[prefix], (
            "Deleting one copy changed the shared envelope in blob storage."
        )
        assert await mongo_store.count_documents_under_path(prefix) == copies.documents[f"prefix:{prefix}"], (
            "Deleting one copy changed the shared envelope's storage documents."
        )
        second_upload = one_copy_deleted["second"].upload_document_id
        assert await mongo_store.find_document(second_upload) is not None, (
            "The surviving copy's own uploaded file lost its storage document."
        )
        upload_path = copies.upload_paths[second_upload]
        assert await blob_store.count_under(upload_path, one_copy_deleted["vendor"]) == copies.blobs[upload_path], (
            "The surviving copy's own uploaded file lost bytes in blob storage."
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_other_copy_is_still_in_the_graph_and_readable(
        self, one_copy_deleted, kb_client, graph_provider
    ) -> None:
        second = one_copy_deleted["second"]
        assert await graph_provider.count_existing_nodes(one_copy_deleted["second_graph"].handles) == len(
            one_copy_deleted["second_graph"].handles
        ), "The surviving copy lost graph nodes."
        record = kb_client.get_record(second.record_id).get("record") or {}
        assert record.get("virtualRecordId") == second.virtual_record_id, (
            f"The surviving copy no longer points at the shared content: {record}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copys_own_upload_leaves_blob_storage(
        self, one_copy_deleted, blob_store
    ) -> None:
        """The original file is per record, not shared, so it goes with its record."""
        copies = one_copy_deleted["copies"]
        document_id = one_copy_deleted["first"].upload_document_id
        await fp.assert_blobs_gone(
            blob_store, copies, [copies.upload_paths[document_id]], vendor=one_copy_deleted["vendor"]
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_deleted_copys_own_upload_leaves_mongodb(
        self, one_copy_deleted, mongo_store
    ) -> None:
        copies = one_copy_deleted["copies"]
        document_id = one_copy_deleted["first"].upload_document_id
        await fp.assert_documents_gone(mongo_store, copies, [f"id:{document_id}"])


class TestDeletingTheLastCopy:
    """Second half: once no record uses the content, every store lets go of it."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_embeddings_are_removed(self, last_copy_deleted, vector_store) -> None:
        await fp.assert_embeddings_gone(
            vector_store, last_copy_deleted["copies"], [last_copy_deleted["second"].virtual_record_id],
            timeout=60,
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_last_copy_leaves_the_graph(self, last_copy_deleted, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, last_copy_deleted["second_graph"], timeout=60)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_envelope_and_uploads_are_removed_from_blob_storage(
        self, last_copy_deleted, blob_store, test_org_id
    ) -> None:
        copies = last_copy_deleted["copies"]
        records = [last_copy_deleted["first"], last_copy_deleted["second"]]
        await fp.assert_blobs_gone(
            blob_store, copies, fp.blob_keys_for(copies, test_org_id, records), vendor=last_copy_deleted["vendor"]
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_storage_documents_are_removed_from_mongodb(
        self, last_copy_deleted, mongo_store, test_org_id
    ) -> None:
        records = [last_copy_deleted["first"], last_copy_deleted["second"]]
        await fp.assert_documents_gone(
            mongo_store, last_copy_deleted["copies"], fp.document_keys_for(test_org_id, records)
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_an_unrelated_record_in_the_same_collection_is_untouched(
        self, last_copy_deleted, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        await fp.assert_unchanged(
            last_copy_deleted["neighbour_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[last_copy_deleted["neighbour"]],
            vendor=last_copy_deleted["vendor"], what="the neighbouring record",
        )
