"""HTTP client for the Docling processing service.

Delegates PDF processing/parsing/block-creation to the standalone Docling
service (port 8081). Built on :class:`BaseServiceClient` so it gets the same
retry, circuit-breaker, and persistent-connection-pool behaviour as the
Parsing and Extraction clients -- fixes propagate here automatically instead
of needing to be duplicated in this file's own retry loop.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Optional

from app.models.blocks import BlocksContainer
from app.services.base_client import BaseServiceClient
from app.utils.request_context import inject_request_headers

MAX_PDF_SIZE_BYTES = 100 * 1024 * 1024  # 100MB


class DoclingClientError(Exception):
    """Raised when the Docling service explicitly reports a processing failure
    (bad input, unsupported document, etc.) -- as opposed to a transport/HTTP
    failure, which raises :class:`app.services.base_client.ServiceCallError`.
    """

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}


class DoclingClient(BaseServiceClient):
    """Async HTTP client for the Docling processing service (port 8081)."""

    def __init__(self, service_url: Optional[str] = None, timeout: float = 1200.0) -> None:
        super().__init__(
            service_url=service_url or os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081"),
            service_name="DoclingService",
            read_timeout=timeout,
            max_retries=3,
            retry_delay=1.0,
        )
        # Kept as a plain attribute (rather than relying on the base class's
        # internal timeout object) since callers/tests inspect it directly.
        self.timeout = timeout

    @staticmethod
    def _validate_pdf(pdf_binary: bytes) -> None:
        if not isinstance(pdf_binary, bytes):
            raise DoclingClientError(
                f"Invalid pdf_binary type: expected bytes, got {type(pdf_binary).__name__}"
            )
        if len(pdf_binary) > MAX_PDF_SIZE_BYTES:
            raise DoclingClientError(
                f"PDF too large for processing: {len(pdf_binary)} bytes (max: {MAX_PDF_SIZE_BYTES})"
            )

    def _parse_blocks_container(self, block_containers_data: Any) -> BlocksContainer:
        """Create BlocksContainer from dict or JSON string. Runs in a thread
        pool by callers to avoid blocking the event loop on large documents.
        """
        try:
            if isinstance(block_containers_data, str):
                block_containers_dict = json.loads(block_containers_data)
            else:
                block_containers_dict = block_containers_data
            return BlocksContainer(**block_containers_dict)
        except Exception as e:
            self.logger.error(f"Failed to parse blocks container: {e}")
            raise

    @staticmethod
    def _raise_if_unsuccessful(result: dict[str, Any]) -> None:
        if not result.get("success"):
            raise DoclingClientError(result.get("error", "Unknown error"))

    async def process_pdf(
        self, record_name: str, pdf_binary: bytes, org_id: Optional[str] = None
    ) -> BlocksContainer:
        """Process a PDF end-to-end (parse + block creation) via the Docling service.

        Raises:
            DoclingClientError: pdf_binary invalid/too large, or the service explicitly failed.
            ServiceCallError / ServiceUnavailableError: transport/HTTP failure after retries exhausted.
        """
        self._validate_pdf(pdf_binary)
        payload = {
            "record_name": record_name,
            "pdf_binary": base64.b64encode(pdf_binary).decode("utf-8"),
            "org_id": org_id,
        }
        response = await self._post_json(
            "/process-pdf", payload, operation="process_pdf", extra_headers=inject_request_headers()
        )
        result = await asyncio.to_thread(response.json)
        self._raise_if_unsuccessful(result)
        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])

    async def parse_pdf(self, record_name: str, pdf_binary: bytes) -> str:
        """Phase 1: parse a PDF with no LLM calls. Returns the serialized parse result.

        Raises the same exceptions as :meth:`process_pdf`.
        """
        self._validate_pdf(pdf_binary)
        payload = {
            "record_name": record_name,
            "pdf_binary": base64.b64encode(pdf_binary).decode("utf-8"),
        }
        response = await self._post_json(
            "/parse-pdf", payload, operation="parse_pdf", extra_headers=inject_request_headers()
        )
        result = await asyncio.to_thread(response.json)
        self._raise_if_unsuccessful(result)
        return result["parse_result"]

    async def create_blocks(
        self, parse_result: str, page_number: Optional[int] = None
    ) -> BlocksContainer:
        """Phase 2: create blocks from a parse result (involves LLM calls for tables).

        Raises the same exceptions as :meth:`process_pdf`.
        """
        payload = {"parse_result": parse_result, "page_number": page_number}
        response = await self._post_json(
            "/create-blocks", payload, operation="create_blocks", extra_headers=inject_request_headers()
        )
        result = await asyncio.to_thread(response.json)
        self._raise_if_unsuccessful(result)
        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])
