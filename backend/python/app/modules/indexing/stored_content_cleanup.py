"""Remove what a deleted record left in blob storage and MongoDB.

A deleted record's data is cleaned up in three separable steps, each callable on
its own (a soft delete, for instance, may run only the first):

1. vectors: ``IndexingPipeline.bulk_delete_embeddings`` and the connector purges;
2. stored content, this module: blob files and their MongoDB storage documents;
3. graph: the record nodes, and last of all the ``virtualRecordToDocIdMapping``
   row, which is the durable "still to clean" marker for a virtual record.

Two kinds of stored content:

* The processed record ("envelope"): the ``record_{virtualRecordId}`` and
  ``metadata_{virtualRecordId}`` documents, under the record's folder path (or
  ``records/{virtualRecordId}`` for older records). Records with identical
  content share a virtual record id, so the envelope may go only once no record
  uses it. Callers decide that, and this module checks the graph
  again right before purging, because the purge cannot be undone. The mapping
  row is dropped only after storage confirms the purge, so a failed purge keeps
  the row and the orphan sweeper (``indexing_main``) retries it.
* A knowledge-base upload's original file: a storage document of its own (the
  record's ``externalRecordId``) that belongs to that one record.

Purges are idempotent: storage answers "0 removed" for what is already gone.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.vector_db.membership import remaining_record_keys

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from logging import Logger

    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# Storage calls in flight at once; a connector delete can release many ids.
PURGE_CONCURRENCY = 8


class StoredContentCleanup:
    def __init__(
        self,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        blob_storage: BlobStorage,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.blob_storage = blob_storage

    async def release_virtual_records(
        self, virtual_record_ids: Iterable[str], *, org_id: str | None = None
    ) -> list[str]:
        """Purge the envelopes of virtual records no record uses, then drop their mapping rows.

        An id a record still uses, a record in the trash included, is left as it
        is: the purge releases it with that record. Returns the ids whose purge
        failed; their mapping rows are kept for a retry.
        """
        ids = [v for v in dict.fromkeys(virtual_record_ids) if v]
        if not ids:
            return []
        in_use: set[str] = set()

        async def purge(vrid: str) -> None:
            records = await self.graph_provider.get_records_by_virtual_record_id(
                virtual_record_id=vrid, raise_on_error=True, visibility=RecordVisibility.ALL
            )
            if remaining_record_keys(records):
                in_use.add(vrid)
                return
            owner = org_id
            if not owner:
                row = await self.graph_provider.get_document(
                    vrid, CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value, raise_on_error=True
                )
                if row is None:
                    # No mapping row: nothing was filed that this id can reach.
                    return
                owner = row.get("orgId")
                if not owner:
                    # The row is the sweeper's only marker for this content; keep it.
                    raise ValueError(f"mapping row for {vrid} names no organisation")
            await self.blob_storage.purge_virtual_record_documents(owner, vrid)

        failed = await self._run_all(ids, purge, "virtual record")
        if in_use:
            self.logger.info(
                "Kept the stored content of %d virtual record(s) still used by another record",
                len(in_use),
            )
        released = [v for v in ids if v not in failed and v not in in_use]
        if released:
            try:
                await self.graph_provider.delete_nodes(
                    keys=released,
                    collection=CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value,
                )
            except Exception as exc:
                # Harmless: the sweeper finds these rows, sees nothing left, and drops them.
                self.logger.error(
                    "Purged the stored content of %d virtual record(s) but could not drop "
                    "their mapping rows: %s",
                    len(released),
                    exc,
                )
        return failed

    async def purge_documents(self, org_id: str, document_ids: Iterable[str]) -> list[str]:
        """Purge storage documents that belonged to deleted records (their original uploads).

        Returns the ids whose purge failed.
        """
        ids = [d for d in dict.fromkeys(document_ids) if d]

        async def purge(document_id: str) -> None:
            await self.blob_storage.purge_document(org_id, document_id)

        return await self._run_all(ids, purge, "storage document")

    async def _run_all(
        self, ids: list[str], purge: Callable[[str], Awaitable[Any]], what: str
    ) -> list[str]:
        failed: list[str] = []
        gate = asyncio.Semaphore(PURGE_CONCURRENCY)

        async def one(item: str) -> None:
            async with gate:
                try:
                    await purge(item)
                except Exception as exc:
                    failed.append(item)
                    self.logger.warning(
                        "Could not remove the stored content of %s %s; it will be retried: %s",
                        what,
                        item,
                        exc,
                    )

        await asyncio.gather(*(one(item) for item in ids))
        return failed
