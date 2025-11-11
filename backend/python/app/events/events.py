import asyncio
import hashlib
import json
from io import BytesIO
from uuid import uuid4

import aiohttp

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EventProcessor:
    def __init__(self, logger, processor, arango_service) -> None:
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

    async def on_event(self, event_data: dict) -> None:
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
            virtual_record_id = event_data.get("virtualRecordId")
            self.logger.info(f"📥 Processing event: {event_type}: for record {record_id} with virtual_record_id {virtual_record_id}")

            if not record_id:
                self.logger.error("❌ No record ID provided in event data")
                return

            record = await self.arango_service.get_document(
                record_id, CollectionNames.RECORDS.value
            )

            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")



            # For both create and update events, we need to process the document
            if event_type == EventTypes.REINDEX_RECORD.value or event_type == EventTypes.UPDATE_RECORD.value:
                # For updates, first delete existing embeddings
                if virtual_record_id is None:
                    raise Exception(f"❌ Virtual record ID not found for record {record_id} for event {event_type}")

                self.logger.info(
                    f"""🔄 Deleting existing embeddings for record {record_id} for event {event_type}"""
                )
                await self.processor.indexing_pipeline.delete_embeddings(record_id, virtual_record_id)

            # Update indexing status to IN_PROGRESS

            doc = dict(record)

            # Extract necessary data
            record_version = event_data.get("version", 0)
            signed_url = event_data.get("signedUrl")
            connector = event_data.get("connectorName", "")
            extension = event_data.get("extension", "unknown")
            mime_type = event_data.get("mimeType", "unknown")
            origin = event_data.get("origin", "CONNECTOR" if connector != "" else "UPLOAD")
            record_type = event_data.get("recordType", "unknown")
            record_name = event_data.get("recordName", f"Untitled-{record_id}")

            if mime_type == "text/gmail_content":
                if virtual_record_id is None:
                    virtual_record_id = str(uuid4())
                self.logger.info("🚀 Processing Gmail Message")
                result = await self.processor.process_gmail_message(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=event_data.get("body"),
                    virtual_record_id = virtual_record_id
                )

                return result

            if signed_url:
                self.logger.debug("Signed URL received")
                file_content = await self._download_from_signed_url(
                    signed_url, record_id, doc
                )
            else:
                file_content = event_data.get("buffer")

            self.logger.debug(f"file_content type: {type(file_content)} length: {len(file_content)}")

            record_type = doc.get("recordType")
            if record_type == RecordTypes.FILE.value:
                try:
                    file = await self.arango_service.get_document(
                        record_id, CollectionNames.FILES.value
                    )
                    file_doc = dict(file)

                    md5_checksum = file_doc.get("md5Checksum")
                    size_in_bytes = file_doc.get("sizeInBytes")
                    if md5_checksum is None:
                        md5_checksum = hashlib.md5(file_content).hexdigest()
                        file_doc.update({"md5Checksum": md5_checksum})
                        self.logger.info(f"🚀 Calculated md5_checksum: {md5_checksum}")
                        await self.arango_service.batch_upsert_nodes([file_doc], CollectionNames.FILES.value)

                    # Add indexingStatus to initial duplicate check to find in-progress files
                    duplicate_files = await self.arango_service.find_duplicate_files(file_doc.get('_key'), md5_checksum, size_in_bytes)
                    duplicate_files = [f for f in duplicate_files if f is not None]
                    if duplicate_files:
                        # Wait and check for processed duplicates
                        for attempt in range(60):
                            processed_duplicate = next(
                                (f for f in duplicate_files if f.get("virtualRecordId")),
                                None
                            )

                            if processed_duplicate:
                                # Use data from processed duplicate
                                doc.update({
                                    "isDirty": False,
                                    "summaryDocumentId": processed_duplicate.get("summaryDocumentId"),
                                    "virtualRecordId": processed_duplicate.get("virtualRecordId"),
                                    "indexingStatus": ProgressStatus.COMPLETED.value,
                                    "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                                    "extractionStatus": ProgressStatus.COMPLETED.value,
                                    "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                                })
                                await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)

                                # Copy all relationships from the processed duplicate to this document
                                await self.arango_service.copy_document_relationships(
                                    processed_duplicate.get("_key"),
                                    doc.get("_key")
                                )
                                return

                            # Check if any duplicate is in progress
                            in_progress = next(
                                (f for f in duplicate_files if f.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value),
                                None
                            )

                            if in_progress:
                                self.logger.info(f"🚀 Duplicate file {in_progress.get('_key')} is being processed, waiting...")
                                self.logger.info(f"Retried {attempt} times")
                                # TODO: Remove this sleep
                                await asyncio.sleep(30)
                                # Refresh duplicate files list
                                duplicate_files = await self.arango_service.find_duplicate_files(
                                    file_doc.get('_key'), md5_checksum, size_in_bytes
                                )
                            else:
                                # No file is being processed, we can proceed
                                break

                        self.logger.info(f"🚀 No processed duplicate found, proceeding with processing for {record_id}")
                    else:
                        self.logger.info(f"🚀 No duplicate files found for record {record_id}")
                        if event_type == EventTypes.UPDATE_RECORD.value:
                            virtual_record_id = str(uuid4())
                except Exception as e:
                    self.logger.error(f"❌ Error in file processing: {repr(e)}")
                    raise

            if virtual_record_id is None:
                virtual_record_id = str(uuid4())

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
                    record_id, record_version, org_id, file_content, virtual_record_id
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
                    record_id, record_version, org_id, file_content, virtual_record_id
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
                    record_id, record_version, org_id, file_content, virtual_record_id
                )
                return result

            if mime_type == MimeTypes.HTML.value:
                result = await self.processor.process_html_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_binary=file_content,
                    virtual_record_id = virtual_record_id,
                )
                return result

            if mime_type == MimeTypes.PLAIN_TEXT.value:
                result = await self.processor.process_txt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    recordType=record_type,
                    connectorName=connector,
                    origin=origin
                )
                return result

            if extension == ExtensionTypes.PDF.value or mime_type == MimeTypes.PDF.value:
                result = await self.processor.process_pdf_with_docling(
                    recordName=record_name,
                    recordId=record_id,
                    pdf_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
                if result is False:
                    result = await self.processor.process_pdf_document(
                        recordName=record_name,
                        recordId=record_id,
                        version=record_version,
                        source=connector,
                        orgId=org_id,
                        pdf_binary=file_content,
                        virtual_record_id = virtual_record_id
                    )

            elif extension == ExtensionTypes.DOCX.value or mime_type == MimeTypes.DOCX.value:
                result = await self.processor.process_docx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=file_content,
                    virtual_record_id = virtual_record_id
                )

            elif extension == ExtensionTypes.DOC.value or mime_type == MimeTypes.DOC.value:
                result = await self.processor.process_doc_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    doc_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
            elif extension == ExtensionTypes.XLSX.value or mime_type == MimeTypes.XLSX.value:
                result = await self.processor.process_excel_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
            elif extension == ExtensionTypes.XLS.value or mime_type == MimeTypes.XLS.value:
                result = await self.processor.process_xls_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    xls_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
            elif extension == ExtensionTypes.CSV.value or mime_type == MimeTypes.CSV.value:
                result = await self.processor.process_csv_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    csv_binary=file_content,
                    virtual_record_id = virtual_record_id,
                    origin=origin,
                )

            elif extension == ExtensionTypes.HTML.value or mime_type == MimeTypes.HTML.value:
                result = await self.processor.process_html_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_binary=file_content,
                    virtual_record_id = virtual_record_id,
                )

            elif extension == ExtensionTypes.PPTX.value or mime_type == MimeTypes.PPTX.value:
                result = await self.processor.process_pptx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content,
                    virtual_record_id = virtual_record_id
                )

            elif extension == ExtensionTypes.PPT.value or mime_type == MimeTypes.PPT.value:
                result = await self.processor.process_ppt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    ppt_binary=file_content,
                    virtual_record_id = virtual_record_id
                )

            elif extension == ExtensionTypes.MD.value or mime_type == MimeTypes.MARKDOWN.value:
                result = await self.processor.process_md_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    md_binary=file_content,
                    virtual_record_id = virtual_record_id
                )

            elif extension == ExtensionTypes.MDX.value or mime_type == MimeTypes.MDX.value:
                result = await self.processor.process_mdx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    mdx_content=file_content,
                    virtual_record_id = virtual_record_id
                )

            elif extension == ExtensionTypes.TXT.value or mime_type == MimeTypes.PLAIN_TEXT.value:
                result = await self.processor.process_txt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    recordType=record_type,
                    connectorName=connector,
                    origin=origin
                )

            elif (
                 extension in {
                    ExtensionTypes.PNG.value,
                    ExtensionTypes.JPG.value,
                    ExtensionTypes.JPEG.value,
                    ExtensionTypes.WEBP.value,
                    # ExtensionTypes.SVG.value,
                    # ExtensionTypes.HEIC.value,
                    # ExtensionTypes.HEIF.value,
                }
                or mime_type in {
                    MimeTypes.PNG.value,
                    MimeTypes.JPG.value,
                    MimeTypes.JPEG.value,
                    MimeTypes.WEBP.value,
                    # MimeTypes.SVG.value,
                    # MimeTypes.HEIC.value,
                    # MimeTypes.HEIF.value,
                }
            ):
                # Route image files to the image processor
                result = await self.processor.process_image(
                    record_id,
                    file_content,
                    virtual_record_id,
                )

            else:
                raise Exception(f"Unsupported file extension: {extension}")

            self.logger.info(
                f"✅ Successfully processed document for record {record_id}"
            )
            return result

        except Exception as e:
            # Let the error bubble up to Kafka consumer
            self.logger.error(f"❌ Error in event processor: {repr(e)}")
            raise
