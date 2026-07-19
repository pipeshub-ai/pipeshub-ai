"""Unit tests for app.modules.parsers.yaml.yaml_parser."""

import json

import pytest

from app.models.blocks import BlockType, DataFormat, GroupType
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.yaml.yaml_parser import YAMLParser
from app.services.parsing.interface import ParseError, ParseErrorCode


@pytest.fixture
def parser():
    return YAMLParser()


class TestSupportedFormats:

    def test_supported_formats(self, parser):
        assert parser.supported_formats() == ["yaml", "yml"]


class TestParseErrors:

    @pytest.mark.asyncio
    async def test_empty_content_raises(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"", "empty.yaml")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_whitespace_only_content_raises(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"   \n  ", "blank.yaml")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_invalid_yaml_raises(self, parser):
        bad_yaml = b"key: [unclosed\n  - broken"
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(bad_yaml, "bad.yaml")
        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED

    @pytest.mark.asyncio
    async def test_document_with_only_null_raises_empty(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"null", "null.yaml")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT


class TestBasicYAML:

    @pytest.mark.asyncio
    async def test_flat_mapping(self, parser):
        content = b"name: Widget\nprice: 100\n"
        result = await parser.parse(content, "widget.yaml")
        bc = result.block_container

        assert len(bc.block_groups) == 1
        assert len(bc.blocks) == 1
        assert bc.blocks[0].data == "name: Widget, price: 100"
        assert bc.blocks[0].format == DataFormat.YAML
        assert bc.block_groups[0].format == DataFormat.YAML

    @pytest.mark.asyncio
    async def test_metadata_document_count(self, parser):
        content = b"name: Widget\n"
        result = await parser.parse(content, "widget.yaml")
        assert result.metadata["document_count"] == 1

    @pytest.mark.asyncio
    async def test_nested_mapping_creates_group(self, parser):
        content = b"""
metadata:
  name: my-app
  labels:
    app: my-app
    env: prod
"""
        result = await parser.parse(content, "manifest.yaml")
        bc = result.block_container

        assert len(bc.block_groups) == 2
        child = bc.block_groups[1]
        assert child.type == GroupType.KEY_VALUE_AREA
        assert child.name == "metadata"
        assert bc.blocks[0].data == (
            "metadata.name: my-app, metadata.labels.app: my-app, metadata.labels.env: prod"
        )

    @pytest.mark.asyncio
    async def test_list_of_mappings_creates_table(self, parser):
        content = b"""
containers:
  - name: web
    image: nginx:latest
  - name: sidecar
    image: envoy:latest
"""
        result = await parser.parse(content, "manifest.yaml")
        bc = result.block_container

        table_group = next(g for g in bc.block_groups if g.type == GroupType.TABLE)
        assert table_group.name == "containers"
        assert table_group.table_metadata.num_of_rows == 2

        rows = [b for b in bc.blocks if b.type == BlockType.TABLE_ROW]
        assert len(rows) == 2
        assert rows[0].data["row_natural_language_text"] == "name: web, image: nginx:latest"


class TestMultiDocumentYAML:

    @pytest.mark.asyncio
    async def test_multi_document_treated_as_list(self, parser):
        content = b"""
name: doc1
---
name: doc2
"""
        result = await parser.parse(content, "multi.yaml")
        assert result.metadata["document_count"] == 2

        bc = result.block_container
        table_group = next(g for g in bc.block_groups if g.type == GroupType.TABLE)
        assert table_group.table_metadata.num_of_rows == 2

        rows = [json.loads(b.data["row"]) for b in bc.blocks if b.type == BlockType.TABLE_ROW]
        assert {"name": "doc1"} in rows
        assert {"name": "doc2"} in rows


class TestDelegationToJSONParser:

    def test_uses_injected_json_parser(self):
        json_parser = JSONParser()
        yaml_parser = YAMLParser(json_parser)
        assert yaml_parser._json_parser is json_parser

    def test_parse_data_delegates_with_yaml_format(self, parser):
        bc = parser.parse_data({"a": "x"}, "a.yaml")
        assert bc.block_groups[0].format == DataFormat.YAML
        assert bc.blocks[0].format == DataFormat.YAML
