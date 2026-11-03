"""Unit tests for app.modules.parsers.yaml.yaml_parser."""

import json

import pytest
import yaml

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
    async def test_document_with_only_null_raises_empty(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"null", "null.yaml")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_non_utf8_raises_parse_failed(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"key: \xff\xfe", "latin.yaml")
        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED


class TestUnloadableYAMLFallsBackToStructuralParser:

    @pytest.mark.asyncio
    async def test_invalid_yaml_is_chunked_instead_of_failing(self, parser):
        bad_yaml = b"key: [unclosed\n  - broken"
        result = await parser.parse(bad_yaml, "bad.yaml")
        assert result.metadata["parser"] == "structural"
        assert result.metadata["template_dialect"] is None
        assert result.block_container.blocks[0].data == bad_yaml.decode()

    @pytest.mark.asyncio
    async def test_helm_template_routes_to_structural_parser(self, parser):
        helm = (
            b"{{- if .Values.enabled }}\n"
            b"apiVersion: v1\nkind: Service\nmetadata:\n"
            b"  name: {{ include \"app.fullname\" . }}\n"
            b"{{- end }}\n"
        )
        result = await parser.parse(helm, "service.yaml")
        assert result.metadata["parser"] == "structural"
        assert result.metadata["template_dialect"] == "go"
        assert result.metadata["document_count"] == 1
        bc = result.block_container
        assert bc.block_groups[0].format == DataFormat.YAML
        assert '{{ include "app.fullname" . }}' in bc.blocks[0].data

    @pytest.mark.asyncio
    async def test_valid_yaml_with_quoted_template_syntax_stays_on_walker(self, parser):
        content = b'value: "{{ .Release.Name }}-redis"\nrun: echo ${{ github.sha }}\n'
        result = await parser.parse(content, "workflow.yaml")
        assert "parser" not in result.metadata
        assert result.block_container.blocks[0].data == (
            "value: {{ .Release.Name }}-redis, run: echo ${{ github.sha }}"
        )


class TestLocalTagsAreKept:

    @pytest.mark.asyncio
    async def test_cloudformation_and_gitlab_tags_load(self, parser):
        content = (
            b"Bucket: !Ref MyBucket\n"
            b"Arn: !Sub 'arn:${AWS::Region}'\n"
            b"script: !reference [.setup, script]\n"
        )
        result = await parser.parse(content, "template.yaml")
        assert "parser" not in result.metadata
        assert result.block_container.blocks[0].data == (
            "Bucket.!Ref: MyBucket, Arn.!Sub: arn:${AWS::Region}, script.!reference: .setup, script"
        )

    def test_python_object_tags_are_still_rejected(self):
        with pytest.raises(yaml.constructor.ConstructorError, match="python/object"):
            YAMLParser._load_documents("!!python/object/apply:os.system ['id']")


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
        assert table_group.table_metadata.num_of_cols == 2
        assert table_group.table_metadata.num_of_cells == 4

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


class TestDockerComposeShape:

    @pytest.mark.asyncio
    async def test_services_nested_mapping_builds_groups(self, parser):
        content = b"""
version: "3.8"
services:
  redis:
    image: redis:7
    ports:
      - "6379:6379"
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: mydb
"""
        result = await parser.parse(content, "docker-compose.yml")
        bc = result.block_container

        assert bc.block_groups[0].format == DataFormat.YAML
        assert any(g.name == "services" for g in bc.block_groups)
        assert len(bc.blocks) >= 1
        joined = " ".join(b.data if isinstance(b.data, str) else str(b.data) for b in bc.blocks)
        assert "redis" in joined or any(g.name == "redis" for g in bc.block_groups)
        assert "postgres" in joined or any(g.name == "postgres" for g in bc.block_groups)
