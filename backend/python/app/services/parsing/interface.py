"""Core abstractions for the parsing service.

IParser    -- protocol every parser implementation must satisfy
ParserProvider -- enum of parser backends (docling_service, ocr, native, …)
ParseResult    -- typed output carrying BlocksContainer + provenance metadata
ParseError     -- structured error surfaced by the parsing service
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.models.blocks import BlocksContainer


class ParserProvider(str, Enum):
    """Identifies which backend implementation performed the parse."""

    DEFAULT = "default"
    DOCLING = "docling"


class ParseResult(BaseModel):
    """Output of a successful parse operation."""

    block_container: BlocksContainer
    provider_used: ParserProvider | None = None
    # Free-form parser-specific metadata (page_count, ocr_pages, was_fallback, …)
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = {"arbitrary_types_allowed": True}


@runtime_checkable
class IParser(Protocol):
    """Contract every parser provider implementation must satisfy.

    Implementations are *not* required to subclass anything — structural
    subtyping (duck-typing checked at runtime via isinstance) is enough.
    """

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        """Parse *content* into a :class:`ParseResult`.

        Args:
            content:     Raw file bytes (or JSON bytes for ``application/blocks``).
            record_name: Human-readable filename used for logging and Docling
                         document-name hints.
            config:      Optional caller-supplied overrides (e.g. ``skip_table_enrichment``).

        Returns:
            :class:`ParseResult` on success.

        Raises:
            :class:`ParseError` on unrecoverable failure.
            Any other exception on unexpected errors (caller treats these as
            ``PARSE_FAILED``).
        """
        ...  # pragma: no cover

    def supported_formats(self) -> list[str]:
        """Return the format keys (mime-type or extension) this parser handles."""
        ...  # pragma: no cover


class ParseErrorCode(str, Enum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    PARSE_FAILED = "PARSE_FAILED"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_INPUT = "INVALID_INPUT"
    NO_PROVIDER_PROVIDED = "NO_PROVIDER_PROVIDED"


class ParseError(Exception):
    """Structured error raised by parser implementations."""

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "details": self.details,
        }
