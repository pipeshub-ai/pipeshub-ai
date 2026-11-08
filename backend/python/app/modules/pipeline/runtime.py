"""Assembles the stage runtime the indexing service hosts: registry, state, coordinator,
ingress, worker and the publisher the consumers bind at startup."""

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import ProgressStatus
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.artifacts.sidecars import ArtifactSidecars
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.duplicates import QueuedCopies
from app.modules.pipeline.ingress import EMBED, StageIngress
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import HeadlineField, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.publisher import DeferredPublisher
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import Deadline, StageIO
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.pipeline.stages.classify_io import Classifier, GraphClassifyIO
from app.modules.pipeline.stages.entities import EntitiesStage
from app.modules.pipeline.stages.entities_io import GraphEntitiesIO
from app.modules.pipeline.stages.record_io import RecordFromDocument
from app.modules.pipeline.state import GraphHeadlineStatusWriter, GraphStageStateStore
from app.modules.pipeline.worker import StageJobHandler
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.concurrency import MAX_CONCURRENT_INDEXING_LLM_CALLS

if TYPE_CHECKING:
    from app.modules.transformers.document_extraction import DocumentExtraction
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.services.extraction.client import ExtractionClient


class StageHealth(TypedDict):
    topic: str
    limit: int
    outcomes: dict[str, int]


class PipelineHealth(TypedDict):
    publisher_bound: bool
    stages: dict[str, StageHealth]


@dataclass(frozen=True)
class PipelineRuntime:
    registry: StageRegistry
    coordinator: Coordinator
    ingress: StageIngress
    handler: StageJobHandler
    publisher: DeferredPublisher
    # Taken while a stage writes the stored record; bound to the lease manager with the consumers.
    record_leases: RecordLeases
    # Releases a record's QUEUED md5 copies once classification ends; bound to the
    # vector-membership sync when the event processor exists (``queued_copies.bind``).
    queued_copies: QueuedCopies
    # Concurrent jobs per stage consumer. Classification is bounded by the LLM, so it
    # shares the existing indexing LLM cap rather than a knob of its own.
    stage_limits: Mapping[str, int]

    def stats(self) -> PipelineHealth:
        """The ``stages`` block of the indexing service's /health."""
        return {
            "publisher_bound": self.publisher.bound,
            "stages": {
                name: {
                    "topic": self.registry.topic_for(name),
                    "limit": self.stage_limits[name],
                    "outcomes": self.handler.outcomes(name),
                }
                for name in self.registry.names()
            },
        }


def _use_extraction_service() -> bool:
    # Same switch as EventProcessor._use_service_pipeline: one extractor per deployment.
    return os.environ.get("USE_PARSING_SERVICE", "false").lower() == "true"


def build_pipeline_runtime(
    *,
    logger: logging.Logger,
    config_service: ConfigurationService,
    graph: IGraphDBProvider,
    sink: "SinkOrchestrator",
    document_extraction: "DocumentExtraction",
    extraction_client: "ExtractionClient | None",
    record_from_document: RecordFromDocument,
    classifier: Classifier | None = None,
) -> PipelineRuntime:
    """``classifier`` replaces the default (the Extraction service with ``USE_PARSING_SERVICE``,
    else ``document_extraction`` in process)."""
    use_service = _use_extraction_service() and extraction_client is not None

    async def default_classifier(
        blocks: BlocksContainer, org_id: str, departments: list[str], record_name: str, record_type: str
    ) -> SemanticMetadata | None:
        if use_service and extraction_client is not None:
            return await extraction_client.classify(
                block_container=blocks,
                org_id=org_id,
                departments=departments,
                record_name=record_name,
                record_type=record_type,
            )
        return await document_extraction.classify(
            blocks.blocks, org_id, departments, record_name, record_type, block_groups=blocks.block_groups
        )

    classify = classifier or default_classifier
    registry = StageRegistry()
    registry.register_external(EMBED)
    # Siblings: both run once the record is searchable, and classification never waits on entities.
    registry.register(ClassifyStage())
    registry.register(EntitiesStage())

    states = GraphStageStateStore(graph)
    headlines = GraphHeadlineStatusWriter(graph)
    publisher = DeferredPublisher()
    record_leases = RecordLeases()
    sidecars = ArtifactSidecars(NodeArtifactStore(sink.blob_storage, logger), config_service, logger)
    queued_copies = QueuedCopies(graph, sink, logger)
    coordinator = Coordinator(
        registry, states, headlines, publisher, policy_for=lambda _org: DEFAULT_POLICY, logger=logger
    )

    def io_for(job: StageJob, deadline: Deadline) -> StageIO:
        if job.stage == EntitiesStage.name:
            return GraphEntitiesIO(
                job,
                deadline,
                policy=DEFAULT_POLICY,
                graph=graph,
                blob_storage=sink.blob_storage,
                sink=sink,
                config_service=config_service,
                record_from_document=record_from_document,
                record_leases=record_leases,
                extraction_client=extraction_client if use_service else None,
                logger=logger,
                sidecars=sidecars,
            )
        return GraphClassifyIO(
            job,
            deadline,
            policy=DEFAULT_POLICY,
            graph=graph,
            blob_storage=sink.blob_storage,
            sink=sink,
            classifier=classify,
            config_service=config_service,
            record_from_document=record_from_document,
            record_leases=record_leases,
            sidecars=sidecars,
        )

    async def on_headline_settled(job: StageJob, record_ids: Sequence[str], status: ProgressStatus) -> None:
        stage = registry.get(job.stage) if registry.has(job.stage) else None
        if stage is not None and stage.headline is HeadlineField.EXTRACTION:
            await queued_copies.release(record_ids)

    return PipelineRuntime(
        registry=registry,
        record_leases=record_leases,
        queued_copies=queued_copies,
        coordinator=coordinator,
        ingress=StageIngress(coordinator, graph, logger),
        handler=StageJobHandler(
            registry, states, headlines, coordinator, io_for, logger=logger, on_headline_settled=on_headline_settled
        ),
        publisher=publisher,
        # Both are bounded by the LLM, so each shares the existing indexing LLM cap rather
        # than a knob of its own.
        stage_limits={
            ClassifyStage.name: MAX_CONCURRENT_INDEXING_LLM_CALLS,
            EntitiesStage.name: MAX_CONCURRENT_INDEXING_LLM_CALLS,
        },
    )
