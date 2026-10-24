"""Read seam for a record's processed content.

Query-side readers go through ``record_artifact_reader`` rather than calling
``BlobStorage.get_record_from_storage`` directly, so the storage layout behind
a virtual record id can change without touching them.
"""

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage


class RecordArtifactReader(Protocol):
    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The record's processed content in the stored-record dict shape.

        ``lookup`` is the entry ``lookup_many`` returned for this id; without
        it the reader resolves the id itself. None when the record has no
        stored content.
        """
        ...

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Resolve many ids in one batch, for ``read_record(lookup=...)``.

        Ids that do not resolve are absent from the result.
        """
        ...


class LegacyRecordArtifactReader:
    """Reads the ``record_{vrid}.json`` document that ``BlobStorage`` stores."""

    def __init__(self, blob_store: "BlobStorage") -> None:
        self._blob_store = blob_store

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return await self._blob_store.get_record_from_storage(
            virtual_record_id, org_id, lookup_result=lookup
        )

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._blob_store.get_document_ids_by_virtual_record_ids(virtual_record_ids)


def record_artifact_reader(blob_store: "BlobStorage") -> RecordArtifactReader:
    """The reader for ``blob_store``'s records. Cheap enough to call per request."""
    return LegacyRecordArtifactReader(blob_store)
