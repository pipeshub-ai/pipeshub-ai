import asyncio
import hashlib
from io import BytesIO
from uuid import uuid4

import aiohttp
import fitz

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.utils.jwt import generate_jwt
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EventProcessor:
    def __init__(self, logger, processor, arango_service, config_service: ConfigurationService = None) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.arango_service = arango_service
        self.config_service = config_service



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

        # Generate JWT token for authentication if config_service is available
        headers = {}
        if self.config_service:
            try:
                org_id = doc.get("orgId")
                if org_id:
                    jwt_payload = {
                        "orgId": org_id,
                        "scopes": ["connector:signedUrl"],
                    }
                    jwt_token = await generate_jwt(self.config_service, jwt_payload)
                    headers["Authorization"] = f"Bearer {jwt_token}"
                    self.logger.debug(f"Generated JWT token for downloading signed URL for record {record_id}")
            except Exception as e:
                self.logger.warning(f"Failed to generate JWT token for signed URL download: {e}")

        for attempt in range(max_retries):
            delay = base_delay * (2**attempt)  # Exponential backoff
            file_buffer = BytesIO()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    try:
                        async with session.get(signed_url, headers=headers) as response:
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

    async def _check_duplicate_by_md5(
        self,
        content: bytes | str,
        doc: dict,
    ) -> bool:
        """
        Check for duplicate records by MD5 hash and handle accordingly.

        Args:
            content: The content to hash (bytes or string)
            doc: The document dictionary to update

        Returns:
            True if duplicate was found and handled (caller should return early)
            False if no duplicate found (caller should proceed with processing)
        """
        # Calculate MD5 from content
        md5_checksum = doc.get("md5Checksum")
        size_in_bytes = doc.get("sizeInBytes")
        record_type = doc.get("recordType")

        if md5_checksum is None and content:
            if isinstance(content, str):
                content = content.encode('utf-8')
            md5_checksum = hashlib.md5(content).hexdigest()
            doc.update({"md5Checksum": md5_checksum})
            self.logger.info(f"🚀 Calculated md5_checksum: {md5_checksum} for record type: {record_type}")
            await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)

        if not md5_checksum:
            return False

        duplicate_records = await self.arango_service.find_duplicate_records(
            doc.get('_key'),
            md5_checksum,
            record_type,
            size_in_bytes
        )

        duplicate_records = [r for r in duplicate_records if r is not None]

        if not duplicate_records:
            self.logger.info(f"🚀 No duplicate records found for record {doc.get('_key')}")
            return False

        # Check for processed or in-progress duplicates
        processed_duplicate = next(
            (r for r in duplicate_records
                if (r.get("virtualRecordId") and r.get("indexingStatus") == ProgressStatus.COMPLETED.value)
                or (r.get("indexingStatus") == ProgressStatus.EMPTY.value)),
            None
        )

        if processed_duplicate:
            # Use data from processed duplicate
            doc.update({
                "isDirty": False,
                "summaryDocumentId": processed_duplicate.get("summaryDocumentId"),
                "virtualRecordId": processed_duplicate.get("virtualRecordId"),
                "indexingStatus": processed_duplicate.get("indexingStatus"),
                "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                "extractionStatus": processed_duplicate.get("extractionStatus"),
                "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
            })
            await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)

            # Copy all relationships from the processed duplicate to this document
            await self.arango_service.copy_document_relationships(
                processed_duplicate.get("_key"),
                doc.get("_key")
            )
            return True  # Duplicate handled

        # Check if any duplicate is in progress
        in_progress = next(
            (r for r in duplicate_records if r.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value),
            None
        )

        # TODO: handle race condition here
        if in_progress:
            self.logger.info(f"🚀 Duplicate record {in_progress.get('_key')} is being processed, changing status to QUEUED.")

            doc.update({
                "indexingStatus": ProgressStatus.QUEUED.value,
            })
            await self.arango_service.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
            return True  # Marked as queued

        self.logger.info(f"🚀 No duplicate found, proceeding with processing for {doc.get('_key')}")
        return False  # No duplicate found, proceed with processing

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
            record_name = event_data.get("recordName", f"Untitled-{record_id}")

            if mime_type == "text/gmail_content":
                if virtual_record_id is None:
                    virtual_record_id = str(uuid4())

                # MD5 deduplication for Gmail messages
                html_content = event_data.get("body")
                if html_content:
                    try:
                        if await self._check_duplicate_by_md5(html_content, doc):
                            self.logger.info("Duplicate Gmail message detected, skipping processing")
                            return
                        
                    except Exception as e:
                        self.logger.error(f"❌ Error in Gmail MD5/duplicate processing: {repr(e)}")
                        raise

                if event_type == EventTypes.UPDATE_RECORD.value:
                    virtual_record_id = str(uuid4())

                self.logger.info("🚀 Processing Gmail Message")
                result = await self.processor.process_gmail_message(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=html_content,
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

            # Calculate MD5 hash and check for duplicates for ALL record types
            try:
                if await self._check_duplicate_by_md5(file_content, doc):
                    self.logger.info("Duplicate record detected, skipping processing")
                    return
            except Exception as e:
                self.logger.error(f"❌ Error in MD5/duplicate processing: {repr(e)}")
                raise

            if event_type == EventTypes.UPDATE_RECORD.value:
                virtual_record_id = str(uuid4())

            if virtual_record_id is None:
                virtual_record_id = str(uuid4())

            if mime_type == MimeTypes.GOOGLE_SLIDES.value:
                self.logger.info("🚀 Processing Google Slides")
                result = await self.processor.process_pptx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
                return result

            if mime_type == MimeTypes.GOOGLE_DOCS.value:
                self.logger.info("🚀 Processing Google Docs")
                result = await self.processor.process_docx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=file_content,
                    virtual_record_id = virtual_record_id
                )
                return result

            if mime_type == MimeTypes.GOOGLE_SHEETS.value:
                self.logger.info("🚀 Processing Google Sheets")
                result = await self.processor.process_excel_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content,
                    virtual_record_id = virtual_record_id
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
                # Check if document needs OCR before using docling

                self.logger.info("🔍 Checking if PDF needs OCR processing")
                try:
                    with fitz.open(stream=file_content, filetype="pdf") as temp_doc:

                        # Check if 50% or more pages need OCR
                        ocr_pages = [OCRStrategy.needs_ocr(page, self.logger) for page in temp_doc]
                        needs_ocr = sum(ocr_pages) >= len(ocr_pages) * 0.5 if ocr_pages else False

                    self.logger.info(f"📊 OCR requirement: {'YES - Using OCR handler' if needs_ocr else 'NO - Using Docling'}")
                except Exception as e:
                    self.logger.warning(f"⚠️ Error checking OCR need: {str(e)}, defaulting to Docling")
                    needs_ocr = False

                if needs_ocr:
                    # Skip docling and use OCR handler directly
                    self.logger.info("🤖 PDF needs OCR, skipping Docling")
                    result = await self.processor.process_pdf_document_with_ocr(
                        recordName=record_name,
                        recordId=record_id,
                        version=record_version,
                        source=connector,
                        orgId=org_id,
                        pdf_binary=file_content,
                        virtual_record_id = virtual_record_id
                    )
                else:
                    # Use docling for PDFs that don't need OCR
                    result = await self.processor.process_pdf_with_docling(
                        recordName=record_name,
                        recordId=record_id,
                        pdf_binary=file_content,
                        virtual_record_id = virtual_record_id
                    )
                    if result is False:
                        result = await self.processor.process_pdf_document_with_ocr(
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
                    ExtensionTypes.SVG.value,
                }
                or mime_type in {
                    MimeTypes.PNG.value,
                    MimeTypes.JPG.value,
                    MimeTypes.JPEG.value,
                    MimeTypes.WEBP.value,
                    MimeTypes.SVG.value,
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

