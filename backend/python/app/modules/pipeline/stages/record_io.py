"""What the record stages share: the records still on a job's revision, the stored record,
and section writes into the stored record under the record's lease.

Two stages write the stored record's ``semantic_metadata`` and may finish in either order:
classify owns every field but ``named_entities``, the entities stage owns ``named_entities``.
Each write re-reads the stored record under the record's lease and replaces only its own
section, so neither overwrites the other.
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import BlocksContainer
from app.models.entities import Record
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import StageJob
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.modules.transformers.transformer import TransformContext
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage

RecordFromDocument = Callable[[dict[str, Any]], Record]
StoredMetadata = dict[str, Any]
# Given the stored record's current semantic_metadata (None when it has none), the new one.
SectionUpdate = Callable[[StoredMetadata | None], StoredMetadata | None]

NAMED_ENTITIES_SECTION = "named_entities"


class StoredRecordMissing(LookupError):
    """No stored record for the revision yet (or any more)."""


def with_classification(current: StoredMetadata | None, classification: StoredMetadata) -> StoredMetadata:
    """``classification`` with the stored named-entity section kept."""
    merged = {key: value for key, value in classification.items() if key != NAMED_ENTITIES_SECTION}
    merged[NAMED_ENTITIES_SECTION] = (current or {}).get(NAMED_ENTITIES_SECTION)
    return merged


def with_named_entities(current: StoredMetadata | None, extraction: StoredMetadata | None) -> StoredMetadata:
    """The stored metadata with its named-entity section replaced; every other field kept.

    With no metadata stored yet (classification has not finished, or produced none) the
    section alone is stored, as ``extraction_blob_holder`` stores it."""
    merged = dict(current or {})
    merged[NAMED_ENTITIES_SECTION] = extraction
    return merged


class RecordStageIO:
    """One job's reads and stored-record writes. Reads are cached for the job, since
    ``fingerprint`` and ``run`` share them."""

    def __init__(
        self,
        job: StageJob,
        deadline: Deadline,
        *,
        policy: PipelinePolicy,
        graph: IGraphDBProvider,
        blob_storage: "BlobStorage",
        record_from_document: RecordFromDocument,
        record_leases: RecordLeases,
    ) -> None:
        super().__init__()
        self._job = job
        self._deadline = deadline
        self._policy = policy
        self._graph = graph
        self._blob_storage = blob_storage
        self._record_from_document = record_from_document
        self._record_leases = record_leases
        self._stored: dict[str, Any] | None = None
        self._stored_loaded = False
        self._documents: dict[str, dict[str, Any]] = {}

    @property
    def policy(self) -> PipelinePolicy:
        return self._policy

    @property
    def deadline(self) -> Deadline:
        return self._deadline

    async def current_record_ids(self, job: StageJob) -> list[str]:
        current: list[str] = []
        for record_id in job.record_ids:
            document = await self._graph.get_document(record_id, CollectionNames.RECORDS.value)
            if document is not None and document.get("contentRev") == job.rev:
                self._documents[record_id] = document
                current.append(record_id)
        return current

    async def _document(self, record_id: str) -> dict[str, Any] | None:
        document = self._documents.get(record_id)
        if document is None:
            document = await self._graph.get_document(record_id, CollectionNames.RECORDS.value)
            if document is not None:
                self._documents[record_id] = document
        return document

    async def _stored_record(self, job: StageJob) -> dict[str, Any] | None:
        if not self._stored_loaded:
            self._stored = await self._blob_storage.get_record_from_storage(job.virtual_record_id, job.org_id)
            self._stored_loaded = True
        return self._stored

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        stored = await self._stored_record(job)
        containers = stored.get("block_containers") if stored else None
        return BlocksContainer.model_validate(containers) if isinstance(containers, dict) else None

    async def _context(self, record_id: str, blocks: BlocksContainer | None) -> TransformContext | None:
        """The record as main's enrichment code reads it: its graph document plus the stored blocks."""
        document = await self._document(record_id)
        if document is None:
            return None
        record = self._record_from_document(document)
        # The graph document's converter leaves the virtual record id unset. The summary
        # point is keyed by it, and the blocks and stored record are this job's content,
        # so the outputs go under the job's virtual record id.
        record.virtual_record_id = self._job.virtual_record_id
        record.block_containers = blocks
        return TransformContext(record=record)

    async def _on_revision(self, job: StageJob, record_id: str) -> bool:
        document = await self._graph.get_document(record_id, CollectionNames.RECORDS.value)
        return document is not None and document.get("contentRev") == job.rev

    def _holding(
        self, job: StageJob, record_id: str, also: Callable[[], Awaitable[bool]] | None = None
    ) -> AbstractAsyncContextManager[None]:
        # The stored record is shared by the revision and rewritten by a re-index: write it only
        # under the lease that re-index holds, with the record still on this revision.
        async def still_current() -> bool:
            return await self._on_revision(job, record_id) and (also is None or await also())

        return self._record_leases.hold(
            record_id,
            owner=f"stage:{job.job_id}",
            wait_s=self._deadline.remaining(),
            still_current=still_current,
        )

    async def _write_section(
        self,
        job: StageJob,
        owner: str,
        update: SectionUpdate,
        *,
        still_current: Callable[[], Awaitable[bool]] | None = None,
        then: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        """Re-read the stored record under the lease and write ``update`` of its metadata, then
        run ``then`` (the stage's other writes for ``owner``) while the lease is still held.

        Raises ``RevisionSuperseded`` / ``RecordBusy`` from the lease, and
        ``StoredRecordMissing`` when no stored record exists."""
        async with self._holding(job, owner, also=still_current):
            stored = await self._blob_storage.get_record_from_storage(job.virtual_record_id, job.org_id)
            if stored is None:
                raise StoredRecordMissing(f"no stored record for virtual record {job.virtual_record_id}")
            current = stored.get("semantic_metadata")
            new = update(current if isinstance(current, dict) else None)
            if new is not None:
                await self._blob_storage.save_semantic_metadata(job.org_id, owner, job.virtual_record_id, new)
                stored["semantic_metadata"] = new
                self._stored, self._stored_loaded = stored, True
            if then is not None:
                _ = await then()

    async def _for_bound_record(self, job: StageJob, record_id: str, write: Callable[[], Awaitable[object]]) -> bool:
        """Run ``write`` for another record bound to the job under that record's lease, with the
        record still on the job's revision; False (nothing written) when it has moved on, since
        its own re-index now owns what it should show."""
        try:
            async with self._holding(job, record_id):
                _ = await write()
        except RevisionSuperseded:
            return False
        return True
