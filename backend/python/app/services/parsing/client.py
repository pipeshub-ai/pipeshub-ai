"""HTTP client for the Parsing Service.

Usage::

    client = ParsingClient(service_url="http://localhost:8092")
    result = await client.parse(
        file_content=pdf_bytes,
        record_name="report.pdf",
        mime_type="application/pdf",
        extension="pdf",
    )
    block_container = result.block_container
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from docling_core.types.doc.document import DoclingDocument
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.config import file_types
from app.config.constants.service import TokenScopes
from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.models.blocks import BlocksContainer
from app.services.base_client import (
    BaseServiceClient,
    ServiceBackpressureError,
    ServiceCallError,
    earlier_attempt_delivered,
)
from app.services.docling.client import DoclingNotServing, DoclingUnavailableError
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.services.messaging.config import messaging_env
from app.services.paid_cache.orphan import RecordGone, RecordHandBack, current_guard
from app.services.paid_cache.run import current_run
from app.services.parsing.interface import (
    DOCLING_UNAVAILABLE_REASON,
    STORED_ROWS_PART,
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.services.parsing.remote_concurrency import remote_parse_concurrency
from app.utils.converters.caption_map import apply_caption_map
from app.utils.table_row_memory import encode_stored_tables, previous_row_texts

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor

logger = logging.getLogger(__name__)


class _ParseSuccessPayload(BaseModel):
    """Shape of a successful ``/api/v1/parse`` response body.

    Validated before field access so a malformed payload raises
    ``ParsingClientError`` instead of an ``AttributeError`` deep in ``parse()``.
    """

    success: bool
    block_container: dict[str, Any] | None = None
    raw_document: str | None = None
    provider_used: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> "_ParseSuccessPayload":
        if (self.block_container is None) == (self.raw_document is None):
            raise ValueError(
                "Exactly one of block_container or raw_document must be set"
            )
        return self


class ParsingClientError(Exception):
    """Raised by ParsingClient when a parsing request fails."""

    def __init__(
        self,
        code: ParseErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}


MAX_PARSE_READ_TIMEOUT_SECONDS = 2400.0  # Docling's own PDF budget
PARSE_SHARE_OF_RECORD_BUDGET = 0.8


async def _stored_rows_part(record_name: str) -> tuple[str, bytes, str] | None:
    """The stored version's table rows this delivery offered, as a part of the parse request, so the parsing service keeps an
    unchanged row's text as indexing does. ``None`` when none were offered (not an update) or they are too large to send."""
    tables = await previous_row_texts()
    if not tables:
        return None
    raw = encode_stored_tables(tables)
    if raw is None:
        logger.warning("Parse of %s: the stored version's table rows are too large to send; its tables are described again", record_name)
        return None
    return ("stored_rows.json", raw, "application/json")


class ParsingClient(BaseServiceClient):
    """Async HTTP client for the Parsing Service (port 8092)."""

    def __init__(
        self,
        service_url: str | None = None,
        read_timeout: float | None = None,
        max_retries: int = 2,
        retry_delay: float = 2.0,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
        config_service: object | None = None,
    ) -> None:
        # The record this parse belongs to is cancelled at
        # RECORD_PROCESSING_TIMEOUT, so a read timeout past that (it was a
        # flat 40 minutes, matching Docling) could never fire: the record
        # timed out first and the parse became a cancellation, with the
        # parsing service still grinding on. Held under the record budget
        # with room for the retry and the stages after it.
        if read_timeout is None:
            read_timeout = min(
                MAX_PARSE_READ_TIMEOUT_SECONDS,
                messaging_env.record_processing_timeout * PARSE_SHARE_OF_RECORD_BUDGET,
            )
        super().__init__(
            service_url=service_url or os.getenv("PARSING_SERVICE_URL", "http://localhost:8092"),
            service_name="ParsingService",
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
            backpressure_coordinator=backpressure_coordinator or get_default_backpressure_coordinator(),
            config_service=config_service,
            service_scope=TokenScopes.DOCUMENT_PARSE,
            # One connection per parse the indexing side may have in flight; the default pool
            # of 10 would hold the rest back whatever PARSING_SERVICE_CONCURRENCY says.
            max_connections=max(10, remote_parse_concurrency() or 0),
        )
        # Docling-backed providers defer block construction to keep the Parsing
        # service stateless; this client completes that phase locally on demand.
        self._docling_processor: DoclingProcessor | None = None

    def _get_docling_processor(self) -> DoclingProcessor:
        if self._docling_processor is None:
            from app.modules.parsers.pdf.docling_processor import (  # noqa: PLC0415
                DoclingProcessor,
            )
            self._docling_processor = DoclingProcessor(
                logger=logger, config=self._config_service
            )
        return self._docling_processor

    async def parse(
        self,
        file_content: bytes,
        record_name: str,
        mime_type: str = "",
        extension: str = "",
        org_id: str | None = None,
        provider: ParserProvider | None = None,
        skip_table_enrichment: bool = False,
        file_path: str | None = None,
        record_id: str | None = None,
    ) -> ParseResult:
        """Parse *file_content* by calling the Parsing Service. A 410 (``RECORD_GONE``: the service saw the record's delete
        marker change) is never taken at its word: the record is read first (``orphan.DeliveryGuard.confirm_gone``). Gone:
        ``RecordGone``. Live: the parse is sent again with the markers just read, at most twice (the third request disables
        the service's stop). The read fails: ``RecordHandBack`` (the delivery is handed back; no drain, no immediate re-parse).
        """
        guard = current_guard()
        while True:
            try:
                return await self._parse_once(
                    file_content, record_name, mime_type, extension, org_id, provider,
                    skip_table_enrichment=skip_table_enrichment, file_path=file_path, record_id=record_id,
                    extra_fields=guard.request_fields() if guard is not None else {},
                )
            except ParsingClientError as exc:
                if exc.code != ParseErrorCode.RECORD_GONE or guard is None:
                    raise
                backend = None
                if guard.backend_of is not None:
                    try:
                        backend = await guard.backend_of()
                    except Exception:
                        backend = None
                verdict = await guard.confirm_gone(backend)
                if verdict == "live":
                    continue
                if verdict == "gone":
                    raise RecordGone(guard.record_id, "the parsing service refused its parse") from exc
                raise RecordHandBack(
                    f"{guard.record_id}: the parsing service refused a deleted record and the record could not be read",
                    delay_s=15.0,
                ) from exc

    async def _parse_once(
        self,
        file_content: bytes,
        record_name: str,
        mime_type: str,
        extension: str,
        org_id: str | None,
        provider: ParserProvider | None,
        *,
        skip_table_enrichment: bool,
        file_path: str | None,
        extra_fields: dict[str, str],
        record_id: str | None = None,
    ) -> ParseResult:
        """One request to the Parsing Service.

        Returns a :class:`ParseResult` on success.
        Raises :class:`ParsingClientError` on expected failures, including
        ``PARSE_BACKPRESSURE`` when the service is saturated and its own
        backpressure attempt budget (see ``BaseServiceClient``) is exhausted
        — this is retryable, unlike the other error codes.
        Raises :class:`ServiceCallError` on connection / retry exhaustion.
        """
        extension = file_types.service_extension(mime_type, extension, record_name)

        form_data: dict[str, str] = {
            "record_name": record_name,
            "mime_type": mime_type,
            "extension": extension,
            "skip_table_enrichment": str(skip_table_enrichment).lower(),
        }
        if org_id is not None:
            form_data["org_id"] = org_id
        if provider is not None:
            form_data["provider"] = provider.value
        if file_path:
            form_data["file_path"] = file_path
        if record_id:
            form_data["record_id"] = record_id
        form_data.update(extra_fields)
        # The record budget this call waits under: the service bounds its stage (and its late writes) by it.
        form_data["record_budget_s"] = str(messaging_env.record_processing_timeout_for(extension, mime_type))
        run = current_run()
        if run is not None and run.refresh:
            # A refreshPaidOutputs run bypasses the stored outputs in the parsing service too.
            form_data["refresh_paid_outputs"] = "true"
            # Names this refresh: the client's own retry of the request carries the same one.
            form_data["refresh_token"] = run.refresh_token

        record_budget = messaging_env.record_processing_timeout_for(extension, mime_type)
        files = {"file": (record_name, file_content, mime_type or "application/octet-stream")}
        stored_rows = await _stored_rows_part(record_name)
        if stored_rows is not None:
            files[STORED_ROWS_PART] = stored_rows
        try:
            response = await self._post_multipart(
                "/api/v1/parse",
                files=files,
                data=form_data,
                operation="parse",
                budget_seconds=record_budget,
                # The client-wide read timeout is sized from the generic
                # record budget and capped at MAX_PARSE_READ_TIMEOUT_SECONDS,
                # which is one Docling *batch* (PDF_PARSING_TIMEOUT_SECONDS),
                # not the sum of them. With USE_PARSING_SERVICE=true the whole
                # page-batch loop is a single /api/v1/parse response, so a
                # large filing hit that cap long before its own budget and no
                # wider retry budget could help it. Per record, per call.
                read_timeout=max(
                    self._timeout.read or 0.0,
                    record_budget * PARSE_SHARE_OF_RECORD_BUDGET,
                ),
                org_id=org_id,
            )
        except ServiceBackpressureError as exc:
            raise ParsingClientError(
                code=ParseErrorCode.PARSE_BACKPRESSURE,
                message=str(exc),
                details={"retry_after": exc.retry_after},
            ) from exc

        body = response.json()

        if not body.get("success"):
            error = body.get("error") or {}
            code_str = error.get("code", "PARSE_FAILED")
            try:
                code = ParseErrorCode(code_str)
            except ValueError:
                code = ParseErrorCode.PARSE_FAILED
            details = error.get("details") or {}
            if code == ParseErrorCode.PARSE_BACKPRESSURE and details.get("reason") == DOCLING_UNAVAILABLE_REASON:
                message = error.get("message", "Docling is not serving")
                if earlier_attempt_delivered(response):
                    # An earlier answer of this call (a 5xx) may have been Docling failing on the file: counted.
                    raise DoclingUnavailableError(message)
                # The service's Docling never received the file: wait for it, as the in-process path does.
                raise DoclingNotServing(message)
            if code == ParseErrorCode.PASSWORD_REQUIRED:
                # The typed error the legacy pipeline raises for the same file.
                raise PasswordProtectedError(
                    error.get("message", "The file is password-protected"),
                    details=details,
                )
            raise ParsingClientError(
                code=code,
                message=error.get("message", "Parsing failed"),
                details=details,
            )

        try:
            payload = _ParseSuccessPayload.model_validate(body)
        except ValidationError as exc:
            raise ParsingClientError(
                code=ParseErrorCode.PARSE_FAILED,
                message=f"Malformed response from parsing service: {exc}",
            ) from exc

        metadata = payload.metadata
        raw_document = payload.raw_document
        if raw_document is not None:
            # Docling-backed provider deferred block construction (incl. LLM table
            # enrichment) to keep the Parsing service stateless - finish it here.
            try:
                doc = await asyncio.to_thread(DoclingDocument.model_validate_json, raw_document)
            except ValidationError as exc:
                raise ParsingClientError(
                    code=ParseErrorCode.PARSE_FAILED,
                    message=f"Malformed raw_document from parsing service: {exc}",
                ) from exc
            block_container = await self._get_docling_processor().create_blocks(
                doc, skip_table_enrichment=skip_table_enrichment
            )
            caption_map = metadata.get("caption_map")
            if isinstance(caption_map, dict) and caption_map:
                apply_caption_map(block_container, caption_map, logger)
        else:
            block_container = BlocksContainer(**(payload.block_container or {}))

        try:
            provider_used = ParserProvider(payload.provider_used or ParserProvider.DEFAULT.value)
        except ValueError:
            provider_used = ParserProvider.DEFAULT

        return ParseResult(
            block_container=block_container,
            provider_used=provider_used,
            metadata=metadata,
        )

    async def list_providers(self) -> dict[str, list[str]]:
        """Return {format -> [provider_names]} from the service."""
        response = await self._get_json("/api/v1/parse/providers", operation="list_providers")
        if response.status_code != 200:
            raise ServiceCallError(
                f"ParsingService /providers returned {response.status_code}",
                status_code=response.status_code,
                service_name=self.service_name,
            )
        return response.json()
