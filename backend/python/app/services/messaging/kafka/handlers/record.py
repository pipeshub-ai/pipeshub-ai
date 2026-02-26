import asyncio
from datetime import datetime
from io import BytesIO
from logging import Logger
from typing import Any, AsyncGenerator, Dict, Optional

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
from app.services.messaging.kafka.handlers.entity import BaseEventService
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt
from app.utils.mimetype_to_extension import get_extension_from_mimetype


class RecordEventHandler(BaseEventService):
    def __init__(self, logger: Logger,
                config_service: ConfigurationService,
                event_processor: EventProcessor,
                ) -> None:

        self.logger = logger
        self.config_service = config_service

        self.event_processor : EventProcessor = event_processor

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
            await self.event_processor.graph_provider._publish_record_event("newRecord", payload)

            self.logger.info(f"✅ Successfully triggered indexing for queued duplicate: {next_record_id}")

        except Exception as e:
            self.logger.warning(f"Failed to trigger next queued duplicate: {str(e)}")
            try:
                await self.event_processor.graph_provider.update_queued_duplicates_status(record_id, ProgressStatus.FAILED.value, virtual_record_id)
            except Exception as e:
                self.logger.warning(f"Failed to update queued duplicates status: {str(e)}")


    async def process_event(self, event_type: str, payload: dict) -> AsyncGenerator[Dict[str, Any], None]:
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
                    f"✅ Bulk deletion complete: {result.get('deleted_count', 0)} embeddings deleted "
                    f"for {result.get('virtual_record_ids_processed', 0)} virtual record IDs"
                )
                yield {"event": "parsing_complete", "data": {"record_id": "bulk_delete", "count": len(virtual_record_ids)}}
                yield {"event": "indexing_complete", "data": {"record_id": "bulk_delete", "count": len(virtual_record_ids)}}
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
            asyncio.get_event_loop
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
                await self.event_processor.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)
                # Yield both events since delete is complete
                yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                yield {"event": "indexing_complete", "data": {"record_id": record_id}}
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
                    await self.event_processor.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)

            doc = dict(record)

            if (event_type == EventTypes.NEW_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value) and doc.get("indexingStatus") == ProgressStatus.COMPLETED.value:
                self.logger.info(f"🔍 Indexing already done for record {record_id} with virtual_record_id {virtual_record_id}")
                yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                yield {"event": "indexing_complete", "data": {"record_id": record_id}}
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
                        yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                        yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                        return
                    if not connector_instance.get("isActive", False):
                        self.logger.info(
                            f"⏭️ Skipping indexing for record {record_id}: "
                            f"connector instance {connector_id} is inactive."
                        )
                        # Update status to CONNECTOR_DISABLED
                        await self.__update_document_status(
                            record_id=record_id,
                            indexing_status=ProgressStatus.CONNECTOR_DISABLED.value,
                            extraction_status=record.get("extractionStatus", ProgressStatus.NOT_STARTED.value)
                        )
                        yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                        yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                        return



            # Fallback: Get mimeType from database record if payload has empty/unknown value
            if mime_type == "unknown" or not mime_type:
                mime_type = record.get("mimeType") or "unknown"

            if (extension is None or extension == "unknown") and mime_type is not None and mime_type != "unknown":
                derived_extension = get_extension_from_mimetype(mime_type)
                if derived_extension:
                    extension = derived_extension

            if extension == "unknown" and mime_type != "text/gmail_content":
                record_name = payload.get("recordName")
                if record_name and "." in record_name:
                    extension = record_name.split(".")[-1]

            self.logger.info("🚀 Checking for mime_type")
            self.logger.info("🚀 mime_type: %s", mime_type)
            self.logger.info("🚀 extension: %s", extension)



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
                MimeTypes.SQL_TABLE.value,
                MimeTypes.SQL_VIEW.value,
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
                ExtensionTypes.SQL_TABLE.value,
                ExtensionTypes.SQL_VIEW.value,
            ]

            if (
                mime_type not in supported_mime_types
                and extension not in supported_extensions
            ):
                self.logger.info(
                    f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {extension} 🔴🔴🔴"
                )

                doc.update(
                    {
                        "indexingStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                        "extractionStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    }
                )
                docs = [doc]
                await self.event_processor.graph_provider.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )

                # Yield both events for unsupported file types
                yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                return



            # Try signed URL first if available, fallback to connector streaming if it fails
            signed_url_success = False

            if payload and payload.get("signedUrl"):
                self.logger.info(f"🔍 Signed URL received for record {record_id}")
                try:
                    response = await self._download_from_signed_url(signed_url=payload["signedUrl"], record_id=record_id, doc=doc)
                    if not response:
                        raise Exception("Failed to download file from signed URL")

                    payload["buffer"] = response
                    event_data_for_processor = {
                        "eventType": event_type,
                        "payload": payload # The original payload
                    }
                    # Yield events from the event processor
                    async for event in self.event_processor.on_event(event_data_for_processor):
                        yield event

                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    signed_url_success = True
                    return
                except Exception as e:
                    self.logger.warning(
                        f"⚠️ Failed to download from signed URL for record {record_id}: {str(e)}. "
                        f"Falling back to connector streaming..."
                    )
                    # Don't raise - fall through to connector streaming fallback

            if not signed_url_success:
                self.logger.info(f"🔍 No signed URL received for record {record_id}")
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

                    # Yield events from the event processor
                    async for event in self.event_processor.on_event(event_data_for_processor):
                        yield event

                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    return
                except Exception as e:
                    error_occurred = True
                    error_msg = str(e)
                    raise Exception(error_msg)
        except IndexingError as e:
            error_occurred = True
            error_msg = str(e)
            self.logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
        except Exception as e:
            error_occurred = True
            error_msg = str(e)
            self.logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
        finally:
            processing_time = (datetime.now() - start_time).total_seconds()
            self.logger.info(
                f"Message {message_id} processing completed in {processing_time:.2f}s. "
                f"Success: {not error_occurred}"
            )

            if error_occurred and record_id:
                record = await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FAILED.value,
                    extraction_status=ProgressStatus.FAILED.value,
                    reason=error_msg,
                )
                virtual_record_id = record.get("virtualRecordId") if record else None
                self.logger.info(f"🔄 Current record {record_id} has failed, triggering next queued duplicate")
                await self._trigger_next_queued_duplicate(record_id,virtual_record_id)
                return

            if record is None:
                return

            # Update queued duplicates for ALL record types (not just FILE)
            if event_type != EventTypes.DELETE_RECORD.value:
                record = await self.event_processor.graph_provider.get_document(
                    record_id, CollectionNames.RECORDS.value
                )
                if record is None:
                    self.logger.warning(f"Record {record_id} not found in database")
                    return

                indexing_status = record.get("indexingStatus")
                virtual_record_id = record.get("virtualRecordId")
                if indexing_status == ProgressStatus.COMPLETED.value or indexing_status == ProgressStatus.EMPTY.value:
                    await self.event_processor.graph_provider.update_queued_duplicates_status(record_id, indexing_status, virtual_record_id)
                elif indexing_status == ProgressStatus.ENABLE_MULTIMODAL_MODELS.value:
                    # Find and trigger indexing for the next queued duplicate
                    self.logger.info(f"🔄 Current record {record_id} has status {indexing_status}, triggering next queued duplicate")
                    await self._trigger_next_queued_duplicate(record_id, virtual_record_id)

    async def __update_document_status(
        self,
        record_id: str,
        indexing_status: str,
        extraction_status: str,
        reason: Optional[str] = None,
    ) -> dict|None:
        """Update document status in database"""
        try:
            record = await self.event_processor.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if not record:
                self.logger.error(f"❌ Record {record_id} not found for status update")
                return None

            doc = dict(record)
            if doc.get("extractionStatus") == ProgressStatus.COMPLETED.value:
                extraction_status = ProgressStatus.COMPLETED.value
            doc.update(
                {
                    "indexingStatus": indexing_status,
                    "extractionStatus": extraction_status,
                }
            )

            if reason:
                doc["reason"] = reason

            docs = [doc]
            await self.event_processor.graph_provider.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )
            self.logger.info(f"✅ Updated document status for record {record_id}")
            return record
        except Exception as e:
            self.logger.error(f"❌ Failed to update document status: {str(e)}")
            return None

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
                                )

                            file_content = file_buffer.getvalue()
                            self.logger.info(
                                f"✅ Download complete. Total size: {total_size / (1024*1024):.2f} MB"
                            )
                            return file_content

                    except aiohttp.ServerDisconnectedError as sde:
                        raise aiohttp.ClientError(f"Server disconnected: {str(sde)}")
                    except aiohttp.ClientConnectorError as cce:
                        raise aiohttp.ClientError(f"Connection error: {str(cce)}")

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
                    )
                await asyncio.sleep(delay)
            finally:
                if not file_buffer.closed:
                    file_buffer.close()
