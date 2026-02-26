import hashlib
import json
from typing import Any, AsyncGenerator, Dict
from uuid import uuid4

import fitz

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
)
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms



class EventProcessor:
    def __init__(self, logger, processor, graph_provider: IGraphDBProvider, config_service: ConfigurationService = None) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.graph_provider = graph_provider
        self.config_service = config_service



    async def mark_record_status(self, doc: dict, status: ProgressStatus) -> None:
        """
        Mark the record status to IN_PROGRESS
        """
        try:
            record_id = doc.get("_key", "unknown")

            doc.update(
                {
                    "indexingStatus": status.value,
                    "extractionStatus": status.value,
                }
            )

            docs = [doc]
            await self.graph_provider.batch_upsert_nodes(
                docs, CollectionNames.RECORDS.value
            )

            self.logger.info(
                f"🔍 Record {record_id}: Successfully updated status to {status.value}"
            )
        except Exception as e:
            self.logger.error(
                f"❌ Record {doc.get('_key', 'unknown')}: Failed to mark record status "
                f"to {status.value}: {repr(e)}"
            )
            if status == ProgressStatus.EMPTY:
                raise Exception(f"Failed to mark record status to EMPTY: {repr(e)}")



    def _normalize_content_for_dedup(self, content: bytes, record_type: str) -> bytes:
        if record_type not in {"SQL_TABLE", "SQL_VIEW", "WEBPAGE", "DATASOURCE", "TICKET", "PROJECT", "COMMENT", "INLINE_COMMENT", "CONFLUENCE_PAGE", "CONFLUENCE_BLOGPOST"}:
            return content
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return content

            # BlocksContainer structure (WEBPAGE, DATASOURCE, TICKET, PROJECT)
            if "block_groups" in parsed or "blocks" in parsed:
                parts: list[str] = []
                for bg in parsed.get("block_groups", []):
                    if bg.get("data") is not None:
                        parts.append(json.dumps(bg["data"], sort_keys=True, default=str))
                for b in parsed.get("blocks", []):
                    if b.get("data") is not None:
                        parts.append(json.dumps(b["data"], sort_keys=True, default=str))
                if parts:
                    return "\n".join(parts).encode("utf-8")
                return content

            return json.dumps(parsed, sort_keys=True, default=str).encode("utf-8")
        except (json.JSONDecodeError, Exception):
            return content

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
            content_for_hash = self._normalize_content_for_dedup(content, record_type)
            md5_checksum = hashlib.md5(content_for_hash).hexdigest()
            doc.update({"md5Checksum": md5_checksum})
            self.logger.info(f"🚀 Calculated md5_checksum: {md5_checksum} for record type: {record_type}")
            await self.graph_provider.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)

        if not md5_checksum:
            return False

        duplicate_records = await self.graph_provider.find_duplicate_records(
            record_key=doc.get('_key'),
            md5_checksum=md5_checksum,
            record_type=record_type,
            size_in_bytes=size_in_bytes
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
            await self.graph_provider.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
            # Copy all relationships from the processed duplicate to this document
            await self.graph_provider.copy_document_relationships(
                processed_duplicate.get("_key") or processed_duplicate.get("id"),
                doc.get("_key") or doc.get("id")
            )
            self.logger.info(f"✅ Duplicate record {processed_duplicate.get('_key')} returning TRUE")
            return True  # Duplicate handled

        # Check if any duplicate is in progress
        in_progress = next(
            (r for r in duplicate_records if r.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value),
            None
        )

        if in_progress:
            self.logger.info(f"🚀 Duplicate record {in_progress.get('_key')} is being processed, changing status to QUEUED.")

            doc.update({
                "indexingStatus": ProgressStatus.QUEUED.value,
            })
            await self.graph_provider.batch_upsert_nodes([doc], CollectionNames.RECORDS.value)
            return True  # Marked as queued

        self.logger.info(f"🚀 No duplicate found, proceeding with processing for {doc.get('_key')}")
        return False  # No duplicate found, proceed with processing

    async def on_event(self, event_data: dict) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process events received from Kafka consumer, yielding phase completion events.

        Args:
            event_data: Dictionary containing:
                - event_type: Type of event (create, update, delete)
                - record_id: ID of the record
                - record_version: Version of the record
                - signed_url: Signed URL to download the file
                - connector_name: Name of the connector
                - metadata_route: Route to get metadata
                
        Yields:
            Dict with 'event' key:
            - {'event': 'parsing_complete', 'data': {...}}
            - {'event': 'indexing_complete', 'data': {...}}
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

            record = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )


            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")

            doc = dict(record)

            # Extract necessary data
            record_version = event_data.get("version", 0)
            connector = event_data.get("connectorName", "")
            extension = event_data.get("extension", "unknown")
            mime_type = event_data.get("mimeType", "unknown")
            origin = event_data.get("origin", "CONNECTOR" if connector != "" else "UPLOAD")
            record_name = event_data.get("recordName", f"Untitled-{record_id}")

            file_content = event_data.get("buffer")

            # Debug: log buffer used for MD5 (to trace why Google Doc copies get different MD5)
            content_len = len(file_content) if file_content else 0
            doc_md5_from_connector = doc.get("md5Checksum")
            self.logger.info(
                f"🔍 [DEBUG] file_content for MD5: type={type(file_content).__name__} len={content_len} "
                f"doc.md5Checksum(from connector)={doc_md5_from_connector}"
            )
            if file_content and content_len > 0:
                content_bytes = file_content.encode("utf-8") if isinstance(file_content, str) else file_content
                computed_md5 = hashlib.md5(content_bytes).hexdigest()
                self.logger.info(f"🔍 [DEBUG] MD5 computed from buffer: {computed_md5}")
            self.logger.debug(f"file_content type: {type(file_content)} length: {content_len}")

            record_type = doc.get("recordType")

            # Calculate MD5 hash and check for duplicates for ALL record types
            try:
                if await self._check_duplicate_by_md5(file_content, doc):
                    self.logger.info("Duplicate record detected, skipping processing")
                    yield {"event": "parsing_complete", "data": {"record_id": record_id}}
                    yield {"event": "indexing_complete", "data": {"record_id": record_id}}
                    return
            except Exception as e:
                self.logger.error(f"❌ Error in MD5/duplicate processing: {repr(e)}")
                raise

            await self.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

            prev_virtual_record_id = None  # Track previous vrid for reconciliation

            if event_type == EventTypes.UPDATE_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value :
                # For reconciliation-enabled types, decide whether to keep or generate new vrid
                from app.config.constants.arangodb import (
                    RECONCILIATION_ENABLED_EXTENSIONS,
                    RECONCILIATION_ENABLED_MIME_TYPES,
                )
                is_reconciliation_type = (
                    mime_type in RECONCILIATION_ENABLED_MIME_TYPES
                    or extension in RECONCILIATION_ENABLED_EXTENSIONS
                )
                if is_reconciliation_type:
                    prev_virtual_record_id = virtual_record_id
                    if prev_virtual_record_id:
                        # Check how many records share this vrid
                        records_with_vrid = await self.graph_provider.get_records_by_virtual_record_id(
                            prev_virtual_record_id
                        )
                        if len(records_with_vrid) > 1:
                            # N:1 case: multiple records share this vrid, isolate with new vrid
                            virtual_record_id = str(uuid4())
                            self.logger.info(
                                f"📊 Multiple records ({len(records_with_vrid)}) share vrid {prev_virtual_record_id}, "
                                f"generated new vrid: {virtual_record_id}"
                            )
                        else:
                            # 1:1 case: only this record uses the vrid, keep for diff-based reconciliation
                            self.logger.info(
                                f"📊 Keeping existing virtual_record_id for reconciliation: {virtual_record_id}"
                            )
                    else:
                        # No existing vrid, treat as new record
                        self.logger.info("📊 No existing virtual_record_id for reconciliation type, treating as new")
                else:
                    virtual_record_id = str(uuid4())

            if virtual_record_id is None:
                virtual_record_id = str(uuid4())

            # Set prev_virtual_record_id on processor for pipeline reconciliation context
            self.processor._prev_virtual_record_id = prev_virtual_record_id

            if mime_type == MimeTypes.GOOGLE_SLIDES.value:
                self.logger.info("🚀 Processing Google Slides")
                async for event in self.processor.process_pptx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.GOOGLE_DOCS.value:
                self.logger.info("🚀 Processing Google Docs")
                async for event in self.processor.process_docx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.GOOGLE_SHEETS.value:
                self.logger.info("🚀 Processing Google Sheets")
                async for event in self.processor.process_excel_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.HTML.value:
                async for event in self.processor.process_html_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.PLAIN_TEXT.value:
                async for event in self.processor.process_txt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    recordType=record_type,
                    connectorName=connector,
                    origin=origin,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.BLOCKS.value:
                self.logger.info("🚀 Processing Blocks Container")
                async for event in self.processor.process_blocks(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    blocks_data=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

            if mime_type == MimeTypes.GMAIL.value:
                async for event in self.processor.process_gmail_message(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_content=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event
                return

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
                    async for event in self.processor.process_pdf_document_with_ocr(
                        recordName=record_name,
                        recordId=record_id,
                        version=record_version,
                        source=connector,
                        orgId=org_id,
                        pdf_binary=file_content,
                        virtual_record_id=virtual_record_id,
                        event_type=event_type
                    ):
                        yield event
                else:
                    # Use docling for PDFs that don't need OCR
                    docling_failed = False
                    async for event in self.processor.process_pdf_with_docling(
                        recordName=record_name,
                        recordId=record_id,
                        pdf_binary=file_content,
                        virtual_record_id=virtual_record_id,
                        event_type=event_type
                    ):
                        if event.get("event") == "docling_failed":
                            docling_failed = True
                        else:
                            yield event

                    if docling_failed:
                        async for event in self.processor.process_pdf_document_with_ocr(
                            recordName=record_name,
                            recordId=record_id,
                            version=record_version,
                            source=connector,
                            orgId=org_id,
                            pdf_binary=file_content,
                            virtual_record_id=virtual_record_id,
                            event_type=event_type
                        ):
                            yield event

            elif extension == ExtensionTypes.DOCX.value or mime_type == MimeTypes.DOCX.value:
                async for event in self.processor.process_docx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    docx_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.DOC.value or mime_type == MimeTypes.DOC.value:
                async for event in self.processor.process_doc_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    doc_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.XLSX.value or mime_type == MimeTypes.XLSX.value:
                async for event in self.processor.process_excel_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    excel_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.XLS.value or mime_type == MimeTypes.XLS.value:
                async for event in self.processor.process_xls_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    xls_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.CSV.value or mime_type == MimeTypes.CSV.value:
                async for event in self.processor.process_delimited_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.TSV.value or mime_type == MimeTypes.TSV.value:
                async for event in self.processor.process_delimited_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    extension=ExtensionTypes.TSV.value,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.HTML.value or mime_type == MimeTypes.HTML.value:
                async for event in self.processor.process_html_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    html_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.PPTX.value or mime_type == MimeTypes.PPTX.value:
                async for event in self.processor.process_pptx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    pptx_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.PPT.value or mime_type == MimeTypes.PPT.value:
                async for event in self.processor.process_ppt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    ppt_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.MD.value or mime_type == MimeTypes.MARKDOWN.value:
                async for event in self.processor.process_md_document(
                    recordName=record_name,
                    recordId=record_id,
                    md_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.MDX.value or mime_type == MimeTypes.MDX.value:
                async for event in self.processor.process_mdx_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    mdx_content=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            elif extension == ExtensionTypes.TXT.value or mime_type == MimeTypes.PLAIN_TEXT.value:
                async for event in self.processor.process_txt_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    txt_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    recordType=record_type,
                    connectorName=connector,
                    origin=origin,
                    event_type=event_type
                ):
                    yield event

            elif mime_type == MimeTypes.SQL_TABLE.value or extension == ExtensionTypes.SQL_TABLE.value:
                self.logger.info(f"🚀 Processing SQL Table: {record_name}")
                async for event in self.processor.process_sql_structured_data(
                    recordName=record_name,
                    recordId=record_id,
                    json_content=file_content,
                    virtual_record_id=virtual_record_id,
                    record_type="SQL_TABLE",
                    event_type=event_type,
                ):
                    yield event

            elif mime_type == MimeTypes.SQL_VIEW.value or extension == ExtensionTypes.SQL_VIEW.value:
                self.logger.info(f"🚀 Processing SQL View: {record_name}")
                async for event in self.processor.process_sql_structured_data(
                    recordName=record_name,
                    recordId=record_id,
                    json_content=file_content,
                    virtual_record_id=virtual_record_id,
                    record_type="SQL_VIEW",
                    event_type=event_type,
                ):
                    yield event

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
                async for event in self.processor.process_image(
                    record_id,
                    file_content,
                    virtual_record_id,
                    event_type=event_type
                ):
                    yield event

            else:
                raise Exception(f"Unsupported file extension: {extension}")

            self.logger.info(
                f"✅ Successfully processed document for record {record_id}"
            )

        except Exception as e:
            # Let the error bubble up to Kafka consumer
            self.logger.error(f"❌ Error in event processor: {repr(e)}")
            raise

