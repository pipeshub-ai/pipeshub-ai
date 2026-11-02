"""Hands a record to the stage runtime once the legacy indexer has made it searchable.

Until parse and embed become stages, the indexer's "vectors written" moment is the
external ``embed`` prerequisite: this records it for the record's content revision and
lets the coordinator dispatch what follows (classification).
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.config.constants.arangodb import CollectionNames, Connectors, ProgressStatus
from app.exceptions.indexing_exceptions import RecordDeletedError
from app.models.entities import Record
from app.modules.pipeline.coordinator import Coordinator
from app.modules.pipeline.fingerprint import content_facts
from app.modules.pipeline.models import Priority, RecordView
from app.modules.transformers.transformer import EXTRACTION_DEFERRED_FIELD
from app.services.graph_db.common.record_visibility import is_live_record
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.resource_governor.tiers import classify as classify_tier

EMBED = "embed"
# ClassifyStage.name; the stage module imports the extraction stack, so not imported here.
CLASSIFY = "classify"


class StageIngress:
    def __init__(self, coordinator: Coordinator, graph: IGraphDBProvider, logger: logging.Logger) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._graph = graph
        self._logger = logger
        # Records whose event asked for a forced reindex, while that event is processed.
        self._forced: set[str] = set()

    def mark_forced(self, record_id: str) -> None:
        """The record's current event is a forced reindex; its stages ignore fingerprints."""
        self._forced.add(record_id)

    def clear_forced(self, record_id: str) -> None:
        self._forced.discard(record_id)

    async def redrive(
        self,
        virtual_record_id: str | None,
        rev: str | None,
        stages: Sequence[str],
        *,
        force: bool = True,
    ) -> list[str] | None:
        """Re-run ``stages`` for a record's current revision because someone asked to.

        ``force=False`` (a record's first use) lets a stage whose output is
        already current finish without work. None when the runtime holds no
        state for that revision (content indexed before the runtime existed); a
        full reindex covers that case."""
        if not virtual_record_id or not rev:
            return None
        return await self._coordinator.redrive_revision(
            virtual_record_id, rev, stages, priority=Priority.INTERACTIVE, force=force
        )

    async def handoff_lost(self, record: Mapping[str, Any]) -> bool:
        """An indexed record still QUEUED for classification whose revision the runtime
        never recorded: the indexer stopped between the indexed write and the hand-off,
        and only re-running the record path dispatches its stages."""
        if record.get("indexingStatus") != ProgressStatus.COMPLETED.value:
            return False
        extraction = record.get("extractionStatus")
        # NOT_STARTED is final unless the indexed write deferred classification.
        deferred = extraction == ProgressStatus.NOT_STARTED.value and record.get(EXTRACTION_DEFERRED_FIELD) is True
        if extraction != ProgressStatus.QUEUED.value and not deferred:
            return False
        vrid, rev = record.get("virtualRecordId"), record.get("contentRev")
        if not vrid or not rev:
            return True
        return not await self._coordinator.has_seen_revision(str(vrid), str(rev))

    async def on_indexed(
        self, record: Record, *, trigger: str | None, defer_classify: bool = False
    ) -> list[str]:
        """Record ``embed`` for the record's revision and dispatch its successors;
        with ``defer_classify``, all but classification, which the record's first
        use asks for (``first_use``)."""
        container = record.block_containers
        facts = content_facts(container)
        document = await self._graph.get_document(
            record.id, CollectionNames.RECORDS.value, raise_on_error=True
        )
        if document is None or not is_live_record(document):
            raise RecordDeletedError(record.id)
        rev = document.get("contentRev")
        if not isinstance(rev, str) or not rev:
            # Paths that never saw the source bytes (re-index from a stored blob) still
            # need a revision: the blocks are that content's identity.
            rev = facts.blocks_digest[:16]
            await self._graph.update_node(record.id, CollectionNames.RECORDS.value, {"contentRev": rev})
        extension = getattr(record, "extension", None)
        view = RecordView(
            org_id=record.org_id,
            virtual_record_id=record.virtual_record_id or "",
            rev=rev,
            record_ids=(record.id,),
            connector_id=record.connector_id or "",
            tier=classify_tier(extension if isinstance(extension, str) else None, record.mime_type),
            mime_type=record.mime_type,
            text_digest=facts.text_digest,
            blocks_digest=facts.blocks_digest,
            text_chars=facts.text_chars,
            has_tables=facts.has_tables,
            has_images=facts.has_images,
        )
        # A person waiting on an upload outranks a connector backfill.
        priority = Priority.INTERACTIVE if record.connector_name == Connectors.KNOWLEDGE_BASE else Priority.BULK
        return await self._coordinator.on_external_done(
            EMBED,
            view,
            priority=priority,
            trigger=trigger or "index",
            force=record.id in self._forced,
            # The record is already searchable; a broker hiccup must not fail it.
            keep_claim_on_publish_failure=True,
            defer=frozenset({CLASSIFY}) if defer_classify else frozenset(),
        )
