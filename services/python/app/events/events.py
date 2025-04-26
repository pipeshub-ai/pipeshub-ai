import asyncio
import json
from io import BytesIO

import aiohttp

from app.config.utils.named_constants.arangodb_constants import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)


class EventProcessor:
    def __init__(self, logger, processor, arango_service):
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.arango_service = arango_service

    async def _download_from_signed_url(
        self, signed_url: str, record_id: str, doc: dict
    ) -> bytes:
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
                            if response.status != 200:
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
                    doc.update(
                        {
                            "indexingStatus": ProgressStatus.FAILED.value,
                            "extractionStatus": ProgressStatus.FAILED.value,
                            "reason": (
                                f"Download failed after {max_retries} attempts. "
                                f"Error: {error_type} - {str(e)}. File id: {record_id}"
                            ),
                        }
                    )
                    await self.arango_service.batch_upsert_nodes(
                        [doc], CollectionNames.RECORDS.value
                    )
                    raise Exception(
                        f"Download failed after {max_retries} attempts. "
                        f"Error: {error_type} - {str(e)}. File id: {record_id}"
                    )

                await asyncio.sleep(delay)

            finally:
                if not file_buffer.closed:
                    file_buffer.close()

    async def on_event(self, event_data: dict):
        """
        Process events received from Kafka consumer
        Args:
            event_data: Dictionary containing:
                - event_type: Type of event (create, update, delete)
                - record_id: ID of the record
                - record_version: Version of the record
                - signed_url: Signed URL to download the file
                - connector_name: Name of the connector
                - metadata_route: Route to get metadata
        """
        try:
            # Extract event type and record ID
            event_type = event_data.get(
                "eventType", EventTypes.NEW_RECORD.value
            )  # default to create
            event_data = event_data.get("payload")
            record_id = event_data.get("recordId")
            org_id = event_data.get("orgId")

            self.logger.info(f"📥 Processing event: {event_type}: {record_id}")

            if not record_id:
                self.logger.error("❌ No record ID provided in event data")
                return

            # Handle delete event
            if event_type == EventTypes.DELETE_RECORD.value:
                self.logger.info(f"🗑️ Deleting embeddings for record {record_id}")
                await self.processor.indexing_pipeline.delete_embeddings(record_id)
                return

            # For both create and update events, we need to process the document
            if event_type == EventTypes.UPDATE_RECORD.value:
                # For updates, first delete existing embeddings
                self.logger.info(
                    f"""🔄 Updating record {record_id} - deleting existing embeddings"""
                )
                await self.processor.indexing_pipeline.delete_embeddings(record_id)

            # Update indexing status to IN_PROGRESS
            record = await self.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if record is None:
                self.logger.error(f"❌ Record {record_id} not found in database")
                return
            doc = dict(record)

            # Update with new metadata fields
            doc.update(
                {
                    "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                    "extractionStatus": ProgressStatus.IN_PROGRESS.value,
                }
            )

            docs = [doc]
            await self.arango_service.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

            # Extract necessary data
            record_version = event_data.get("version", 0)
            signed_url = event_data.get("signedUrl")
            connector = event_data.get("connectorName", "")
            extension = event_data.get("extension", "unknown")
            mime_type = event_data.get("mimeType", "unknown")

            if extension is None and mime_type != "text/gmail_content":
                extension = event_data["recordName"].split(".")[-1]

            self.logger.info("🚀 Checking for mime_type")
            self.logger.info("🚀 mime_type: %s", mime_type)
            self.logger.info("🚀 extension: %s", extension)

            supported_mime_types = [
                MimeTypes.GMAIL.value,
                MimeTypes.GOOGLE_SLIDES.value,
                MimeTypes.GOOGLE_DOCS.value,
                MimeTypes.GOOGLE_SHEETS.value,
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
                ExtensionTypes.TXT.value,
            ]

            if (
                mime_type not in supported_mime_types
                and extension not in supported_extensions
            ):
                self.logger.info(
                    f"🔴🔴🔴 Unsupported file: Mime Type: {mime_type}, Extension: {extension} 🔴🔴🔴"
                )
                doc = docs[0]
                doc.update(
                    {
                        "indexingStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                        "extractionStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    }
                )
                docs = [doc]
                await self.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )

                return

            if mime_type == "text/gmail_content":
                self.logger.info("🚀 Processing Gmail Message")
                result = await self.processor.process_gmail_message(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=event_data.get("body"),
                )

                return result

            if signed_url:
                self.logger.debug("Signed URL received")
                file_content = await self._download_from_signed_url(
                    signed_url, record_id, doc
                )
            else:
                file_content = event_data.get("buffer")

            self.logger.debug(f"file_content type: {type(file_content)}")

            if mime_type == MimeTypes.GOOGLE_SLIDES.value:
                self.logger.info("🚀 Processing Google Slides")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"Failed to decode Google Slides content: {str(e)}"
                        )
                        raise
                result = await self.processor.process_google_slides(
                    record_id, record_version, org_id, file_content
                )
                return result

            if mime_type == MimeTypes.GOOGLE_DOCS.value:
                self.logger.info("🚀 Processing Google Docs")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"Failed to decode Google Docs content: {str(e)}"
                        )
                        raise
                result = await self.processor.process_google_docs(
                    record_id, record_version, org_id, file_content
                )
                return result

            if mime_type == MimeTypes.GOOGLE_SHEETS.value:
                self.logger.info("🚀 Processing Google Sheets")
                # Decode JSON content if it's streamed data
                if isinstance(file_content, bytes):
                    try:
                        file_content = json.loads(file_content.decode("utf-8"))
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"Failed to decode Google Sheets content: {str(e)}"
                        )
                        raise
                result = await self.processor.process_google_sheets(
                    record_id, record_version, org_id, file_content
                )
                return result

            if extension == ExtensionTypes.PDF.value:
                result = await self.processor.process_pdf_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pdf_binary=file_content,
                )

            elif extension == ExtensionTypes.DOCX.value:
                result = await self.processor.process_docx_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=BytesIO(file_content),
                )

            elif extension == ExtensionTypes.DOC.value:
                result = await self.processor.process_doc_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    doc_binary=file_content,
                )
            elif extension == ExtensionTypes.XLSX.value:
                result = await self.processor.process_excel_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content,
                )
            elif extension == ExtensionTypes.XLS.value:
                result = await self.processor.process_xls_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    xls_binary=file_content,
                )
            elif extension == ExtensionTypes.CSV.value:
                result = await self.processor.process_csv_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    csv_binary=file_content,
                )

            elif extension == ExtensionTypes.HTML.value:
                result = await self.processor.process_html_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=file_content,
                )

            elif extension == ExtensionTypes.PPTX.value:
                result = await self.processor.process_pptx_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content,
                )

            elif extension == ExtensionTypes.PPT.value:
                result = await self.processor.process_ppt_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    ppt_binary=file_content,
                )

            elif extension == ExtensionTypes.MD.value:
                result = await self.processor.process_md_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    md_binary=file_content,
                )
            elif extension == ExtensionTypes.TXT.value:
                result = await self.processor.process_txt_document(
                    recordName=f"Record-{record_id}",
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content,
                )

            else:
                self.logger.info(
                    f"""🔴🔴🔴 Unsupported file extension: {
                               extension} 🔴🔴🔴"""
                )
                doc = docs[0]
                doc.update(
                    {
                        "indexingStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                        "extractionStatus": ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
                    }
                )
                docs = [doc]
                await self.arango_service.batch_upsert_nodes(
                    docs, CollectionNames.RECORDS.value
                )

                return

            self.logger.info(
                f"✅ Successfully processed document for record {record_id}"
            )
            return result

        except Exception as e:
            # Let the error bubble up to Kafka consumer
            self.logger.error(f"❌ Error in event processor: {repr(e)}")
            raise
