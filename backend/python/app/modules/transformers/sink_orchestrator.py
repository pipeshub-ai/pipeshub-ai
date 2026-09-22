import logging
from typing import TYPE_CHECKING, Optional

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ProgressStatus,
)
from app.models.blocks import (
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
)
from app.models.entities import Record, RecordGroupType
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.image_description import ImageDescriber, harvest_descriptions
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.transformer import TransformContext, Transformer
from app.modules.transformers.vectorstore import VectorStore
from app.services.cache.invalidation_hooks import notify_record_indexed
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.modules.activity_metrics import record_service_activity
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.exceptions.indexing_exceptions import IndexingError

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.modules.entity_resolution import EntityResolver


class SinkOrchestrator(Transformer):
    def __init__(
        self,
        graphdb: GraphDBTransformer,
        blob_storage: BlobStorage,
        vector_store: VectorStore,
        graph_provider: IGraphDBProvider,
        logger,
        config_service: "ConfigurationService",
        entity_vector_store: Optional[EntityVectorStore] = None,
        entity_resolver: Optional["EntityResolver"] = None,
    ) -> None:
        super().__init__()
        self.graphdb = graphdb
        self.logger = logging.getLogger(__name__)
        self.blob_storage = blob_storage
        self.vector_store = vector_store
        self.graph_provider = graph_provider
        self.entity_vector_store = entity_vector_store
        # Canonicalises taxonomy names between classification and the graph
        # write; optional so the sink stays constructible without it.
        self.entity_resolver = entity_resolver
        self.logger = logger
        # Runs before the blob write below, which is the only reason it lives
        # here: a description generated later would never reach the stored
        # record, and the stored record is what `fetch_record` serves.
        self.image_describer = ImageDescriber(logger, config_service)

    # This is not a good long-term solution and should be improved in the future.
    LIMIT_SQL_ROW_BLOCKS_TO = 10
    def _build_limited_sql_block_container(
        self, block_containers: BlocksContainer, limit: int
    ) -> BlocksContainer:
        """Build a BlocksContainer with at most `limit` row blocks for blob storage.
        """
        original_blocks = block_containers.blocks
        row_blocks = [b for b in original_blocks if b.type == BlockType.TABLE_ROW]

        if len(row_blocks) <= limit:
            return block_containers 
        limited_blocks =  row_blocks[:limit]

        kept_indices = {b.index for b in limited_blocks}

        limited_block_groups = []
        for bg in block_containers.block_groups:
            bg_copy = bg.model_copy(deep=True)
            if bg_copy.children and bg_copy.children.block_ranges:
                kept_child_indices = []
                for r in bg_copy.children.block_ranges:
                    for idx in range(r.start, r.end + 1):
                        if idx in kept_indices:
                            kept_child_indices.append(idx)
                bg_copy.children = BlockGroupChildren.from_indices(
                    block_indices=kept_child_indices,
                    block_group_indices=(
                        [idx for r in bg_copy.children.block_group_ranges for idx in range(r.start, r.end + 1)]
                        if bg_copy.children.block_group_ranges
                        else None
                    ),
                )
            limited_block_groups.append(bg_copy)

        self.logger.info(
            "📦 SQL blob-storage limit applied: %d / %d row blocks kept",
            len(limited_blocks),
            len(row_blocks),
        )
        return BlocksContainer(blocks=limited_blocks, block_groups=limited_block_groups)


    @staticmethod
    def _activity_labels(record: Record) -> tuple[str, str, str]:
        """
        Returns ``(connector, org, kb)``. ``kb`` is the Knowledge Base id for
        KB-sourced records and ``"none"`` for everything else.

        KB record docs don't carry ``recordGroupId`` — for KB uploads the
        group is "external" to the connector framework, so the KB UUID is
        stored as ``externalGroupId``.
        """
        connector = record.connector_name.value if record.connector_name else "unknown"
        org = record.org_id or "unknown"
        is_kb = (
            record.connector_name == Connectors.KNOWLEDGE_BASE
            or record.record_group_type == RecordGroupType.KB
        )
        kb_id = record.record_group_id or record.external_record_group_id
        kb = kb_id if is_kb and kb_id else "none"
        return connector, org, kb

    async def apply(self, ctx: TransformContext) -> None:
        """Legacy entry-point: runs both phases (index + enrich) sequentially.

        Preserved for backward compatibility with code that has not been
        migrated to the split ``index()`` / ``enrich()`` API.
        """
        await self.index(ctx)
        await self.enrich(ctx)

    # ------------------------------------------------------------------
    # Phase 1: INDEX — vector store + blob.  Document becomes searchable.
    # ------------------------------------------------------------------

    async def index(self, ctx: TransformContext) -> None:
        """Phase 1: write to BlobStorage and VectorStore.

        After this method returns, the document is queryable via the vector
        store and ``indexingStatus`` is set to ``COMPLETED``.  The graph
        enrichment (``extractionStatus``) is left unchanged here.
        """
        record = ctx.record
        full_block_containers = None
        skip_blob = bool(ctx.settings.get("skip_blob"))
        skip_vector_store = bool(ctx.settings.get("skip_vector_store")) or bool(
            ctx.settings.get("sink_only")
        )

        if not skip_blob:
            is_sql = any(
                bg.sub_type in (GroupSubType.SQL_TABLE, GroupSubType.SQL_VIEW)
                for bg in record.block_containers.block_groups
            ) if record.block_containers and record.block_containers.block_groups else False

            if is_sql and self.LIMIT_SQL_ROW_BLOCKS_TO is not None:
                full_block_containers = record.block_containers
                record.block_containers = self._build_limited_sql_block_container(
                    full_block_containers, self.LIMIT_SQL_ROW_BLOCKS_TO
                )

            await self._describe_images(ctx)

            try:
                await self.blob_storage.apply(ctx)
            finally:
                if full_block_containers is not None:
                    record.block_containers = full_block_containers

        record_id = record.id
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is None:
            self.logger.error(f"❌ Record {record_id} not found in database")
            raise Exception(f"Record {record_id} not found in database")

        if skip_vector_store:
            success = await self.graph_provider.batch_update_nodes(
                [
                    {
                        "id": record_id,
                        "virtualRecordId": record.virtual_record_id,
                        "indexingStatus": ProgressStatus.NOT_STARTED.value,
                        "processingStartedAt": None,
                        "isDirty": False,
                    }
                ],
                CollectionNames.RECORDS.value,
            )
            if not success:
                self.logger.warning(
                    "⚠️ Failed to update record %s status - record may not exist in database",
                    record_id,
                )
                return
            self.logger.info(
                "✅ Sink-only mode completed for record %s (vector indexing skipped)",
                record_id,
            )
            return

        indexing_status = record_doc.get("indexingStatus")
        if skip_blob or indexing_status != ProgressStatus.COMPLETED.value:
            connector, org, kb = self._activity_labels(record)
            result = await self.vector_store.apply(ctx)
            if result is False:
                record_service_activity("indexing_service", "document_indexed", connector=connector, status="failed", org=org, kb=kb, mimetype=record.mime_type or "none")
                raise IndexingError(
                    message=f"Vector store did not index record {record_id}",
                    record_id=record_id,
                )

            self.logger.debug(f"✅ Vector store indexing succeeded for record {record_id}")
            # Per-record indexing success counter (powers the Ingestion dashboard).
            record_service_activity("indexing_service", "document_indexed", connector=connector, status="ok", org=org, kb=kb, mimetype=record.mime_type or "none")
            self.logger.debug(f"Saving reconciliation metadata for record {record_id}")
            await self._update_indexing_status(ctx)
            # await self.graphdb.apply(ctx)
            await self._save_reconciliation_metadata(ctx)
            await self._sync_record_name_entity(ctx)
            await self._sync_record_group_entity(ctx)

    async def _sync_record_group_entity(self, ctx: TransformContext) -> None:
        """Sync the record's RecordGroup (e.g. Jira project, Drive folder) into
        the entities vector collection as a Layer-0 deterministic entity.

        Runs for every record regardless of connector, rather than being
        wired per-connector, since ``record_group_id`` is a generic field on
        every ``Record`` once ``_handle_record_group``/connector sync has
        resolved it. Best-effort.
        """
        if not self.entity_vector_store:
            return
        record = ctx.record
        if not record.record_group_id:
            return
        try:
            group_doc = await self.graph_provider.get_record_group_by_id(
                record.record_group_id
            )
            if not group_doc:
                return
            group_name = group_doc.get("groupName") or group_doc.get("name")
            if not group_name or not group_name.strip():
                return

            from app.models.entities import EntityRecord, EntityType, EntityTypeCategory

            await self.entity_vector_store.upsert_entity(
                EntityRecord(
                    entity_id=record.record_group_id,
                    entity_type=EntityType.RECORD_GROUP,
                    name=group_name,
                    org_id=record.org_id,
                    connector_ids=[record.connector_id] if record.connector_id else [],
                    # Self-reference: a record group entity is only reachable
                    # via `should={connectorIds}` in Stage 1's vector filter
                    # without this — a user with record-group-level (but not
                    # connector-level) access would otherwise never see it.
                    record_group_ids=[record.record_group_id],
                    type_category=EntityTypeCategory.PREDEFINED,
                )
            )
        except Exception as exc:
            self.logger.warning(
                "Record group entity sync failed for record %s (non-fatal): %s",
                record.id,
                exc,
            )

    async def _sync_record_name_entity(self, ctx: TransformContext) -> None:
        """Sync the record's name into the entities vector collection.

        Runs after successful vector-store indexing so the record's name is
        resolvable as a filter facet (entityType=record) alongside categories,
        topics, etc. Best-effort: failures here must not fail the record
        pipeline since the record is already searchable via the records
        collection.
        """
        if not self.entity_vector_store:
            return
        record = ctx.record
        if not record.record_name or not record.record_name.strip():
            return
        try:
            from app.models.entities import EntityRecord, EntityType, EntityTypeCategory

            await self.entity_vector_store.upsert_entity(
                EntityRecord(
                    entity_id=record.id,
                    entity_type=EntityType.RECORD,
                    name=record.record_name,
                    org_id=record.org_id,
                    connector_ids=[record.connector_id] if record.connector_id else [],
                    record_group_ids=[record.record_group_id] if record.record_group_id else [],
                    type_category=EntityTypeCategory.PREDEFINED,
                )
            )
        except Exception as exc:
            self.logger.warning(
                "Record name entity sync failed for record %s (non-fatal): %s",
                record.id,
                exc,
            )

    async def sync_entities_for_duplicate(self, record_doc: dict) -> None:
        """Re-project a deduplicated record's taxonomy into the entities
        vector collection, and create its own record / record_group points.

        Called from the MD5-dedup short-circuit
        (``EventProcessor._check_duplicate_by_md5`` and the QUEUED-promotion
        path in the record Kafka handler) after ``copy_document_relationships``
        has copied this record's taxonomy edges from the primary/duplicate it
        was matched against. That copy only touches the graph — the shared
        category/topic/etc. points in the entities collection still carry
        only the *other* record's connectorId/recordGroupId, and this record
        has no ``record``/``record_group`` point at all, since the dedup
        path skips ``index()``/``enrich()`` (and therefore
        ``_sync_record_name_entity``/``_sync_record_group_entity``) entirely.

        ``upsert_entities_batch`` merges membership rather than replacing it,
        so this appends this record's connectorId/recordGroupId to whatever
        is already stored.

        Takes the raw graph record document (not a parsed ``Record``/
        ``TransformContext``) since the dedup path never parses one.
        """
        if not self.entity_vector_store:
            return
        record_key = record_doc.get("_key") or record_doc.get("id")
        org_id = record_doc.get("orgId")
        if not record_key or not org_id:
            return
        connector_id = record_doc.get("connectorId")
        record_group_id = record_doc.get("recordGroupId")
        connector_ids = [connector_id] if connector_id else []
        record_group_ids = [record_group_id] if record_group_id else []

        try:
            from app.models.entities import EntityRecord, EntityType, EntityTypeCategory

            taxonomy_rows = await self.graph_provider.get_taxonomy_entities_for_record(
                record_key
            )
            valid_types = {e.value for e in EntityType}
            entities: list[EntityRecord] = []
            for row in taxonomy_rows:
                entity_id = row.get("entityId")
                entity_type = row.get("entityType")
                if not entity_id or entity_type not in valid_types:
                    continue
                entities.append(
                    EntityRecord(
                        entity_id=str(entity_id),
                        entity_type=EntityType(entity_type),
                        name=str(row.get("name") or entity_id),
                        org_id=org_id,
                        level=row.get("level"),
                        aliases=[str(a) for a in (row.get("aliases") or []) if a],
                        connector_ids=connector_ids,
                        record_group_ids=record_group_ids,
                        # Matches GraphDBTransformer.save_metadata_to_db —
                        # taxonomy entities are schema-free, not part of a
                        # fixed ontology.
                        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
                    )
                )

            record_name = record_doc.get("recordName")
            if record_name and record_name.strip():
                entities.append(
                    EntityRecord(
                        entity_id=str(record_key),
                        entity_type=EntityType.RECORD,
                        name=record_name,
                        org_id=org_id,
                        connector_ids=connector_ids,
                        record_group_ids=record_group_ids,
                        type_category=EntityTypeCategory.PREDEFINED,
                    )
                )

            if record_group_id:
                group_doc = await self.graph_provider.get_record_group_by_id(
                    record_group_id
                )
                group_name = (
                    (group_doc.get("groupName") or group_doc.get("name"))
                    if group_doc
                    else None
                )
                if group_name and group_name.strip():
                    entities.append(
                        EntityRecord(
                            entity_id=record_group_id,
                            entity_type=EntityType.RECORD_GROUP,
                            name=group_name,
                            org_id=org_id,
                            connector_ids=connector_ids,
                            record_group_ids=[record_group_id],
                            type_category=EntityTypeCategory.PREDEFINED,
                        )
                    )

            if entities:
                await self.entity_vector_store.upsert_entities_batch(entities)
        except Exception as exc:
            self.logger.warning(
                "Entity vector sync failed for deduplicated record %s (non-fatal): %s",
                record_key,
                exc,
            )

    # A record with a handful of images is cheaper to describe outright than
    # to fetch its previous version for; past this many, the fetch pays for
    # itself several times over.
    _INHERIT_DESCRIPTIONS_THRESHOLD = 5

    async def _describe_images(self, ctx: TransformContext) -> None:
        """Annotate image blocks with prose before the record is stored.

        Text is the only representation of an image that always reaches the
        model -- see `ImageDescriber`. Failure-isolated: an undescribed record
        still indexes.
        """
        record = ctx.record
        containers = record.block_containers
        if not containers or not containers.blocks:
            return
        image_count = sum(1 for b in containers.blocks if b.type == BlockType.IMAGE)
        if not image_count:
            return

        inherited: dict[str, str] = {}
        if image_count >= self._INHERIT_DESCRIPTIONS_THRESHOLD and record.virtual_record_id:
            try:
                previous = await self.blob_storage.get_record_from_storage(
                    virtual_record_id=record.virtual_record_id, org_id=record.org_id,
                )
                inherited = harvest_descriptions(previous)
            except Exception:
                # A missing or unreadable previous version just means paying
                # for the descriptions again, not failing the record.
                self.logger.debug(
                    "No previous version to inherit image descriptions from", exc_info=True,
                )

        await self.image_describer.annotate(containers, inherited=inherited)

    async def _update_indexing_status(self, ctx: TransformContext) -> None:
        """Mark indexingStatus=COMPLETED without touching extractionStatus."""
        record = ctx.record
        timestamp = get_epoch_timestamp_in_ms()
        await self.graph_provider.batch_upsert_nodes(
            [
                {
                    "id": record.id,
                    "virtualRecordId": record.virtual_record_id,
                    "indexingStatus": ProgressStatus.COMPLETED.value,
                    "processingStartedAt": None,
                    "lastIndexTimestamp": timestamp,
                    "isDirty": False,
                }
            ],
            CollectionNames.RECORDS.value,
        )
        self.logger.debug(
            "✅ indexingStatus=COMPLETED recorded for %s", record.id
        )
        # The record is only searchable now, so the accessible-record map that
        # gates search is stale. KB-only: a connector sync flips thousands of
        # records here in a burst and invalidates once, at sync completion.
        await notify_record_indexed(
            connector_name=record.connector_name,
            connector_id=record.connector_id,
            external_record_group_id=record.external_record_group_id,
            org_id=record.org_id,
        )

    # ------------------------------------------------------------------
    # Phase 2: ENRICH — graph DB taxonomy.  Can run later (deferred).
    # ------------------------------------------------------------------

    async def resolve_entities(self, ctx: TransformContext) -> None:
        """Canonicalise the record's extracted taxonomy names.

        Must run after classification and before the blob write and
        ``enrich()``, so every store sees the same canonical names. A no-op
        without a resolver or without semantic metadata; the resolver itself
        decides between off, shadow and apply.
        """
        if self.entity_resolver is None:
            return
        if getattr(ctx.record, "semantic_metadata", None) is None:
            return
        await self.entity_resolver.resolve(ctx)

    async def enrich(self, ctx: TransformContext) -> None:
        """Phase 2: write classification metadata to the graph database.

        Calls ``graphdb.apply()`` which already sets
        ``extractionStatus=COMPLETED`` once it finishes.  Callers should
        ensure ``ctx.record.semantic_metadata`` is populated before calling
        this method.

        After graph enrichment, the entities touched during this run are
        synced to the entity vector store (non-blocking: failures are logged
        but do not affect the record's extractionStatus).
        """
        touched_entities = await self.graphdb.apply(ctx)
        self.logger.debug(
            "✅ Graph enrichment completed for record %s", ctx.record.id
        )

        if self.entity_vector_store and touched_entities:
            try:
                await self.entity_vector_store.upsert_entities_batch(touched_entities)
            except Exception as exc:
                # Entity sync is best-effort; do not fail the record pipeline
                self.logger.warning(
                    "Entity vector sync failed for record %s (non-fatal): %s",
                    ctx.record.id,
                    exc,
                )

    async def _save_reconciliation_metadata(self, ctx: TransformContext) -> None:
        if ctx.reconciliation_context and ctx.reconciliation_context.new_metadata:
            record = ctx.record
            await self.blob_storage.save_reconciliation_metadata(
                record.org_id,
                record.id,
                record.virtual_record_id,
                ctx.reconciliation_context.new_metadata,
            )
