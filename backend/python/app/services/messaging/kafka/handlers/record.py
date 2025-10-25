import asyncio
from datetime import datetime
from logging import Logger
from typing import Optional

import aiohttp  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.events.events import EventProcessor
from app.exceptions.indexing_exceptions import IndexingError
from app.services.messaging.kafka.handlers.entity import BaseEventService

# from app.connectors.sources.google.common.arango_service import ArangoService
from app.services.scheduler.interface.scheduler import Scheduler
from app.services.scheduler.scheduler_factory import SchedulerFactory
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt
from app.utils.mimetype_to_extension import get_extension_from_mimetype
from app.utils.redis_util import build_redis_url


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
async def make_signed_url_api_call(signed_url: str) -> dict:
    """
    Make an API call with the JWT token.

    Args:
        signed_url (str): The signed URL to send the request to

    Returns:
        dict: The response from the API
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = signed_url
            # Make the request
            async with session.get(url) as response:
                data = await response.read()
                return data
    except Exception:
        raise Exception("Failed to make signed URL API call")


class RecordEventHandler(BaseEventService):
    def __init__(self, logger: Logger,
                config_service: ConfigurationService,
                event_processor: EventProcessor,
                scheduler: Optional[Scheduler] = None
                ) -> None:

        self.logger = logger
        self.config_service = config_service
        if scheduler:
            self.scheduler : Scheduler = scheduler
        else:
            self.scheduler : Scheduler = self.__buildDefaultScheduler(logger, config_service)
        self.scheduled_update_task = asyncio.create_task(self.scheduler.process_scheduled_events(event_processor))
        self.event_processor : EventProcessor = event_processor

    def __buildDefaultScheduler(self, logger: Logger, config_service: ConfigurationService) -> Scheduler:
        redis_config = config_service.get_config(config_node_constants.REDIS.value)
        if not redis_config or not isinstance(redis_config, dict):
            raise ValueError("Redis configuration not found")
        # Build Redis URL with password if provided
        redis_url = build_redis_url(redis_config)
        return SchedulerFactory.scheduler("redis", redis_url, logger, config_service, delay_hours=1)

    async def process_event(self, event_type: str, payload: dict) -> bool:
        start_time = datetime.now()
        record_id = None
        message_id = f"{event_type}-unknown"
        error_occurred = False
        error_msg = None
        try:
            record_id = payload.get("recordId")
            extension = payload.get("extension", "unknown")
            mime_type = payload.get("mimeType", "unknown")
            virtual_record_id = payload.get("virtualRecordId")
            message_id = f"{event_type}-{record_id}"
            if not event_type:
                self.logger.error(f"Missing event_type in message {payload}")
                return False

            if not record_id:
                self.logger.error(f"Missing record_id in message {payload}")
                return False

            record = await self.event_processor.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )


            self.logger.info(
                f"Processing record {record_id} with event type: {event_type}. "
                f"Virtual Record ID: {virtual_record_id} "
                f"Extension: {extension}, Mime Type: {mime_type}"
            )

            # Handle delete event
            if event_type == EventTypes.DELETE_RECORD.value:
                await self.event_processor.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)
                return True

            if event_type == EventTypes.UPDATE_RECORD.value:
                # await self.scheduler.schedule_event({"eventType": event_type, "payload": payload})
                # self.logger.info(f"Scheduled update for record {record_id}")
                await self.event_processor.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)

            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
                return False

            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")


            if extension is None and mime_type != "text/gmail_content":
                extension = payload.get("extension", None)
                if extension is None:
                    record_name = payload.get("recordName")
                    if record_name and "." in record_name:
                        extension = payload["recordName"].split(".")[-1]

            if (extension is None or extension == "unknown") and mime_type is not None and mime_type != "unknown":
                derived_extension = get_extension_from_mimetype(mime_type)
                if derived_extension:
                    extension = derived_extension

            self.logger.info("🚀 Checking for mime_type")
            self.logger.info("🚀 mime_type: %s", mime_type)
            self.logger.info("🚀 extension: %s", extension)

            doc = dict(record)

            if event_type == EventTypes.NEW_RECORD.value and doc.get("indexingStatus") == ProgressStatus.COMPLETED.value:
                self.logger.info(f"🔍 Embeddings already exist for record {record_id} with virtual_record_id {virtual_record_id}")
                return True

            supported_mime_types = [
                MimeTypes.GMAIL.value,
                MimeTypes.GOOGLE_SLIDES.value,
                MimeTypes.GOOGLE_DOCS.value,
                MimeTypes.GOOGLE_SHEETS.value,
                MimeTypes.HTML.value,
                MimeTypes.PLAIN_TEXT.value,
                MimeTypes.PNG.value,
                MimeTypes.JPG.value,
                MimeTypes.JPEG.value,
                MimeTypes.WEBP.value,
                MimeTypes.SVG.value,
                MimeTypes.HEIC.value,
                MimeTypes.HEIF.value,
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
                ExtensionTypes.HEIC.value,
                ExtensionTypes.HEIF.value,
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
                await self.event_processor.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )

                return True

            # Update with new metadata fields
            doc.update(
                {
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "extractionStatus": ProgressStatus.IN_PROGRESS.value,
                }
            )

            docs = [doc]
            await self.event_processor.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

            # Signed URL handling
            if payload and payload.get("signedUrlRoute"):
                try:
                    jwt_payload  = {
                        "orgId": payload["orgId"],
                        "scopes": ["storage:token"],
                    }
                    token = await generate_jwt(self.config_service, jwt_payload)
                    self.logger.debug(f"Generated JWT token for message {message_id}")

                    response = await make_api_call(
                        route=payload["signedUrlRoute"], token=token
                    )
                    self.logger.debug(
                        f"Received signed URL response for message {message_id}"
                    )

                    event_data_for_processor = {
                        "eventType": event_type,
                        "payload": payload # The original payload
                    }

                    if response.get("is_json"):
                        signed_url = response["data"]["signedUrl"]
                        event_data_for_processor["payload"]["signedUrl"] = signed_url
                    else:
                        event_data_for_processor["payload"]["buffer"] = response["data"]

                    await self.event_processor.on_event(event_data_for_processor)
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    return True
                except Exception as e:
                    error_occurred = True
                    error_msg = f"Failed to process signed URL: {str(e)}"
                    raise Exception(error_msg)

            elif payload and payload.get("signedUrl"):
                try:
                    response = await make_signed_url_api_call(signed_url=payload["signedUrl"])
                    if response:
                        payload["buffer"] = response
                    event_data_for_processor = {
                        "eventType": event_type,
                        "payload": payload # The original payload
                    }
                    await self.event_processor.on_event(event_data_for_processor)
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    return True
                except Exception as e:
                    error_occurred = True
                    error_msg = f"Failed to process signed URL: {str(e)}"
                    raise Exception(error_msg)
            else:
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

                    await self.event_processor.on_event(event_data_for_processor)
                    processing_time = (datetime.now() - start_time).total_seconds()
                    self.logger.info(
                        f"✅ Successfully processed document for event: {event_type}. "
                        f"Record: {record_id}, Time: {processing_time:.2f}s"
                    )
                    return True
                except Exception as e:
                    error_occurred = True
                    error_msg = f"Failed to process signed URL: {str(e)}"
                    raise Exception(error_msg)
        except IndexingError as e:
            error_occurred = True
            error_msg = f"❌ Indexing error for record {record_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
        except Exception as e:
            error_occurred = True
            error_msg = f"Error processing message {message_id}: {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise Exception(error_msg)
        finally:
            processing_time = (datetime.now() - start_time).total_seconds()
            self.logger.info(
                f"Message {message_id} processing completed in {processing_time:.2f}s. "
                f"Success: {not error_occurred}"
            )

            if error_occurred and record_id:
                await self.__update_document_status(
                    record_id=record_id,
                    indexing_status=ProgressStatus.FAILED.value,
                    extraction_status=ProgressStatus.FAILED.value,
                    reason=error_msg,
                )
                return False


    async def __create_scheduler(self, scheduler_type: str, logger: Logger, config_service: ConfigurationService) -> Scheduler:
        """Create a Redis scheduler instance"""
        redis_config = await config_service.get_config(config_node_constants.REDIS.value)
        if not redis_config or not isinstance(redis_config, dict):
            raise ValueError("Redis configuration not found")
        # Build Redis URL with password if provided
        password = redis_config.get('password', '')
        if password:
            redis_url = f"redis://:{password}@{redis_config['host']}:{redis_config['port']}/{redis_config.get('db', 0)}"
        else:
            redis_url = f"redis://{redis_config['host']}:{redis_config['port']}/{redis_config.get('db', 0)}"
        return SchedulerFactory.scheduler(scheduler_type, redis_url, logger, config_service, delay_hours=1)

    async def __update_document_status(
        self,
        record_id: str,
        indexing_status: str,
        extraction_status: str,
        reason: Optional[str] = None,
    ) -> None:
        """Update document status in Arango"""
        try:
            record = await self.event_processor.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if not record:
                self.logger.error(f"❌ Record {record_id} not found for status update")
                return

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
            await self.event_processor.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )
            self.logger.info(f"✅ Updated document status for record {record_id}")

        except Exception as e:
            self.logger.error(f"❌ Failed to update document status: {str(e)}")

    async def clean_event_handler(self) -> None:
        """Clean up the event handler"""
        # await self.scheduler.stop()
        if self.scheduled_update_task:
            self.scheduled_update_task.cancel()
            try:
                await self.scheduled_update_task
            except asyncio.CancelledError:
                pass
