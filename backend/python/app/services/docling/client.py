from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from app.exceptions.indexing_exceptions import DocumentProcessingError, IndexingError
from app.models.blocks import BlocksContainer
from app.services.base_client import (
    TRANSIENT_STATUS_CODES,
    BaseServiceClient,
    ServiceAuthRefusedError,
    ServiceBackpressureError,
    ServiceCallError,
    ServiceUnavailableError,
)
from app.services.messaging.backpressure import get_default_backpressure_coordinator
from app.utils.env_config import env_int
from app.utils.error_class import classify_error, mark_error

if TYPE_CHECKING:
    import contextlib

    from app.services.messaging.backpressure import BackpressureCoordinator
import httpx
from docling_core.types.doc.document import DoclingDocument

from app.config.constants.http_status_code import HttpStatusCode
from app.utils.logger import create_logger
from app.utils.pdf_utils import PAGE_BATCH_SIZE, get_pdf_page_count

MAX_PDF_BYTES = 100 * 1024 * 1024
# Milliseconds a small conversion may wait for its turn at the Docling service (PARSE_ADMISSION=classes).
PARSE_DEADLINE_HEADER = "X-Parse-Deadline-Ms"


class DoclingUnavailableError(IndexingError):
    """Docling was not serving: it is down, refused this service, is shedding load,
    or failed outside the conversion (a 5xx, a connection dropped mid-answer).

    An ``IndexingError`` so that the record is retried later. Read as a failed
    parse, it sent every PDF to the per-page OCR fallback while Docling was down.
    """

    reason = "unavailable"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DoclingParseError(DocumentProcessingError):
    """Docling gave no document for this PDF; the caller reads it another way.

    ``status_code`` is Docling's HTTP status (None when it gave no answer) and
    ``reason`` Docling's ``errorCode`` in lower case, or why nothing was sent.
    """

    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def within_docling_size_limit(size_bytes: int) -> bool:
    return size_bytes <= MAX_PDF_BYTES


MAX_PAGES_ENV = "PDF_DOCLING_MAX_PAGES"
DEFAULT_MAX_PAGES = 30


_warned_negative_max_pages: set[int] = set()


def docling_max_pages() -> int:
    """Text PDFs with more pages than this go to the PdfPlumber+OpenCV parser."""
    value = env_int(MAX_PAGES_ENV, DEFAULT_MAX_PAGES)
    if value >= 0:
        return value
    if value not in _warned_negative_max_pages:
        _warned_negative_max_pages.add(value)
        logging.getLogger(__name__).warning(
            "Ignoring negative %s=%d; falling back to %d", MAX_PAGES_ENV, value, DEFAULT_MAX_PAGES
        )
    return DEFAULT_MAX_PAGES


def docling_takes(size_bytes: int, page_count: int | None) -> bool:
    """Whether a text PDF goes to Docling rather than the PdfPlumber+OpenCV parser.

    An unknown page count goes to Docling: it reads PDFs with pdfium, which opens
    files pdfminer cannot count.
    """
    if not within_docling_size_limit(size_bytes):
        return False
    return page_count is None or page_count <= docling_max_pages()


def _refusal(status: int, code: object) -> DoclingParseError:
    """Docling's verdict on the file is its ``errorCode``, or any 2xx (only Docling
    itself answers 2xx; before #3270 a failed parse was a 200 without a code). A
    non-2xx answer without a code means the two services disagree on the API
    (version skew, a wrong URL): our fault, not the file's."""
    has_code = isinstance(code, str) and bool(code)
    if has_code or 200 <= status < 300:
        verdict = f"{code}, status {status}" if has_code else f"status {status}"
        exc = DoclingParseError(
            f"Docling could not parse the document ({verdict})",
            status_code=status, reason=code.lower() if has_code else None,
        )
        mark_error(exc, "bad_request")
        return exc
    exc = DoclingParseError(f"Docling refused the request (status {status})", status_code=status)
    mark_error(exc, "misconfigured")
    return exc


def _docling_unavailable(exc: ServiceCallError) -> bool:
    """Docling answers a file it cannot parse with a 422 and its errorCode. A status the base client retried and gave
    up on (a 5xx, a 429 without Retry-After) or a connection dropped mid-answer means it was not serving."""
    return (
        isinstance(exc, (ServiceUnavailableError, ServiceAuthRefusedError, ServiceBackpressureError))
        or exc.status_code in TRANSIENT_STATUS_CODES
        or isinstance(exc.__cause__, (httpx.ReadError, httpx.RemoteProtocolError))
    )


class DoclingClient(BaseServiceClient):
    """Client for communicating with the Docling processing service.

    Extends :class:`BaseServiceClient` for its retry/circuit-breaker/429
    handling. Every failure is raised, :class:`DoclingUnavailableError` or
    :class:`DoclingParseError`, and logged by the caller that falls back.
    """

    def __init__(
        self,
        service_url: Optional[str] = None,
        timeout: float = 2450.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
    ) -> None:
        from app.modules.parse_engine import wiring as parse_wiring

        # PARSE_ADMISSION=classes: a Docling 429 slows Docling calls only. It never reaches the coordinator (which
        # pauses the consumers' reads) or the governor's downstream feedback; the client's own limiter takes it.
        isolated = parse_wiring.classes_enabled()
        # 2400s (Docling's own internal PDF_PROCESSING_TIMEOUT_SECONDS) plus a
        # margin for the resource governor's admission-gate wait on the
        # Docling side (see docling_service.py's DOCLING_GATE_TIMEOUT_SECONDS)
        # — without this margin, a request that legitimately queues for
        # admission and then runs to Docling's own timeout could have its
        # response arrive after this client already gave up.
        super().__init__(
            service_url=service_url or os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081"),
            service_name="DoclingService",
            read_timeout=timeout,
            write_timeout=60.0,  # PDF uploads
            max_retries=max_retries,
            retry_delay=retry_delay,
            backpressure_coordinator=(
                None if isolated else backpressure_coordinator or get_default_backpressure_coordinator()
            ),
        )
        self.timeout = timeout
        self._limiter = None
        if isolated:
            from app.modules.parse_engine.engines import shared_docling_engine

            self._limiter = shared_docling_engine()
            self.on_backpressure = self._limiter.on_throttled

    def _validate_pdf_binary(self, pdf_binary: bytes) -> None:
        if not isinstance(pdf_binary, bytes):
            raise DoclingParseError(f"pdf_binary is {type(pdf_binary).__name__}, not bytes", reason="invalid_input")
        if not within_docling_size_limit(len(pdf_binary)):
            raise DoclingParseError(f"PDF of {len(pdf_binary)} bytes is over {MAX_PDF_BYTES}", reason="too_large")

    def _class_headers(self) -> dict[str, str]:
        """``X-Parse-Class`` and, for a small file, ``X-Parse-Deadline-Ms`` (the service reserves the conversion
        under the class and gives up a small one that has not started by its deadline)."""
        from app.capacity.classes import CostClass
        from app.config.constants.ai_models import PARSE_CLASS_HEADER
        from app.modules.parse_engine.engines import docling_deadline_s
        from app.modules.parse_engine.pools import current_class, current_est_seconds

        cls = current_class()
        if cls is None:
            return {}
        headers = {PARSE_CLASS_HEADER: cls.value}
        if cls is CostClass.SMALL:
            headers[PARSE_DEADLINE_HEADER] = str(int(docling_deadline_s(current_est_seconds() or 0.0) * 1000))
        return headers

    def _attempt_slot(self) -> contextlib.AbstractAsyncContextManager[Any]:
        """Under classes every HTTP attempt takes a slot of the Docling limiter, so a retry after a ``Retry-After``
        wait is admitted again at the limit that wait reduced, not at the one it started with."""
        return self._limiter.slot() if self._limiter is not None else super()._attempt_slot()

    async def _call_parse_pdf(self, record_name: str, pdf_binary: bytes, form_data: dict) -> httpx.Response:
        """One ``/parse-pdf`` call; under classes each HTTP attempt holds a slot of the Docling limiter."""
        response = await self._post_multipart(
            "/parse-pdf",
            files={"file": (record_name, pdf_binary, "application/pdf")},
            data=form_data,
            operation="parse_pdf",  # no name: this text rides on the cause of every failure
            extra_headers=self._class_headers() if self._limiter is not None else None,
        )
        if self._limiter is not None:
            self._limiter.on_success()
        return response

    async def parse_pdf(
        self,
        record_name: str,
        pdf_binary: bytes,
        page_range: tuple[int, int] | None = None,
    ) -> str:
        """
        Parse PDF using the external Docling service (phase 1 - no block creation).

        Args:
            record_name: Name of the record/document
            pdf_binary: Binary PDF data
            page_range: Optional 1-based inclusive (start, end) page range.

        Returns:
            Serialized parse result (JSON-encoded document).

        Raises:
            DoclingUnavailableError: Docling was not serving (down, refused this service, shedding load,
                failed outside the conversion).
            DoclingParseError: Docling gave no document for this PDF.
        """
        self._validate_pdf_binary(pdf_binary)

        form_data: dict = {"record_name": record_name}
        if page_range is not None:
            form_data["start_page"] = str(page_range[0])
            form_data["end_page"] = str(page_range[1])

        try:
            response = await self._call_parse_pdf(record_name, pdf_binary, form_data)
        except ServiceCallError as exc:
            if _docling_unavailable(exc):
                raise DoclingUnavailableError(
                    "Docling is not serving", status_code=exc.status_code,
                ) from exc
            failed = DoclingParseError(
                "Docling request failed" if exc.status_code is None else f"Docling failed with status {exc.status_code}",
                status_code=exc.status_code,
            )
            # Only a client-side request error is left (a wrong URL scheme, a bad header): ours, not the file's.
            mark_error(failed, classify_error(exc))
            raise failed from exc

        try:
            result = await asyncio.to_thread(response.json)
        except ValueError as exc:
            not_json = DoclingParseError(
                f"Docling's answer was not JSON (status {response.status_code})", status_code=response.status_code,
            )
            # Docling always answers JSON: this came from a proxy or a wrong URL, whatever the status.
            mark_error(not_json, "misconfigured")
            raise not_json from exc
        if not result.get("success"):
            raise _refusal(response.status_code, result.get("errorCode"))

        parse_result = result.get("parse_result")
        if not isinstance(parse_result, str):
            # Docling sends its document as a string on success: anything else is API skew, not this file.
            skew = DoclingParseError(
                f"Docling's answer had no document (status {response.status_code})", status_code=response.status_code,
            )
            mark_error(skew, "misconfigured")
            raise skew
        return parse_result

    async def parse_pdf_batched(
        self, record_name: str, pdf_binary: bytes, batch_size: int = PAGE_BATCH_SIZE
    ) -> DoclingDocument:
        """Parse a PDF via the Docling service, splitting it into page-range batches
        to cap Docling's peak memory usage, then concatenate the results into a
        single DoclingDocument.

        Raises what :meth:`parse_pdf` raises; a PDF whose pages cannot be
        counted is a :class:`DoclingParseError`.
        """
        self._validate_pdf_binary(pdf_binary)

        try:
            page_count = await asyncio.to_thread(get_pdf_page_count, pdf_binary)
        except Exception as e:
            raise DoclingParseError("Could not count the PDF's pages", reason="unreadable") from e

        if page_count <= batch_size:
            serialized = await self.parse_pdf(record_name, pdf_binary)
            return await asyncio.to_thread(DoclingDocument.model_validate_json, serialized)

        self.logger.info(
            f"Parsing '{record_name}' ({page_count} pages) in batches of {batch_size} pages"
        )
        docs: list[DoclingDocument] = []
        for start in range(1, page_count + 1, batch_size):
            end = min(start + batch_size - 1, page_count)
            serialized = await self.parse_pdf(record_name, pdf_binary, page_range=(start, end))
            docs.append(await asyncio.to_thread(DoclingDocument.model_validate_json, serialized))
            self.logger.info(f"Parsed pages {start}-{end} of {page_count} for '{record_name}'")

        merged = await asyncio.to_thread(DoclingDocument.concatenate, docs)
        # concatenate() names the result by joining every input name with " + ".
        merged.name = record_name
        return merged

    async def _check_service_health(self, client: httpx.AsyncClient) -> bool:
        """
        Internal method to check service health using an existing client

        Args:
            client: Existing httpx.AsyncClient instance

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = await client.get(
                f"{self.service_url}/health",
                timeout=10.0
            )
            is_healthy = response.status_code == HttpStatusCode.SUCCESS.value
            if is_healthy:
                self.logger.info("✅ Docling service is healthy")
            else:
                self.logger.warning(f"⚠️ Docling service health check returned status {response.status_code}")
            return is_healthy
        except httpx.ConnectError:
            self.logger.error("❌ Cannot connect to Docling service - service appears to be down")
            return False
        except Exception as e:
            self.logger.error(f"❌ Health check failed: {str(e)}")
            return False

    async def health_check(self) -> bool:
        """Check if the Docling service is healthy"""
        try:
            timeout_config = httpx.Timeout(
                connect=10.0,
                read=10.0,
                write=10.0,
                pool=10.0
            )

            async with httpx.AsyncClient(timeout=timeout_config) as client:
                return await self._check_service_health(client)
        except Exception as e:
            self.logger.error(f"❌ Health check failed: {str(e)}")
            return False
