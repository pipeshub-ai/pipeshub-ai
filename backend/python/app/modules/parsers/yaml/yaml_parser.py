"""YAML Parser.

Thin wrapper around :class:`JSONParser` — YAML is decoded to plain Python
data via ``yaml.safe_load_all`` and handed to the same schema-aware,
natural-language chunking walker used for JSON, tagged with
``DataFormat.YAML`` so provenance is preserved on blocks/groups.

Two things extend what plain ``safe_load`` accepts:

- unknown local tags (CloudFormation ``!Ref``/``!Sub``, GitLab CI
  ``!reference``, Ansible ``!vault``, Home Assistant ``!include``) are kept
  as ``{"!Tag": value}`` instead of aborting the load;
- files PyYAML still cannot load -- templated YAML such as Helm charts,
  Ansible/Jinja and ERB, or hand-written files with syntax errors -- are
  chunked verbatim by :class:`StructuralYAMLParser` rather than failing.
"""
from __future__ import annotations

import asyncio
from typing import Any

import yaml

from app.models.blocks import BlocksContainer, DataFormat
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.yaml.structural_yaml_parser import StructuralYAMLParser
from app.modules.parsers.yaml.template_dialect import detect_template_dialect
from app.services.parsing.interface import ParseError, ParseErrorCode, ParseResult
from app.utils.logger import create_logger

logger = create_logger("yaml_parser")


class _TagKeepingLoader(yaml.SafeLoader):
    """SafeLoader that keeps unknown ``!local`` tags as data.

    Only ``!``-prefixed local tags are affected; ``!!python/...`` resolves to
    the ``tag:yaml.org,2002:`` namespace and stays rejected.
    """


def _keep_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> dict[str, Any]:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {"!" + suffix: value}


_TagKeepingLoader.add_multi_constructor("!", _keep_tag)


class YAMLParser:
    """Parser for YAML bytes -> BlocksContainer, delegating to JSONParser."""

    def __init__(
        self,
        json_parser: JSONParser | None = None,
        structural_parser: StructuralYAMLParser | None = None,
    ) -> None:
        self._json_parser = json_parser or JSONParser()
        self._structural_parser = structural_parser or StructuralYAMLParser()

    async def parse(
        self,
        content: bytes,
        record_name: str,
        config: dict[str, Any] | None = None,
    ) -> ParseResult:
        if not content or not content.strip():
            raise ParseError(ParseErrorCode.EMPTY_CONTENT, "YAML content is empty")

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Failed to decode YAML for '{record_name}': {e}",
                {"error": str(e)},
            ) from e

        try:
            # yaml.safe_load_all + the tree walk below are synchronous CPU
            # work; keep large documents off the event loop.
            documents = await asyncio.to_thread(self._load_documents, text)
        except yaml.YAMLError as e:
            return await self._parse_structurally(text, record_name, e)

        if not documents:
            raise ParseError(ParseErrorCode.EMPTY_CONTENT, "YAML content has no documents")

        data: Any = documents[0] if len(documents) == 1 else documents
        block_container = await asyncio.to_thread(self.parse_data, data, record_name)
        return ParseResult(
            block_container=block_container,
            metadata={"record_name": record_name, "document_count": len(documents)},
        )

    def supported_formats(self) -> list[str]:
        return ["yaml", "yml"]

    @staticmethod
    def _load_documents(text: str) -> list[Any]:
        """Sync YAML decode. Called via ``asyncio.to_thread`` from :meth:`parse`.

        ``load_all`` handles both single-document and multi-document
        (``---``-separated) YAML; single-document files yield one item.
        """
        return [doc for doc in yaml.load_all(text, Loader=_TagKeepingLoader) if doc is not None]

    async def _parse_structurally(
        self, text: str, record_name: str, yaml_error: yaml.YAMLError
    ) -> ParseResult:
        """Fallback for text PyYAML rejects: templated YAML or broken syntax."""
        dialect = detect_template_dialect(text)
        logger.info(
            "YAML for '%s' is not loadable (%s); chunking structurally as %s",
            record_name,
            str(yaml_error).splitlines()[0] if str(yaml_error) else type(yaml_error).__name__,
            dialect.value if dialect else "plain text",
        )
        try:
            parsed = await asyncio.to_thread(
                self._structural_parser.parse_text, text, record_name, dialect
            )
        except Exception as e:
            raise ParseError(
                ParseErrorCode.PARSE_FAILED,
                f"Failed to parse YAML for '{record_name}': {yaml_error}",
                {"error": str(yaml_error), "structural_error": str(e)},
            ) from e
        return ParseResult(
            block_container=parsed.container,
            metadata={
                "record_name": record_name,
                "document_count": parsed.document_count,
                "parser": "structural",
                "template_dialect": dialect.value if dialect else None,
            },
        )

    def parse_data(self, data: Any, record_name: str) -> BlocksContainer:
        return self._json_parser.parse_data(data, record_name, data_format=DataFormat.YAML)
