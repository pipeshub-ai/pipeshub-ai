"""YAML Parser.

Thin wrapper around :class:`JSONParser` — YAML is decoded to plain Python
data via ``yaml.safe_load`` and handed to the same schema-aware,
natural-language chunking walker used for JSON, tagged with
``DataFormat.YAML`` so provenance is preserved on blocks/groups.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import yaml

from app.models.blocks import BlocksContainer, DataFormat
from app.modules.parsers.json.json_parser import JSONParser
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.utils.logger import create_logger

logger = create_logger("yaml_parser")


class YAMLParser:
    """Parser for YAML bytes -> BlocksContainer, delegating to JSONParser."""

    def __init__(self, json_parser: Optional[JSONParser] = None) -> None:
        self._json_parser = json_parser or JSONParser()

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> ParseResult:
        if not content or not content.strip():
            raise ParseError(ParseErrorCode.EMPTY_CONTENT, "YAML content is empty")

        try:
            # yaml.safe_load_all + the tree walk below are synchronous CPU
            # work; keep large documents off the event loop.
            documents = await asyncio.to_thread(self._load_documents, content)
        except Exception as e:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Failed to parse YAML for '{record_name}': {e}",
                {"error": str(e)},
            )

        if not documents:
            raise ParseError(ParseErrorCode.EMPTY_CONTENT, "YAML content has no documents")

        data: Any = documents[0] if len(documents) == 1 else documents
        block_container = await asyncio.to_thread(self.parse_data, data, record_name)
        return ParseResult(
            block_container=block_container,
            metadata={"record_name": record_name, "document_count": len(documents)},
        )

    def supported_formats(self) -> List[str]:
        return ["yaml", "yml"]

    @staticmethod
    def _load_documents(content: bytes) -> List[Any]:
        """Sync YAML decode. Called via ``asyncio.to_thread`` from :meth:`parse`.

        ``safe_load_all`` handles both single-document and multi-document
        (``---``-separated) YAML; single-document files yield one item.
        """
        return [doc for doc in yaml.safe_load_all(content.decode("utf-8")) if doc is not None]

    def parse_data(self, data: Any, record_name: str) -> BlocksContainer:
        return self._json_parser.parse_data(data, record_name, data_format=DataFormat.YAML)
