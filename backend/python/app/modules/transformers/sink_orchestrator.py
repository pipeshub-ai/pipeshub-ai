import logging
from typing import TYPE_CHECKING, Any, Optional

from app.config.constants.arangodb import (
    RECORD_TYPE_COLLECTION_MAPPING,
    CollectionNames,
    Connectors,
    ProgressStatus,
)
from app.exceptions.indexing_exceptions import IndexingError
from app.models.blocks import (
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    GroupSubType,
)
from app.models.entities import Record, RecordGroupType, TicketRecord
from app.models.record_metadata import RecordMetadata
from app.modules.artifacts.models import WrittenRevision
from app.modules.artifacts.store import ArtifactStoreError, NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter
from app.modules.record_metadata.builtin import ticket_metadata
from app.modules.transformers.blob_storage import (
    STORED_RECORD_FIELDS,
    STORED_REV,
    BlobStorage,
)
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.image_description import (
    ImageDescriber,
    harvest_descriptions,
)
from app.modules.transformers.transformer import (
    ENRICHMENT_FOLLOWS,
    IMAGE_DESCRIPTION_BUDGET_S,
    TransformContext,
    Transformer,
)
from app.modules.transformers.vectorstore import VectorStore
from app.services.cache.invalidation_hooks import notify_record_indexed
from app.services.featureflag.platform_settings import is_artifact_write_enabled
from app.services.graph_db.common.record_visibility import indexed_copy_fields
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.telemetry.modules.activity_metrics import record_service_activity
from app.telemetry.modules.record_storage_metrics import record_artifact_write
from app.utils.indexing_timing import stage
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.models.entities import EntityRecord, Record
    from app.modules.entity_resolution import EntityResolver
    from app.modules.named_entities.domain.models import NamedEntityExtraction


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
        self.config_service = config_service
        self.logger = logger
        # Runs before the blob write below, which is the only reason it lives
        # here: a description generated later would never reach the stored
        # record, and the stored record is what `fetch_record` serves.
        self.image_describer = ImageDescriber(logger, config_service)
        self.artifact_writer = ArtifactWriter(NodeArtifactStore(blob_storage, logger), logger)

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

    def _stored_block_containers(self, record: Record) -> BlocksContainer:
        """The blocks a stored copy of ``record`` keeps: SQL tables keep at most
        ``LIMIT_SQL_ROW_BLOCKS_TO`` rows."""
        containers = record.block_containers
        is_sql = any(
            bg.sub_type in (GroupSubType.SQL_TABLE, GroupSubType.SQL_VIEW)
            for bg in containers.block_groups
        ) if containers and containers.block_groups else False
        if is_sql and self.LIMIT_SQL_ROW_BLOCKS_TO is not None:
            return self._build_limited_sql_block_container(containers, self.LIMIT_SQL_ROW_BLOCKS_TO)
        return containers

    async def write_blob(self, ctx: TransformContext) -> None:
        """Write the record blob with the SQL row limit applied for the duration of the write.

        Every blob write must go through here; a direct ``blob_storage.apply`` (e.g. the
        rewrite after enrichment) would store every row the limit exists to drop.
        """
        record = ctx.record
        full_block_containers = record.block_containers
        record.block_containers = self._stored_block_containers(record)
        try:
            await self.blob_storage.apply(ctx)
        finally:
            record.block_containers = full_block_containers

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
        """``Transformer`` entry point: index the record. Classification is a pipeline stage."""
        await self.index(ctx)

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
        skip_blob = bool(ctx.settings.get("skip_blob"))
        skip_vector_store = bool(ctx.settings.get("skip_vector_store")) or bool(
            ctx.settings.get("sink_only")
        )

        record_id = record.id
        with stage("graph.read"):
            record_doc = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
        if record_doc is None:
            self.logger.error(f"❌ Record {record_id} not found in database")
            raise Exception(f"Record {record_id} not found in database")

        if not skip_blob:
            ctx.settings[STORED_REV] = record_doc.get("contentRev") or None
            with stage("images.describe"):
                await self._describe_images(ctx)
            with stage("blob.write"):
                await self.write_blob(ctx)

        written: WrittenRevision | None = None
        if not skip_blob and not skip_vector_store:
            with stage("artifacts.write"):
                written = await self._write_artifacts(ctx, record_doc)

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
            with stage("graph.status"):
                completed = await self._update_indexing_status(ctx)
            if completed and written is not None:
                await self._flip_revision_pointer(ctx.record, written)
            # await self.graphdb.apply(ctx)
            with stage("blob.metadata"):
                await self._save_reconciliation_metadata(ctx)
            with stage("entities.identity"):
                await self._sync_record_identity_entities(ctx)

    async def _sync_record_identity_entities(self, ctx: TransformContext) -> None:
        """Write the record's own entity points, its title and its record group
        (a Jira project, a Drive folder), in one batch.

        Runs after the record is searchable, for every connector, since
        ``record_group_id`` is generic. Membership is replaced, not merged:
        a record has exactly one connector and one group (a union kept a
        moved record matching its old group's users), and a group point lists
        itself so users with group-level access reach it. Best-effort: the
        record is already searchable through the records collection.
        """
        if not self.entity_vector_store:
            return
        from app.models.entities import EntityRecord

        record = ctx.record
        entities: list[EntityRecord] = []
        if isinstance(record.record_name, str) and record.record_name.strip():
            entities.append(EntityRecord.for_record(
                record.id, record.record_name, record.org_id, record.connector_id, record.record_group_id,
            ))
        if record.record_group_id:
            try:
                group_doc = await self.graph_provider.get_record_group_by_id(record.record_group_id)
            except Exception as exc:
                group_doc = None
                self.logger.warning(
                    "Record group lookup failed for record %s (non-fatal): %s", record.id, exc,
                )
            group_name = (group_doc or {}).get("groupName") or (group_doc or {}).get("name")
            if isinstance(group_name, str) and group_name.strip():
                entities.append(EntityRecord.for_record_group(
                    record.record_group_id, group_name, record.org_id, record.connector_id,
                ))
        if not entities:
            return
        try:
            outcome = await self.entity_vector_store.upsert_entities_batch(entities, merge_membership=False)
            if outcome.failed:
                self.logger.warning(
                    "Entity points for record %s not written (%d of %d)", record.id, outcome.failed, len(entities),
                )
        except Exception as exc:
            self.logger.warning("Record entity sync failed for record %s (non-fatal): %s", record.id, exc)

    async def sync_entities_for_duplicate(self, record_doc: dict) -> bool:
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
        ``_sync_record_identity_entities``) entirely.

        ``upsert_entities_batch`` merges membership rather than replacing it,
        so this appends this record's connectorId/recordGroupId to whatever
        is already stored.

        Only nodes of the record's org are projected with their aliases. A
        node without an org (legacy, or a global department) is projected
        with none, since its aliases may be any org's; another org's node is
        skipped.

        Takes the raw graph record document (not a parsed ``Record``/
        ``TransformContext``) since the dedup path never parses one.

        Returns whether every point was written; reconciliation keeps its
        pending flag otherwise. Nothing to do counts as success.
        """
        if not self.entity_vector_store:
            return True
        record_key = record_doc.get("_key") or record_doc.get("id")
        org_id = record_doc.get("orgId")
        if not record_key or not org_id:
            return True
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
                node_org = row.get("orgId")
                if node_org and node_org != org_id:
                    self.logger.warning(
                        "Duplicate record %s links to %s %s of another org; not projected",
                        record_key, entity_type, entity_id,
                    )
                    continue
                entities.append(
                    EntityRecord(
                        entity_id=str(entity_id),
                        entity_type=EntityType(entity_type),
                        name=str(row.get("name") or entity_id),
                        org_id=org_id,
                        level=row.get("level"),
                        aliases=(
                            [str(a) for a in (row.get("aliases") or []) if a] if node_org else []
                        ),
                        connector_ids=connector_ids,
                        record_group_ids=record_group_ids,
                        # Matches GraphDBTransformer.save_metadata_to_db —
                        # taxonomy entities are schema-free, not part of a
                        # fixed ontology.
                        type_category=EntityTypeCategory.GENERIC_SCHEMA_FREE,
                    )
                )

            from app.modules.named_entities.domain.kinds import spec_for

            for row in await self.graph_provider.get_named_entities_for_record(str(record_key)) or []:
                kind = row.get("kind")
                spec = spec_for(kind) if kind else None
                entity_id = row.get("_key") or row.get("id")
                if not entity_id or spec is None or not spec.embeddable or spec.is_pii:
                    continue
                if row.get("orgId") and row.get("orgId") != org_id:
                    continue
                entities.append(
                    EntityRecord(
                        entity_id=str(entity_id),
                        entity_type=EntityType.NAMED_ENTITY,
                        name=str(row.get("name") or entity_id),
                        org_id=org_id,
                        kind=str(kind),
                        connector_ids=connector_ids,
                        record_group_ids=record_group_ids,
                        type_category=EntityTypeCategory.PREDEFINED,
                    )
                )

            # A record and its group are written like on the index path:
            # their membership is replaced, never unioned.
            identities: list[EntityRecord] = []
            record_name = record_doc.get("recordName")
            if record_name and record_name.strip():
                identities.append(EntityRecord.for_record(
                    str(record_key), record_name, org_id, connector_id, record_group_id,
                ))

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
                    identities.append(EntityRecord.for_record_group(
                        record_group_id, group_name, org_id, connector_id,
                    ))

            failed = 0
            if entities:
                failed += (await self.entity_vector_store.upsert_entities_batch(entities)).failed
            if identities:
                failed += (await self.entity_vector_store.upsert_entities_batch(
                    identities, merge_membership=False,
                )).failed
            if failed:
                self.logger.warning(
                    "Entity vector sync for deduplicated record %s left %d entities unwritten",
                    record_key, failed,
                )
            return not failed
        except Exception as exc:
            self.logger.warning(
                "Entity vector sync failed for deduplicated record %s (non-fatal): %s",
                record_key,
                exc,
            )
            return False

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

        await self.image_describer.annotate(
            containers,
            inherited=inherited,
            budget_s=ctx.settings.get(IMAGE_DESCRIPTION_BUDGET_S),
        )

    async def _write_artifacts(
        self, ctx: TransformContext, record_doc: dict[str, Any]
    ) -> WrittenRevision | None:
        """Store this content revision's artifact set, when the flag is on.

        Never fails indexing: the stored record written just before is still
        what every reader reads. A run with no ``contentRev`` (a re-embed)
        writes nothing, since there is no revision to file it under.
        """
        record = ctx.record
        if not await is_artifact_write_enabled(self.config_service):
            return None
        rev = record_doc.get("contentRev")
        if not rev or not record.virtual_record_id:
            return None
        reconciliation = (
            ctx.reconciliation_context.new_metadata if ctx.reconciliation_context else None
        )
        properties = await self._provided_properties(record, record_doc)
        try:
            written = await self.artifact_writer.write_revision(
                record,
                record.org_id,
                record.virtual_record_id,
                str(rev),
                self._stored_block_containers(record),
                semantic_metadata=record.semantic_metadata,
                reconciliation_metadata=reconciliation or None,
                stored_record_fields=ctx.settings.get(STORED_RECORD_FIELDS),
                properties=properties,
            )
        except Exception as exc:
            # Only the type and our own messages: an aiohttp error can carry a signed URL.
            reason = str(exc) if isinstance(exc, (ArtifactStoreError, ValueError)) else ""
            self.logger.warning(
                "Artifact set for record %s (rev %s) not written; indexing goes on: %s %s",
                record.id, rev, type(exc).__name__, reason,
            )
            record_artifact_write("failed")
            return None
        record_artifact_write("written" if written is not None else "unavailable")
        return written

    async def _provided_properties(
        self, record: Record, record_doc: dict[str, Any]
    ) -> RecordMetadata | None:
        """The values a ticket's source provided, for ``properties.json``.

        Indexing builds a plain ``Record`` from the record document; a ticket's
        fields are in its tickets document, read here only for a record whose
        type is stored there. Never fails the write: without them the set is
        stored without ``properties.json``.
        """
        if isinstance(record, TicketRecord):
            return ticket_metadata(record)
        tickets = CollectionNames.TICKETS.value
        if RECORD_TYPE_COLLECTION_MAPPING.get(record_doc.get("recordType")) != tickets:
            return None
        try:
            ticket_doc = await self.graph_provider.get_document(record.id, tickets)
            if ticket_doc is None:
                return None
            return ticket_metadata(TicketRecord.from_arango_record(ticket_doc, record_doc))
        except Exception as exc:
            self.logger.warning(
                "Properties of ticket %s not read; its artifact set goes without them: %s",
                record.id, type(exc).__name__,
            )
            return None

    async def _flip_revision_pointer(self, record: Record, written: WrittenRevision) -> None:
        """Point the virtual record's mapping at the revision just written.

        A compare-and-set on ``currentRev``: a writer that read an older value
        than the one stored now lost a race, and the pointer is left to the
        winner. Best-effort, like the artifact write itself.
        """
        vrid = record.virtual_record_id
        rev = written.manifest.rev
        collection = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
        try:
            # The CAS only orders writers that read the same pointer. A run the record
            # has since left (re-indexed to a newer revision without a record lease)
            # must not point search back at its older revision.
            current_doc = await self.graph_provider.get_document(
                record.id, CollectionNames.RECORDS.value
            )
            if current_doc is None or current_doc.get("contentRev") != rev:
                self.logger.info(
                    "Record %s moved off revision %s; revision pointer of %s left as it is",
                    record.id, rev, vrid,
                )
                return
            mapping = await self.graph_provider.get_document(vrid, collection)
            if mapping is None:
                return
            current = mapping.get("currentRev")
            if current == rev and mapping.get("manifestDocId") == written.manifest_ref.document_id:
                return
            flipped = await self.graph_provider.update_node_fields_if_match(
                vrid,
                collection,
                {
                    "currentRev": rev,
                    "previousRev": current if current != rev else mapping.get("previousRev"),
                    "manifestDocId": written.manifest_ref.document_id,
                },
                {"currentRev": current},
            )
            if not flipped:
                self.logger.info(
                    "Revision pointer of %s moved while %s was written; left as it is", vrid, rev,
                )
        except Exception as exc:
            self.logger.warning(
                "Revision pointer of %s not moved to %s: %s", vrid, rev, type(exc).__name__,
            )

    async def _update_indexing_status(self, ctx: TransformContext) -> bool:
        """Mark indexingStatus=COMPLETED. False when the record no longer exists.

        With ``ENRICHMENT_FOLLOWS`` set, the same write marks extractionStatus
        QUEUED: no reader may see the record indexed with an extractionStatus
        that looks final, which a duplicate would copy as its own. The classify
        stage owns the status from here, and nothing on the record path is
        still running, so ``processingStartedAt`` is cleared.

        ``isVLMOcrProcessed`` is written here because only the record path
        knows how the record was parsed; the stage that enriches it later
        reads the stored record, not the parse.
        """
        record = ctx.record
        timestamp = get_epoch_timestamp_in_ms()
        status: dict[str, Any] = {
            "id": record.id,
            "virtualRecordId": record.virtual_record_id,
            "indexingStatus": ProgressStatus.COMPLETED.value,
            **indexed_copy_fields(ProgressStatus.COMPLETED),
            # An indexed record was parsed. The parsing service already wrote
            # this; the in-process pipeline has no other place that does.
            "parsingStatus": ProgressStatus.COMPLETED.value,
            "lastIndexTimestamp": timestamp,
            "processingStartedAt": None,
            "isDirty": False,
        }
        if ctx.settings.get(ENRICHMENT_FOLLOWS):
            status["extractionStatus"] = ProgressStatus.QUEUED.value
        if getattr(record, "is_vlm_ocr_processed", False) is True:
            status["isVLMOcrProcessed"] = True
        # Update, never upsert: a record deleted mid-indexing would come back as
        # a bare node that keeps its vectors from the orphan sweep.
        updated = await self.graph_provider.batch_update_nodes(
            [status],
            CollectionNames.RECORDS.value,
        )
        if not updated:
            self.logger.warning(
                "⚠️ Record %s no longer exists; indexingStatus=COMPLETED not recorded",
                record.id,
            )
            return False
        record.record_status = ProgressStatus.COMPLETED
        record.indexing_status = ProgressStatus.COMPLETED.value
        record.parsing_status = ProgressStatus.COMPLETED.value
        self.logger.debug(
            "✅ indexingStatus=COMPLETED recorded for %s", record.id
        )
        # The record is only searchable now, so the accessible-record map that
        # gates search is stale. Connector records arrive in bursts, so their
        # deletes are coalesced (see `on_record_indexed`).
        await notify_record_indexed(
            connector_name=record.connector_name,
            connector_id=record.connector_id,
            external_record_group_id=record.external_record_group_id,
            org_id=record.org_id,
        )
        return True

    # ------------------------------------------------------------------
    # Phase 2: ENRICH — run by the classify pipeline stage once the record is
    # searchable (``app/modules/pipeline/stages/classify.py``).
    # ------------------------------------------------------------------

    async def resolve_entities(self, ctx: TransformContext) -> None:
        """Resolve the record's extracted taxonomy names to canonical nodes.

        Must run after classification and before the blob write and
        ``enrich()``, so every store sees the same cleaned names. A no-op
        without a resolver or without semantic metadata; the resolver itself
        decides between off, shadow and apply.
        """
        if self.entity_resolver is None:
            return
        if getattr(ctx.record, "semantic_metadata", None) is None:
            return
        await self.entity_resolver.resolve(ctx)

    def entity_resolution_mode(self) -> str:
        """The resolver's mode, ``off`` without one. Part of the classify stage's fingerprint."""
        if self.entity_resolver is None:
            return "off"
        mode = getattr(self.entity_resolver, "mode", None)
        return str(getattr(mode, "value", mode) or "off")

    async def prepare_enrichment(self, ctx: TransformContext) -> None:
        """Resolve the classification's taxonomy names to canonical nodes, in place.

        Taxonomy names must be resolved before the stored record, summary and
        graph writes all consume them. Writes nothing.
        """
        metadata = ctx.record.semantic_metadata
        if metadata is None:
            return
        # A None list is unknown (a fallback summary) and must keep the
        # record's edges; the resolver hands every list back as one.
        unknown = [name for name in ("topics", "languages") if getattr(metadata, name) is None]
        with stage("entities.resolve"):
            await self.resolve_entities(ctx)
        for name in unknown:
            setattr(metadata, name, None)

    async def write_summary_point(self, ctx: TransformContext) -> None:
        """The summary point of ``ctx.record``'s classification; nothing when it has no summary."""
        record = ctx.record
        metadata = record.semantic_metadata
        if metadata is None or not (metadata.summary or "").strip():
            return
        # Not wrapped in a stage: it times its own steps.
        await self.vector_store.index_record_summary(
            record.id,
            record.virtual_record_id,
            record.org_id,
            metadata,
            record,
        )

    async def write_enrichment(self, ctx: TransformContext, *, index_summary: bool = True) -> None:
        """The summary point, graph taxonomy and the entity points it touches, of a
        prepared classification (``prepare_enrichment``), for ``ctx.record``.

        ``extractionStatus`` is not written: the classify stage's headline owns it,
        and writes it only while the record is still on the revision classified.
        ``index_summary`` is False for the other records of the same content, which
        share the summary point. Named entities are the entities stage's.
        """
        record = ctx.record
        metadata = record.semantic_metadata
        if metadata is None:
            return
        if index_summary:
            await self.write_summary_point(ctx)
        with stage("graph.enrich"):
            touched_entities = await self.graphdb.write_taxonomy(
                record.id, metadata, resolution=self.graphdb.resolution_of(ctx)
            )
        await self._upsert_touched_entities(ctx, touched_entities)

    async def enrich(self, ctx: TransformContext) -> None:
        """Write classification metadata to the graph database, with extractionStatus.

        Calls ``graphdb.apply()`` which already sets
        ``extractionStatus=COMPLETED`` once it finishes.  Callers should
        ensure ``ctx.record.semantic_metadata`` is populated before calling
        this method. Indexing enriches through the classify stage
        (``prepare_enrichment`` / ``write_enrichment``), which leaves the status
        to the stage.

        After graph enrichment, the entities touched during this run are
        synced to the entity vector store (non-blocking: failures are logged
        but do not affect the record's extractionStatus).
        """
        with stage("graph.enrich"):
            touched_entities = await self.graphdb.apply(ctx)
        self.logger.debug(
            "✅ Graph enrichment completed for record %s", ctx.record.id
        )
        await self._upsert_touched_entities(ctx, touched_entities)

    async def _upsert_touched_entities(
        self, ctx: TransformContext, touched_entities: "list[EntityRecord] | None"
    ) -> None:
        if self.entity_vector_store and touched_entities:
            try:
                with stage("entities.upsert", detail=f"{len(touched_entities)} entities"):
                    await self.entity_vector_store.upsert_entities_batch(touched_entities)
            except Exception as exc:
                # Entity sync is best-effort; do not fail the record pipeline
                self.logger.warning(
                    "Entity vector sync failed for record %s (non-fatal): %s",
                    ctx.record.id,
                    exc,
                )

    async def extract_named_entities(self, ctx: TransformContext, *, client=None, reextract: bool = False):
        """Bounded extraction. A failure returns FAILED and does not raise.
        ``reextract``: completing an earlier extraction that left text unread."""
        import time

        from app.modules.named_entities.domain.models import NamedEntityExtraction
        from app.modules.named_entities.stage import extract_named_entities
        from app.services.featureflag.platform_settings import (
            is_named_entity_extraction_enabled,
        )
        from app.telemetry.modules.named_entity_metrics import observe_extract

        started = time.perf_counter()
        strategy = "none"
        try:
            enabled = await is_named_entity_extraction_enabled(self.config_service)
            extraction = await extract_named_entities(
                ctx, config_service=self.config_service, client=client, enabled=enabled, reextract=reextract,
            )
            strategy = extraction.strategy
            return extraction
        except Exception as exc:
            # Tracebacks from the model or extraction client can echo document text.
            self.logger.warning(
                "Named-entity extraction failed for record %s: %s", ctx.record.id, type(exc).__name__
            )
            return NamedEntityExtraction(status="FAILED", termination_reason="llm_error", strategy="deterministic")
        finally:
            observe_extract(strategy, time.perf_counter() - started)

    async def reproject_named_entities(
        self,
        ctx: TransformContext,
        *,
        content_rev: str | None,
        retry_due: int | None = None,
        retry_attempts: int = 0,
    ) -> str | None:
        """Write the record's named entities again from the extraction kept for its revision:
        no model call. Returns the persist outcome, or None when none is kept. ``retry_due``
        makes it a persist retry's write."""
        from app.modules.named_entities.kept import read_kept_extraction

        metadata = ctx.record.semantic_metadata
        extraction = await read_kept_extraction(
            self.graph_provider,
            ctx.record.virtual_record_id,
            content_rev,
            getattr(metadata, "named_entities", None) if metadata else None,
        )
        if extraction is None:
            return None
        return await self.persist_named_entities(
            ctx, extraction, retry_due=retry_due, retry_attempts=retry_attempts,
        )

    async def persist_named_entities(
        self, ctx: TransformContext, extraction, *, retry_due: int | None = None, retry_attempts: int = 0,
    ) -> str:
        """Never raises, and never fails the document's own indexing. A failed
        write is recorded as a durable retry: the recovery pass writes the record's
        stored extraction again when it is due (see named_entity_persist_retry)."""
        from app.modules.named_entities.stage import persist_named_entities
        from app.services.featureflag.platform_settings import (
            is_named_entity_extraction_enabled,
        )

        error = "persist_failed"
        try:
            enabled = await is_named_entity_extraction_enabled(self.config_service)
            outcome = await persist_named_entities(
                ctx,
                extraction,
                graph_provider=self.graph_provider,
                graph_data_store=self.graphdb.graph_data_store,
                entity_vector_store=self.entity_vector_store,
                enabled=enabled,
                retry_due=retry_due,
            )
        except Exception as exc:
            self.logger.warning(
                "Named-entity persist failed for record %s: %s", ctx.record.id, type(exc).__name__
            )
            outcome, error = "failed", type(exc).__name__
        if outcome == "failed":
            await self._schedule_named_entity_retry(ctx.record, error, retry_attempts)
        elif outcome == "written":
            await self._schedule_named_entity_reextract(ctx.record, extraction)
        # A newer write replaced this one, and dates the record from its own content.
        if outcome != "superseded":
            await self._apply_content_dates(ctx, extraction)
        return outcome

    async def _apply_content_dates(self, ctx: TransformContext, extraction: "NamedEntityExtraction") -> None:
        """A knowledge-base upload takes the dates its content states. The record
        keeps the dates it has when this fails; never raises."""
        from app.modules.indexing.content_dates import apply_content_dates

        try:
            outcome = await apply_content_dates(self.graph_provider, ctx.record, extraction)
        except Exception as exc:
            self.logger.warning(
                "Content dates for record %s not applied: %s", ctx.record.id, type(exc).__name__
            )
            return
        if outcome in ("date", "redate", "revert"):
            self.logger.info("Content dates for record %s: %s", ctx.record.id, outcome)

    async def _schedule_named_entity_reextract(self, record: "Record", extraction: "NamedEntityExtraction") -> None:
        """After the write: the write itself drops any pending job for the record."""
        from app.modules.named_entities.stage import needs_reextract

        if not needs_reextract(extraction):
            return
        try:
            await self.graph_provider.schedule_named_entity_reextract(record.org_id, record.id)
            self.logger.info("Named entities of record %s left text unread; queued to extract again", record.id)
        except Exception as exc:
            # The record keeps the entities just written until it is next indexed.
            self.logger.warning(
                "Named-entity re-extraction for record %s could not be queued: %s", record.id, type(exc).__name__
            )

    async def _schedule_named_entity_retry(self, record, error: str, attempts_so_far: int = 0) -> None:
        try:
            attempts = await self.graph_provider.schedule_named_entity_persist_retry(
                record.org_id, record.id, error, attempts_so_far,
            )
            self.logger.info("Named-entity persist for record %s queued for retry (failure %d)", record.id, attempts)
        except Exception as exc:
            # The record keeps its last good entities until it is next indexed.
            self.logger.warning(
                "Named-entity persist retry for record %s could not be recorded: %s", record.id, type(exc).__name__
            )

    async def _save_reconciliation_metadata(self, ctx: TransformContext) -> None:
        if ctx.reconciliation_context and ctx.reconciliation_context.new_metadata:
            record = ctx.record
            await self.blob_storage.save_reconciliation_metadata(
                record.org_id,
                record.id,
                record.virtual_record_id,
                ctx.reconciliation_context.new_metadata,
                document_path=ctx.settings.get("storage_path"),
                connector_id=getattr(record, "connector_id", None),
                record_group_id=getattr(record, "record_group_id", None),
            )
