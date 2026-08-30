import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from logging import Logger

import aiohttp  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    SUPPORTED_CODE_FILE_EXTENSIONS,
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.events.events import EventProcessor
from app.events.processor import convert_record_dict_to_record
from app.exceptions.indexing_exceptions import IndexingError, ProcessingError
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.modules.transformers.transformer import TransformContext
from app.services.cache.invalidation_hooks import notify_record_indexed
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    Topic,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.handlers.entity import BaseEventService
from app.services.vector_db.rebuild_state import (
    PHASE_FAILED,
    PHASE_READY,
    mark_cleanup_phase,
)
from app.services.vector_db.strategy import DeleteContext, RecordContext
from app.services.vector_db.strategy_resolver import reset_strategy_cache
from app.utils.api_call import make_api_call
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.jwt import generate_jwt


class RecordEventHandler(BaseEventService):
    def __init__(self, logger: Logger,
                config_service: ConfigurationService,
                event_processor: EventProcessor,
                producer: IMessagingProducer | None = None,
                ) -> None:

        self.logger = logger
        self.config_service = config_service

        self.event_processor : EventProcessor = event_processor
        self.producer = producer

    async def _propagate_primary_failure_to_queued_duplicates(
        self,
        record_id: str,
        virtual_record_id: str | None,
        reason: str | None,
    ) -> None:
        """Mark same-MD5 QUEUED copies failed when the primary copy fails.

        Does not re-run indexing for queued copies. Re-OCR on identical content
        would usually repeat the same failure (e.g. rate limits) and waste resources.
        """
        try:
            propagated_reason = (
                f"Primary duplicate indexing failed: {reason}"
                if reason
                else "Primary duplicate indexing failed"
            )
            updated = await self.event_processor.graph_provider.update_queued_duplicates_status(
                record_id,
                ProgressStatus.FAILED.value,
                virtual_record_id,
                reason=propagated_reason,
            )
            if updated > 0:
                self.logger.info(
                    "Propagated primary failure to %d queued duplicate(s) for record %s",
                    updated,
                    record_id,
                )
            else:
                self.logger.info(
                    "No queued duplicates to update after primary failure for record %s",
                    record_id,
                )
        except Exception as e:
            self.logger.warning(
                "Failed to propagate primary failure to queued duplicates for %s: %s",
                record_id,
                e,
            )

    async def _publish_reindex_event(self, record_id: str, payload: dict) -> None:
        if not self.producer:
            raise IndexingError("No messaging producer configured; cannot publish newRecord event")
        await self.producer.send_event(
            topic=Topic.RECORD_EVENTS.value,
            event_type="newRecord",
            payload=payload,
            key=str(record_id),
        )

    async def _trigger_next_queued_duplicate(self, record_id: str, virtual_record_id) -> None:
        try:
            self.logger.info(f"🔍 Looking for next queued duplicate for record {record_id}")

            next_queued_record = await self.event_processor.graph_provider.find_next_queued_duplicate(record_id)

            if not next_queued_record:
                self.logger.info(f"✅ No queued duplicates found for record {record_id}")
                return

            next_record_id = next_queued_record.get("_key")
            self.logger.info(f"🚀 Found queued duplicate: {next_record_id}, triggering indexing")

            file_record = None
            if next_queued_record.get("recordType") == RecordTypes.FILE.value:
                file_record = await self.event_processor.graph_provider.get_document(
                    next_record_id, CollectionNames.FILES.value
                )

            payload = await self.event_processor.graph_provider._create_reindex_event_payload(
                next_queued_record,
                file_record,
            )

            await self._publish_reindex_event(str(next_record_id), payload)

            self.logger.info(f"✅ Successfully triggered indexing for queued duplicate: {next_record_id}")

        except Exception as e:
            self.logger.warning(f"Failed to trigger next queued duplicate: {str(e)}")
            try:
                await self.event_processor.graph_provider.update_queued_duplicates_status(record_id, ProgressStatus.FAILED.value, virtual_record_id)
            except Exception as e:
                self.logger.warning(f"Failed to update queued duplicates status: {str(e)}")

    @staticmethod
    def _blob_has_blocks(blob: dict) -> bool:
        containers = blob.get("block_containers") if isinstance(blob, dict) else None
        if not isinstance(containers, dict):
            return False
        return bool(containers.get("blocks") or containers.get("block_groups"))

    async def _delete_vector_collection(self, payload: dict | None = None) -> AsyncGenerator[PipelineEvent, None]:
        # The cleanup job polls for a phase and otherwise waits out its whole
        # deadline, so every exit from here must publish one — a failure
        # included — rather than let the job time out with no explanation.
        try:
            await self._recreate_managed_collections()
        except Exception:
            await mark_cleanup_phase(
                self.config_service, PHASE_FAILED, logger=self.logger
            )
            raise
        await mark_cleanup_phase(self.config_service, PHASE_READY, logger=self.logger)
        yield PipelineEvent(
            event=IndexingEvent.PARSING_COMPLETE,
            data=PipelineEventData(record_id="delete_vector_collection"),
        )
        yield PipelineEvent(
            event=IndexingEvent.INDEXING_COMPLETE,
            data=PipelineEventData(record_id="delete_vector_collection"),
        )

    async def _recreate_managed_collections(self) -> list[str]:
        """Drop and rebuild every collection the registry manages.

        The embedding dimension is re-derived from the *live* model rather
        than the manifest, because this event fires precisely when the model
        has changed — the manifest still records the outgoing model's width.
        """
        sink = getattr(self.event_processor, "sink_orchestrator", None)
        vector_store = getattr(sink, "vector_store", None) if sink is not None else None
        if vector_store is None:
            # Nothing here recovers on redelivery — an unconfigured vector
            # store is the same on the next attempt.
            raise IndexingError("Vector store is not configured; cannot drop the records collection")

        await vector_store.get_embedding_model_instance()
        embedding_size = vector_store.embedding_size
        if not embedding_size:
            raise IndexingError(
                "Could not resolve the embedding dimension; refusing to recreate "
                "collections without knowing their vector width"
            )

        registry = self.event_processor.processor.indexing_pipeline.collection_registry
        recreated = await registry.recreate_all_collections(embedding_size)
        # A rebuild is also the supported way to change the strategy, and the
        # resolved one is memoised per process. Without this the collections
        # are rebuilt but every later resolution still uses the outgoing
        # strategy's names until the service restarts.
        reset_strategy_cache()
        self.logger.info(
            "♻️ Recreated %d collection(s) at dimension %s: %s",
            len(recreated),
            embedding_size,
            recreated,
        )
        return recreated

    async def _index_from_blob(
        self,
        record_id: str,
        record: dict,
        payload: dict,
        virtual_record_id: str | None,
        event_type: str,
    ) -> AsyncGenerator[PipelineEvent, None]:
        org_id = payload.get("orgId") or record.get("orgId") or ""
        extraction_status = record.get("extractionStatus", ProgressStatus.NOT_STARTED.value)

        async def _fail(reason: str) -> AsyncGenerator[PipelineEvent, None]:
            await self.__update_document_status(
                record_id=record_id,
                indexing_status=ProgressStatus.FAILED.value,
                extraction_status=extraction_status,
                reason=reason,
            )
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )

        if not virtual_record_id:
            # Not a failure: no virtualRecordId means the record was never
            # indexed, so there is nothing in blob to re-embed. A vector-only
            # reindex re-embeds what is already indexed — it deliberately does
            # not download or parse sources — so such a record is simply out of
            # scope. Marking it FAILED would misreport a healthy record, never
            # succeed on retry, and overwrite whatever status it actually had.
            self.logger.info(
                "Skipping vector-only reindex for record %s: no virtualRecordId, "
                "so it has never been indexed and has nothing to rebuild from",
                record_id,
            )
            yield PipelineEvent(
                event=IndexingEvent.PARSING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )
            return

        sink = getattr(self.event_processor, "sink_orchestrator", None)
        if sink is None:
            # A service-level misconfiguration, not a bad record. _fail would
            # brand this record FAILED and acknowledge the message, so a wiring
            # problem would silently burn every record it touched.
            raise IndexingError(
                "Sink orchestrator is not configured; cannot reindex from blob"
            )

        try:
            blob = await sink.blob_storage.get_record_from_storage(
                virtual_record_id, org_id
            )
        except Exception as exc:
            self.logger.exception(
                "Blob fetch failed for vector-only reindex of record %s", record_id
            )
            # _fail writes FAILED and yields both completion events, so the
            # broker counts the message as handled and nothing retries it. A
            # storage timeout or 5xx would therefore burn every record it
            # touched for the whole rebuild. Re-raise instead and let the
            # consumer redeliver under its capped-attempt policy, which
            # dead-letters only if the outage outlasts the retries.
            if (
                MessageErrorClassifier.classify_by_exception(exc)
                == MessageErrorType.TRANSIENT
            ):
                raise
            async for event in _fail("Failed to retrieve record from blob storage"):
                yield event
            return

        if not blob or not self._blob_has_blocks(blob):
            async for event in _fail("Blob has no parsed blocks"):
                yield event
            return

        # Everything that can fail deterministically happens before the delete.
        # _blob_has_blocks only checks the container is non-empty, so a malformed
        # block still raises here — and a validation error classifies TERMINAL,
        # which after the delete would acknowledge a record whose vectors are
        # already gone and never revisit it.
        try:
            record_obj = convert_record_dict_to_record(record)
            record_obj.virtual_record_id = virtual_record_id
            record_obj.block_containers = BlocksContainer.model_validate(
                blob["block_containers"]
            )
            raw_semantic = blob.get("semantic_metadata")
            if raw_semantic:
                record_obj.semantic_metadata = SemanticMetadata.model_validate(
                    raw_semantic
                )
            ctx = TransformContext(
                record=record_obj,
                settings={"skip_blob": True},
                event_type=event_type,
            )
        except Exception:
            # Terminal by nature — the same blob parses the same way next time —
            # and safe to mark FAILED because the old vectors are still intact.
            self.logger.exception(
                "Vector-only reindex could not build blocks for record %s", record_id
            )
            async for event in _fail("Blob blocks are malformed"):
                yield event
            return

        # Unconditional: bulk_delete_embeddings only deletes when the VRID has no
        # remaining graph record, which is never true on a re-embed, so it would
        # leave the old points in place and the upsert below would duplicate them.
        # Scoped to this record's own collection — the same VRID can be indexed
        # from another connector, and re-embedding one must not wipe the other.
        await self.event_processor.processor.indexing_pipeline.delete_points_for_virtual_record(
            virtual_record_id,
            RecordContext.from_record(record_obj, record_obj.org_id),
        )

        try:
            await sink.index(ctx)
        except Exception:
            self.logger.exception(
                "Vector-only reindex failed for record %s", record_id
            )
            # Past the point of no return: the old points are gone, so the only
            # way back to a searchable record is a successful re-run. _fail would
            # acknowledge the message and end that possibility, so every failure
            # here is re-raised regardless of classification — the content was
            # already validated above, which leaves infrastructure as the likely
            # cause and that is worth retrying. A genuinely terminal error just
            # exhausts its attempts and lands on the same FAILED status.
            raise

        yield PipelineEvent(
            event=IndexingEvent.PARSING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )
        yield PipelineEvent(
            event=IndexingEvent.INDEXING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

    async def process_event(self, event_type: str, payload: dict) -> AsyncGenerator[PipelineEvent, None]:
        """Process record events, yielding phase completion events.

        Yields:
            Dict with 'event' key:
            - {'event': 'parsing_complete', 'data': {...}}
            - {'event': 'indexing_complete', 'data': {...}}
        """
        start_time = datetime.now()
        record_id = None
        message_id = f"{event_type}-unknown"
        error_occurred = False
        error_msg = None
        last_exception: Exception | None = None
        record = None
        try:
            if not event_type:
                # A message with no event type is a producer bug: acking it
                # silently would hide that, so it still dead-letters. Raise a
                # TERMINAL-classified error rather than returning bare, which the
                # consumer reports as "Handler ended without INDEXING_COMPLETE"
                # and classifies as transient — three deliveries of something no
                # retry can fix.
                self.logger.error(f"Missing event_type in message {payload}")
                raise ProcessingError(
                    "Message has no eventType; cannot be routed",
                    details={"payload_keys": sorted(payload.keys())},
                )

            # Handle bulk delete event FIRST - for connector instance deletion (doesn't have record_id)
            if event_type == EventTypes.BULK_DELETE_RECORDS.value:
                virtual_record_ids = payload.get("virtualRecordIds", [])
                connector_id = payload.get("connectorId")
                self.logger.info(f"🗑️ Bulk deleting embeddings for {len(virtual_record_ids)} records")

                indexing_pipeline = self.event_processor.processor.indexing_pipeline
                if connector_id:
                    # Routes through the active collection strategy: a
                    # dedicated-collection strategy that confirms no other
                    # connector writes here can drop the collection outright;
                    # `single` (and any collection still shared with a live
                    # connector) falls through to the membership-aware VRID
                    # delete below, unchanged from today's behavior.
                    delete_ctx = DeleteContext(
                        org_id=payload.get("orgId", ""),
                        connector_id=connector_id,
                        connector_name=payload.get("connectorName"),
                    )
                    result = await indexing_pipeline.purge_connector(
                        delete_ctx, virtual_record_ids
                    )
                else:
                    result = await indexing_pipeline.bulk_delete_embeddings(virtual_record_ids)

                self.logger.info(
                    f"✅ Bulk deletion complete: {result}"
                )
                # `bulk_delete_embeddings` reports success=False when it refused
                # to proceed — no managed collection resolved, so nothing was
                # deleted and the mapping rows were deliberately kept. Yielding
                # the completion events below would ack that as done and strip
                # the only handle a later run has on those points. Raise so the
                # consumer redelivers; IndexingError classifies as transient,
                # and the refusal leaves nothing half-applied to retry over.
                # `is False` deliberately: purge_connector's drop and noop
                # results carry no success key at all.
                if result.get("success") is False:
                    raise IndexingError(
                        "Bulk deletion did not complete; no managed collection "
                        "resolved, so nothing was purged",
                        details={"result": result},
                    )
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="bulk_delete", count=len(virtual_record_ids)))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="bulk_delete", count=len(virtual_record_ids)))
                return

            if event_type == EventTypes.SYNC_VECTOR_MEMBERSHIP.value:
                virtual_record_id = payload.get("virtualRecordId")
                if virtual_record_id:
                    await self.event_processor.sync_vector_membership(virtual_record_id)
                yield PipelineEvent(
                    event=IndexingEvent.PARSING_COMPLETE,
                    data=PipelineEventData(record_id="sync_vector_membership"),
                )
                yield PipelineEvent(
                    event=IndexingEvent.INDEXING_COMPLETE,
                    data=PipelineEventData(record_id="sync_vector_membership"),
                )
                return

            if event_type == EventTypes.DELETE_VECTOR_COLLECTION.value:
                async for event in self._delete_vector_collection(payload):
                    yield event
                return

            # For all other event types, require record_id
            record_id = payload.get("recordId")
            extension = payload.get("extension", "unknown")
            mime_type = payload.get("mimeType", "unknown")
            virtual_record_id = payload.get("virtualRecordId")
            message_id = f"{event_type}-{record_id}"

            if not record_id:
                # As above: malformed payload, surfaced via the dead-letter queue
                # in one attempt rather than three.
                self.logger.error(f"Missing record_id in message {payload}")
                raise ProcessingError(
                    f"Message of type {event_type} has no recordId",
                    details={"event_type": event_type},
                )

        

            record = await self.event_processor.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )

            self.logger.debug(
                f"Processing record {record_id} with event type: {event_type}. "
                f"Virtual Record ID: {virtual_record_id} "
                f"Extension: {extension}, Mime Type: {mime_type}"
            )

            # Handle delete event - no parsing/indexing phases
            if event_type == EventTypes.DELETE_RECORD.value:
                await self.event_processor.processor.indexing_pipeline.bulk_delete_embeddings([ virtual_record_id])
                # Yield both events since delete is complete
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            if record is None:
                # Legitimately reachable: the record can be deleted between the
                # event being published and consumed. There is nothing to index
                # and nothing to fail, so drain the message like the delete path
                # does instead of retrying it three times.
                self.logger.error(f"❌ Record {record_id} not found in database")
                yield PipelineEvent(
                    event=IndexingEvent.PARSING_COMPLETE,
                    data=PipelineEventData(record_id=record_id),
                )
                yield PipelineEvent(
                    event=IndexingEvent.INDEXING_COMPLETE,
                    data=PipelineEventData(record_id=record_id),
                )
                return

            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")

            #Reconciliation
            vector_db_only = bool(payload.get("vectorDbOnly"))
            if (
                not vector_db_only
                and (
                    event_type == EventTypes.UPDATE_RECORD.value
                    or event_type == EventTypes.REINDEX_RECORD.value
                )
            ):
                from app.config.constants.arangodb import (
                    RECONCILIATION_ENABLED_EXTENSIONS,
                    RECONCILIATION_ENABLED_MIME_TYPES,
                )
                is_reconciliation_type = (
                    mime_type in RECONCILIATION_ENABLED_MIME_TYPES
                    or extension in RECONCILIATION_ENABLED_EXTENSIONS
                )
                if is_reconciliation_type:
                    self.logger.info(
                        f"📊 Reconciliation-enabled type detected for record {record_id}, "
                        f"skipping full embedding deletion"
                    )
                else:
                    await self.event_processor.processor.indexing_pipeline.bulk_delete_embeddings([virtual_record_id])

            doc = dict(record)

            # The guard stops a replayed newRecord from re-running the pipeline over
            # an indexed corpus. An explicit reindex is the one case that must run
            # anyway, so it opts out rather than the guard being relaxed for
            # everyone: without this, reindex reports success while doing nothing.
            force_reindex = bool(payload.get("forceReindex"))
            if (not force_reindex) and (event_type == EventTypes.NEW_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value) and doc.get("indexingStatus") == ProgressStatus.COMPLETED.value:
                self.logger.info(f"🔍 Indexing already done for record {record_id} with virtual_record_id {virtual_record_id}")
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            # Check if record is from a connector and if the connector is active.
            # UPDATE_RECORD is included: without it an update for a disabled
            # connector runs the full pipeline, fails once the connector has been
            # removed from connectors_map, and burns every delivery attempt (each
            # holding a Pool.INDEX slot) before landing on FAILED instead of
            # AUTO_INDEX_OFF. vectorDbOnly still opts out — the vector-store
            # rebuild deliberately re-embeds disabled connectors from blob.
            if (
                not vector_db_only
                and (
                    event_type == EventTypes.NEW_RECORD.value
                    or event_type == EventTypes.REINDEX_RECORD.value
                    or event_type == EventTypes.UPDATE_RECORD.value
                )
            ):
                connector_id = record.get("connectorId")
                origin = record.get("origin")
                if connector_id and origin == OriginTypes.CONNECTOR.value:
                    connector_instance = await self.event_processor.graph_provider.get_document(
                        connector_id, CollectionNames.APPS.value
                    )
                    if not connector_instance:
                        self.logger.info(
                            f"⏭️ Skipping indexing for record {record_id}: "
                            f"connector instance {connector_id} not found (possibly deleted)."
                        )
                        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                        return
                    if not connector_instance.get("isActive", False):
                        self.logger.info(
                            f"⏭️ Skipping indexing for record {record_id}: "
                            f"connector instance {connector_id} is inactive."
                        )
                        # Update status to MANUAL_INDEXING and reson to connector is inactive
                        await self.__update_document_status(
                            record_id=record_id,
                            indexing_status=ProgressStatus.AUTO_INDEX_OFF.value,
                            extraction_status=record.get("extractionStatus", ProgressStatus.NOT_STARTED.value),
                            reason="Connector is inactive"
                        )
                        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                        return



            # Fallback: Get mimeType from database record if payload has empty/unknown value
            if mime_type == "unknown" or not mime_type:
                mime_type = record.get("mimeType") or "unknown"

            # CODE_FILE records always carry text/plain as their mime type, so the
            # mime-based extension fallback below would resolve to "txt" for every
            # code file. Derive the extension from the file name instead, which is
            # always present as recordName (e.g. "main.py", "index.ts").
            code_file_extension = None
            if doc.get("recordType") == RecordTypes.CODE_FILE.value:
                record_name = payload.get("recordName") or record.get("recordName")
                if record_name and "." in record_name:
                    code_file_extension = record_name.rsplit(".", 1)[-1].lower()

            if (extension is None or extension == "unknown") and mime_type is not None and mime_type != "unknown":
                derived_extension = get_extension_from_mimetype(mime_type)
                if derived_extension:
                    extension = derived_extension

            if extension == "unknown" and mime_type != "text/gmail_content":
                record_name = payload.get("recordName")
                if record_name and "." in record_name:
                    extension = record_name.split(".")[-1]

            self.logger.debug("🚀 Checking for mime_type")
            self.logger.debug("🚀 mime_type: %s", mime_type)
            self.logger.debug("🚀 extension: %s", extension)

            # Folder / tree-node records are skeleton graph entries with no
            # streamable content (created by tree-aware connectors like
            # GitLab repo sync, Azure Blob, Google Drive, Dropbox). They
            # legitimately enter Kafka as NEW_RECORD / REINDEX_RECORD
            # events because the connector still needs them in the graph
            # for parent/child traversal, but they must NOT be indexed —
            # downstream streaming would either 404 or no-op. Mark them
            # COMPLETED (same pattern as the "already indexed" short-
            # circuit above) so subsequent reindex events are no-ops.
            is_folder_mime = mime_type in (
                MimeTypes.FOLDER.value,
                MimeTypes.GOOGLE_DRIVE_FOLDER.value,
            )
            is_folder_record = record.get("isFile") is False
            if is_folder_mime or is_folder_record:
                self.logger.debug(
                    f"⏭️ Skipping indexing for folder record {record_id} "
                    f"(mime_type={mime_type}, isFile={record.get('isFile')})"
                )
                await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.COMPLETED.value,
                    extraction_status=ProgressStatus.COMPLETED.value,
                    reason="Folder record — no content to index",
                )
                yield PipelineEvent(
                    event=IndexingEvent.PARSING_COMPLETE,
                    data=PipelineEventData(record_id=record_id),
                )
                yield PipelineEvent(
                    event=IndexingEvent.INDEXING_COMPLETE,
                    data=PipelineEventData(record_id=record_id),
                )
                return

            if vector_db_only and event_type == EventTypes.REINDEX_RECORD.value:
                async for event in self._index_from_blob(
                    record_id=record_id,
                    record=record,
                    payload=payload,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                ):
                    yield event
                return

            is_code_file = doc.get("recordType") == RecordTypes.CODE_FILE.value

            supported_mime_types = [
                MimeTypes.GMAIL.value,
                MimeTypes.GOOGLE_SLIDES.value,
                MimeTypes.GOOGLE_DOCS.value,
                MimeTypes.GOOGLE_SHEETS.value,
                MimeTypes.HTML.value,
                MimeTypes.PLAIN_TEXT.value,
                MimeTypes.MARKDOWN.value,
                MimeTypes.BLOCKS.value,
                MimeTypes.PNG.value,
                MimeTypes.JPG.value,
                MimeTypes.JPEG.value,
                MimeTypes.WEBP.value,
                MimeTypes.SVG.value,
                MimeTypes.PDF.value,
                MimeTypes.DOCX.value,
                MimeTypes.DOC.value,
                MimeTypes.XLSX.value,
                MimeTypes.XLS.value,
                MimeTypes.CSV.value,
                MimeTypes.PPTX.value,
                MimeTypes.PPT.value,
                MimeTypes.MDX.value,
                MimeTypes.TSV.value,
                MimeTypes.JSON.value,
                MimeTypes.YAML.value,
                # Node's storage layer (backend/nodejs/.../mimetypes.ts) maps
                # .yaml/.yml to "application/x-yaml", not MimeTypes.YAML's
                # "application/yaml" — accept both so records created from
                # KB uploads aren't gated on this mismatch.
                "application/x-yaml",
                MimeTypes.SQL_TABLE.value,
                MimeTypes.SQL_VIEW.value,
                MimeTypes.PYTHON.value,
                MimeTypes.PYTHON_SCRIPT.value,
                MimeTypes.PYTHON_SCRIPT_X.value,
                MimeTypes.JAVA_SOURCE.value,
                MimeTypes.C_SOURCE.value,
                MimeTypes.CPP.value,
                MimeTypes.PHP.value,
                MimeTypes.JAVASCRIPT.value,
                MimeTypes.JAVASCRIPT_TEXT.value,
                MimeTypes.TYPESCRIPT.value,
                MimeTypes.CSHARP.value,
                MimeTypes.GO.value,
                MimeTypes.RUST.value,
                MimeTypes.RUBY.value,
                MimeTypes.SWIFT.value,
                MimeTypes.KOTLIN.value,
                MimeTypes.DART.value,
                MimeTypes.SHELL.value,
                MimeTypes.SHELL_TEXT.value,
                MimeTypes.SHELLSCRIPT.value,
                MimeTypes.EPUB.value,
            ]

            supported_extensions = [
                ExtensionTypes.PDF.value,
                ExtensionTypes.DOCX.value,
                ExtensionTypes.DOC.value,
                ExtensionTypes.XLSX.value,
                ExtensionTypes.XLS.value,
                ExtensionTypes.CSV.value,
                ExtensionTypes.HTML.value,
                ExtensionTypes.PPTX.value,
                ExtensionTypes.PPT.value,
                ExtensionTypes.MD.value,
                ExtensionTypes.MDX.value,
                ExtensionTypes.TXT.value,
                ExtensionTypes.PNG.value,
                ExtensionTypes.JPG.value,
                ExtensionTypes.JPEG.value,
                ExtensionTypes.WEBP.value,
                ExtensionTypes.SVG.value,
                ExtensionTypes.TSV.value,
                ExtensionTypes.JSON.value,
                ExtensionTypes.YAML.value,
                ExtensionTypes.YML.value,
                ExtensionTypes.SQL_TABLE.value,
                ExtensionTypes.SQL_VIEW.value,
                ExtensionTypes.PY.value,
                ExtensionTypes.JS.value,
                ExtensionTypes.JSX.value,
                ExtensionTypes.MJS.value,
                ExtensionTypes.CJS.value,
                ExtensionTypes.TS.value,
                ExtensionTypes.TSX.value,
                ExtensionTypes.JAVA.value,
                ExtensionTypes.C.value,
                ExtensionTypes.H.value,
                ExtensionTypes.CPP.value,
                ExtensionTypes.CC.value,
                ExtensionTypes.CXX.value,
                ExtensionTypes.HPP.value,
                ExtensionTypes.HXX.value,
                ExtensionTypes.CS.value,
                ExtensionTypes.GO.value,
                ExtensionTypes.RS.value,
                ExtensionTypes.RB.value,
                ExtensionTypes.PHP.value,
                ExtensionTypes.SWIFT.value,
                ExtensionTypes.KT.value,
                ExtensionTypes.KTS.value,
                ExtensionTypes.DART.value,
                ExtensionTypes.SH.value,
                ExtensionTypes.BASH.value,
                ExtensionTypes.HTM.value,
                ExtensionTypes.EPUB.value,
            ]

            if is_code_file:
                # A CODE_FILE's mime is not trustworthy — connectors that walk a
                # git tree default it to text/plain for anything they don't
                # recognise, which would let archives and media through as text.
                # Judge it on the filename extension alone: a known language, or
                # a type the generic pipeline handles (images, json, yaml).
                judged_extension = code_file_extension
                is_supported = (
                    code_file_extension in SUPPORTED_CODE_FILE_EXTENSIONS
                    or code_file_extension in supported_extensions
                )
            else:
                judged_extension = extension
                is_supported = (
                    mime_type in supported_mime_types
                    or extension in supported_extensions
                )

            if not is_supported:
                self.logger.info(
                    f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {judged_extension} 🔴🔴🔴"
                )

                await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    extraction_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    reason=f"Unsupported file type: {mime_type} ({judged_extension})",
                )

                # Yield both events for unsupported file types
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return



            # Try signed URL first if available, fallback to connector streaming if it fails
            signed_url_success = False

            if payload and payload.get("signedUrl"):
                self.logger.info(f"🔍 Signed URL received for record {record_id}")
                try:
                    response = await self._download_from_signed_url(
                        signed_url=payload["signedUrl"], record_id=record_id, doc=doc,
                    )
                    if not response:
                        raise Exception("Failed to download file from signed URL")
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Failed to download from signed URL for record {record_id}: {str(e)}. "
                        f"Falling back to connector streaming..."
                    )
                else:
                    payload["buffer"] = response
                    event_data_for_processor = {
                        "eventType": event_type,
                        "payload": payload
                    }
                    on_event_gen = self.event_processor.on_event(event_data_for_processor)
                    try:
                        async for event in on_event_gen:
                            yield event
                    finally:
                        await on_event_gen.aclose()
                        payload.pop("buffer", None)
                        response = None

                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    signed_url_success = True
                    return

            if not signed_url_success:
                self.logger.debug(f"🔍 No signed URL received for record {record_id}")
                try:
                    jwt_payload  = {
                        "orgId": payload["orgId"],
                        "scopes": ["connector:signedUrl"],
                    }
                    token = await generate_jwt(self.config_service, jwt_payload)
                    self.logger.debug(f"Generated JWT token for message {message_id}")

                    endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
                    connector_url = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

                    response = await make_api_call(
                        route=f"{connector_url}/api/v1/internal/stream/record/{record_id}", token=token,
                    )

                    event_data_for_processor = {
                        "eventType": event_type,
                        "payload": payload
                    }

                    event_data_for_processor["payload"]["buffer"] = response["data"]

                    # Yield events from the event processor.
                    # Explicitly aclose() the generator so its frame (which holds the large
                    # file bytes) is released immediately — not deferred to async-GC.
                    on_event_gen = self.event_processor.on_event(event_data_for_processor)
                    try:
                        async for event in on_event_gen:
                            yield event
                    finally:
                        await on_event_gen.aclose()
                        payload.pop("buffer", None)
                        # Drop the local reference too: process_event is itself an
                        # async generator, so its frame outlives this block until
                        # the caller closes it.
                        response = None

                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    return
                except IndexingError:
                    error_occurred = True
                    raise  # preserve DocumentProcessingError and other IndexingError subtypes
                except Exception as e:
                    error_occurred = True
                    error_msg = str(e)
                    raise Exception(error_msg) from e  # unknown errors only
        except GeneratorExit:
            # The consumer closes this generator (via aclose()) when a
            # timeout/shutdown cancels the task that was iterating it —
            # e.g. while parked waiting for the parsing semaphore. That
            # cancellation is delivered to the consumer's loop, not here, so
            # without this handler error_occurred would stay False and the
            # record would be left IN_PROGRESS forever. is_final_failure
            # (set by the consumer before we started) still governs whether
            # this becomes a terminal FAILED or a QUEUED retry below.
            error_occurred = True
            error_msg = "Record processing was cancelled (handler closed)"
            raise
        except asyncio.CancelledError as ce:
            error_occurred = True
            error_msg = "Record processing was cancelled"
            last_exception = ce
            raise
        except IndexingError as ie:
            error_occurred = True
            error_msg = str(ie)
            last_exception = ie
            raise  # preserve DocumentProcessingError and other IndexingError subtypes
        except Exception as e:
            error_occurred = True
            error_msg = str(e)
            last_exception = e
            # No traceback here: the Kafka consumer already logs the full
            # exception chain on every attempt. A traceback is logged below
            # only once, when this turns out to be the final attempt.
            self.logger.warning(f"Record {message_id} processing failed: {error_msg}")
            raise  # bare re-raise — preserves IndexingError / DocumentProcessingError
        finally:
            processing_time = (datetime.now() - start_time).total_seconds()
            self.logger.info(
                f"Message {message_id} processing completed in {processing_time:.2f}s. "
                f"Success: {not error_occurred}"
            )

            if error_occurred and record_id:
                # Only update DB status to FAILED if this is the final failure
                # (terminal error or dead-letter after max retries)
                is_final = payload.get("is_final_failure")

                # Terminal errors are always final, even on the first attempt.
                # is_final_failure is set before the handler runs (based on retry count),
                # so it is False on attempt 1 — but the consumer classifies terminal errors
                # only after the handler raises. Check the exception here instead.
                if last_exception is not None and (
                    MessageErrorClassifier.classify_by_exception(last_exception)
                    == MessageErrorType.TERMINAL
                ):
                    is_final = True

                if is_final is None:
                    self.logger.warning(
                        f"Missing is_final_failure flag for record {record_id}, "
                        f"defaulting to True (safe fail-fast). This may indicate a bug in the consumer."
                    )
                    is_final = True
                    
                if is_final:
                    # Traceback logged once here (not on every transient retry attempt)
                    # so final, unrecoverable failures remain fully debuggable.
                    self.logger.error(
                        f"Final failure for record {record_id}: {error_msg}",
                        exc_info=last_exception,
                    )
                    try:
                        record = await self.__update_document_status(
                            record_id=record_id,
                            indexing_status=ProgressStatus.FAILED.value,
                            extraction_status=ProgressStatus.FAILED.value,
                            reason=error_msg,
                        )
                    except Exception as status_exc:
                        # A status-write failure here must not replace the
                        # exception already propagating (e.g. a terminal
                        # DocumentProcessingError or a CancelledError) with a
                        # new one that the consumer would reclassify/mishandle.
                        self.logger.error(
                            f"Failed to persist FAILED status for record {record_id} "
                            f"(original error preserved): {status_exc}"
                        )
                        if last_exception is not None:
                            raise last_exception from status_exc
                        raise
                    if record is not None:
                        virtual_record_id = record.get("virtualRecordId")
                        
                        # Decide duplicate handling based on error type
                        if (last_exception and 
                            MessageErrorClassifier.classify_by_exception(last_exception)
                            == MessageErrorType.TERMINAL):
                            # Terminal error → content issue → fail ALL duplicates
                            self.logger.info(
                                f"🔄 Terminal failure for record {record_id}, "
                                f"propagating failure to all queued duplicates"
                            )
                            await self._propagate_primary_failure_to_queued_duplicates(
                                record_id, virtual_record_id, error_msg
                            )
                        else:
                            # Transient error exhausted retries → try next duplicate
                            self.logger.info(
                                f"🔄 Record {record_id} failed after max retries, "
                                f"triggering next queued duplicate"
                            )
                            await self._trigger_next_queued_duplicate(record_id, virtual_record_id)
                    else:
                        self.logger.warning(f"Record {record_id} not found, skipping duplicate handling")
                else:
                    # Clear IN_PROGRESS so the record stops counting against
                    # concurrency limits while it waits in the broker re-queue.
                    # indexingStatus becomes QUEUED (same as a newly published
                    # record); phase statuses that were mid-flight reset to
                    # NOT_STARTED. Never downgrade a status that already
                    # advanced past IN_PROGRESS (e.g. COMPLETED/EMPTY).
                    reverted = False
                    try:
                        current = await self.event_processor.graph_provider.get_document(
                            record_id, CollectionNames.RECORDS.value
                        )
                        updates: dict = {}
                        if current:
                            if current.get("parsingStatus") == ProgressStatus.IN_PROGRESS.value:
                                updates["parsingStatus"] = ProgressStatus.NOT_STARTED.value
                            if current.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value:
                                updates["indexingStatus"] = ProgressStatus.QUEUED.value
                                if current.get("extractionStatus") != ProgressStatus.COMPLETED.value:
                                    updates["extractionStatus"] = ProgressStatus.NOT_STARTED.value
                        if updates:
                            updates["reason"] = f"Transient failure, retry scheduled: {error_msg}"
                            updates["processingStartedAt"] = None
                            updated = await self.event_processor.graph_provider.update_node(
                                record_id, CollectionNames.RECORDS.value, updates
                            )
                            reverted = True
                    except Exception as revert_exc:
                        self.logger.error(
                            f"Failed to re-queue record {record_id} after transient failure: {revert_exc}"
                        )
                        # Preserve the exception already propagating (e.g. a
                        # CancelledError during shutdown) instead of masking
                        # it with a new one raised from this cleanup step.
                        if last_exception is not None:
                            raise last_exception from revert_exc
                        raise RuntimeError(
                            f"Failed to clear transient IN_PROGRESS status for {record_id}"
                        ) from revert_exc

                    if reverted:
                        self.logger.info(
                            f"🔄 Record {record_id} failed but will retry, "
                            f"reverted IN_PROGRESS -> QUEUED"
                        )
                    else:
                        self.logger.info(
                            f"🔄 Record {record_id} failed but will retry, not updating status to FAILED yet"
                        )
            elif record is not None and event_type != EventTypes.DELETE_RECORD.value:
                # Update queued duplicates for ALL record types (not just FILE)
                record = await self.event_processor.graph_provider.get_document(
                    record_id, CollectionNames.RECORDS.value
                )
                if record is not None:
                    indexing_status = record.get("indexingStatus")
                    virtual_record_id = record.get("virtualRecordId")
                    if indexing_status == ProgressStatus.COMPLETED.value or indexing_status == ProgressStatus.EMPTY.value:
                        await self.event_processor.graph_provider.update_queued_duplicates_status(record_id, indexing_status, virtual_record_id)
                        if indexing_status == ProgressStatus.COMPLETED.value:
                            # Duplicates just became searchable too. They can live in
                            # a different KB than this record, which only the TTL
                            # covers — the provider returns a count, not the ids.
                            await notify_record_indexed(
                                connector_name=record.get("connectorName"),
                                connector_id=record.get("connectorId"),
                                external_record_group_id=record.get("externalGroupId"),
                                org_id=record.get("orgId"),
                            )
                    elif indexing_status == ProgressStatus.ENABLE_MULTIMODAL_MODELS.value:
                        # Find and trigger indexing for the next queued duplicate
                        self.logger.info(f"🔄 Current record {record_id} has status {indexing_status}, triggering next queued duplicate")
                        await self._trigger_next_queued_duplicate(record_id, virtual_record_id)
                else:
                    self.logger.warning(f"Record {record_id} not found in database")

    async def __update_document_status(
        self,
        record_id: str,
        indexing_status: str,
        extraction_status: str,
        reason: str | None = None,
    ) -> dict|None:
        """Update document status in database"""
        try:
            record = await self.event_processor.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if not record:
                self.logger.error(f"❌ Record {record_id} not found for status update")
                return None

            if record.get("extractionStatus") == ProgressStatus.COMPLETED.value:
                extraction_status = ProgressStatus.COMPLETED.value
            updates = {
                "indexingStatus": indexing_status,
                "extractionStatus": extraction_status,
                "processingStartedAt": None,
            }
            # Mirror the terminal status onto parsingStatus, but never
            # downgrade a parse that already completed in this attempt.
            if record.get("parsingStatus") == ProgressStatus.IN_PROGRESS.value:
                updates["parsingStatus"] = indexing_status

            if reason:
                updates["reason"] = reason

            success = await self.event_processor.graph_provider.update_node(
                record_id,
                CollectionNames.RECORDS.value,
                updates,
            )
            if not success:
                self.logger.warning(
                    "⚠️ Failed to update document status for record %s - record may not exist",
                    record_id,
                )
                return None
            self.logger.info(f"✅ Updated document status for record {record_id}")
            return record
        except Exception as e:
            self.logger.error(f"❌ Failed to update document status: {str(e)}")
            raise

    async def _download_from_signed_url(
        self, signed_url: str, record_id: str, doc: dict, from_route: bool = False,
    ) -> bytes|None:
        """
        Download file from signed URL with exponential backoff retry

        Args:
            signed_url: The signed URL to download from
            record_id: Record ID for logging
            doc: Document object for status updates

        Returns:
            bytes: The downloaded file content
        """
        chunk_size = 1024 * 1024 * 3  # 3MB chunks
        max_retries = 3
        base_delay = 1  # Start with 1 second delay

        timeout = aiohttp.ClientTimeout(
            total=1200,  # 20 minutes total
            connect=120,  # 2 minutes for initial connection
            sock_read=1200,  # 20 minutes per chunk read
        )

        for attempt in range(max_retries):
            delay = base_delay * (2**attempt)  # Exponential backoff
            file_buffer = bytearray()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    try:
                        async with session.get(signed_url) as response:
                            if response.status != HttpStatusCode.SUCCESS.value:
                                raise aiohttp.ClientError(
                                    f"Failed to download file: {response.status}"
                                )

                            content_length = response.headers.get("Content-Length")
                            if content_length:
                                self.logger.info(
                                    f"Expected file size: {int(content_length) / (1024*1024):.2f} MB"
                                )

                            last_logged_size = 0
                            total_size = 0
                            log_interval = chunk_size

                            self.logger.info("Starting chunked download...")
                            try:
                                async for chunk in response.content.iter_chunked(
                                    chunk_size
                                ):
                                    file_buffer.extend(chunk)
                                    total_size += len(chunk)
                                    if total_size - last_logged_size >= log_interval:
                                        self.logger.debug(
                                            f"Total size so far: {total_size / (1024*1024):.2f} MB"
                                        )
                                        last_logged_size = total_size
                            except IOError as io_err:
                                raise aiohttp.ClientError(
                                    f"IO error during chunk download: {str(io_err)}"
                                ) from io_err

                            file_content = bytes(file_buffer)
                            self.logger.info(
                                f"✅ Download complete. Total size: {total_size / (1024*1024):.2f} MB"
                            )
                            return file_content

                    except aiohttp.ServerDisconnectedError as sde:
                        raise aiohttp.ClientError(f"Server disconnected: {str(sde)}") from sde
                    except aiohttp.ClientConnectorError as cce:
                        raise aiohttp.ClientError(f"Connection error: {str(cce)}") from cce

            except (aiohttp.ClientError, asyncio.TimeoutError, IOError) as e:
                error_type = type(e).__name__
                self.logger.warning(
                    f"Download attempt {attempt + 1} failed with {error_type}: {str(e)}. "
                    f"Retrying in {delay} seconds..."
                )

                if attempt == max_retries - 1:  # Last attempt failed
                    self.logger.error(
                        f"❌ All download attempts failed for record {record_id}. "
                        f"Error type: {error_type}, Details: {repr(e)}"
                    )
                    raise Exception(
                        f"Download failed after {max_retries} attempts. "
                        f"Error: {error_type} - {str(e)}. File id: {record_id}"
                    ) from e
                await asyncio.sleep(delay)
