from __future__ import annotations

import asyncio
import json
import os
from typing import TYPE_CHECKING, Optional

from app.models.blocks import BlocksContainer
from app.services.base_client import BaseServiceClient, ServiceCallError
from app.services.messaging.backpressure import get_default_backpressure_coordinator

if TYPE_CHECKING:
    from app.services.messaging.backpressure import BackpressureCoordinator

MAX_PDF_BYTES = 100 * 1024 * 1024


class DoclingClient(BaseServiceClient):
    """Client for communicating with the Docling processing service.

    Extends :class:`BaseServiceClient` for its retry/circuit-breaker/429
    handling; failures are swallowed here (logged, ``None`` returned) so the
    ``Optional[...]`` contract callers (e.g. ``DoclingServiceParser``) already
    depend on is unchanged.
    """

    def __init__(
        self,
        service_url: Optional[str] = None,
        timeout: float = 2450.0,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backpressure_coordinator: "BackpressureCoordinator | None" = None,
    ) -> None:
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
            backpressure_coordinator=backpressure_coordinator or get_default_backpressure_coordinator(),
        )
        self.timeout = timeout

    def _parse_blocks_container(self, block_containers_data) -> BlocksContainer:
        """
        Create BlocksContainer object from dictionary or JSON string.
        This method runs in a thread pool to avoid blocking the event loop.
        """
        try:
            # Handle both dict and JSON string cases
            if isinstance(block_containers_data, str):
                block_containers_dict = json.loads(block_containers_data)
            else:
                block_containers_dict = block_containers_data

            return BlocksContainer(**block_containers_dict)
        except Exception as e:
            self.logger.error(f"❌ Failed to parse blocks container: {str(e)}")
            raise

    def _validate_pdf_binary(self, pdf_binary: bytes) -> bool:
        if not isinstance(pdf_binary, bytes):
            self.logger.error(f"❌ Invalid pdf_binary type: expected bytes, got {type(pdf_binary).__name__}")
            return False

        if len(pdf_binary) > MAX_PDF_BYTES:
            self.logger.error(f"❌ PDF too large for processing: {len(pdf_binary)} bytes (max: {MAX_PDF_BYTES} bytes)")
            return False

        return True

    async def process_pdf(self, record_name: str, pdf_binary: bytes) -> Optional[BlocksContainer]:
        """Parse a PDF and build its blocks via the Docling service in a single request.

        The service parses the PDF in page batches internally, so the binary is uploaded
        once regardless of page count.

        Returns:
            BlocksContainer if successful, None if failed
        """
        if not self._validate_pdf_binary(pdf_binary):
            return None

        try:
            response = await self._post_multipart(
                "/process-pdf",
                files={"file": (record_name, pdf_binary, "application/pdf")},
                data={"record_name": record_name},
                operation=f"process_pdf({record_name})",
            )
        except ServiceCallError as exc:
            self.logger.error(f"❌ Processing PDF {record_name} failed: {exc}")
            return None

        result = await asyncio.to_thread(response.json)
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error for {record_name}: {result.get('error', 'Unknown error')}")
            return None

        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])

    async def parse_pdf(
        self,
        record_name: str,
        pdf_binary: bytes,
        page_range: tuple[int, int] | None = None,
    ) -> Optional[str]:
        """
        Parse PDF using the external Docling service (phase 1 - no block creation).

        Args:
            record_name: Name of the record/document
            pdf_binary: Binary PDF data
            page_range: Optional 1-based inclusive (start, end) page range.

        Returns:
            Serialized parse result (JSON-encoded document) if successful, None if failed
        """
        if not self._validate_pdf_binary(pdf_binary):
            return None

        form_data: dict = {"record_name": record_name}
        if page_range is not None:
            form_data["start_page"] = str(page_range[0])
            form_data["end_page"] = str(page_range[1])

        try:
            response = await self._post_multipart(
                "/parse-pdf",
                files={"file": (record_name, pdf_binary, "application/pdf")},
                data=form_data,
                operation=f"parse_pdf({record_name})",
            )
        except ServiceCallError as exc:
            self.logger.error(f"❌ Parsing PDF {record_name} failed: {exc}")
            return None

        result = await asyncio.to_thread(response.json)
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error for {record_name}: {result.get('error', 'Unknown error')}")
            return None

        return result["parse_result"]

    async def create_blocks(self, parse_result: str, page_number: int = None) -> Optional[BlocksContainer]:
        """
        Create blocks from parse result using the external Docling service (phase 2).

        Args:
            parse_result: Serialized parse result from parse_pdf
            page_number: Optional page number for page-specific processing

        Returns:
            BlocksContainer if successful, None if failed
        """
        try:
            response = await self._post_json(
                "/create-blocks",
                {"parse_result": parse_result, "page_number": page_number},
                operation="create_blocks",
            )
        except ServiceCallError as exc:
            self.logger.error(f"❌ Creating blocks failed: {exc}")
            return None

        result = await asyncio.to_thread(response.json)
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error: {result.get('error', 'Unknown error')}")
            return None

        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])
