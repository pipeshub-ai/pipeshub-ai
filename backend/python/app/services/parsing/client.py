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

from app.models.blocks import BlocksContainer
from app.services.base_client import BaseServiceClient, ServiceCallError
from app.services.parsing.interface import (
    ParseErrorCode,
    ParseResult,
    ParserProvider,
)
from app.utils.converters.caption_map import apply_caption_map
from app.utils.image_utils import get_extension_from_mimetype

if TYPE_CHECKING:
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


class ParsingClient(BaseServiceClient):
    """Async HTTP client for the Parsing Service (port 8092)."""

    def __init__(
        self,
        service_url: str | None = None,
        read_timeout: float = 2400.0,  # 40 min — matching DoclingClient
        max_retries: int = 3,
        retry_delay: float = 2.0,
        config_service: object | None = None,
    ) -> None:
        super().__init__(
            service_url=service_url or os.getenv("PARSING_SERVICE_URL", "http://localhost:8092"),
            service_name="ParsingService",
            read_timeout=read_timeout,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
        # Docling-backed providers defer block construction to keep the Parsing
        # service stateless; this client completes that phase locally on demand.
        self._config_service = config_service
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
