"""Parser Registry — two-level registry: (format_key, ParserProvider) -> IParser.

Usage
-----
    registry = ParserRegistry()
    registry.register("pdf", ParserProvider.DOCLING, my_parser)
    registry.set_default("pdf", ParserProvider.DOCLING)
    registry.set_fallback_chain("pdf", [ParserProvider.DOCLING, ParserProvider.DEFAULT])

    # Resolve and parse
    result = await registry.parse_with_fallback(content, mime_type, extension, record_name)
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.parsing.interface import (
    IParser,
    ParseError,
    ParseErrorCode,
    ParserProvider,
)

logger = logging.getLogger(__name__)

# Canonical mime-type / extension strings mapped to a short format key
_MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "text/csv": "csv",
    "text/tab-separated-values": "tsv",
    "text/html": "html",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/mdx": "mdx",
    "application/json": "json",
    "application/yaml": "yaml",
    "text/yaml": "yaml",
    "application/x-yaml": "yaml",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    # Google workspace export types
    "application/vnd.google-apps.document": "docx",
    "application/vnd.google-apps.presentation": "pptx",
    "application/vnd.google-apps.spreadsheet": "xlsx",
    # Internal PipesHub types
    "application/blocks": "blocks",
    "text/gmail_content": "html",
    "application/vnd.google-apps.gmail": "html",
    "application/sql_table": "sql_table",
    "application/sql_view": "sql_view",
    # Canonical MIME types used by MimeTypes enum in arangodb.py
    "application/vnd.sql.table": "sql_table",
    "application/vnd.sql.view": "sql_view",
}

_EXT_TO_FORMAT: dict[str, str] = {
    "pdf": "pdf",
    "docx": "docx",
    "doc": "doc",
    "pptx": "pptx",
    "ppt": "ppt",
    "xlsx": "xlsx",
    "xls": "xls",
    "csv": "csv",
    "tsv": "tsv",
    "html": "html",
    "htm": "html",
    "txt": "txt",
    "md": "md",
    "mdx": "mdx",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "webp": "webp",
    "svg": "svg",
    "heic": "heic",
    "heif": "heif",
    "sql_table": "sql_table",
    "sql_view": "sql_view",
}


def _normalize_format(mime_type: str, extension: str) -> str | None:
    """Return a canonical format key or None if unknown."""
    if mime_type and mime_type in _MIME_TO_FORMAT:
        return _MIME_TO_FORMAT[mime_type]
    ext = (extension or "").lower().lstrip(".")
    return _EXT_TO_FORMAT.get(ext)


class ParserRegistry:
    """Two-level registry: format_key -> {ParserProvider -> IParser}."""

    def __init__(self) -> None:
        # format_key -> {provider -> parser}
        self._registry: dict[str, dict[ParserProvider, IParser]] = {}
        # format_key -> default provider
        self._default_provider: dict[str, ParserProvider] = {}
        # format_key -> ordered fallback chain (first = highest priority)
        self._fallback_chains: dict[str, list[ParserProvider]] = {}

    # ------------------------------------------------------------------
    # Registration API
    # ------------------------------------------------------------------

    def register(
        self,
        format_key: str,
        provider: ParserProvider,
        parser: IParser,
    ) -> None:
        """Register *parser* for *(format_key, provider)*.  Overwrites any
        existing registration for the same pair.
        """
        if format_key not in self._registry:
            self._registry[format_key] = {}
        self._registry[format_key][provider] = parser
        logger.debug("Registered parser %s for format '%s'", provider.value, format_key)

    def set_default(self, format_key: str, provider: ParserProvider) -> None:
        """Set *provider* as the default for *format_key*."""
        self._default_provider[format_key] = provider

    def set_fallback_chain(
        self,
        format_key: str,
        chain: list[ParserProvider],
    ) -> None:
        """Set the ordered fallback chain for *format_key*.

        When ``parse_with_fallback`` is called the registry tries each provider
        in *chain* order until one succeeds.
        """
        self._fallback_chains[format_key] = list(chain)

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def resolve(
        self,
        mime_type: str,
        extension: str,
        provider: ParserProvider | None = None,
    ) -> IParser:
        """Return the appropriate :class:`IParser`.

        If *provider* is ``None`` the registry uses the registered default for
        the resolved format key.  Raises :class:`ParseError` when no parser is
        found.
        """
        format_key = _normalize_format(mime_type, extension)
        if format_key is None:
            raise ParseError(
                ParseErrorCode.UNSUPPORTED_FORMAT,
                f"No format key for mime_type='{mime_type}' extension='{extension}'",
            )

        providers_map = self._registry.get(format_key, {})
        if not providers_map:
            raise ParseError(
                ParseErrorCode.UNSUPPORTED_FORMAT,
                f"No parsers registered for format '{format_key}'",
            )

        target_provider = provider or self._default_provider.get(format_key)
        if target_provider is None:
            # Pick the first registered provider as implicit default
            target_provider = next(iter(providers_map))

        parser = providers_map.get(target_provider)
        if parser is None:
            parser = providers_map.get(ParserProvider.DEFAULT)
            if parser is None:
                raise ParseError(
                    ParseErrorCode.PROVIDER_UNAVAILABLE,
                    f"Provider '{target_provider.value}' not registered for format '{format_key}'",
                    {"available": [p.value for p in providers_map]},
                )
        return parser

    def list_providers(self, format_key: str) -> list[ParserProvider]:
        """Return all providers registered for *format_key*."""
        return list(self._registry.get(format_key, {}).keys())

    def list_all_formats(self) -> dict[str, list[str]]:
        """Return a dict of format_key -> [provider_names]."""
        return {
            fmt: [p.value for p in providers]
            for fmt, providers in self._registry.items()
        }

    def get_default_provider(self, format_key: str) -> ParserProvider | None:
        return self._default_provider.get(format_key)

    def get_fallback_chain(self, format_key: str) -> list[ParserProvider]:
        return self._fallback_chains.get(format_key, [])

    # ------------------------------------------------------------------
    # Parse with automatic fallback
    # ------------------------------------------------------------------

