"""Deleting a whole collection has to clear all four stores too.

Deleting a collection is a different code path from deleting a record, and it
behaves the same way: the graph and the vector database are cleared, blob
storage and MongoDB are not. Testing both is what shows the gap is in the
delete design rather than in one endpoint — a distinction that decides whether
the fix is one call or a shared step every delete path needs.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper import cleanup_sources as src
from helper import delete_footprint as fp
from helper.cleanup_errors import StoreNotEmptied
from helper.mongo_store import records_folder

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

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {SHARED_CAUSE}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        vendor = indexed_record["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        kb_client.delete_kb(indexed_record["kb_id"])

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {SHARED_CAUSE}")
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


SHARED = b"# Mews Inspection Checklist\n\nPerches, bath, scales and jesses are checked weekly.\n"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def collection_delete(
    kb_client, graph_provider, vector_store, blob_store, mongo_store, test_org_id
) -> AsyncGenerator[dict[str, Any], None]:
    """A collection with folders and a file whose content another collection also holds.

    doomed KB:  shared.md (uploaded first, so the shared content starts here),
                root.md, folder/in-folder.md, folder/sub/in-sub.md
    survivor KB: copy.md (same bytes as shared.md, so it reuses its virtual id),
                 own.md
    """
    tag = uuid.uuid4().hex[:6]
    doomed_kb = kb_client.create_kb(f"cleanup-doomed-{tag}")["id"]
    survivor_kb = kb_client.create_kb(f"cleanup-survivor-{tag}")["id"]
    try:
        shared = await src.upload_to_kb(kb_client, vector_store, doomed_kb, f"shared-{tag}.md", SHARED)
        folder = src.folder_id_of(kb_client.create_folder(doomed_kb, f"folder-{tag}"))
        sub = src.folder_id_of(kb_client.create_folder(doomed_kb, f"sub-{tag}", parent_id=folder))
        unique = [
            await src.upload_to_kb(kb_client, vector_store, doomed_kb, f"root-{tag}.md", src.unique_text("root")),
            await src.upload_to_kb(
                kb_client, vector_store, doomed_kb, f"in-folder-{tag}.md", src.unique_text("in folder"),
                folder_id=folder,
            ),
            await src.upload_to_kb(
                kb_client, vector_store, doomed_kb, f"in-sub-{tag}.md", src.unique_text("in sub"),
                folder_id=sub,
            ),
        ]
        survivors = [
            await src.upload_to_kb(kb_client, vector_store, survivor_kb, f"copy-{tag}.md", SHARED),
            await src.upload_to_kb(kb_client, vector_store, survivor_kb, f"own-{tag}.md", src.unique_text("own")),
        ]
        assert survivors[0].virtual_record_id == shared.virtual_record_id, (
            "The survivor's copy did not reuse the shared file's virtual record id "
            f"({survivors[0].virtual_record_id} vs {shared.virtual_record_id}); this "
            "scenario cannot test shared content surviving."
        )
        for kb_id, records in ((doomed_kb, [shared, *unique]), (survivor_kb, survivors)):
            await fp.wait_for_connector_records(graph_provider, kb_id, [r.name for r in records])

        doomed_folder = records_folder(test_org_id, doomed_kb)
        vendor = await fp.storage_vendor(mongo_store, test_org_id, shared.virtual_record_id, within=doomed_folder)
        doomed_graph = await fp.graph_footprint_of_connector(graph_provider, doomed_kb)
        before = await fp.capture_when_stable(
            doomed_graph, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[shared, *unique], within=doomed_folder, vendor=vendor,
        )
        fp.assert_every_store_holds_it(before)
        undecided = fp.pending_shared_envelopes(test_org_id, [shared.virtual_record_id])
        survivor_before = await fp.capture_when_stable(
            await fp.graph_footprint_of_connector(graph_provider, survivor_kb),
            vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=survivors, within=records_folder(test_org_id, survivor_kb),
            vendor=vendor, envelope_paths=undecided,
        )

        kb_client.delete_kb(doomed_kb)
        await fp.settle(
            fp.assert_embeddings_gone(vector_store, before, [r.virtual_record_id for r in unique]),
            "the collection",
        )
        yield {
            "kb_id": doomed_kb,
            "before": before,
            "unique": unique,
            "shared": shared,
            "survivors": survivors,
            "survivor_before": survivor_before,
            "vendor": vendor,
        }
    finally:
        for kb_id in (doomed_kb, survivor_kb):
            try:
                kb_client.delete_kb(kb_id)
            except Exception as exc:  # noqa: BLE001 - 404 when the test already deleted it
                logger.debug("Could not delete knowledge base %s: %s", kb_id, exc)


class TestDeletingACollectionWithFoldersAndSharedContent:
    """The whole collection goes: folders, sub-folders, records, and what they own in each store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_no_node_or_edge_of_it_is_left_in_the_graph(self, collection_delete, graph_provider) -> None:
        await fp.assert_graph_gone(graph_provider, collection_delete["before"].graph)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_every_records_own_embeddings_are_removed(self, collection_delete, vector_store) -> None:
        await fp.assert_embeddings_gone(
            vector_store, collection_delete["before"],
            [r.virtual_record_id for r in collection_delete["unique"]], timeout=30,
        )

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {SHARED_CAUSE}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_files_are_removed_from_blob_storage(
        self, collection_delete, blob_store
    ) -> None:
        """Envelopes of its own content, and every original upload, including the shared file's."""
        before = collection_delete["before"]
        paths = fp.blob_keys_for(before, collection_delete["unique"])
        paths.append(before.upload_paths[collection_delete["shared"].upload_document_id])
        await fp.assert_blobs_gone(blob_store, before, paths, vendor=collection_delete["vendor"])

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {SHARED_CAUSE}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_storage_documents_are_removed_from_mongodb(
        self, collection_delete, mongo_store
    ) -> None:
        keys = fp.document_keys_for(collection_delete["before"], collection_delete["unique"])
        keys.append(f"id:{collection_delete['shared'].upload_document_id}")
        await fp.assert_documents_gone(mongo_store, collection_delete["before"], keys)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_another_collection_and_the_content_it_shares_are_untouched(
        self, collection_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        await fp.assert_unchanged(
            collection_delete["survivor_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=collection_delete["survivors"],
            vendor=collection_delete["vendor"], what="the other collection",
        )
