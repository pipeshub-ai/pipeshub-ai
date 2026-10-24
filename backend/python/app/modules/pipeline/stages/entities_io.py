"""``EntitiesIO`` over the indexing service's graph, blob storage and ``SinkOrchestrator``.

Extraction and the graph/vector writes are the indexing named-entity path
(``SinkOrchestrator.extract_named_entities`` and ``persist_named_entities``, which also
queue persist retries and re-extractions), each record's under its lease. The extraction is
kept in the stage state's output (``app.modules.named_entities.kept``), not in the stored
record, which classification alone writes.
"""

import logging
from typing import TYPE_CHECKING, Any

from app.config.configuration_service import ConfigurationService
from app.models.blocks import BlocksContainer
from app.modules.artifacts.sidecars import ENTITIES, ArtifactSidecars
from app.modules.named_entities.domain.config import load_named_entity_config
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import StageJob
from app.modules.pipeline.policy import PipelinePolicy
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.record_io import RecordFromDocument, RecordStageIO
from app.services.featureflag.platform_settings import (
    is_named_entity_extraction_enabled,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.indexing_timing import stage
from app.utils.llm import indexing_model_identity, load_ai_models

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator
    from app.services.extraction.client import ExtractionClient

__all__ = ["GraphEntitiesIO"]


class GraphEntitiesIO(RecordStageIO):
    def __init__(
        self,
        job: StageJob,
        deadline: Deadline,
        *,
        policy: PipelinePolicy,
        graph: IGraphDBProvider,
        blob_storage: "BlobStorage",
        sink: "SinkOrchestrator",
        config_service: ConfigurationService,
        record_from_document: RecordFromDocument,
        record_leases: RecordLeases,
        extraction_client: "ExtractionClient | None" = None,
        logger: logging.Logger | None = None,
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
        self._config_service = config_service
        self._extraction_client = extraction_client
        self._sidecars = sidecars
        self._logger = logger or logging.getLogger(__name__)
        self._enabled: bool | None = None

    async def enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = await is_named_entity_extraction_enabled(self._config_service)
        return self._enabled

    async def entities_config(self, job: StageJob) -> dict[str, Any]:
        config = await load_named_entity_config(self._config_service)
        document = await self._document(job.record_ids[0]) or {}
        return {
            "enabled": await self.enabled(),
            "model": indexing_model_identity(await load_ai_models(self._config_service)),
            "kinds": sorted(kind.value for kind in config.enabled()),
            "tz": config.tz,
            "budgets": config.budgets.model_dump(mode="json"),
            # Dates are normalized against the record's own times; its name and type go to the model.
            "record": [
                document.get("recordName"),
                document.get("recordType"),
                document.get("sourceLastModifiedTimestamp") or document.get("sourceCreatedAtTimestamp"),
            ],
            "service": self._extraction_client is not None,
        }

    async def extract(self, job: StageJob, blocks: BlocksContainer) -> NamedEntityExtraction:
        """Never raises for a model failure: that is a FAILED extraction."""
        ctx = await self._context(job.record_ids[0], blocks)
        if ctx is None:
            raise RevisionSuperseded(job.record_ids[0])
        with stage("entities.extract"):
            return await self._sink.extract_named_entities(ctx, client=self._extraction_client)

    async def save(self, job: StageJob, record_ids: list[str], extraction: NamedEntityExtraction) -> str:
        """Each record's entities in the graph and the entity points, under its lease. Returns
        the owner's persist outcome (``written``, ``kept``, ``disabled``, ...)."""
        owner = record_ids[0]
        outcomes: dict[str, str] = {}

        async def persist(record_id: str) -> None:
            ctx = await self._context(record_id, None)
            if ctx is None:
                return
            with stage("entities.persist"):
                outcomes[record_id] = await self._sink.persist_named_entities(ctx, extraction)

        # Each record's entities are written under its lease, on this revision; the owner's
        # moving on is the job's (RevisionSuperseded), a bound record's is skipped.
        async with self._holding(job, owner):
            await persist(owner)
            # Only an extraction the graph now holds: a kept, failed or turned-off run leaves
            # the revision's entities as they were, so it has no new output to file.
            if outcomes.get(owner) == "written" and self._sidecars is not None:
                _ = await self._sidecars.write(
                    job.org_id, job.virtual_record_id, job.rev, ENTITIES, job.stage_version,
                    extraction.model_dump(mode="json"),
                )
        for record_id in record_ids[1:]:
            _ = await self._for_bound_record(job, record_id, lambda rid=record_id: persist(rid))
        outcome = outcomes.get(owner, "kept")
        if outcome == "written":
            await self._copy_to_promoted_copies(job, owner, set(record_ids))
        return outcome

    async def _copy_to_promoted_copies(self, job: StageJob, owner: str, bound: set[str]) -> None:
        """Copies of the content promoted before this run finished took the owner's mentions
        as they were then; give them these. Best-effort: a persist retry or the next reindex
        repairs a miss."""
        try:
            siblings = await self._graph.get_records_by_virtual_record_id(job.virtual_record_id) or []
        except Exception as exc:
            self._logger.warning("Could not list the copies of record %s: %s", owner, type(exc).__name__)
            return
        for sibling in siblings:
            if not sibling or sibling in bound:
                continue
            try:
                document = await self._document(sibling)
                if document is None or document.get("orgId") != job.org_id:
                    continue
                await self._graph.copy_named_entity_mentions(owner, sibling)
                await self._sink.sync_entities_for_duplicate(document)
            except Exception as exc:
                self._logger.warning(
                    "Named entities of record %s not copied to its copy %s: %s", owner, sibling, type(exc).__name__
                )
