"""``ClassifyIO`` over the indexing service's graph, blob, vector-store and taxonomy services."""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.models.entities import Record
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import StageJob
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.llm import indexing_model_identity, load_ai_models

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.modules.transformers.graphdb import GraphDBTransformer
    from app.modules.transformers.vectorstore import VectorStore

Classifier = Callable[[BlocksContainer, str, list[str]], Awaitable[SemanticMetadata | None]]
RecordFromDocument = Callable[[dict[str, Any]], Record]


class GraphClassifyIO:
    """One job's IO. Reads are cached for the job, since ``fingerprint`` and ``run`` share them."""

    def __init__(
        self,
        job: StageJob,
        deadline: Deadline,
        *,
        policy: PipelinePolicy,
        graph: IGraphDBProvider,
        blob_storage: "BlobStorage",
        vector_store: "VectorStore",
        taxonomy: "GraphDBTransformer",
        classifier: Classifier,
        config_service: ConfigurationService,
        record_from_document: RecordFromDocument,
        record_leases: RecordLeases,
    ) -> None:
        super().__init__()
        self._job = job
        self._deadline = deadline
        self._policy = policy
        self._graph = graph
        self._blob_storage = blob_storage
        self._vector_store = vector_store
        self._taxonomy = taxonomy
        self._classifier = classifier
        self._config_service = config_service
        self._record_from_document = record_from_document
        self._record_leases = record_leases
        self._stored: dict[str, Any] | None = None
        self._stored_loaded = False
        self._documents: dict[str, dict[str, Any]] = {}
        self._departments: list[str] | None = None

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

    async def departments(self, org_id: str) -> list[str]:
        if self._departments is None:
            self._departments = list(await self._graph.get_departments(org_id) or [])
        return self._departments

    async def model_identity(self, org_id: str) -> str:
        return indexing_model_identity(await load_ai_models(self._config_service))

    async def _stored_record(self, job: StageJob) -> dict[str, Any] | None:
        if not self._stored_loaded:
            self._stored = await self._blob_storage.get_record_from_storage(job.virtual_record_id, job.org_id)
            self._stored_loaded = True
        return self._stored

    async def stored_metadata_present(self, job: StageJob) -> bool:
        stored = await self._stored_record(job)
        return bool(stored and stored.get("semantic_metadata"))

    async def load_blocks(self, job: StageJob) -> BlocksContainer | None:
        stored = await self._stored_record(job)
        containers = stored.get("block_containers") if stored else None
        return BlocksContainer.model_validate(containers) if isinstance(containers, dict) else None

    async def classify(self, blocks: BlocksContainer, org_id: str, departments: list[str]) -> SemanticMetadata | None:
        return await self._classifier(blocks, org_id, departments)

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

    async def restore_stored_metadata(
        self, job: StageJob, metadata: SemanticMetadata, *, still_current: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        owner = job.record_ids[0]
        async with self._holding(job, owner, also=still_current):
            await self._blob_storage.save_semantic_metadata(
                job.org_id, owner, job.virtual_record_id, metadata.model_dump(mode="json")
            )

    async def save(self, job: StageJob, record_ids: list[str], metadata: SemanticMetadata) -> None:
        # Content-level outputs are shared by every record on the revision; edges are per record.
        owner = record_ids[0]
        async with self._holding(job, owner):
            await self._blob_storage.save_semantic_metadata(
                job.org_id, owner, job.virtual_record_id, metadata.model_dump(mode="json")
            )
        if (metadata.summary or "").strip():
            document = self._documents.get(owner) or await self._graph.get_document(owner, CollectionNames.RECORDS.value)
            record = self._record_from_document(document) if document is not None else None
            await self._vector_store.index_record_summary(owner, job.virtual_record_id, job.org_id, metadata, record)
        for record_id in record_ids:
            await self._taxonomy.write_taxonomy(record_id, metadata)
