"""Assembles the stage runtime the indexing service hosts: registry, state, coordinator,
ingress, worker and the publisher the consumers bind at startup."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

from app.config.configuration_service import ConfigurationService
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.ingress import EMBED, StageIngress
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.publisher import DeferredPublisher
from app.modules.pipeline.registry import StageRegistry
from app.modules.pipeline.stage import Deadline, StageIO
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.pipeline.stages.classify_io import (
    Classifier,
    GraphClassifyIO,
    RecordFromDocument,
)
from app.modules.pipeline.state import GraphHeadlineStatusWriter, GraphStageStateStore
from app.modules.pipeline.worker import StageJobHandler
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.concurrency import MAX_CONCURRENT_INDEXING_LLM_CALLS

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.modules.transformers.graphdb import GraphDBTransformer
    from app.modules.transformers.vectorstore import VectorStore


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


def build_pipeline_runtime(
    *,
    logger: logging.Logger,
    config_service: ConfigurationService,
    graph: IGraphDBProvider,
    blob_storage: "BlobStorage",
    vector_store: "VectorStore",
    taxonomy: "GraphDBTransformer",
    classifier: Classifier,
    record_from_document: RecordFromDocument,
) -> PipelineRuntime:
    registry = StageRegistry()
    registry.register_external(EMBED)
    registry.register(ClassifyStage())

    states = GraphStageStateStore(graph)
    headlines = GraphHeadlineStatusWriter(graph)
    publisher = DeferredPublisher()
    record_leases = RecordLeases()
    coordinator = Coordinator(
        registry, states, headlines, publisher, policy_for=lambda _org: DEFAULT_POLICY, logger=logger
    )

    def io_for(job: StageJob, deadline: Deadline) -> StageIO:
        return GraphClassifyIO(
            job,
            deadline,
            policy=DEFAULT_POLICY,
            graph=graph,
            blob_storage=blob_storage,
            vector_store=vector_store,
            taxonomy=taxonomy,
            classifier=classifier,
            config_service=config_service,
            record_from_document=record_from_document,
            record_leases=record_leases,
        )

    return PipelineRuntime(
        registry=registry,
        record_leases=record_leases,
        coordinator=coordinator,
        ingress=StageIngress(coordinator, graph, logger),
        handler=StageJobHandler(registry, states, headlines, coordinator, io_for, logger=logger),
        publisher=publisher,
        stage_limits={ClassifyStage.name: MAX_CONCURRENT_INDEXING_LLM_CALLS},
    )
