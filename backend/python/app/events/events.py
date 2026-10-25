import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from bs4 import BeautifulSoup

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CODE_FILE_EXTENSION_VALUES,
    CODE_FILE_MIME_TYPE_VALUES,
    CollectionNames,
    EventTypes,
    ExtensionTypes,
    MimeTypes,
    ProgressStatus,
    RecordTypes,
    get_mime_type_for_extension,
    normalize_file_extension,
)
from app.events.dedup import (
    DedupDecision,
    DuplicateMatch,
    is_finished,
    select_duplicate,
    will_promote_queued_copies,
)
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import (
    IndexingError,
    ProcessingError,
)
from app.models.entities import Record
from app.modules.parsers.code_parser.file_role import is_generated_file_name
from app.modules.parsers.code_parser.routing import CodeFileRoute, plan_code_file
from app.modules.parsers.code_parser.secret_scan import redact_secrets_bytes
from app.modules.parsers.pdf.untrusted_pdf import inspect_untrusted_pdf
from app.modules.pipeline.fingerprint import content_revision
from app.modules.pipeline.ingress import StageIngress
from app.modules.reconciliation.service import ReconciliationMetadata
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import ENRICHMENT_FOLLOWS
from app.services.base_client import ServiceUnavailableError
from app.services.cache.invalidation_hooks import notify_record_indexed
from app.services.docling.client import within_docling_size_limit
from app.services.extraction.client import ExtractionClient
from app.services.graph_db.common.record_visibility import (
    indexed_copy_fields,
    is_live_record,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    messaging_env,
)
from app.services.parsing.client import ParsingClient
from app.services.parsing.interface import ParserProvider
from app.services.resource_governor import classify
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import (
    CollectionStrategy,
    IncompleteCollectionContext,
    RecordContext,
    resolve_write_collection_name,
)
from app.utils.cpu_offload import offload_if_large
from app.utils.file_signatures import match_metadata_file_signature
from app.utils.indexing_timing import stage
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def _record_key(doc: dict[str, Any] | None) -> str | None:
    """Record id from either Arango (_key) or generic graph (id) document format."""
    return (doc.get("_key") or doc.get("id")) if doc is not None else None


_PARSE_DONE = frozenset({ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value})


def _parsed_like(twin: dict[str, Any]) -> dict[str, str]:
    """A duplicate shares its twin's parse, so it takes a finished twin's parsing status."""
    status = twin.get("parsingStatus")
    return {"parsingStatus": status} if status in _PARSE_DONE else {}


def _is_code_file(mime_type: str | None, code_ext: str | None) -> bool:
    """Whether this record should be parsed as source and projected into the code graph.

    Keyed on mime/extension rather than recordType because a connector can hand
    us a code blob without one; every site that decides "is this code" reads this
    so the dispatch and the projection cannot drift apart.
    """
    return (
        mime_type in CODE_FILE_MIME_TYPE_VALUES
        or normalize_file_extension(code_ext) in CODE_FILE_EXTENSION_VALUES
    )


# Storage cleanups of abandoned virtual record ids still running in the background.
_ABANDONED_VRID_CLEANUPS: set[asyncio.Task] = set()

class EventProcessor:
    def __init__(
        self,
        logger: logging.Logger,
        processor: Processor,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService | None = None,
        parsing_client: ParsingClient | None = None,
        extraction_client: ExtractionClient | None = None,
        sink_orchestrator: SinkOrchestrator | None = None,
        collection_strategy: CollectionStrategy | None = None,
        stage_ingress: StageIngress | None = None,
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
        # Hands searchable records to the pipeline stages (classification and after).
        self.stage_ingress = stage_ingress
        # Pure/synchronous — only used to compare "does this duplicate resolve
        # to the same collection as the record being processed", never for I/O.
        self.collection_strategy = collection_strategy or SingleCollectionStrategy()

    def _indexing_pipeline(self):
        """The single owner of vector-membership writes.

        Returns None only if the processor was built without an indexing
        pipeline; callers log rather than silently skipping, because a quiet
        no-op here leaves vector membership permanently stale.
        """
        return getattr(self.processor, "indexing_pipeline", None)

    async def sync_vector_membership(self, virtual_record_id: str) -> None:
        """Recompute a VRID's connector/group arrays from graph. Never deletes."""
        if not virtual_record_id or not isinstance(virtual_record_id, str):
            return
        pipeline = self._indexing_pipeline()
        if pipeline is None:
            # Raise rather than return: the handler yields INDEXING_COMPLETE
            # straight after this call, so returning would acknowledge a
            # syncVectorMembership event whose work never happened, and nothing
            # would revisit it. IndexingError classifies as transient, which is
            # what this needs — an event arriving before the pipeline is wired
            # succeeds on retry, while a genuine misconfiguration dead-letters
            # visibly instead of being silently dropped.
            raise IndexingError(
                "Indexing pipeline unavailable; cannot apply vector membership",
                details={"virtual_record_id": virtual_record_id},
            )
        try:
            await pipeline.sync_vector_membership(virtual_record_id)
        except Exception as e:
            # Propagate so the consumer redelivers. Swallowing here leaves the
            # record attached to the VRID with its connectorId missing from the
            # points and nothing to repair it; both callers are event handlers,
            # and re-running a duplicate attach is idempotent.
            self.logger.error(
                "Failed to sync vector membership for %s: %s", virtual_record_id, e
            )
            raise

    async def _rewrite_or_delete_vrid_vectors(self, virtual_record_id: str) -> None:
        if not virtual_record_id or not isinstance(virtual_record_id, str):
            return
        pipeline = self._indexing_pipeline()
        if pipeline is None:
            self.logger.error(
                "No indexing pipeline available — vectors for abandoned VRID %s "
                "were not rewritten or deleted",
                virtual_record_id,
            )
            return
        try:
            await pipeline.rewrite_or_delete_vector_membership(virtual_record_id)
        except Exception as e:
            self.logger.error(
                "Failed to rewrite/delete vectors for %s: %s", virtual_record_id, e
            )

    def _spawn_abandoned_vrid_cleanup(self, org_id: str, virtual_record_id: str) -> asyncio.Task:
        task = asyncio.create_task(
            self._cleanup_abandoned_vrid_storage(org_id, virtual_record_id),
            name=f"abandoned-vrid-cleanup-{virtual_record_id}",
        )
        # Held until done: the event loop keeps only weak references to tasks.
        _ABANDONED_VRID_CLEANUPS.add(task)
        task.add_done_callback(_ABANDONED_VRID_CLEANUPS.discard)
        return task

    async def _cleanup_abandoned_vrid_storage(
        self, org_id: str, virtual_record_id: str
    ) -> None:
        """Delete blob storage documents and the VRID mapping for an abandoned VRID.

        Retries up to 3 times with exponential backoff on transient failures
        (HTTP timeouts, connection errors) to prevent orphaned storage docs
        from accumulating when the Node.js service is momentarily unavailable.
        """
        blob_storage = getattr(
            getattr(self.processor, "sink_orchestrator", None),
            "blob_storage",
            None,
        )
        if blob_storage is None:
            self.logger.error(
                "No blob storage available — storage docs for abandoned VRID %s "
                "were not cleaned up",
                virtual_record_id,
            )
            return
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await blob_storage.delete_storage_docs_for_vrid(org_id, virtual_record_id)
                return
            except Exception as e:
                if attempt < max_attempts:
                    delay = 2 ** attempt
                    self.logger.warning(
                        "Cleanup attempt %d/%d for abandoned VRID %s failed: %s — "
                        "retrying in %ds",
                        attempt, max_attempts, virtual_record_id, e, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    self.logger.error(
                        "Failed to clean up storage docs for abandoned VRID %s "
                        "after %d attempts: %s",
                        virtual_record_id, max_attempts, e,
                    )

    async def _dispatch_pdf_binary(
        self,
        record_name: str,
        record_id: str,
        record_version: int,
        connector: str,
        org_id: str,
        pdf_binary: bytes,
        virtual_record_id: str,
        event_type: str | None = None,
        prev_virtual_record_id: str | None = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Route PDF bytes to OCR, pdfplumber+OpenCV, or Docling — whichever the
        existing PDF pipeline would pick for a native PDF.
        """
        self.logger.info("🔍 Checking if PDF needs OCR processing")
        inspected = await inspect_untrusted_pdf(pdf_binary, label=record_name)
        # Every parser below reads the bytes that passed inspection.
        pdf_binary = inspected.content
        needs_ocr = inspected.needs_ocr
        self.logger.info("📊 OCR requirement: %s", 'YES - Using OCR handler' if needs_ocr else 'NO - Using layout parser')

        if needs_ocr:
            # Skip docling and use OCR handler directly
            self.logger.info("🤖 PDF needs OCR, skipping layout parser")
            async for event in self.processor.process_pdf_document_with_ocr(
                recordName=record_name,
                recordId=record_id,
                version=record_version,
                source=connector,
                orgId=org_id,
                pdf_binary=pdf_binary,
                virtual_record_id=virtual_record_id,
                event_type=event_type,
                prev_virtual_record_id=prev_virtual_record_id,
            ):
                yield event
            return

        # A PDF with a text layer is never sent to OCR, which costs one model
        # call per page: when Docling is not used or cannot read the file, the
        # local layout parser reads it instead.
        use_pdfplumber = os.environ.get("ENABLE_PDFPLUMBER_PROCESSOR", "false").lower() == "true"
        if use_pdfplumber:
            self.logger.info("📄 Using PdfPlumber+OpenCV processor (ENABLE_PDFPLUMBER_PROCESSOR=true)")
        elif not within_docling_size_limit(len(pdf_binary)):
            self.logger.info("📄 PDF is larger than Docling accepts; using the PdfPlumber+OpenCV processor")
        else:
            docling_failed = False
            async for event in self.processor.process_pdf_with_docling(
                recordName=record_name,
                recordId=record_id,
                pdf_binary=pdf_binary,
                virtual_record_id=virtual_record_id,
                event_type=event_type,
                prev_virtual_record_id=prev_virtual_record_id,
            ):
                if event.event == IndexingEvent.DOCLING_FAILED:
                    docling_failed = True
                else:
                    yield event
            if not docling_failed:
                return
            self.logger.warning("⚠️ Docling could not read the PDF; using the PdfPlumber+OpenCV processor")

        async for event in self.processor.process_pdf_with_pdf_plumber(
            recordName=record_name,
            recordId=record_id,
            pdf_binary=pdf_binary,
            virtual_record_id=virtual_record_id,
            event_type=event_type,
            prev_virtual_record_id=prev_virtual_record_id,
        ):
            yield event

    @staticmethod
    def _reads_as_code_file(
        record_type: str | None, mime_type: str, code_ext: str | None, record_name: str
    ) -> bool:
        """True when ``Processor.process_code_document`` decides how a record is read.

        Source code always does. So does a repository file that would otherwise
        be parsed whole as text, or that is a generated file whatever its type
        (``package-lock.json`` would reach the JSON parser): that method caps
        the first and skips the second.
        """
        if _is_code_file(mime_type, code_ext):
            return True
        return record_type == RecordTypes.CODE_FILE.value and (
            mime_type == MimeTypes.PLAIN_TEXT.value or is_generated_file_name(record_name)
        )

    async def _dispatch_stages(self, record: Record, event_type: str | None) -> None:
        """Hand the now-searchable record to the stage runtime."""
        if self.stage_ingress is None:
            raise RuntimeError("the pipeline stage runtime is not wired into this EventProcessor")
        _ = await self.stage_ingress.on_indexed(record, trigger=event_type)

    @staticmethod
    def _scans_for_secrets(record_type: str | None, *, reads_as_code_file: bool) -> bool:
        """Every file from a code repository, whatever parser reads it (S-2).

        Credentials sit in docker-compose.yml, values.yaml and appsettings.json
        at least as often as in source; those reach the JSON/YAML/XML/Markdown
        parsers, not the code one. Binary content is left as it is by the scanner.
        """
        return reads_as_code_file or record_type == RecordTypes.CODE_FILE.value

    async def _redact_code_secrets(
        self, doc: dict[str, Any], record_name: str, content: bytes | str
    ) -> bytes | str:
        """Strip credentials from a code file before anything indexes it (S-2)."""
        raw = content.encode("utf-8") if isinstance(content, str) else content
        redacted, result = await asyncio.to_thread(redact_secrets_bytes, raw)
        if result.count:
            self.logger.info(
                "Redacted %s secret(s) (%s) from %s before indexing",
                result.count, ", ".join(result.kinds), record_name,
            )
        # Written even when 0: its presence is what tells dedup the content was scanned.
        if result.count != doc.get("secretsRedacted"):
            await self.update_record_fields(doc, {"secretsRedacted": result.count})
        return redacted if result.count else content

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
        *,
        is_code: bool = False,
        file_path: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """New orchestration path: parse → index → enrich via standalone services.

        1. POST /api/v1/parse → BlocksContainer (Parsing Service, port 8092)
        2. VectorStore + BlobStorage via SinkOrchestrator.index()
        3. Hand the record to the stage runtime: the classify stage calls the
           Extraction Service (port 8093) and writes the enrichment.

        Documents are searchable after step 2 regardless of what classification does.
        """
        from app.events.processor import convert_record_dict_to_record  # noqa: PLC0415
        from app.modules.transformers.transformer import (
            TransformContext,  # noqa: PLC0415
        )

        if self.parsing_client is None or self.extraction_client is None or self.sink_orchestrator is None:
            raise RuntimeError("Service pipeline requires the parsing, extraction and sink clients")

        # ── Step 1: Parse ────────────────────────────────────────────────────
        self.logger.debug(
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
            file_path=file_path,
        )
        block_container = parse_result.block_container
        if block_container is None:
            raise RuntimeError(f"Parsing service returned no block container for record {record_id}")
        # IndexingPipeline.apply validates the in-process path; this path never
        # calls it, so without this a malformed container reaches every sink.
        from app.modules.transformers.block_container_validator import (
            BlockContainerValidator,
        )
        BlockContainerValidator(
            logger=self.logger,
            record_id=record_id,
            virtual_record_id=virtual_record_id,
            record_name=record_name,
        ).validate(block_container)
        self.logger.debug(
            "✅ Parsing complete via provider '%s' (%d blocks)",
            parse_result.provider_used.value if parse_result.provider_used else "unknown",
            len(block_container.blocks),
        )

        record_doc = await self.graph_provider.get_document(
            record_id,
            CollectionNames.RECORDS.value,
            # Otherwise a graph that cannot be read raises "not found after
            # parsing", which sends whoever reads it looking for a deletion.
            raise_on_error=True,
        )
        if record_doc is None:
            raise RuntimeError(f"Record {record_id} not found after parsing")

        await self.update_record_fields(
            record_doc,
            {"parsingStatus": ProgressStatus.COMPLETED.value},
        )

        yield PipelineEvent(
            event=IndexingEvent.PARSING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

        if not block_container.blocks and not block_container.block_groups:
            self.logger.info(
                "⚠️ Empty document for %s — marking EMPTY", record_id
            )
            if is_code:
                # As process_code_document does: an emptied file kept its last
                # version's blocks, and their calls and imports stayed (B3).
                await self.processor._drop_code_blocks(record_id)
            await self._drop_the_previous_vectors_of_an_emptied_record(
                record_doc, prev_virtual_record_id or virtual_record_id, event_type
            )
            self._require_persisted(
                await self.update_record_fields(
                    record_doc,
                    {
                        # parsingStatus is already COMPLETED from the write above.
                        "indexingStatus": ProgressStatus.EMPTY.value,
                        **indexed_copy_fields(ProgressStatus.EMPTY),
                        "extractionStatus": ProgressStatus.NOT_STARTED.value,
                        "isDirty": False,
                    },
                ),
                "Failed to persist EMPTY status for record",
                record_doc,
            )
            yield PipelineEvent(
                event=IndexingEvent.INDEXING_COMPLETE,
                data=PipelineEventData(record_id=record_id),
            )
            return

        # ── Build Record + TransformContext ─────────────────────────────────
        await self.update_record_fields(
            record_doc,
            {"indexingStatus": ProgressStatus.IN_PROGRESS.value},
        )

        record = convert_record_dict_to_record(record_doc)
        record.block_containers = block_container
        record.virtual_record_id = virtual_record_id
        record.org_id = org_id

        ctx = TransformContext(
            record=record,
            event_type=event_type,
            prev_virtual_record_id=prev_virtual_record_id,
        )

        with stage("reconcile"):
            ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(
                ctx, self.logger, self.sink_orchestrator
            )

        # The classify stage enriches the record once it is searchable.
        ctx.settings = {**ctx.settings, ENRICHMENT_FOLLOWS: True}

        # Before indexing, which marks the record COMPLETED, and so also before
        # enrichment. COMPLETED is what tells the edge builder the repo has
        # drained; a file carrying it while its blocks are still missing gets
        # the whole repo resolved against a symbol table it does not appear in.
        # Being ahead of enrichment additionally means a deferred or failing
        # extraction still leaves the code graph populated.
        if is_code:
            await self.processor.project_code_blocks_to_graph(
                record_id=record_id,
                org_id=org_id,
                record_group_id=record_doc.get("recordGroupId"),
                connector_id=record_doc.get("connectorId"),
                record_name=record_name,
                file_path=file_path,
                block_containers=block_container,
                propagate_failure=True,
            )

        # ── Step 2: Index (VectorStore + BlobStorage) ────────────────────────
        self.logger.debug("📥 Indexing record %s (making searchable)", record_id)
        await self.sink_orchestrator.index(ctx)
        self.logger.debug("✅ Record %s is now searchable (indexingStatus=COMPLETED)", record_id)

        # ── Step 3: classification runs as a pipeline stage, on its own permits ──
        await self._dispatch_stages(record, event_type)

        yield PipelineEvent(
            event=IndexingEvent.INDEXING_COMPLETE,
            data=PipelineEventData(record_id=record_id),
        )

    async def update_record_fields(self, doc: dict[str, Any], fields: dict[str, Any]) -> bool:
        """Persist a partial record update or fail the current attempt."""
        record_id = _record_key(doc) or "unknown"
        doc.update(fields)
        success = await self.graph_provider.update_node(
            record_id,
            CollectionNames.RECORDS.value,
            fields,
        )
        if not success:
            self.logger.warning(
                f"❌ Failed to update record {record_id} fields {tuple(fields)}"
            )
            return False
        
        return True

    def _require_persisted(self, success: bool, what: str, doc: dict[str, Any]) -> None:
        """Refuse to report success for a graph write that failed.

        `on_event` turns `skip_indexing=True` into PARSING_COMPLETE +
        INDEXING_COMPLETE and consumes the message, and the reconciliation
        sweep in `indexing_main` only revisits QUEUED/IN_PROGRESS records — so
        a write that failed here would leave a record that is neither indexed
        nor ever looked at again. IndexingError classifies as transient, so the
        consumer redelivers and a persistent failure dead-letters visibly.
        """
        if not success:
            raise IndexingError(what, details={"record_id": _record_key(doc)})

    async def _drop_the_previous_vectors_of_an_emptied_record(
        self, doc: dict[str, Any], virtual_record_id: str | None, event_type: str | None
    ) -> None:
        """An update that leaves a record empty must not leave its old content searchable.

        Types not reconciled block by block lost their vectors before this
        event ran; reconciled ones still hold them. A VRID another record also
        holds is that record's content, so it is left alone.
        """
        if not virtual_record_id or event_type not in (
            EventTypes.UPDATE_RECORD.value,
            EventTypes.REINDEX_RECORD.value,
        ):
            return
        # An unreadable graph also answers []; raising lets the event retry rather
        # than reading the failure as "nobody else holds this content".
        try:
            holders = await self.graph_provider.get_records_by_virtual_record_id(
                virtual_record_id, raise_on_error=True
            )
        except Exception as e:
            raise IndexingError(
                "Could not tell whether another record shares this content",
                details={"record_id": _record_key(doc), "virtual_record_id": virtual_record_id},
            ) from e
        if set(holders or []) != {_record_key(doc)}:
            return
        pipeline = self._indexing_pipeline()
        if pipeline is None:
            # Completing anyway would leave the old content in the vector store for good.
            raise IndexingError(
                "No indexing pipeline to delete the previous vectors of an emptied record",
                details={"record_id": _record_key(doc), "virtual_record_id": virtual_record_id},
            )
        await pipeline.delete_points_for_virtual_record(
            virtual_record_id, RecordContext.from_graph_document(doc)
        )
        # The VRID is kept, so without this the next diff would pair unchanged
        # blocks with the points just deleted and embed nothing.
        orchestrator = self.sink_orchestrator or getattr(self.processor, "sink_orchestrator", None)
        try:
            if orchestrator is None:
                raise RuntimeError("no sink orchestrator to reset the reconciliation metadata")
            org_id = doc.get("orgId")
            await orchestrator.blob_storage.save_reconciliation_metadata(
                org_id, _record_key(doc), virtual_record_id, ReconciliationMetadata().to_dict(),
                document_path=await orchestrator.blob_storage.get_actual_content_path(org_id, virtual_record_id),
                connector_id=doc.get("connectorId"),
                record_group_id=doc.get("recordGroupId"),
            )
        except Exception as e:
            raise IndexingError(
                "Could not reset the stored block list of an emptied record",
                details={"record_id": _record_key(doc), "virtual_record_id": virtual_record_id},
            ) from e

    async def mark_record_status(
        self,
        doc: dict[str, Any],
        status: ProgressStatus,
        *,
        content_rev: str | None = None,
        parsing_starts: bool = False,
    ) -> None:
        """Persist the legacy pipeline's indexing and extraction status."""
        record_id = _record_key(doc) or "unknown"
        fields = {
            "indexingStatus": status.value,
            **indexed_copy_fields(status),
            "processingStartedAt": (
                get_epoch_timestamp_in_ms()
                if status == ProgressStatus.IN_PROGRESS
                else None
            ),
        }
        if parsing_starts:
            # In-process, parsing and indexing run as one step: until the indexed write marks it
            # COMPLETED, a re-index must not keep showing the previous run's parse as done. Only
            # where parsing really starts: the stale-record scan requeues a parse left IN_PROGRESS.
            fields["parsingStatus"] = ProgressStatus.IN_PROGRESS.value
        if content_rev is not None:
            fields["contentRev"] = content_rev
        success = await self.update_record_fields(doc, fields)
        self._require_persisted(
            success, f"Failed to persist status {status.value} for record", doc
        )
        self.logger.debug(
            f"🔍 Record {record_id}: Successfully updated status to {status.value}"
        )



    def _hash_for_dedup(
        self,
        content: bytes,
        record_type: str | None = None,
        mime_type: str | None = None,
    ) -> str:
        """Normalise then hash, as one synchronous unit for ``offload_if_large``."""
        return hashlib.md5(
            self._normalize_content_for_dedup(
                content=content, record_type=record_type, mime_type=mime_type
            )
        ).hexdigest()

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

    async def _find_duplicate_records(
        self,
        doc: dict[str, Any],
        md5_checksum: str,
        record_type: str | None,
        size_in_bytes: int | None,
    ) -> list[dict]:
        # Dedup must never cross org boundaries — two orgs holding
        # byte-identical content are not duplicates of each other.
        org_id = doc.get("orgId") or ""
        if not org_id:
            # The filter is `orgId == ""`, which matches nothing — dedup is
            # effectively off for this record. That fails safe (it re-indexes
            # rather than borrowing another org's VRID), but silently, so say
            # so: a record with no orgId is a data problem upstream.
            self.logger.debug(
                "Record %s has no orgId; MD5 dedup will match nothing",
                _record_key(doc),
            )
        return await self.graph_provider.find_duplicate_records(
            record_key=_record_key(doc),
            md5_checksum=md5_checksum,
            org_id=org_id,
            record_type=record_type,
            size_in_bytes=size_in_bytes,
        )

    def _resolve_write_collection(self, record_doc: dict[str, Any]) -> str | None:
        """Which collection this record document's points belong (or would belong) to.

        Goes through ``resolve_write_collection_name`` — the same validate,
        resolve, sanitize sequence the write path uses — because the answer is
        compared against another record's to decide whether to skip indexing.
        A second spelling of that sequence (an unsanitized name, say) would let
        a record be marked a duplicate of something living in a collection its
        own vectors never reached.

        ``None`` when the document lacks a field the strategy needs. Callers
        treat that as "cannot prove same collection" and index anyway, which
        costs a re-index and never loses a record.
        """
        ctx = RecordContext.from_graph_document(record_doc)
        try:
            return resolve_write_collection_name(self.collection_strategy, ctx)
        except IncompleteCollectionContext as e:
            self.logger.warning(
                "Could not resolve a collection for record %s (%s); treating it as "
                "a different collection so it is indexed rather than skipped",
                _record_key(record_doc),
                e,
            )
            return None

    def _resolves_to_same_collection(
        self, duplicate_doc: dict[str, Any], current_collection: str | None
    ) -> bool:
        """True only when both records provably resolve to the same collection.

        Deliberately asymmetric: an unresolvable side is *not* a match, so the
        record gets indexed. Skipping indexing on an unproven match is the one
        outcome with no repair path — the record would be COMPLETED with no
        vectors anywhere.
        """
        if current_collection is None:
            return False
        return self._resolve_write_collection(duplicate_doc) == current_collection

    async def _vrid_has_stored_content(self, org_id: str, virtual_record_id: str) -> bool:
        blob_storage = getattr(
            getattr(self.processor, "sink_orchestrator", None), "blob_storage", None
        )
        if blob_storage is None:
            # Cannot check; keep reusing the duplicate as before.
            return True
        # Raises StorageLookupUnavailableError when storage cannot tell (a 503
        # mid-move): neither "present" nor "gone" is safe to assume, so the event
        # fails and is retried.
        return await blob_storage.get_actual_content_path(org_id, virtual_record_id) is not None

    async def _check_duplicate_by_md5(
        self,
        content: bytes | str | dict | list | None,
        doc: dict[str, Any],
        before_reusing_status: Callable[[], Awaitable[None]] | None = None,
        *,
        content_rev: str | None = None,
        scans_for_secrets: bool = False,
    ) -> DedupDecision:
        """Check for duplicate records by MD5 hash and decide whether to skip indexing.

        A duplicate that resolves to the SAME collection as this record is fully
        reused (metadata copied, indexing skipped). A duplicate that resolves to
        a DIFFERENT collection (e.g. a different connector type under a future
        per-connector-type strategy) still contributes its virtualRecordId —
        content/blob identity is collection-independent — but this record is
        indexed anyway, since its target collection has no vectors for it yet.
        Under the default SingleCollectionStrategy every record resolves to the
        same collection, so this degenerates to the original skip-or-not behaviour.

        ``before_reusing_status`` runs just before a finished duplicate's
        ``indexingStatus`` is copied onto this record, for work that has to be
        in place by the time the record reads as done.

        ``scans_for_secrets`` records are not attached to a finished twin
        that was never scanned: its vectors and blob hold the raw content.
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
            # Normalising (BeautifulSoup, for HTML-ish records) and hashing are
            # both synchronous and both scale with document size, on the one
            # worker loop every in-flight record shares. Offloaded together as
            # a unit so a large document costs one thread hop, not two.
            md5_checksum = await offload_if_large(
                self._hash_for_dedup, content, record_type, mime_type
            )
            if existing_md5_checksum != md5_checksum:
                success = await self.update_record_fields(
                    doc,
                    {"md5Checksum": md5_checksum},
                )
                self._require_persisted(
                    success, "Failed to persist md5Checksum for record", doc
                )

            self.logger.debug("🚀 Calculated md5_checksum: %s for record type: %s", md5_checksum, record_type)

        if not md5_checksum:
            return DedupDecision(virtual_record_id=None, skip_indexing=False)
        duplicate_records = await self._find_duplicate_records(
            doc=doc,
            md5_checksum=md5_checksum,
            record_type=record_type,
            size_in_bytes=size_in_bytes,
        )

        duplicate_records = [r for r in duplicate_records if r is not None]

        if not duplicate_records:
            self.logger.debug(
                f"🚀 No duplicate records found for record {_record_key(doc)}"
            )
            return DedupDecision(virtual_record_id=None, skip_indexing=False)

        current_collection = self._resolve_write_collection(doc)
        enrichment_live_after_ms = get_epoch_timestamp_in_ms() - int(
            messaging_env.stale_recovery_after_seconds * 1000
        )
        match = select_duplicate(
            duplicate_records,
            current_collection,
            self._resolve_write_collection,
            enrichment_live_after_ms=enrichment_live_after_ms,
        )
        if match is None:
            self.logger.info(
                "🚀 No usable duplicate for %s, proceeding with processing",
                _record_key(doc),
            )
            return DedupDecision()

        if match.is_processed:
            return await self._reuse_finished_duplicate(
                doc, match, before_reusing_status, content_rev=content_rev, scans_for_secrets=scans_for_secrets
            )

        if not match.same_collection:
            # In flight, but for a different collection — waiting would buy
            # this record nothing, since that work leaves its own collection
            # empty.
            return DedupDecision()

        self.logger.info(
            f"🚀 Duplicate record {_record_key(match.record)} is being processed "
            "into the same collection, changing status to QUEUED."
        )
        # Before QUEUED: the twin's completion promotes this record straight
        # to COMPLETED, which nothing on that path follows with blocks (R-5).
        if before_reusing_status is not None:
            await before_reusing_status()
            before_reusing_status = None
        self._require_persisted(
            await self.update_record_fields(
                doc,
                {"indexingStatus": ProgressStatus.QUEUED.value},
            ),
            "Failed to persist QUEUED status for duplicate record",
            doc,
        )
        # The twin's completion promotes only the copies already QUEUED, so a
        # twin that finished between the read above and this write would leave
        # this record parked until the stranded-record sweep. Applying the
        # finished twin twice is harmless: the reuse writes are idempotent.
        # A failed read must not ack: the stranded sweep skips a QUEUED row
        # with an md5 and a vrid, so nothing would revisit this record. The
        # redelivery decides again, and its QUEUED write and reuse are idempotent.
        twin_key = _record_key(match.record)
        try:
            twin = await self.graph_provider.get_document(
                twin_key, CollectionNames.RECORDS.value, raise_on_error=True
            )
        except Exception as e:
            raise IndexingError(
                f"Could not re-read duplicate {twin_key} after queueing {_record_key(doc)}",
                details={"record_id": _record_key(doc), "duplicate_id": twin_key},
            ) from e
        if twin is None or not self._still_a_same_collection_copy(twin, doc, md5_checksum, current_collection):
            # Deleted, or its content or org changed since it was selected: no
            # completion of it will promote this record, and its identity is
            # not this record's to copy.
            self.logger.info("Duplicate %s no longer stands for %s; indexing it itself", twin_key, _record_key(doc))
            return DedupDecision()
        if is_finished(twin):
            self.logger.info("Duplicate %s finished while %s was being queued; reusing it now", twin_key, _record_key(doc))
            return await self._reuse_finished_duplicate(
                doc,
                DuplicateMatch(record=twin, same_collection=True, is_processed=True),
                before_reusing_status,
                content_rev=content_rev,
                scans_for_secrets=scans_for_secrets,
            )
        if will_promote_queued_copies(twin, enrichment_live_after_ms):
            return DedupDecision(skip_indexing=True)
        # Ended without finishing (FAILED, or an end state such as an
        # unsupported type) and already past promoting its copies. Indexed
        # here rather than failed: whether the twin's failure would repeat on
        # this content is not known here, and this record's own message can
        # still find out.
        self.logger.info(
            "Duplicate %s ended %s while %s was being queued; indexing it itself",
            twin_key, twin.get("indexingStatus"), _record_key(doc),
        )
        return DedupDecision()

    def _still_a_same_collection_copy(
        self,
        twin: dict[str, Any],
        doc: dict[str, Any],
        md5_checksum: str,
        current_collection: str | None,
    ) -> bool:
        return (
            # A twin moved to the trash meanwhile is losing its vectors.
            is_live_record(twin)
            and twin.get("md5Checksum") == md5_checksum
            and (twin.get("orgId") or "") == (doc.get("orgId") or "")
            and self._resolves_to_same_collection(twin, current_collection)
        )

    async def _reuse_finished_duplicate(
        self,
        doc: dict[str, Any],
        match: DuplicateMatch,
        before_reusing_status: Callable[[], Awaitable[None]] | None = None,
        *,
        content_rev: str | None = None,
        scans_for_secrets: bool = False,
    ) -> DedupDecision:
        attached_vrid = match.record.get("virtualRecordId")

        if scans_for_secrets and match.record.get("secretsRedacted") is None:
            # Indexed before redaction existed: reusing it would keep the
            # secrets searchable for good. Rebuilding a shared VRID redacts
            # the twin's copy too.
            self.logger.info(
                "Duplicate %s was never scanned for secrets; indexing %s instead of reusing it",
                _record_key(match.record), _record_key(doc),
            )
            return DedupDecision(
                rebuild_shared_vrid=doc.get("virtualRecordId") == attached_vrid
            )

        if (
            attached_vrid
            and match.record.get("indexingStatus") == ProgressStatus.COMPLETED.value
            and not await self._vrid_has_stored_content(doc.get("orgId") or "", attached_vrid)
        ):
            # The twin's stored content is gone (e.g. deleted with the connector
            # that indexed it first). Reusing its VRID would leave this record
            # unreadable, and every re-index would skip against the same twin.
            # Indexing instead rewrites the content and re-points the mapping,
            # which heals every record sharing that VRID.
            self.logger.warning(
                "Duplicate %s has no stored content for VRID %s; indexing %s instead of reusing it",
                _record_key(match.record), attached_vrid, _record_key(doc),
            )
            return DedupDecision(
                rebuild_shared_vrid=doc.get("virtualRecordId") == attached_vrid
            )

        if match.same_collection:
            # The vectors this record needs already exist.
            if before_reusing_status is not None:
                await before_reusing_status()
            await self._attach_to_finished_twin(match.record, doc, content_rev=content_rev)
        else:
            if attached_vrid:
                # Same content, different collection: reuse the content
                # identity (and with it the stored blob), but leave
                # indexingStatus alone so this record still gets vectors of its
                # own in its own collection. A twin with no virtualRecordId has
                # no identity to lend, and writing its None would blank ours.
                self._require_persisted(
                    await self.update_record_fields(doc, {"virtualRecordId": attached_vrid}),
                    "Failed to persist duplicate record fields",
                    doc,
                )
            await self._copy_twin_edges(match.record, doc)
        if match.same_collection and self.sink_orchestrator is not None:
            # The copy above only touched the graph — this record still
            # has no `record`/`record_group` point, and the taxonomy
            # points it now shares carry only the other record's
            # connectorId/recordGroupId. Different-collection duplicates
            # skip this: they continue to full indexing and get entity
            # sync from SinkOrchestrator.index()/enrich() there instead.
            # sync_entities_for_duplicate is already best-effort
            # internally; caught again here so a mock/mis-wired sink in
            # a caller can never turn dedup's own bookkeeping into a
            # failed event.
            try:
                await self.sink_orchestrator.sync_entities_for_duplicate(doc)
            except Exception as exc:
                self.logger.warning(
                    "Entity vector sync failed for duplicate %s (non-fatal): %s",
                    _record_key(doc),
                    exc,
                )
        self.logger.debug(
            "✅ Duplicate record %s resolved (same_collection=%s)",
            _record_key(match.record),
            match.same_collection,
        )
        return DedupDecision(
            virtual_record_id=attached_vrid, skip_indexing=match.same_collection
        )

    async def _copy_twin_edges(self, twin: Mapping[str, Any], doc: dict[str, Any]) -> None:
        self._require_persisted(
            await self.graph_provider.copy_document_relationships(
                _record_key(twin),
                _record_key(doc),
            ),
            "Failed to copy duplicate record relationships",
            doc,
        )
        from app.services.featureflag.platform_settings import (
            is_named_entity_extraction_enabled,
        )

        # With the flag off the copy is skipped, so a deployment that never turned
        # it on cannot fail a duplicate over named entities.
        if not await is_named_entity_extraction_enabled(self.config_service):
            return
        # Fatal like the taxonomy copy: a same-collection duplicate is marked
        # COMPLETED and never indexed, so nothing else would copy the mentions.
        try:
            await self.graph_provider.copy_named_entity_mentions(_record_key(twin), _record_key(doc))
        except Exception as exc:
            raise IndexingError(
                "Failed to copy duplicate record mentions", details={"record_id": _record_key(doc)}
            ) from exc

    async def _attach_to_finished_twin(
        self, twin: Mapping[str, Any], doc: dict[str, Any], *, content_rev: str | None = None
    ) -> None:
        """Make ``doc`` a copy of a finished twin in the same collection.

        Status is written last. The handler acknowledges a redelivered event
        for a COMPLETED record, so writing it first turned any failure in the
        edge copy or the membership sync into a permanent one: the record
        claimed to be indexed while its connectorId was missing from the
        VRID's points.
        """
        target_key = _record_key(doc)
        vrid = twin.get("virtualRecordId")
        prior_identity = {
            "virtualRecordId": doc.get("virtualRecordId"),
            "summaryDocumentId": doc.get("summaryDocumentId"),
        }

        # A QUEUED record with this md5 is what a finishing twin promotes. Left
        # QUEUED for the length of the attach, this one could be promoted to
        # COMPLETED underneath it, and a failure afterwards would then undo the
        # identity of a record nothing retries. IN_PROGRESS takes it out of
        # the promotion's reach; a failed attempt is retried by the handler,
        # as for any record.
        await self.mark_record_status(doc, ProgressStatus.IN_PROGRESS)

        await self._copy_twin_edges(twin, doc)
        sync_attempted = False
        try:
            # Inside the try: a write whose answer is lost has still landed.
            self._require_persisted(
                await self.update_record_fields(
                    doc,
                    {
                        "virtualRecordId": vrid,
                        "summaryDocumentId": twin.get("summaryDocumentId"),
                        # Lets this copy be reused as a scanned twin in turn.
                        **(
                            {"secretsRedacted": twin["secretsRedacted"]}
                            if twin.get("secretsRedacted") is not None else {}
                        ),
                    },
                ),
                "Failed to persist duplicate record fields",
                doc,
            )
            if vrid:
                # Set first: a sync that raises or is cancelled may already
                # have written some of the points.
                sync_attempted = True
                await self.sync_vector_membership(vrid)
            self._require_persisted(
                await self.update_record_fields(
                    doc,
                    {
                        "isDirty": False,
                        "indexingStatus": twin.get("indexingStatus"),
                        **indexed_copy_fields(twin.get("indexingStatus")),
                        **_parsed_like(twin),
                        "processingStartedAt": None,
                        "lastIndexTimestamp": get_epoch_timestamp_in_ms(),
                        # EMPTY duplicates never ran extraction, so this can be
                        # missing/None on the source record — don't propagate None.
                        "extractionStatus": (
                            twin.get("extractionStatus") or ProgressStatus.NOT_STARTED.value
                        ),
                        "lastExtractionTimestamp": get_epoch_timestamp_in_ms(),
                        # The twin's revision: its stage states are this copy's too,
                        # and a stage retry for this record finds them under it.
                        **(
                            {"contentRev": twin.get("contentRev") or content_rev}
                            if twin.get("contentRev") or content_rev
                            else {}
                        ),
                    },
                ),
                "Failed to persist duplicate record fields",
                doc,
            )
        except (Exception, asyncio.CancelledError):
            # Left holding the twin's VRID after a failure, the record would
            # keep its connectorId on that VRID's points, and a later reindex
            # of it would overwrite the twin's vectors.
            await self._restore_identity_after_failed_attach(
                target_key, prior_identity, vrid if sync_attempted else None
            )
            raise

    async def _restore_identity_after_failed_attach(
        self,
        record_key: str,
        prior_identity: dict[str, Any],
        attached_vrid: str | None,
    ) -> None:
        """Best effort: the event is already failing, and its retry redoes the attach."""
        try:
            current = await self.graph_provider.get_document(
                record_key, CollectionNames.RECORDS.value, raise_on_error=True
            )
            if (current or {}).get("indexingStatus") in (
                ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value,
            ):
                # The status write landed and only its answer was lost. The
                # attach is complete; taking its identity away now would leave
                # a finished record with no VRID and nothing to retry it.
                return
            if not await self.graph_provider.update_node(
                record_key, CollectionNames.RECORDS.value, prior_identity
            ):
                raise IndexingError("record no longer exists")
        except Exception as e:
            self.logger.error(
                "Could not restore the previous content identity of %s after a failed "
                "duplicate attach; it may still hold VRID %s until its retry: %s",
                record_key,
                attached_vrid,
                e,
            )
            return
        if not attached_vrid:
            return
        try:
            await self.sync_vector_membership(attached_vrid)
        except Exception as e:
            self.logger.error(
                "Could not re-sync VRID %s after a failed duplicate attach; its points may "
                "still list %s until its retry or the next sync of that VRID: %s",
                attached_vrid,
                record_key,
                e,
            )

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
        forced_record_id: str | None = None
        try:
            # Extract event type and record ID
            event_type = event_data.get(
                "eventType", EventTypes.NEW_RECORD.value
            )  # default to create
            # The three guards below used to `return` bare, yielding neither
            # PARSING_COMPLETE nor INDEXING_COMPLETE. The consumers read that as
            # a handler that failed for an unknown reason and retried it to the
            # dead-letter ceiling — three deliveries, no diagnosis, and on Kafka
            # not even a status write, because the handler took its no-error
            # path on the way out. A malformed envelope cannot come good on a
            # retry, so the first two raise a TERMINAL error and are reported
            # once; the third is a real race and drains instead.
            payload = event_data.get("payload")
            if payload is None:
                raise ProcessingError(
                    "Event has no payload",
                    details={"event_type": event_type},
                )
            event_data = payload
            record_id = event_data.get("recordId")
            org_id = event_data.get("orgId")
            virtual_record_id = event_data.get("virtualRecordId")
            self.logger.debug(f"📥 Processing event: {event_type}: for record {record_id} with virtual_record_id {virtual_record_id}")

            if not record_id:
                raise ProcessingError(
                    "Event has no recordId",
                    details={"event_type": event_type},
                )

            record = await self.graph_provider.get_document(
                record_id,
                CollectionNames.RECORDS.value,
                # None below drains the message, so it has to mean "deleted" and
                # nothing else. Without this a graph that is restarting answers
                # None for every record in flight, each one is drained as though
                # it had been deleted, and they sit at QUEUED until the stranded
                # sweep notices an hour later.
                raise_on_error=True,
            )

            if record is None:
                # Legitimately reachable: a record can be deleted between an
                # event being published and consumed. There is nothing to index
                # and nothing to fail, so drain the message rather than retry it.
                self.logger.error("❌ Record %s not found", record_id)
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

            doc: dict[str, Any] = dict(record)

            # A successful attempt never clears `reason` (the status writer only
            # sets it when non-empty), so drop the previous failure text here or
            # it outlives a good reindex.
            if doc.get("reason"):
                self._require_persisted(
                    await self.update_record_fields(doc, {"reason": None}),
                    "Failed to clear the previous failure reason for record",
                    doc,
                )

            # Extract necessary data
            record_version = event_data.get("version", 0)
            connector = event_data.get("connectorName", "")
            extension = event_data.get("extension", "unknown")
            mime_type = event_data.get("mimeType", "unknown")
            origin = event_data.get("origin", "CONNECTOR" if connector != "" else "UPLOAD")
            record_name = event_data.get("recordName", f"Untitled-{record_id}")

            # A CODE_FILE's mime is not trustworthy: connectors that walk a git
            # tree default it to text/plain for anything they do not recognise,
            # and they do not always populate `extension`. Derive a separate
            # extension for code dispatch and language detection — the original
            # value must stay intact for reconciliation, tier, and generic dispatch.
            code_ext = extension
            if not code_ext or code_ext == "unknown":
                file_path_raw = event_data.get("filePath") or ""
                fp_base = file_path_raw.rsplit("/", 1)[-1]
                rn_base = record_name.rsplit("/", 1)[-1]
                if "." in fp_base and fp_base.rsplit(".", 1)[-1]:
                    code_ext = fp_base.rsplit(".", 1)[-1].lower()
                elif "." in rn_base and rn_base.rsplit(".", 1)[-1]:
                    code_ext = rn_base.rsplit(".", 1)[-1].lower()
            file_content = event_data.get("buffer")

            # Connector streaming or JSON API responses may deliver already-parsed
            # Python objects (dict/list) instead of raw bytes.  Serialize them back
            # to a deterministic JSON byte string so every downstream consumer
            # (MD5 hashing, parsers, service pipeline) receives bytes uniformly.
            if isinstance(file_content, (dict, list)):
                file_content = json.dumps(file_content, sort_keys=True, ensure_ascii=False).encode("utf-8")

            # The revision every stage state and record-status write for this content is tied to.
            content_rev = content_revision(
                bytes(file_content) if isinstance(file_content, (bytes, bytearray)) else str(file_content or "").encode("utf-8")
            )

            content_len = len(file_content) if file_content else 0
            doc_md5_from_connector = doc.get("md5Checksum")
            self.logger.debug(
                f"🔍 [DEBUG] file_content for MD5: type={type(file_content).__name__} len={content_len} "
                f"doc.md5Checksum(from connector)={doc_md5_from_connector}"
            )
            self.logger.debug(f"file_content type: {type(file_content)} length: {content_len}")
            record_type = doc.get("recordType")
            is_code = _is_code_file(mime_type, code_ext)
            reads_as_code_file = self._reads_as_code_file(
                record_type, mime_type, code_ext, record_name
            )
            scans_for_secrets = self._scans_for_secrets(
                record_type, reads_as_code_file=reads_as_code_file
            )

            # The blob and the vectors are shared through the duplicate's
            # virtualRecordId, but block nodes are keyed by record, so a deduped
            # code file would otherwise be searchable and yet missing from the
            # code graph. Projected before the duplicate's COMPLETED status is
            # copied over: that status is what tells the edge builder the repo
            # has drained, so it must not precede the blocks. For the same
            # reason a failure propagates instead of leaving a COMPLETED record
            # with no blocks.
            before_reusing_status: Callable[[], Awaitable[None]] | None = None
            if is_code:
                # `_check_duplicate_by_md5` encodes only its own local binding,
                # so a str buffer reaches here unconverted and `decode_source`
                # (tree-sitter indexes by byte offset) would fail on it.
                code_bytes = (
                    file_content.encode("utf-8")
                    if isinstance(file_content, str)
                    else file_content
                )

                async def _project_duplicate_code() -> None:
                    await self.processor.project_code_blocks_to_graph(
                        record_id=record_id,
                        org_id=org_id,
                        record_group_id=doc.get("recordGroupId"),
                        connector_id=doc.get("connectorId"),
                        record_name=record_name,
                        file_path=event_data.get("filePath"),
                        content=code_bytes,
                        propagate_failure=True,
                    )

                before_reusing_status = _project_duplicate_code

            rebuild_shared_vrid = False
            # Calculate MD5 hash and check for duplicates for ALL record types
            try:
                dedup_decision = await self._check_duplicate_by_md5(
                    file_content, doc, before_reusing_status=before_reusing_status,
                    content_rev=content_rev, scans_for_secrets=scans_for_secrets,
                )
                if dedup_decision.skip_indexing:
                    self.logger.info("Duplicate record detected, skipping processing")
                    await notify_record_indexed(
                        connector_name=doc.get("connectorName"),
                        connector_id=doc.get("connectorId"),
                        external_record_group_id=doc.get("externalGroupId"),
                        org_id=doc.get("orgId"),
                    )
                    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                    return
                rebuild_shared_vrid = dedup_decision.rebuild_shared_vrid
                if dedup_decision.virtual_record_id:
                    # Different-collection duplicate: content identity was copied
                    # onto `doc` inside _check_duplicate_by_md5; pick it up here
                    # so the rest of this pipeline indexes under the reused VRID.
                    virtual_record_id = dedup_decision.virtual_record_id
            except Exception as e:
                self.logger.error(f"❌ Error in MD5/duplicate processing: {repr(e)}")
                raise
            if isinstance(file_content, str):
                file_content = file_content.strip()

            if not file_content or file_content == b"":
                if is_code:
                    # A file emptied to zero bytes is caught here, before parsing (B3).
                    await self.processor._drop_code_blocks(record_id)
                await self._drop_the_previous_vectors_of_an_emptied_record(
                    doc, virtual_record_id, event_type
                )
                await self.mark_record_status(doc, ProgressStatus.EMPTY)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            metadata_file_match = match_metadata_file_signature(file_content)
            if metadata_file_match:
                self.logger.info(
                    "❌ Skipping OS metadata file (%s): %s",
                    metadata_file_match,
                    record_name,
                )
                await self.mark_record_status(doc, ProgressStatus.FILE_TYPE_NOT_SUPPORTED)
                yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id=record_id))
                yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id=record_id))
                return

            if self._use_service_pipeline():
                # The parsing and extraction services act for the org named in
                # the service token, and refuse a token that names none.
                org_id = org_id or doc.get("orgId")
                if not org_id:
                    raise ProcessingError(
                        f"Record {record_id} has no organisation: neither the event "
                        "nor the stored record carries an orgId",
                        record_id=record_id,
                        details={"event_type": event_type},
                    )

            # Fail fast, before writing IN_PROGRESS, if the parsing service's
            # circuit breaker is already open. This is an in-memory check (no
            # HTTP call) so it doesn't add latency on the happy path, but it
            # stops every incoming record from being written to IN_PROGRESS
            # and burning a parsing-semaphore slot on a service we already
            # know is down.
            if self._use_service_pipeline() and self.parsing_client.circuit_open:
                raise ServiceUnavailableError(
                    "Parsing service circuit breaker is open; failing fast",
                    status_code=503,
                    service_name="ParsingService",
                )

            if self._use_service_pipeline():
                # Consumer holds MAX_CONCURRENT_INDEXING before invoking us.
                # Parsing slots are requested below via START_PARSING so up to
                # MAX_CONCURRENT_INDEXING records can show IN_PROGRESS while at
                # most MAX_CONCURRENT_PARSING are actively parsing.
                processing_started_at = event_data.get(
                    "_processing_started_at",
                    get_epoch_timestamp_in_ms(),
                )
                await self.update_record_fields(
                    doc,
                    {
                        "parsingStatus": ProgressStatus.IN_PROGRESS.value,
                        "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                        "processingStartedAt": processing_started_at,
                        "contentRev": content_rev,
                    },
                )
            else:
                # Legacy inline pipeline: parse+index run in-process without a
                # phase boundary we can hook, keep the historical behaviour.
                await self.mark_record_status(
                    doc, ProgressStatus.IN_PROGRESS, content_rev=content_rev, parsing_starts=True
                )

            if self.stage_ingress is not None and bool(event_data.get("forceReindex")):
                self.stage_ingress.mark_forced(record_id)
                forced_record_id = record_id

            prev_virtual_record_id = None
            abandoned_virtual_record_id = None
            if rebuild_shared_vrid:
                # Identical content under a VRID that lost its stored content:
                # isolating would fix only this record and leave the others
                # sharing that VRID unreadable, so rebuild it in place.
                virtual_record_id = doc.get("virtualRecordId")
                self.logger.info(
                    f"📊 Rebuilding shared vrid {virtual_record_id} whose stored content is missing"
                )
                prev_virtual_record_id = virtual_record_id
            elif event_type == EventTypes.UPDATE_RECORD.value or event_type == EventTypes.REINDEX_RECORD.value:
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
                            abandoned_virtual_record_id = prev_virtual_record_id
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
                    abandoned_virtual_record_id = virtual_record_id
                    virtual_record_id = str(uuid4())

            if virtual_record_id is None:
                virtual_record_id = str(uuid4())

            # Persist the vrid this attempt will index under *before* parsing
            # starts. If this attempt fails partway through (after vectors are
            # written but before indexingStatus=COMPLETED), the retry re-reads
            # this same vrid from the record instead of minting a fresh one —
            # so record.py's bulk_delete_embeddings (for non-reconciliation
            # types) or the reconciliation diff (for reconciliation-enabled
            # types) targets the orphaned vectors instead of abandoning them.
            if virtual_record_id != doc.get("virtualRecordId"):
                await self.update_record_fields(
                    doc, {"virtualRecordId": virtual_record_id}
                )
            if (
                abandoned_virtual_record_id
                and abandoned_virtual_record_id != virtual_record_id
            ):
                # Off the indexing slot: the cleanup waits to confirm no record
                # still uses the vrid, and backs off on errors, before deleting.
                self._spawn_abandoned_vrid_cleanup(org_id, abandoned_virtual_record_id)
                await self._rewrite_or_delete_vrid_vectors(abandoned_virtual_record_id)

            # Ask the consumer for a nested parsing slot only after the record
            # is already IN_PROGRESS under the outer indexing gate. Tier/size
            # are already known here (extension, mime and content_len were
            # read above) so the consumer can route to the matching
            # resource_governor pool instead of re-deriving format itself.
            # content_len is a char count for str content (set before we knew
            # the type); re-derive actual bytes here so XL-cost routing isn't
            # underestimated for non-ASCII text.
            size_bytes = (
                len(file_content.encode("utf-8"))
                if isinstance(file_content, str)
                else content_len
            )
            yield PipelineEvent(
                event=IndexingEvent.START_PARSING,
                data=PipelineEventData(
                    record_id=record_id,
                    tier=classify(extension, mime_type),
                    size_bytes=size_bytes,
                ),
            )

            if scans_for_secrets:
                # Once, ahead of both pipelines: blocks, embeddings, LLM
                # extraction and read_code all derive from these bytes.
                file_content = await self._redact_code_secrets(doc, record_name, file_content)

            # ── New service pipeline (opt-in via USE_PARSING_SERVICE=true) ──
            if self._use_service_pipeline():
                if isinstance(file_content, str):
                    content_bytes = file_content.encode("utf-8")
                else:
                    content_bytes = file_content

                service_mime_type, service_extension = mime_type, extension
                if reads_as_code_file:
                    # The parsing service picks a parser from mime and extension
                    # alone, so the decision the in-process code path makes is
                    # made here before the bytes are sent.
                    plan = plan_code_file(
                        record_name,
                        event_data.get("filePath"),
                        code_ext,
                        content_bytes,
                        repository_file=record_type == RecordTypes.CODE_FILE.value,
                    )
                    if plan.route is CodeFileRoute.SKIP:
                        async for event in self.processor.process_code_document(
                            recordName=record_name,
                            recordId=record_id,
                            code_binary=content_bytes,
                            virtual_record_id=virtual_record_id,
                            extension=code_ext,
                            file_path=event_data.get("filePath"),
                            event_type=event_type,
                            prev_virtual_record_id=prev_virtual_record_id,
                        ):
                            yield event
                        return
                    if plan.route in (CodeFileRoute.DELIMITED, CodeFileRoute.STRUCTURED):
                        service_extension = plan.parser
                        service_mime_type = get_mime_type_for_extension(
                            plan.parser, fallback=mime_type
                        )
                    elif plan.route is CodeFileRoute.CODE:
                        # The event may carry no extension, or one that
                        # disagrees with the file's name. The one the grammar
                        # was chosen by is what makes the service pick the code
                        # parser over the text one a text/plain mime implies.
                        service_extension = plan.extension

                async for event in self._orchestrate_via_services(
                    record_id=record_id,
                    org_id=org_id,
                    virtual_record_id=virtual_record_id,
                    record_name=record_name,
                    mime_type=service_mime_type,
                    extension=service_extension,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                    file_content=content_bytes,
                    is_code=is_code,
                    file_path=event_data.get("filePath"),
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

            # Must precede the PLAIN_TEXT branch: code files routinely arrive as
            # text/plain, and that branch returns early.
            if reads_as_code_file:
                async for event in self.processor.process_code_document(
                    recordName=record_name,
                    recordId=record_id,
                    code_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    extension=code_ext,
                    file_path=event_data.get("filePath"),
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
                self.logger.debug("🚀 Processing Blocks Container")
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
                async for event in self._dispatch_pdf_binary(
                    record_name=record_name,
                    record_id=record_id,
                    record_version=record_version,
                    connector=connector,
                    org_id=org_id,
                    pdf_binary=file_content,
                    virtual_record_id=virtual_record_id,
                    event_type=event_type,
                    prev_virtual_record_id=prev_virtual_record_id,
                ):
                    yield event

            elif extension == ExtensionTypes.EPUB.value or mime_type == MimeTypes.EPUB.value:
                async for event in self.processor.process_epub_document(
                    recordName=record_name,
                    recordId=record_id,
                    version=record_version,
                    source=connector,
                    orgId=org_id,
                    epub_binary=file_content,
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
            if forced_record_id is not None and self.stage_ingress is not None:
                self.stage_ingress.clear_forced(forced_record_id)
            # Release the file-content reference so the async-generator frame
            # does not keep megabytes of raw bytes alive after aclose().
            # Buffer cleanup from the payload dict is handled by record.py's
            # finally (payload.pop("buffer", None)), which holds the unambiguous
            # reference to the inner payload dict.
            file_content = None

