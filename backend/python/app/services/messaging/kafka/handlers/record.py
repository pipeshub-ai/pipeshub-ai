import asyncio
from collections.abc import AsyncGenerator
from datetime import datetime
from io import BytesIO
from logging import Logger

import aiohttp  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
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
from app.exceptions.indexing_exceptions import IndexingError
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
from app.utils.api_call import make_api_call
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.jwt import generate_jwt


SUPPORTED_CODE_FILE_EXTENSIONS = {
    # C
    "c", "h",
    # C++
    "cpp", "cc", "cxx", "hpp", "hxx",
    # C#
    "cs",
    # Java
    "java",
    # Python
    "py",
    # JavaScript
    "js", "jsx", "mjs", "cjs",
    # TypeScript
    "ts", "tsx",
    # Go
    "go",
    # Rust
    "rs",
    # Ruby
    "rb",
    # PHP
    "php",
    # Swift
    "swift",
    # Kotlin
    "kt", "kts",
    # Dart
    "dart",
    # Bash
    "sh", "bash",
    # HTML
    "html", "htm",
    #Markdown
    "md"
}

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

    async def _trigger_next_queued_duplicate(self, record_id: str, virtual_record_id) -> None:
        try:
            self.logger.info(f"🔍 Looking for next queued duplicate for record {record_id}")

            # Find the next queued duplicate
            next_queued_record = await self.event_processor.graph_provider.find_next_queued_duplicate(record_id)

            if not next_queued_record:
                self.logger.info(f"✅ No queued duplicates found for record {record_id}")
                return

            next_record_id = next_queued_record.get("_key")
            self.logger.info(f"🚀 Found queued duplicate: {next_record_id}, triggering indexing")

            # Get file record for the queued duplicate
            file_record = None
            if next_queued_record.get("recordType") == RecordTypes.FILE.value:
                file_record = await self.event_processor.graph_provider.get_document(
                    next_record_id, CollectionNames.FILES.value
                )

            # Create event payload for the queued record
            payload = await self.event_processor.graph_provider._create_reindex_event_payload(
                next_queued_record,
                file_record,
            )

            # Publish the event to trigger indexing
            if not self.producer:
                raise IndexingError("No messaging producer configured; cannot publish newRecord event")
            await self.producer.send_event(
                topic=Topic.RECORD_EVENTS.value,
                event_type="newRecord",
                payload=payload,
                key=str(next_record_id),
            )

            self.logger.info(f"✅ Successfully triggered indexing for queued duplicate: {next_record_id}")

        except Exception as e:
            self.logger.warning(f"Failed to trigger next queued duplicate: {str(e)}")
            try:
                await self.event_processor.graph_provider.update_queued_duplicates_status(record_id, ProgressStatus.FAILED.value, virtual_record_id)
            except Exception as e:
                self.logger.warning(f"Failed to update queued duplicates status: {str(e)}")


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
                self.logger.error(f"Missing event_type in message {payload}")
                return

            # Handle bulk delete event FIRST - for connector instance deletion (doesn't have record_id)
            if event_type == EventTypes.BULK_DELETE_RECORDS.value:
                virtual_record_ids = payload.get("virtualRecordIds", [])
                self.logger.info(f"🗑️ Bulk deleting embeddings for {len(virtual_record_ids)} records")

                result = await self.event_processor.processor.indexing_pipeline.bulk_delete_embeddings(
                    virtual_record_ids
                )

                self.logger.info(
                    f"✅ Bulk deletion complete: embeddings deleted for "
                    f"{result.get('virtual_record_ids_processed', 0)} virtual record IDs"
                )
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="bulk_delete", count=len(virtual_record_ids)))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="bulk_delete", count=len(virtual_record_ids)))
                return

            # For all other event types, require record_id
            record_id = payload.get("recordId")
            extension = payload.get("extension", "unknown")
            mime_type = payload.get("mimeType", "unknown")
            virtual_record_id = payload.get("virtualRecordId")
            message_id = f"{event_type}-{record_id}"

            if not record_id:
                self.logger.error(f"Missing record_id in message {payload}")
                return

        

            record = await self.event_processor.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )

            self.logger.info(
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
                self.logger.error(f"❌ Record {record_id} not found in database")
                return

            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")

            #Reconciliation
            if event_type == EventTypes.UPDATE_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value:
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

            if (event_type == EventTypes.NEW_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value) and doc.get("indexingStatus") == ProgressStatus.COMPLETED.value:
                self.logger.info(f"🔍 Indexing already done for record {record_id} with virtual_record_id {virtual_record_id}")
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            # Check if record is from a connector and if the connector is active
            if event_type == EventTypes.NEW_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value:
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

            # Gate: CODE_FILE records only index supported programming languages.
            # Code files typically arrive as text/plain (which passes the general
            # mime check below), so we need an explicit allowlist here.
            if doc.get("recordType") == RecordTypes.CODE_FILE.value and (code_file_extension is None or code_file_extension not in SUPPORTED_CODE_FILE_EXTENSIONS):
                self.logger.info(
                    f"🔴 CODE_FILE with unsupported language extension '{code_file_extension}' "
                    f"for record {record_id} — marking FILE_TYPE_NOT_SUPPORTED"
                )
                await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    extraction_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    reason=f"Unsupported code file extension: {code_file_extension}",
                )
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

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
            ]

            if (
                mime_type not in supported_mime_types
                and extension not in supported_extensions
            ):
                self.logger.info(
                    f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {extension} 🔴🔴🔴"
                )

                await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    extraction_status=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    reason=f"Unsupported file type: {mime_type} ({extension})",
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
                    response = await self._download_from_signed_url(signed_url=payload["signedUrl"], record_id=record_id, doc=doc)
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
                        route=f"{connector_url}/api/v1/internal/stream/record/{record_id}", token=token
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
                            if not updated:
                                raise RuntimeError(
                                    f"Failed to persist transient status reset for {record_id}"
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
        self, signed_url: str, record_id: str, doc: dict,from_route: bool = False
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
            file_buffer = BytesIO()
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
                                    file_buffer.write(chunk)
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

                            file_content = file_buffer.getvalue()
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
            finally:
                if not file_buffer.closed:
                    file_buffer.close()
