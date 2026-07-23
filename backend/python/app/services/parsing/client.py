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

import logging
import os
from typing import Any

from app.models.blocks import BlocksContainer
from app.services.base_client import BaseServiceClient, ServiceCallError
from app.services.parsing.interface import (
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.utils.image_utils import get_extension_from_mimetype

logger = logging.getLogger(__name__)


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


class ParsingClient(BaseServiceClient):
    """Async HTTP client for the Parsing Service (port 8092)."""

    def __init__(
        self,
        service_url: str | None = None,
        # Must stay below RECORD_PROCESSING_TIMEOUT (default 1800s) so the
        # Kafka consumer's per-record timeout never fires first and leaves
        # this request's server-side work orphaned. See DoclingClient, whose
        # timeout must in turn stay below this one (nested call).
        read_timeout: float = 1500.0,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> None:
        super().__init__(
            service_url=service_url or os.getenv("PARSING_SERVICE_URL", "http://localhost:8092"),
            service_name="ParsingService",
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )

    async def parse(
        self,
        file_content: bytes,
        record_name: str,
        mime_type: str = "",
        extension: str = "",
        org_id: str | None = None,
        provider: ParserProvider | None = None,
        skip_table_enrichment: bool = False,
    ) -> ParseResult:
        """Parse *file_content* by calling the Parsing Service.

        Returns a :class:`ParseResult` on success.
        Raises :class:`ParsingClientError` on expected failures.
        Raises :class:`ServiceCallError` on connection / retry exhaustion.
        """
        if not extension:
            extension = get_extension_from_mimetype(mime_type)
            if not extension and "." in record_name:
                extension = record_name.rsplit(".", 1)[-1]

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

        response = await self._post_multipart(
            "/api/v1/parse",
            files={"file": (record_name, file_content, mime_type or "application/octet-stream")},
            data=form_data,
            operation="parse",
        )

        body = response.json()

        if not body.get("success"):
            error = body.get("error") or {}
            code_str = error.get("code", "PARSE_FAILED")
            try:
                code = ParseErrorCode(code_str)
            except ValueError:
                code = ParseErrorCode.PARSE_FAILED
            raise ParsingClientError(
                code=code,
                message=error.get("message", "Parsing failed"),
                details=error.get("details", {}),
            )

        bc_dict = body.get("block_container") or {}
        block_container = BlocksContainer(**bc_dict)

        provider_used_str = body.get("provider_used", ParserProvider.DEFAULT.value)
        try:
            provider_used = ParserProvider(provider_used_str)
        except ValueError:
            provider_used = ParserProvider.DEFAULT

        return ParseResult(
            block_container=block_container,
            provider_used=provider_used,
            metadata=body.get("metadata") or {},
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
