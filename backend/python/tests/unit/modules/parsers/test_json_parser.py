"""Unit tests for app.modules.parsers.json.json_parser."""

import json

import pytest

from app.models.blocks import BlockType, DataFormat, GroupSubType, GroupType
from app.modules.parsers.json.json_parser import JSONParser
from app.services.parsing.interface import ParseError, ParseErrorCode


@pytest.fixture
def parser():
    return JSONParser()


def _bytes(data) -> bytes:
    return json.dumps(data).encode("utf-8")


class TestSupportedFormats:

    def test_supported_formats(self, parser):
        assert parser.supported_formats() == ["json"]


class TestParseErrors:

    @pytest.mark.asyncio
    async def test_empty_content_raises(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"", "empty.json")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_whitespace_only_content_raises(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"   \n\t  ", "blank.json")
        assert exc_info.value.code == ParseErrorCode.EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_invalid_json_raises(self, parser):
        with pytest.raises(ParseError) as exc_info:
            await parser.parse(b"{not valid json", "bad.json")
        assert exc_info.value.code == ParseErrorCode.PARSE_FAILED


class TestFlatObject:

    @pytest.mark.asyncio
    async def test_single_root_group_and_block(self, parser):
        data = {"name": "Widget", "price": 100}
        result = await parser.parse(_bytes(data), "widget.json")
        bc = result.block_container

        assert len(bc.block_groups) == 1
        assert len(bc.blocks) == 1

        root = bc.block_groups[0]
        assert root.type == GroupType.KEY_VALUE_AREA
        assert root.sub_type == GroupSubType.RECORD
        assert root.name == "widget.json"
        assert root.parent_index is None
        assert root.content_hash is not None

        block = bc.blocks[0]
        assert block.type == BlockType.TEXT
        assert block.format == DataFormat.JSON
        assert block.parent_index == root.index
        assert block.data == "name: Widget, price: 100"

    @pytest.mark.asyncio
    async def test_root_children_reference_block(self, parser):
        data = {"a": 1}
        result = await parser.parse(_bytes(data), "a.json")
        root = result.block_container.block_groups[0]
        assert root.children.block_ranges[0].start == 0
        assert root.children.block_ranges[0].end == 0
        assert root.children.block_group_ranges == []

    @pytest.mark.asyncio
    async def test_null_and_bool_values(self, parser):
        data = {"flag": True, "note": None}
        result = await parser.parse(_bytes(data), "flags.json")
        block = result.block_container.blocks[0]
        assert "flag: True" in block.data
        assert "note: null" in block.data


class TestNestedObject:

    @pytest.mark.asyncio
    async def test_nested_dict_creates_child_group(self, parser):
        # "config" itself contains a nested dict ("nested"), so it is promoted
        # to its own group rather than being flattened inline (see
        # test_single_level_nested_dict_flattened_inline for the shallow case).
        data = {"config": {"nested": {"host": "localhost", "port": 5432}}}
        result = await parser.parse(_bytes(data), "config.json")
        bc = result.block_container

        assert len(bc.block_groups) == 2
        root, child = bc.block_groups
        assert child.type == GroupType.KEY_VALUE_AREA
        assert child.name == "config"
        assert child.parent_index == root.index

        assert root.children.block_group_ranges[0].start == child.index
        assert root.children.block_ranges == []
        assert len(bc.blocks) == 1
        assert bc.blocks[0].parent_index == child.index
        assert bc.blocks[0].data == "config.nested.host: localhost, config.nested.port: 5432"

    @pytest.mark.asyncio
    async def test_single_level_nested_dict_flattened_inline(self, parser):
        """A dict-of-scalars nested one level deep is inlined, not promoted to
        its own group — only dicts that themselves contain nested dicts are."""
        data = {"config": {"host": "localhost", "nested": {"x": 1, "y": 2}}}
        result = await parser.parse(_bytes(data), "config.json")
        bc = result.block_container

        # "config" is nested_object (contains a nested dict) -> own group.
        # "nested" (only scalars) is flat_object -> inlined into config's block.
        assert len(bc.block_groups) == 2
        assert len(bc.blocks) == 1
        text = bc.blocks[0].data
        assert "config.host: localhost" in text
        assert "config.nested.x: 1" in text
        assert "config.nested.y: 2" in text

    @pytest.mark.asyncio
    async def test_depth_limit_falls_back_to_json_dump(self, parser):
        def make_chain(depth, leaf="leaf"):
            if depth == 0:
                return leaf
            return {"level": make_chain(depth - 1, leaf)}

        data = {"level": make_chain(9)}
        bc = parser.parse_data(data, "chain.json")

        # root + 6 levels of nested groups = 7; deeper levels collapse to JSON text.
        assert len(bc.block_groups) == 1 + parser.MAX_DEPTH
        assert len(bc.blocks) == 1
        assert "leaf" in bc.blocks[0].data
        assert bc.blocks[0].parent_index == bc.block_groups[-1].index


class TestScalarValues:

    @pytest.mark.asyncio
    async def test_long_string_gets_own_text_block(self, parser):
        long_text = "A" * 400
        data = {"name": "Widget", "description": long_text}
        result = await parser.parse(_bytes(data), "widget.json")
        bc = result.block_container

        assert len(bc.blocks) == 2
        long_block = next(b for b in bc.blocks if b.data == long_text)
        assert long_block.format == DataFormat.TXT
        assert long_block.type == BlockType.TEXT

        short_block = next(b for b in bc.blocks if b.data != long_text)
        assert short_block.data == "name: Widget"
        assert short_block.format == DataFormat.JSON

    @pytest.mark.asyncio
    async def test_short_string_stays_inline(self, parser):
        data = {"name": "x" * 299}
        result = await parser.parse(_bytes(data), "short.json")
        bc = result.block_container
        assert len(bc.blocks) == 1
        assert bc.blocks[0].format == DataFormat.JSON


class TestScalarArray:

    @pytest.mark.asyncio
    async def test_scalar_array_inlined(self, parser):
        data = {"tags": ["a", "b", "c"]}
        result = await parser.parse(_bytes(data), "tags.json")
        block = result.block_container.blocks[0]
        assert block.data == "tags: a, b, c"

    @pytest.mark.asyncio
    async def test_empty_array_inlined_as_bracket_string(self, parser):
        data = {"tags": []}
        result = await parser.parse(_bytes(data), "tags.json")
        block = result.block_container.blocks[0]
        assert block.data == "tags: []"


class TestObjectArray:

    @pytest.mark.asyncio
    async def test_array_of_objects_creates_table_group(self, parser):
        data = {
            "users": [
                {"name": "admin", "role": "superuser"},
                {"name": "guest", "role": "viewer"},
            ]
        }
        result = await parser.parse(_bytes(data), "users.json")
        bc = result.block_container

        assert len(bc.block_groups) == 2
        table_group = bc.block_groups[1]
        assert table_group.type == GroupType.TABLE
        assert table_group.name == "users"
        assert table_group.table_metadata.num_of_rows == 2
        assert table_group.table_metadata.column_names == ["name", "role"]
        assert table_group.content_hash is not None

        rows = [b for b in bc.blocks if b.type == BlockType.TABLE_ROW]
        assert len(rows) == 2
        assert rows[0].data["row_natural_language_text"] == "name: admin, role: superuser"
        assert json.loads(rows[0].data["row"]) == {"name": "admin", "role": "superuser"}
        assert rows[0].parent_index == table_group.index

    @pytest.mark.asyncio
    async def test_top_level_array_of_objects(self, parser):
        data = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = await parser.parse(_bytes(data), "records.json")
        bc = result.block_container

        assert len(bc.block_groups) == 2
        root, table_group = bc.block_groups
        assert table_group.type == GroupType.TABLE
        assert root.children.block_group_ranges[0].start == table_group.index
        assert root.children.block_ranges == []
        assert len(bc.blocks) == 3

    @pytest.mark.asyncio
    async def test_nested_dict_within_row_is_flattened(self, parser):
        data = {"users": [{"name": "Alice", "address": {"city": "NYC"}}]}
        result = await parser.parse(_bytes(data), "users.json")
        row = next(b for b in result.block_container.blocks if b.type == BlockType.TABLE_ROW)
        assert row.data["row_natural_language_text"] == "name: Alice, address.city: NYC"

    @pytest.mark.asyncio
    async def test_non_dict_items_skipped_in_object_array(self, parser):
        # 3/4 items are dicts (>= OBJECT_ARRAY_MIN_DICT_RATIO) so the list is
        # still classified as an object_array; the stray string is skipped.
        data = {"items": [{"a": 1}, {"b": 2}, {"c": 3}, "stray"]}
        result = await parser.parse(_bytes(data), "items.json")
        bc = result.block_container
        rows = [b for b in bc.blocks if b.type == BlockType.TABLE_ROW]
        assert len(rows) == 3


class TestMixedArray:

    @pytest.mark.asyncio
    async def test_mixed_array_falls_back_to_json_text(self, parser):
        data = {"values": [1, "two", {"three": 3}]}
        result = await parser.parse(_bytes(data), "mixed.json")
        block = result.block_container.blocks[0]
        assert block.data.startswith("values: ")
        assert '"three": 3' in block.data


class TestTopLevelShapes:

    @pytest.mark.asyncio
    async def test_top_level_scalar_array(self, parser):
        data = ["a", "b", "c"]
        result = await parser.parse(_bytes(data), "list.json")
        bc = result.block_container
        assert len(bc.block_groups) == 1
        assert len(bc.blocks) == 1
        assert bc.blocks[0].data == "a, b, c"

    @pytest.mark.asyncio
    async def test_top_level_empty_object(self, parser):
        result = await parser.parse(_bytes({}), "empty_obj.json")
        bc = result.block_container
        assert len(bc.block_groups) == 1
        assert bc.blocks == []
        assert bc.block_groups[0].children.block_ranges == []

    @pytest.mark.asyncio
    async def test_top_level_bare_scalar(self, parser):
        result = await parser.parse(_bytes("hello"), "bare.json")
        bc = result.block_container
        assert len(bc.blocks) == 1
        assert bc.blocks[0].data == "hello"


class TestContentHash:

    def test_content_hash_deterministic(self, parser):
        data = {"a": 1}
        bc1 = parser.parse_data(data, "a.json")
        bc2 = parser.parse_data(data, "a.json")
        assert bc1.block_groups[0].content_hash == bc2.block_groups[0].content_hash

    def test_content_hash_changes_with_data(self, parser):
        bc1 = parser.parse_data({"a": 1}, "a.json")
        bc2 = parser.parse_data({"a": 2}, "a.json")
        assert bc1.block_groups[0].content_hash != bc2.block_groups[0].content_hash

    def test_block_content_hash_set(self, parser):
        bc = parser.parse_data({"a": 1}, "a.json")
        assert bc.blocks[0].content_hash is not None
        assert ":" in bc.blocks[0].content_hash


class TestParseDataFormatTagging:

    def test_default_format_is_json(self, parser):
        bc = parser.parse_data({"a": "x"}, "a.json")
        assert bc.block_groups[0].format == DataFormat.JSON
        assert bc.blocks[0].format == DataFormat.JSON

    def test_custom_format_propagates(self, parser):
        bc = parser.parse_data({"a": "x"}, "a.yaml", data_format=DataFormat.YAML)
        assert bc.block_groups[0].format == DataFormat.YAML
        assert bc.blocks[0].format == DataFormat.YAML
