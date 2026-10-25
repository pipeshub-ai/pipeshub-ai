"""``ClassifyIO`` over the indexing service's graph, blob storage and ``SinkOrchestrator``.

The writes are the indexing enrichment (``SinkOrchestrator.prepare_enrichment`` and
``write_enrichment``): taxonomy names resolved to canonical nodes, the classification in
the stored record, the summary point, each record's taxonomy edges and the entity points
they touch. Named entities are the entities stage's; ``extractionStatus`` is the stage
worker's headline.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.config.configuration_service import ConfigurationService
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.artifacts.sidecars import (
    CLASSIFICATION,
    ArtifactSidecars,
    classification_payload,
)
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import StageJob
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.record_io import (
    NAMED_ENTITIES_SECTION,
    RecordFromDocument,
    RecordStageIO,
    with_classification,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.indexing_timing import stage
from app.utils.llm import indexing_model_identity, load_ai_models

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator

# (blocks, org id, departments, record name, record type) -> the classification.
Classifier = Callable[[BlocksContainer, str, list[str], str, str], Awaitable[SemanticMetadata | None]]

__all__ = ["Classifier", "GraphClassifyIO", "RecordFromDocument"]


class GraphClassifyIO(RecordStageIO):
    def __init__(
        self,
        job: StageJob,
        deadline: Deadline,
        *,
        policy: PipelinePolicy,
        graph: IGraphDBProvider,
        blob_storage: "BlobStorage",
        sink: "SinkOrchestrator",
        classifier: Classifier,
        config_service: ConfigurationService,
        record_from_document: RecordFromDocument,
        record_leases: RecordLeases,
        sidecars: ArtifactSidecars | None = None,
    ) -> None:
        super().__init__(
            job,
            deadline,
            policy=policy,
            graph=graph,
            blob_storage=blob_storage,
            record_from_document=record_from_document,
            record_leases=record_leases,
        )
        self._sink = sink
        self._classifier = classifier
        self._config_service = config_service
        self._sidecars = sidecars
        self._departments: list[str] | None = None

    async def departments(self, org_id: str) -> list[str]:
        if self._departments is None:
            self._departments = list(await self._graph.get_departments(org_id) or [])
        return self._departments

    async def model_identity(self, org_id: str) -> str:
        return indexing_model_identity(await load_ai_models(self._config_service))

    async def _record_header(self, job: StageJob) -> tuple[str, str]:
        """The owner's name and type, which the prompt sends with the content."""
        document = await self._document(job.record_ids[0]) or {}
        return str(document.get("recordName") or ""), str(document.get("recordType") or "")

    async def classify_config(self, job: StageJob) -> dict[str, Any]:
        name, record_type = await self._record_header(job)
        return {
            "record": [name, record_type],
            "resolution": self._sink.entity_resolution_mode(),
        }

    async def classify(self, blocks: BlocksContainer, org_id: str, departments: list[str]) -> SemanticMetadata | None:
        name, record_type = await self._record_header(self._job)
        with stage("extract"):
            return await self._classifier(blocks, org_id, departments, name, record_type)

    async def stored_metadata_present(self, job: StageJob) -> bool:
        stored = await self._stored_record(job)
        metadata = stored.get("semantic_metadata") if stored else None
        return isinstance(metadata, dict) and any(
            value for key, value in metadata.items() if key != NAMED_ENTITIES_SECTION
        )

    async def restore_stored_metadata(
        self, job: StageJob, metadata: SemanticMetadata, *, still_current: Callable[[], Awaitable[bool]] | None = None
    ) -> None:
        classification = metadata.model_dump(mode="json")
        owner = job.record_ids[0]
        await self._write_section(
            job,
            owner,
            lambda current: with_classification(current, classification),
            still_current=still_current,
            written=lambda: self._write_sidecar(job, metadata),
            then=lambda: self._restore_summary_point(owner, metadata),
        )

    async def _restore_summary_point(self, record_id: str, metadata: SemanticMetadata) -> None:
        # The re-index of unchanged content that left the stored record without its
        # classification also deleted the record's vectors, its summary point among them.
        # Put that back from the kept classification too; the graph taxonomy survives a re-index.
        ctx = await self._context(record_id, None)
        if ctx is None:
            raise RevisionSuperseded(record_id)
        ctx.record.semantic_metadata = metadata
        await self._sink.write_summary_point(ctx)

    async def _write_sidecar(self, job: StageJob, metadata: SemanticMetadata) -> None:
        # Under the lease, right after the stored record: the revision's sidecar never says
        # something its stored record does not.
        if self._sidecars is not None:
            _ = await self._sidecars.write(
                job.org_id, job.virtual_record_id, job.rev, CLASSIFICATION, job.stage_version,
                classification_payload(metadata),
            )

    async def save(self, job: StageJob, record_ids: list[str], metadata: SemanticMetadata) -> None:
        # Content-level outputs are shared by every record on the revision; edges are per record.
        owner = record_ids[0]
        ctx = await self._context(owner, None)
        if ctx is None:
            raise RevisionSuperseded(owner)
        ctx.record.semantic_metadata = metadata
        # Resolution reads the graph (and may ask the model) before anything is written.
        await self._sink.prepare_enrichment(ctx)
        classification = metadata.model_dump(mode="json")
        # Every output is written under the record's lease with the record still on this
        # revision, so a stale job cannot put its summary or taxonomy on newer content.
        await self._write_section(
            job,
            owner,
            lambda current: with_classification(current, classification),
            written=lambda: self._write_sidecar(job, metadata),
            then=lambda: self._sink.write_enrichment(ctx),
        )
        for record_id in record_ids[1:]:
            other = await self._context(record_id, None)
            if other is None:
                continue
            other.record.semantic_metadata = metadata
            other.entity_resolution = ctx.entity_resolution
            _ = await self._for_bound_record(
                job, record_id, lambda bound=other: self._sink.write_enrichment(bound, index_summary=False)
            )
