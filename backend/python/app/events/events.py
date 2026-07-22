import asyncio
import atexit
import hashlib
import logging
import math
import multiprocessing
import os
from collections.abc import AsyncGenerator
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from typing import Any
import json
from uuid import uuid4

from app.services.parsing.interface import ParserProvider
from app.modules.transformers.pipeline import IndexingPipeline
from bs4 import BeautifulSoup

from io import BytesIO

import pdfplumber

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
    CODE_FILE_EXTENSION_VALUES,
    CODE_FILE_MIME_TYPE_VALUES,
    normalize_file_extension,
)
from app.events.processor import Processor
from app.modules.parsers.pdf.ocr_handler import OCRStrategy
from app.services.messaging.config import IndexingEvent, PipelineEvent, PipelineEventData
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def _get_pdf_ocr_detection_worker_count() -> int:
    raw_value = os.getenv("PDF_OCR_DETECTION_WORKERS")
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            return 1

    return 1

PDF_OCR_DETECTION_WORKERS = _get_pdf_ocr_detection_worker_count()


@lru_cache(maxsize=1)
def _get_pdf_ocr_detection_pool() -> ProcessPoolExecutor:
    pool = ProcessPoolExecutor(
        max_workers=PDF_OCR_DETECTION_WORKERS,
        mp_context=multiprocessing.get_context("spawn"),
    )
    # Ensure spawned processes are reaped when the interpreter exits.
    # The explicit shutdown_pdf_ocr_pool() call in indexing_main.lifespan is
    # the primary cleanup path; atexit is the safety net for unclean exits.
    atexit.register(pool.shutdown, wait=False, cancel_futures=True)
    return pool


def _record_key(doc: dict[str, Any] | None) -> str | None:
    """Record id from either Arango (_key) or generic graph (id) document format."""
    return (doc.get("_key") or doc.get("id")) if doc is not None else None


def shutdown_pdf_ocr_pool() -> bool:
    """Shut down the PDF OCR detection process pool if it was initialised.

    Returns True if a pool existed and was shut down, False if no pool had
    been created during this process's lifetime (so there was nothing to
    clean up). Safe to call multiple times.
    """
    if _get_pdf_ocr_detection_pool.cache_info().currsize == 0:
        return False
    _get_pdf_ocr_detection_pool().shutdown(wait=False, cancel_futures=True)
    _get_pdf_ocr_detection_pool.cache_clear()
    return True


def _detect_pdf_needs_ocr(file_content: bytes) -> bool:
    logger = logging.getLogger(__name__)

    with pdfplumber.open(BytesIO(file_content)) as pdf:
        page_count = len(pdf.pages)
        if page_count == 0:
            return False

        threshold = math.ceil(page_count * 0.5)
        ocr_pages = 0

        for page_index, page in enumerate(pdf.pages):
            if OCRStrategy.needs_ocr(page, logger):
                ocr_pages += 1
                if ocr_pages >= threshold:
                    return True

            remaining_pages = page_count - (page_index + 1)
            if ocr_pages + remaining_pages < threshold:
                return False

        return ocr_pages >= threshold

class EventProcessor:
    def __init__(
        self,
        logger: logging.Logger,
        processor: Processor,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService | None = None,
        parsing_client=None,
        extraction_client=None,
        sink_orchestrator=None,
    ) -> None:
        self.logger = logger
        self.logger.info("🚀 Initializing EventProcessor")
        self.processor = processor
        self.graph_provider = graph_provider
        self.config_service = config_service
        # Optional HTTP service clients (used when USE_PARSING_SERVICE=true)
        self.parsing_client = parsing_client
        self.extraction_client = extraction_client
        self.sink_orchestrator = sink_orchestrator

    async def _pdf_needs_ocr(self, file_content: bytes) -> bool:
        if PDF_OCR_DETECTION_WORKERS <= 1:
            return await asyncio.to_thread(_detect_pdf_needs_ocr, file_content)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _get_pdf_ocr_detection_pool(),
            _detect_pdf_needs_ocr,
            file_content,
        )



    def _use_service_pipeline(self) -> bool:
        """Return True when the new HTTP service pipeline should be used."""
        return (
            self.parsing_client is not None
            and self.extraction_client is not None
            and self.sink_orchestrator is not None
            and os.environ.get("USE_PARSING_SERVICE", "false").lower() == "true"
        )

    async def _orchestrate_via_services(
        self,
        record_id: str,
        org_id: str,
        virtual_record_id: str,
        record_name: str,
        mime_type: str,
        extension: str,
        event_type: str,
        prev_virtual_record_id: str | None,
        file_content: bytes,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """New orchestration path: parse → index → enrich via standalone services.

        1. POST /api/v1/parse → BlocksContainer (Parsing Service, port 8092)
        2. VectorStore + BlobStorage via SinkOrchestrator.index()
        3. POST /api/v1/extract/classify → SemanticMetadata (Extraction Service, port 8093)
        4. GraphDB via SinkOrchestrator.enrich()

        Documents are searchable after step 2 regardless of step 3/4 outcome.
        """
        from app.events.processor import convert_record_dict_to_record  # noqa: PLC0415
        from app.modules.transformers.transformer import TransformContext  # noqa: PLC0415

        # ── Step 1: Parse ────────────────────────────────────────────────────
        # Advisory only: health probes can race a service startup/restart. The
        # actual parse call owns retry and circuit-breaker behavior.
        if not await self.parsing_client.health_check_cached():
            self.logger.warning(
                "ParsingService pre-flight health check failed; proceeding with "
                "the resilient parse request"
            )

        self.logger.info(
            "📤 Sending '%s' to Parsing Service (mime=%s ext=%s)", record_name, mime_type, extension
        )
        provider = ParserProvider(os.getenv("PARSER_BACKEND") or ParserProvider.DEFAULT.value)
        parse_result = await self.parsing_client.parse(
            file_content=file_content,
            record_name=record_name,
            mime_type=mime_type,
            extension=extension,
            org_id=org_id,
            provider=provider,
        )
        block_container = parse_result.block_container
        self.logger.info(
            "✅ Parsing complete via provider '%s' (%d blocks)",
            parse_result.provider_used.value if parse_result.provider_used else "unknown",
            len(block_container.blocks),
        )
        yield PipelineEvent(
            event=IndexingEvent.PARSING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

        if not block_container.blocks and not block_container.block_groups:
            self.logger.info(
                "⚠️ Empty document for %s — marking EMPTY", record_id
            )
            record_doc = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value
            )
            if record_doc:
                record_doc.update({
                    "indexingStatus": ProgressStatus.EMPTY.value,
                    "extractionStatus": ProgressStatus.NOT_STARTED.value,
                    "isDirty": False,
                })
                await self.graph_provider.batch_upsert_nodes(
                    [record_doc], CollectionNames.RECORDS.value
                )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )
            return

        # ── Build Record + TransformContext ─────────────────────────────────
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is None:
            self.logger.warning(f"❌ Record {record_id} not found after parsing")
            return

        record = convert_record_dict_to_record(record_doc)
        record.block_containers = block_container
        record.virtual_record_id = virtual_record_id
        record.org_id = org_id

        ctx = TransformContext(
            record=record,
            event_type=event_type,
            prev_virtual_record_id=prev_virtual_record_id,
        )

        ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
            ctx, self.logger, self.sink_orchestrator
        )

        # ── Step 2: Index (VectorStore + BlobStorage) ────────────────────────
        self.logger.info("📥 Indexing record %s (making searchable)", record_id)
        await self.sink_orchestrator.index(ctx)
        self.logger.info("✅ Record %s is now searchable (indexingStatus=COMPLETED)", record_id)

        # ── Step 3: Enrich (Extraction Service → GraphDB) ────────────────────
        defer_extraction = (
            ctx.settings.get("defer_extraction")
            or os.environ.get("DEFER_EXTRACTION", "false").lower() == "true"
        )
        if defer_extraction:
            self.logger.info(
                "📨 Deferring graph enrichment for record %s", record_id
            )
        else:
            try:
                departments = await self.graph_provider.get_departments(org_id)
                semantic_metadata = await self.extraction_client.classify(
                    block_container=block_container,
                    org_id=org_id,
                    departments=departments or [],
                )

                record.semantic_metadata = semantic_metadata
                if semantic_metadata and (semantic_metadata.summary or "").strip():
                    await self.sink_orchestrator.vector_store.index_record_summary(
                        record_id,
                        virtual_record_id,
                        org_id,
                        semantic_metadata,
                    )

                if semantic_metadata:
                    await self.sink_orchestrator.blob_storage.apply(ctx)

                await self.sink_orchestrator.enrich(ctx)
                self.logger.info(
                    "✅ Graph enrichment completed for record %s", record_id
                )
            except Exception as enrich_exc:
                self.logger.error(
                    "❌ Enrichment failed for record %s (document remains searchable): %s",
                    record_id,
                    enrich_exc,
                )

        yield PipelineEvent(
            event=IndexingEvent.INDEXING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

    async def mark_record_status(self, doc: dict[str, Any], status: ProgressStatus) -> None:
        """
        Mark the record status to IN_PROGRESS
        """
        try:
            record_id = _record_key(doc) or "unknown"

            doc.update(
                {
                    "indexingStatus": status.value,
                    "extractionStatus": status.value,
                }
            )

            docs = [doc]
            success = await self.graph_provider.batch_update_nodes(
                docs, CollectionNames.RECORDS.value
            )
            if not success:
                self.logger.warning(
                    "⚠️ Failed to update record %s status to %s - record may not exist",
                    record_id, status.value
                )
                return

            self.logger.debug(
                f"🔍 Record {record_id}: Successfully updated status to {status.value}"
            )
        except Exception as e:
            self.logger.error(
                f"❌ Record {_record_key(doc) or 'unknown'}: Failed to mark record status "
                f"to {status.value}: {repr(e)}"
            )
            if status == ProgressStatus.EMPTY:
                raise Exception(f"Failed to mark record status to EMPTY: {repr(e)}") from e



    def _normalize_content_for_dedup(
        self,
        content: bytes,
        record_type: str | None = None,
        mime_type: str | None = None,
    ) -> bytes:

        normalized_mime_type = (mime_type or "").lower()
        should_normalize_html = (
            "html" in normalized_mime_type
            or record_type in {"CONFLUENCE_PAGE", "CONFLUENCE_BLOGPOST", "COMMENT", "INLINE_COMMENT"}
        )

        if should_normalize_html:
            try: 
                # self.logger.info(f"🔍 Normalizing HTML for dedup: {content}")
                html_str = content.decode("utf-8") if isinstance(content, bytes) else content
                # self.logger.info(f"🔍 HTML string: {html_str}")
                soup = BeautifulSoup(html_str, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                for tag in soup.find_all(True):
                    tag.attrs.pop("local-id", None)
                    tag.attrs.pop("id", None)
                    tag.attrs.pop("data-emoji-id", None)
                    tag.attrs.pop("data-emoji-fallback", None)
                text = soup.get_text(separator="\n", strip=True)
                if text:
                    return text.encode("utf-8")
                # self.info(f"🔍 HTML normalized for dedup: {text}")  
                return content    
            except Exception as e:
                self.logger.error(f"❌ Error normalizing HTML for dedup: {repr(e)}")
                return content
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                return content

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
        content: bytes | str | dict | list | None,
        doc: dict[str, Any],
    ) -> bool:
        """Check for duplicate records by MD5 hash and handle accordingly.

        Returns True if a duplicate was found and handled (caller should skip),
        False otherwise.
        """
        # Calculate MD5 from content
        existing_md5_checksum = doc.get("md5Checksum")
        size_in_bytes = doc.get("sizeInBytes")
        record_type = doc.get("recordType")
        mime_type = doc.get("mimeType")
        md5_checksum = None

        if content:
            if isinstance(content, (dict, list)):
                content = json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')
            elif isinstance(content, str):
                content = content.encode('utf-8')
            content_for_hash = self._normalize_content_for_dedup(content=content, record_type=record_type, mime_type=mime_type)
            md5_checksum = hashlib.md5(content_for_hash).hexdigest()
            if existing_md5_checksum != md5_checksum:
                doc.update({"md5Checksum": md5_checksum})
                success = await self.graph_provider.batch_update_nodes([doc], CollectionNames.RECORDS.value)
                if not success:
                    self.logger.warning(
                        "⚠️ Failed to update MD5 checksum for record %s - record may not exist",
                        _record_key(doc),
                    )
                    return True

            self.logger.debug("🚀 Calculated md5_checksum: %s for record type: %s", md5_checksum, record_type)

        if not md5_checksum:
            return False

        rec_key = doc.get('_key') or doc.get('id')
        duplicate_records = await self.graph_provider.find_duplicate_records(
            record_key=_record_key(doc),
            md5_checksum=md5_checksum,
            record_type=record_type,
            size_in_bytes=size_in_bytes
        )

        duplicate_records = [r for r in duplicate_records if r is not None]

        if not duplicate_records:
            self.logger.info(
                f"🚀 No duplicate records found for record {_record_key(doc)}"
            )
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
            success = await self.graph_provider.batch_update_nodes([doc], CollectionNames.RECORDS.value)
            if not success:
                self.logger.warning(
                    "⚠️ Failed to update duplicate record %s - record may have been deleted. Skipping relationship copy.",
                    _record_key(doc),
                )
                # Don't proceed with copy_document_relationships if record doesn't exist
                return True
            
            # Copy all relationships from the processed duplicate to this document
            await self.graph_provider.copy_document_relationships(
                _record_key(processed_duplicate),
                _record_key(doc),
            )
            self.logger.debug(
                f"✅ Duplicate record {_record_key(processed_duplicate)} returning TRUE"
            )
            return True  # Duplicate handled

        # Check if any duplicate is in progress
        in_progress = next(
            (r for r in duplicate_records if r.get("indexingStatus") == ProgressStatus.IN_PROGRESS.value),
            None
        )

        if in_progress:
            self.logger.info(
                f"🚀 Duplicate record {_record_key(in_progress)} is being processed, "
                "changing status to QUEUED."
            )
            doc.update({
                "indexingStatus": ProgressStatus.QUEUED.value,
            })
            success = await self.graph_provider.batch_update_nodes([doc], CollectionNames.RECORDS.value)
            if not success:
                self.logger.warning(
                    "⚠️ Failed to mark record %s as QUEUED - record may not exist",
                    _record_key(doc),
                )
            return True  # Marked as queued (or record deleted — skip processing)

        self.logger.info(
            f"🚀 No duplicate found, proceeding with processing for {_record_key(doc)}"
        )
        return False  # No duplicate found, proceed with processing

    async def on_event(self, event_data: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
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
        # Initialised here so the finally block can always safely release the
        # reference, regardless of where in the try block an exception occurs.
        file_content: bytes | str | None = None
        try:
            # Extract event type and record ID
            event_type = event_data.get(
                "eventType", EventTypes.NEW_RECORD.value
            )  # default to create
            payload = event_data.get("payload")
            if payload is None:
                self.logger.error("❌ No payload in event data")
                return
            event_data = payload
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

            if record is None:
                self.logger.error("❌ Record %s not found", record_id)
                return

            if virtual_record_id is None:
                virtual_record_id = record.get("virtualRecordId")

            doc: dict[str, Any] = dict(record)

            # Extract necessary data
            record_version = event_data.get("version", 0)
            connector = event_data.get("connectorName", "")
            extension = event_data.get("extension", "unknown")
            mime_type = event_data.get("mimeType", "unknown")
            origin = event_data.get("origin", "CONNECTOR" if connector != "" else "UPLOAD")
            record_name = event_data.get("recordName", f"Untitled-{record_id}")

            file_content = event_data.get("buffer")

            # Connector streaming or JSON API responses may deliver already-parsed
            # Python objects (dict/list) instead of raw bytes.  Serialize them back
            # to a deterministic JSON byte string so every downstream consumer
            # (MD5 hashing, parsers, service pipeline) receives bytes uniformly.
            if isinstance(file_content, (dict, list)):
                file_content = json.dumps(file_content, sort_keys=True, ensure_ascii=False).encode("utf-8")

            content_len = len(file_content) if file_content else 0
            doc_md5_from_connector = doc.get("md5Checksum")
            self.logger.debug(
                f"🔍 [DEBUG] file_content for MD5: type={type(file_content).__name__} len={content_len} "
                f"doc.md5Checksum(from connector)={doc_md5_from_connector}"
            )
            self.logger.debug(f"file_content type: {type(file_content)} length: {content_len}")
            record_type = doc.get("recordType")

            # Calculate MD5 hash and check for duplicates for ALL record types
            try:
                if await self._check_duplicate_by_md5(file_content, doc):
                    self.logger.info("Duplicate record detected, skipping processing")
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    return
            except Exception as e:
                self.logger.error(f"❌ Error in MD5/duplicate processing: {repr(e)}")
                raise
            if isinstance(file_content, str):
                file_content = file_content.strip()

            if not file_content or file_content == b"":
                await self.mark_record_status(doc, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            await self.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

            prev_virtual_record_id = None
            if event_type == EventTypes.UPDATE_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value:
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

            # ── New service pipeline (opt-in via USE_PARSING_SERVICE=true) ──
            if self._use_service_pipeline():
                if isinstance(file_content, str):
                    content_bytes = file_content.encode("utf-8")
                else:
                    content_bytes = file_content

                async for event in self._orchestrate_via_services(
                    record_id=record_id,
                    org_id=org_id,
                    virtual_record_id=virtual_record_id,
                    record_name=record_name,
                    mime_type=mime_type,
                    extension=extension,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                    file_content=content_bytes,
                ):
                    yield event
                return

            # ── Legacy per-format dispatch (existing behaviour) ──────────────
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    mail_content=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event
                return

            if extension == ExtensionTypes.PDF.value or mime_type == MimeTypes.PDF.value:
                # Check if document needs OCR before using docling

                self.logger.info("🔍 Checking if PDF needs OCR processing")
                try:
                    needs_ocr = await self._pdf_needs_ocr(file_content)
                    self.logger.info("📊 OCR requirement: %s", 'YES - Using OCR handler' if needs_ocr else 'NO - Using layout parser')
                except Exception as e:
                    self.logger.warning("⚠️ Error checking OCR need: %s, defaulting to layout parser", str(e))
                    needs_ocr = False

                if needs_ocr:
                    # Skip docling and use OCR handler directly
                    self.logger.info("🤖 PDF needs OCR, skipping layout parser")
                    async for event in self.processor.process_pdf_document_with_ocr(
                        recordName=record_name,
                        recordId=record_id,
                        version=record_version,
                        source=connector,
                        orgId=org_id,
                        pdf_binary=file_content,
                        virtual_record_id=virtual_record_id,
                        event_type=event_type,
                        prev_virtual_record_id=prev_virtual_record_id,
                    ):
                        yield event
                else:
                    use_pdfplumber = os.environ.get("ENABLE_PDFPLUMBER_PROCESSOR", "false").lower() == "true"
                    if use_pdfplumber:
                        self.logger.info("📄 Using PdfPlumber+OpenCV processor (ENABLE_PDFPLUMBER_PROCESSOR=true)")
                        try:
                            async for event in self.processor.process_pdf_with_pdf_plumber(
                                recordName=record_name,
                                recordId=record_id,
                                pdf_binary=file_content,
                                virtual_record_id=virtual_record_id,
                                event_type=event_type,
                                prev_virtual_record_id=prev_virtual_record_id,
                            ):
                                yield event
                        except Exception as e:
                            self.logger.warning(f"⚠️ PdfPlumber+OpenCV processing failed, falling back to OCR: {e}")
                            async for event in self.processor.process_pdf_document_with_ocr(
                                recordName=record_name,
                                recordId=record_id,
                                version=record_version,
                                source=connector,
                                orgId=org_id,
                                pdf_binary=file_content,
                                virtual_record_id=virtual_record_id,
                                event_type=event_type,
                                prev_virtual_record_id=prev_virtual_record_id,
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
                            event_type=event_type,
                            prev_virtual_record_id=prev_virtual_record_id,
                        ):
                            if event.event == IndexingEvent.DOCLING_FAILED:
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
                                event_type=event_type,
                                prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension == ExtensionTypes.CSV.value or mime_type == MimeTypes.CSV.value:
                async for event in self.processor.process_delimited_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension == ExtensionTypes.TSV.value or mime_type == MimeTypes.TSV.value:
                async for event in self.processor.process_delimited_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    extension=ExtensionTypes.TSV.value,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension == ExtensionTypes.MD.value or mime_type == MimeTypes.MARKDOWN.value:
                async for event in self.processor.process_md_document(
                    recordName=record_name,
                    recordId=record_id,
                    md_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif mime_type in CODE_FILE_MIME_TYPE_VALUES or normalize_file_extension(extension) in CODE_FILE_EXTENSION_VALUES:
                async for event in self.processor.process_md_document(
                    recordName=record_name,
                    recordId=record_id,
                    md_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension == ExtensionTypes.JSON.value or mime_type == MimeTypes.JSON.value:
                async for event in self.processor.process_structured_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_content=file_content,
                    virtual_record_id=virtual_record_id,
                    extension=ExtensionTypes.JSON.value,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension in {ExtensionTypes.YAML.value, ExtensionTypes.YML.value} or mime_type == MimeTypes.YAML.value:
                async for event in self.processor.process_structured_document(
                    recordName=record_name,
                    recordId=record_id,
                    file_content=file_content,
                    virtual_record_id=virtual_record_id,
                    extension=ExtensionTypes.YAML.value,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
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
        finally:
            # Release the file-content reference so the async-generator frame
            # does not keep megabytes of raw bytes alive after aclose().
            # Buffer cleanup from the payload dict is handled by record.py's
            # finally (payload.pop("buffer", None)), which holds the unambiguous
            # reference to the inner payload dict.
            file_content = None

