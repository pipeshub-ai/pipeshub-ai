"""Unit tests for app.modules.parsers.json.json_parser.JSONParser."""

import json

import pytest

from app.models.blocks import BlocksContainer, BlockSubType, BlockType, DataFormat
from app.modules.parsers.json.json_parser import JSONParser


@pytest.fixture
def parser():
    return JSONParser()


class TestParse:
    def test_returns_blocks_container(self, parser):
        result = parser.parse('{"a": 1}')
        assert isinstance(result, BlocksContainer)

    def test_flat_object(self, parser):
        result = parser.parse('{"name": "Alice", "age": 30}')
        texts = [b.data for b in result.blocks]
        assert "name: Alice" in texts
        assert "age: 30" in texts

    def test_nested_object_dotted_path(self, parser):
        result = parser.parse('{"user": {"address": {"city": "NYC"}}}')
        assert "user.address.city: NYC" in [b.data for b in result.blocks]

    def test_array_indexed_path(self, parser):
        result = parser.parse('{"items": [{"name": "foo"}, {"name": "bar"}]}')
        texts = [b.data for b in result.blocks]
        assert "items[0].name: foo" in texts
        assert "items[1].name: bar" in texts

    def test_top_level_array(self, parser):
        result = parser.parse('["a", "b"]')
        texts = [b.data for b in result.blocks]
        assert "[0]: a" in texts
        assert "[1]: b" in texts

    def test_accepts_bytes(self, parser):
        result = parser.parse(b'{"k": "v"}')
        assert "k: v" in [b.data for b in result.blocks]

    def test_primitive_rendering(self, parser):
        result = parser.parse('{"b": true, "n": null, "f": 1.5}')
        texts = [b.data for b in result.blocks]
        assert "b: true" in texts
        assert "n: null" in texts
        assert "f: 1.5" in texts

    def test_empty_containers_keep_a_leaf(self, parser):
        result = parser.parse('{"obj": {}, "arr": []}')
        texts = [b.data for b in result.blocks]
        assert "obj: {}" in texts
        assert "arr: []" in texts

    def test_block_type_and_format(self, parser):
        result = parser.parse('{"k": "v"}')
        block = result.blocks[0]
        assert block.type == BlockType.TEXT
        assert block.sub_type == BlockSubType.PARAGRAPH
        assert block.format == DataFormat.TXT

    def test_block_indices_sequential(self, parser):
        result = parser.parse('{"a": 1, "b": 2, "c": 3}')
        assert [b.index for b in result.blocks] == list(range(len(result.blocks)))

    def test_invalid_json_raises(self, parser):
        with pytest.raises(json.JSONDecodeError):
            parser.parse("{not valid json}")
