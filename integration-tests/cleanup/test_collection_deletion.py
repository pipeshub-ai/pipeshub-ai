"""Deleting a whole collection has to clear all four stores too.

Deleting a collection is a different code path from deleting a record. What it
does today (``kb_service.py`` ``delete_knowledge_base``): it clears the graph
and the vector database, then, in the background, deletes the collection's
whole ``records/{kbId}`` storage tree in blob storage and MongoDB
(``_cleanup_kb_storage``), so every processed envelope filed there goes. That
tree also held any content another collection shares, so the delete then
re-indexes the other collection's copy (``repair_shared_records``), which files
a new envelope under that collection's own folder.

What it does not remove is each file as it was uploaded: those sit under the
uploader's ``KnowledgeBase/private/{userId}`` folder, outside that tree and
carrying no collection id, so they stay in blob storage and MongoDB. The two
upload tests are strict expected failures and will turn red, on purpose, when
that is fixed.
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

UPLOAD_GAP = (
    "A collection delete removes the records/{kbId} storage tree "
    "(kb_service.py _cleanup_kb_storage -> storage.controller.ts deleteByConnector), "
    "but each original upload is filed under the uploader's "
    "{orgId}/PipesHub/KnowledgeBase/private/{userId} folder with no connectorId tag "
    "(knowledge_base/utils/utils.ts createPlaceholderDocument), so its bytes stay in "
    "blob storage and its storage document stays in MongoDB."
)


class TestDeletingACollection:
    """The test list's 'Deleting collection' scenario, store by store."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_records_embeddings_are_removed(
        self, indexed_record, kb_client, vector_store
    ) -> None:
        virtual_id = indexed_record["virtual_record_id"]
        await vector_store.assert_embeddings_present(virtual_id)

        kb_client.delete_kb(indexed_record["kb_id"])

        await vector_store.assert_embeddings_gone(virtual_id, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_processed_files_are_removed_from_blob_storage(
        self, indexed_record, kb_client, blob_store
    ) -> None:
        prefix = indexed_record["storage_prefix"]
        vendor = indexed_record["storage_vendor"]
        await blob_store.assert_blobs_present(prefix, vendor)

        kb_client.delete_kb(indexed_record["kb_id"])

        await blob_store.assert_blobs_gone(prefix, vendor, timeout=120)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_processed_storage_documents_are_removed_from_mongodb(
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
    # Per run, so the only other holder of the shared content is this run's survivor.
    shared_body = SHARED + f"\nRun {tag}\n".encode()
    doomed_kb = kb_client.create_kb(f"cleanup-doomed-{tag}")["id"]
    survivor_kb = kb_client.create_kb(f"cleanup-survivor-{tag}")["id"]
    try:
        shared = await src.upload_to_kb(kb_client, vector_store, doomed_kb, f"shared-{tag}.md", shared_body)
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
            await src.upload_to_kb(kb_client, vector_store, survivor_kb, f"copy-{tag}.md", shared_body),
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
        # The survivor's copy is re-indexed by the delete, so it is counted on its own,
        # against the one envelope filed under the doomed copy before the delete.
        holder, own = survivors
        survivor_before = await fp.capture_when_stable(
            await fp.graph_footprint_of_connector(graph_provider, survivor_kb, excluding_records=[holder.record_id]),
            vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[own], within=records_folder(test_org_id, survivor_kb), vendor=vendor,
        )
        shared_before = await fp.capture_when_stable(
            await fp.graph_footprint_of_records(graph_provider, [holder.record_id]),
            vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[holder], vendor=vendor,
            envelope_paths={shared.virtual_record_id: before.envelope_paths[shared.virtual_record_id]},
        )
        fp.assert_shared_envelope_counted(shared_before, shared.virtual_record_id)

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
            "survivor_kb": survivor_kb,
            "own": own,
            "holder": holder,
            "survivor_before": survivor_before,
            "shared_before": shared_before,
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

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_processed_files_are_removed_from_blob_storage(
        self, collection_delete, blob_store
    ) -> None:
        """The envelopes of its own content, in every folder and sub-folder."""
        before = collection_delete["before"]
        paths = [before.envelope_paths[r.virtual_record_id] for r in collection_delete["unique"]]
        await fp.assert_blobs_gone(blob_store, before, paths, vendor=collection_delete["vendor"])

    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_processed_storage_documents_are_removed_from_mongodb(
        self, collection_delete, mongo_store
    ) -> None:
        before = collection_delete["before"]
        keys = [f"prefix:{before.envelope_paths[r.virtual_record_id]}" for r in collection_delete["unique"]]
        await fp.assert_documents_gone(mongo_store, before, keys)

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {UPLOAD_GAP}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_uploaded_files_are_removed_from_blob_storage(
        self, collection_delete, blob_store
    ) -> None:
        """Every original upload, including the shared file's: the other collection has its own."""
        before = collection_delete["before"]
        records = [collection_delete["shared"], *collection_delete["unique"]]
        paths = [before.upload_paths[r.upload_document_id] for r in records]
        await fp.assert_blobs_gone(blob_store, before, paths, vendor=collection_delete["vendor"])

    @pytest.mark.xfail(strict=True, raises=StoreNotEmptied, reason=f"Collection delete: {UPLOAD_GAP}")
    @pytest.mark.asyncio(loop_scope="session")
    async def test_its_uploaded_files_storage_documents_are_removed_from_mongodb(
        self, collection_delete, mongo_store
    ) -> None:
        records = [collection_delete["shared"], *collection_delete["unique"]]
        keys = [f"id:{r.upload_document_id}" for r in records]
        await fp.assert_documents_gone(mongo_store, collection_delete["before"], keys)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_another_collection_is_untouched(
        self, collection_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        """Everything of it except its copy of the shared content, which the next test checks."""
        await fp.assert_unchanged(
            collection_delete["survivor_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, records=[collection_delete["own"]],
            vendor=collection_delete["vendor"], what="the other collection",
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_the_content_it_shared_is_rebuilt_under_the_other_collection(
        self, collection_delete, graph_provider, vector_store, blob_store, mongo_store, test_org_id
    ) -> None:
        await fp.assert_rebuilt(
            collection_delete["shared_before"], graph_provider, vector_store, blob_store, mongo_store,
            org_id=test_org_id, connector_id=collection_delete["survivor_kb"],
            holder=collection_delete["holder"], vendor=collection_delete["vendor"],
            what="the content shared with the other collection",
        )
