"""Unit tests for app.modules.transformers.block_container_validator."""

from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from app.exceptions.indexing_exceptions import BlockContainerValidationError
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlockType,
    BlocksContainer,
    DataFormat,
    GroupType,
    IndexRange,
    TableMetadata,
)
from app.modules.transformers.block_container_validator import (
    BlockContainerValidator,
    Severity,
    ValidationIssue,
    _MAX_CANDIDATES,
    _decode_b64_prefix,
    _is_image_data,
    contains_base64_image,
    strip_base64_images,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validator(**kwargs) -> BlockContainerValidator:
    return BlockContainerValidator(**kwargs)


def _block(
    index: int,
    *,
    block_type=BlockType.TEXT,
    data: str = "",
    fmt=DataFormat.TXT,
    parent_index=None,
    sub_type=None,
) -> Block:
    """Build a block. Default TEXT + empty data skips content-specific checks."""
    return Block(
        index=index,
        type=block_type,
        data=data,
        format=fmt,
        parent_index=parent_index,
        sub_type=sub_type,
    )


def _block_construct(**kwargs) -> Block:
    """Build a Block bypassing Pydantic enum/required checks (for edge-case tests)."""
    defaults = {
        "index": 0,
        "type": BlockType.TEXT,
        "data": "",
        "format": DataFormat.TXT,
        "parent_index": None,
    }
    defaults.update(kwargs)
    return Block.model_construct(**defaults)


def _text_block(index: int, data: str = "hello", fmt=DataFormat.TXT) -> Block:
    return _block(index, block_type=BlockType.TEXT, data=data, fmt=fmt)


def _image_block(index: int, data=None, fmt=DataFormat.BASE64) -> Block:
    return _block(index, block_type=BlockType.IMAGE, data=data, fmt=fmt)


def _table_row_block(
    index: int,
    *,
    data=None,
    parent_index=0,
    fmt=DataFormat.JSON,
) -> Block:
    return _block(
        index,
        block_type=BlockType.TABLE_ROW,
        data=data,
        fmt=fmt,
        parent_index=parent_index,
    )


def _block_group(
    index: int,
    *,
    group_type=GroupType.TEXT_SECTION,
    parent_index=None,
    children=None,
    table_metadata=None,
    data=None,
) -> BlockGroup:
    return BlockGroup(
        index=index,
        type=group_type,
        parent_index=parent_index,
        children=children,
        table_metadata=table_metadata,
        data=data,
    )


def _table_group(
    index: int,
    *,
    children=None,
    table_metadata=None,
    parent_index=None,
) -> BlockGroup:
    return _block_group(
        index,
        group_type=GroupType.TABLE,
        children=children,
        table_metadata=table_metadata,
        parent_index=parent_index,
    )


def _container(blocks=None, block_groups=None) -> BlocksContainer:
    return BlocksContainer(blocks=blocks or [], block_groups=block_groups or [])


def _error_codes(exc: BlockContainerValidationError) -> set[str]:
    return {e.code for e in exc.errors}


def _warning_codes(warnings: list[ValidationIssue]) -> set[str]:
    return {w.code for w in warnings}


def _table_group_with_linked_row(
    row_index: int = 0,
    *,
    table_metadata=None,
) -> BlockGroup:
    """TABLE group with children.block_ranges pointing at the given row index."""
    children = BlockGroupChildren(
        block_ranges=[IndexRange(start=row_index, end=row_index)]
    )
    return _table_group(
        0,
        children=children,
        table_metadata=table_metadata or TableMetadata(num_of_cells=2),
    )


def _assert_raises_with_codes(container: BlocksContainer, *codes: str) -> None:
    with pytest.raises(BlockContainerValidationError) as exc_info:
        _validator().validate(container)
    assert _error_codes(exc_info.value) == set(codes)


# ---------------------------------------------------------------------------
# Batch 1: Index contiguity (_check_index_contiguity / _check_list_indices)
# ---------------------------------------------------------------------------


class TestIndexContiguity:
    def test_valid_contiguous_blocks_and_groups(self):
        container = _container(
            blocks=[_block(0), _block(1), _block(2)],
            block_groups=[_block_group(0), _block_group(1)],
        )
        assert _validator().validate(container) == []

    def test_empty_container_passes(self):
        assert _validator().validate(_container()) == []

    def test_single_block_at_index_zero(self):
        container = _container(blocks=[_block(0)])
        assert _validator().validate(container) == []

    def test_single_group_at_index_zero(self):
        container = _container(block_groups=[_block_group(0)])
        assert _validator().validate(container) == []

    def test_block_index_null(self):
        block = _block_construct(index=None)
        _assert_raises_with_codes(_container(blocks=[block]), "INDEX_NULL")

    def test_block_group_index_null(self):
        group = BlockGroup.model_construct(index=None, type=GroupType.TEXT_SECTION)
        _assert_raises_with_codes(_container(block_groups=[group]), "INDEX_NULL")

    def test_block_index_mismatch(self):
        container = _container(blocks=[_block(0), _block(5)])
        _assert_raises_with_codes(container, "INDEX_MISMATCH")

    def test_block_group_index_mismatch(self):
        container = _container(block_groups=[_block_group(0), _block_group(3)])
        _assert_raises_with_codes(container, "INDEX_MISMATCH")

    def test_non_zero_starting_index(self):
        container = _container(blocks=[_block(1), _block(2)])
        _assert_raises_with_codes(container, "INDEX_MISMATCH")

    def test_gap_in_block_indices(self):
        container = _container(blocks=[_block(0), _block(1), _block(3)])
        _assert_raises_with_codes(container, "INDEX_MISMATCH")

    def test_duplicate_block_index(self):
        container = _container(blocks=[_block(0), _block(0)])
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        codes = _error_codes(exc_info.value)
        assert "INDEX_DUPLICATE" in codes
        assert "INDEX_MISMATCH" in codes

    def test_duplicate_block_group_index(self):
        container = _container(block_groups=[_block_group(0), _block_group(0)])
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        codes = _error_codes(exc_info.value)
        assert "INDEX_DUPLICATE" in codes
        assert "INDEX_MISMATCH" in codes

    def test_multiple_index_issues_reported_together(self):
        b0 = _block(0)
        b1 = _block_construct(index=None)
        b2 = _block(2)
        container = _container(blocks=[b0, b1, b2])
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        assert "INDEX_NULL" in _error_codes(exc_info.value)

    def test_check_list_indices_directly(self):
        issues: list[ValidationIssue] = []
        items = [_block(0), _block(2)]
        BlockContainerValidator._check_list_indices("block", items, issues)
        assert len(issues) == 1
        assert issues[0].code == "INDEX_MISMATCH"
        assert issues[0].location == "block[1]"


# ---------------------------------------------------------------------------
# Batch 2: Parent-child linkage (_check_parent_child_linkage)
# ---------------------------------------------------------------------------


class TestParentChildLinkage:
    def test_valid_block_parent_index(self):
        groups = [_table_group_with_linked_row(table_metadata=TableMetadata(num_of_cells=2))]
        blocks = [
            _table_row_block(
                0,
                data={"cells": ["a", "b"]},
                parent_index=0,
            )
        ]
        assert _validator().validate(_container(blocks=blocks, block_groups=groups)) == []

    def test_block_parent_index_none_allowed(self):
        container = _container(blocks=[_block(0, parent_index=None)])
        assert _validator().validate(container) == []

    def test_block_parent_index_out_of_range(self):
        container = _container(
            blocks=[_block(0, parent_index=5)],
            block_groups=[_block_group(0)],
        )
        _assert_raises_with_codes(container, "PARENT_INDEX_OUT_OF_RANGE")

    def test_block_parent_index_negative(self):
        container = _container(blocks=[_block(0, parent_index=-1)])
        _assert_raises_with_codes(container, "PARENT_INDEX_OUT_OF_RANGE")

    def test_group_parent_index_out_of_range(self):
        container = _container(block_groups=[_block_group(0, parent_index=3)])
        _assert_raises_with_codes(container, "PARENT_INDEX_OUT_OF_RANGE")

    def test_group_direct_self_reference_cycle(self):
        container = _container(block_groups=[_block_group(0, parent_index=0)])
        _assert_raises_with_codes(container, "PARENT_INDEX_CYCLE")

    def test_group_two_hop_cycle(self):
        container = _container(
            block_groups=[
                _block_group(0, parent_index=1),
                _block_group(1, parent_index=0),
            ]
        )
        _assert_raises_with_codes(container, "PARENT_INDEX_CYCLE")

    def test_group_three_hop_cycle(self):
        container = _container(
            block_groups=[
                _block_group(0, parent_index=1),
                _block_group(1, parent_index=2),
                _block_group(2, parent_index=0),
            ]
        )
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        assert "PARENT_INDEX_CYCLE" in _error_codes(exc_info.value)

    def test_valid_parent_chain(self):
        container = _container(
            block_groups=[
                _block_group(
                    0,
                    children=BlockGroupChildren(
                        block_group_ranges=[IndexRange(start=1, end=1)]
                    ),
                ),
                _block_group(
                    1,
                    parent_index=0,
                    children=BlockGroupChildren(
                        block_group_ranges=[IndexRange(start=2, end=2)]
                    ),
                ),
                _block_group(2, parent_index=1),
            ]
        )
        assert _validator().validate(container) == []

    def test_children_block_range_out_of_range(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=5, end=5)])
        container = _container(
            blocks=[_block(0)],
            block_groups=[_block_group(0, children=children)],
        )
        _assert_raises_with_codes(container, "CHILDREN_BLOCK_INDEX_OUT_OF_RANGE")

    def test_children_block_range_huge_end_single_error(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=10**9)])
        container = _container(
            blocks=[_block(0)],
            block_groups=[_block_group(0, children=children)],
        )
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        range_errors = [
            e for e in exc_info.value.errors
            if e.code == "CHILDREN_BLOCK_INDEX_OUT_OF_RANGE"
        ]
        assert len(range_errors) == 1
        assert "[0, 1000000000]" in range_errors[0].message

    def test_children_block_range_inverted(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=3, end=1)])
        container = _container(
            blocks=[_block(0), _block(1), _block(2)],
            block_groups=[_block_group(0, children=children)],
        )
        _assert_raises_with_codes(container, "CHILDREN_BLOCK_INDEX_OUT_OF_RANGE")

    def test_children_group_range_huge_end_single_error(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=0, end=10**9)])
        container = _container(
            block_groups=[
                _block_group(0, children=children),
                _block_group(1),
            ]
        )
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        range_errors = [
            e for e in exc_info.value.errors
            if e.code == "CHILDREN_GROUP_INDEX_OUT_OF_RANGE"
        ]
        assert len(range_errors) == 1

    def test_children_group_range_out_of_range(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=2, end=2)])
        container = _container(block_groups=[_block_group(0, children=children)])
        _assert_raises_with_codes(container, "CHILDREN_GROUP_INDEX_OUT_OF_RANGE")

    def test_children_parent_index_mismatch_block(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)])
        groups = [
            _block_group(0),
            _block_group(1, children=children),
        ]
        blocks = [_block(0, parent_index=0)]
        warnings = _validator().validate(_container(blocks=blocks, block_groups=groups))
        assert "CHILDREN_PARENT_INDEX_MISMATCH" in _warning_codes(warnings)

    def test_children_parent_index_mismatch_group(self):
        children = BlockGroupChildren(block_group_ranges=[IndexRange(start=1, end=1)])
        groups = [
            _block_group(0),
            _block_group(1, parent_index=0),
            _block_group(2, children=children),
        ]
        warnings = _validator().validate(_container(block_groups=groups))
        assert "CHILDREN_PARENT_INDEX_MISMATCH" in _warning_codes(warnings)

    def test_reverse_linkage_missing_block(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [
            _table_row_block(
                0,
                data={"cells": ["a"]},
                parent_index=0,
            )
        ]
        warnings = _validator().validate(_container(blocks=blocks, block_groups=groups))
        assert "REVERSE_LINKAGE_MISSING" in _warning_codes(warnings)

    def test_reverse_linkage_missing_group(self):
        groups = [
            _block_group(0),
            _block_group(1, parent_index=0),
        ]
        warnings = _validator().validate(_container(block_groups=groups))
        assert "REVERSE_LINKAGE_MISSING" in _warning_codes(warnings)

    def test_valid_bidirectional_linkage(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)])
        groups = [_table_group(0, children=children, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [
            _table_row_block(
                0,
                data={"cells": ["cell"]},
                parent_index=0,
            )
        ]
        assert _validator().validate(_container(blocks=blocks, block_groups=groups)) == []


# ---------------------------------------------------------------------------
# Batch 3: TEXT blocks (_check_text_blocks)
# ---------------------------------------------------------------------------


class TestTextBlocks:
    def test_valid_text_block(self):
        container = _container(blocks=[_text_block(0, data="hello", fmt=DataFormat.TXT)])
        assert _validator().validate(container) == []

    def test_valid_markdown_format(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.MARKDOWN)])
        assert _validator().validate(container) == []

    def test_empty_string_data_allowed(self):
        container = _container(blocks=[_text_block(0, data="")])
        assert _validator().validate(container) == []

    def test_text_data_not_string_none(self):
        container = _container(blocks=[_text_block(0, data=None)])
        _assert_raises_with_codes(container, "TEXT_DATA_NOT_STRING")

    def test_text_data_not_string_int(self):
        container = _container(blocks=[_text_block(0, data=123)])
        _assert_raises_with_codes(container, "TEXT_DATA_NOT_STRING")

    def test_text_data_not_string_dict(self):
        container = _container(blocks=[_text_block(0, data={"text": "x"})])
        _assert_raises_with_codes(container, "TEXT_DATA_NOT_STRING")

    def test_text_data_not_string_list(self):
        container = _container(blocks=[_text_block(0, data=["text"])])
        _assert_raises_with_codes(container, "TEXT_DATA_NOT_STRING")

    def test_text_format_missing_warns(self):
        container = _container(
            blocks=[_block_construct(data="hello", format=None)]
        )
        warnings = _validator().validate(container)
        assert "TEXT_FORMAT_MISSING" in _warning_codes(warnings)

    def test_valid_code_format(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.CODE)])
        assert _validator().validate(container) == []

    def test_valid_html_format(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.HTML)])
        assert _validator().validate(container) == []

    def test_text_format_unexpected_warns(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.XML)])
        warnings = _validator().validate(container)
        assert "TEXT_FORMAT_UNEXPECTED" in _warning_codes(warnings)

    def test_valid_json_format(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.JSON)])
        assert _validator().validate(container) == []

    def test_valid_yaml_format(self):
        container = _container(blocks=[_text_block(0, fmt=DataFormat.YAML)])
        assert _validator().validate(container) == []


# ---------------------------------------------------------------------------
# Batch 4: IMAGE blocks (_check_image_blocks)
# ---------------------------------------------------------------------------


class TestImageBlocks:
    _VALID_DATA_URI = "data:image/png;base64,iVBORw0KGgo="

    def test_valid_image_with_data_uri(self):
        container = _container(
            blocks=[_image_block(0, data={"uri": self._VALID_DATA_URI})]
        )
        assert _validator().validate(container) == []

    def test_image_uri_invalid_https_url(self):
        container = _container(
            blocks=[_image_block(0, data={"uri": "https://example.com/img.png"})]
        )
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID")

    def test_image_uri_invalid_http_url(self):
        container = _container(
            blocks=[_image_block(0, data={"uri": "http://example.com/img.png"})]
        )
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID")

    def test_image_format_not_base64(self):
        container = _container(
            blocks=[
                _image_block(0, fmt=DataFormat.TXT, data={"uri": self._VALID_DATA_URI})
            ]
        )
        _assert_raises_with_codes(container, "IMAGE_FORMAT_NOT_BASE64")

    def test_image_format_none(self):
        container = _container(
            blocks=[
                _block_construct(
                    type=BlockType.IMAGE,
                    format=None,
                    data={"uri": self._VALID_DATA_URI},
                )
            ]
        )
        _assert_raises_with_codes(container, "IMAGE_FORMAT_NOT_BASE64")

    def test_image_data_missing_warns(self):
        container = _container(blocks=[_image_block(0, data=None)])
        warnings = _validator().validate(container)
        assert "IMAGE_DATA_MISSING" in _warning_codes(warnings)

    def test_image_data_raw_string_raises(self):
        container = _container(blocks=[_image_block(0, data="iVBORw0KGgo=")])
        _assert_raises_with_codes(container, "IMAGE_DATA_NOT_NORMALIZED")

    def test_image_uri_empty_warns(self):
        container = _container(blocks=[_image_block(0, data={"uri": ""})])
        warnings = _validator().validate(container)
        assert "IMAGE_URI_EMPTY" in _warning_codes(warnings)

    def test_image_uri_missing_warns(self):
        container = _container(blocks=[_image_block(0, data={})])
        warnings = _validator().validate(container)
        assert "IMAGE_URI_EMPTY" in _warning_codes(warnings)

    def test_image_uri_invalid_type_bool(self):
        container = _container(blocks=[_image_block(0, data={"uri": True})])
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID_TYPE")

    def test_image_uri_invalid_type_int(self):
        container = _container(blocks=[_image_block(0, data={"uri": 123})])
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID_TYPE")

    def test_image_uri_invalid_string(self):
        container = _container(blocks=[_image_block(0, data={"uri": "not-a-url"})])
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID")

    def test_image_uri_invalid_ftp(self):
        container = _container(
            blocks=[_image_block(0, data={"uri": "ftp://example.com/img.png"})]
        )
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID")

    def test_image_uri_invalid_data_url_missing_base64_marker(self):
        container = _container(blocks=[_image_block(0, data={"uri": "data:image/png"})])
        _assert_raises_with_codes(container, "IMAGE_URI_INVALID")

    def test_image_data_invalid_type(self):
        container = _container(blocks=[_image_block(0, data=123)])
        _assert_raises_with_codes(container, "IMAGE_DATA_INVALID_TYPE")


# ---------------------------------------------------------------------------
# Batch 5: TABLE_ROW blocks (_check_table_row_blocks)
# ---------------------------------------------------------------------------


class TestTableRowBlocks:
    def _valid_table_container(self, row_data=None, row_index=0):
        groups = [_table_group_with_linked_row(row_index=row_index)]
        blocks = [
            _table_row_block(
                row_index,
                data=row_data or {"cells": ["a", "b"]},
                parent_index=0,
            )
        ]
        return _container(blocks=blocks, block_groups=groups)

    def test_valid_table_row_with_cells(self):
        assert _validator().validate(self._valid_table_container()) == []

    def test_valid_table_row_with_row_text(self):
        container = self._valid_table_container(
            row_data={"row_natural_language_text": "Row one: a, b"}
        )
        assert _validator().validate(container) == []

    def test_valid_table_row_with_row_number(self):
        container = self._valid_table_container(
            row_data={"cells": ["a"], "row_number": 1}
        )
        assert _validator().validate(container) == []

    def test_table_row_format_not_json(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [
            _table_row_block(
                0,
                fmt=DataFormat.TXT,
                data={"cells": ["a"]},
                parent_index=0,
            )
        ]
        _assert_raises_with_codes(_container(blocks=blocks, block_groups=groups), "TABLE_ROW_FORMAT_NOT_JSON")

    def test_table_row_data_missing(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [_table_row_block(0, data=None, parent_index=0)]
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(_container(blocks=blocks, block_groups=groups))
        codes = _error_codes(exc_info.value)
        assert "TABLE_ROW_DATA_MISSING" in codes
        assert "TABLE_ROW_NO_EMBEDDABLE_CONTENT" not in codes

    def test_table_row_data_not_dict(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [_table_row_block(0, data="row text", parent_index=0)]
        _assert_raises_with_codes(
            _container(blocks=blocks, block_groups=groups),
            "TABLE_ROW_DATA_NOT_DICT",
        )

    def test_table_row_parent_index_missing(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [_table_row_block(0, data={"cells": ["a"]}, parent_index=None)]
        _assert_raises_with_codes(
            _container(blocks=blocks, block_groups=groups),
            "TABLE_ROW_PARENT_INDEX_MISSING",
        )

    def test_table_row_parent_not_table(self):
        groups = [_block_group(0, group_type=GroupType.TEXT_SECTION)]
        blocks = [_table_row_block(0, data={"cells": ["a"]}, parent_index=0)]
        _assert_raises_with_codes(
            _container(blocks=blocks, block_groups=groups),
            "TABLE_ROW_PARENT_NOT_TABLE",
        )

    def test_table_row_no_embeddable_content(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [_table_row_block(0, data={}, parent_index=0)]
        warnings = _validator().validate(_container(blocks=blocks, block_groups=groups))
        assert "TABLE_ROW_NO_EMBEDDABLE_CONTENT" in _warning_codes(warnings)

    def test_table_row_container_with_fragment_children_skips_warning(self):
        groups = [_table_group_with_linked_row(0)]
        blocks = [
            _table_row_block(0, data={"row_number": 1}, parent_index=0),
            Block(
                index=1,
                type=BlockType.TEXT,
                data="**Status**: Done",
                format=DataFormat.MARKDOWN,
                parent_block_index=0,
            ),
        ]
        warnings = _validator().validate(_container(blocks=blocks, block_groups=groups))
        assert "TABLE_ROW_NO_EMBEDDABLE_CONTENT" not in _warning_codes(warnings)

    def test_table_row_empty_cells_and_empty_row_text(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [
            _table_row_block(
                0,
                data={"cells": [], "row_natural_language_text": "   "},
                parent_index=0,
            )
        ]
        warnings = _validator().validate(_container(blocks=blocks, block_groups=groups))
        assert "TABLE_ROW_NO_EMBEDDABLE_CONTENT" in _warning_codes(warnings)

    def test_table_row_cells_not_list(self):
        container = self._valid_table_container(row_data={"cells": "not-a-list"})
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        assert _error_codes(exc_info.value) == {"TABLE_ROW_CELLS_NOT_LIST"}

    def test_table_row_cell_not_string(self):
        container = self._valid_table_container(row_data={"cells": [1, 2]})
        _assert_raises_with_codes(container, "TABLE_ROW_CELL_NOT_STRING")

    def test_table_row_text_not_string_with_cells(self):
        container = self._valid_table_container(
            row_data={"cells": ["a", "b"], "row_natural_language_text": 123}
        )
        _assert_raises_with_codes(container, "TABLE_ROW_TEXT_NOT_STRING")

    def test_table_row_text_not_string_without_cells(self):
        container = self._valid_table_container(
            row_data={"row_natural_language_text": 123}
        )
        _assert_raises_with_codes(container, "TABLE_ROW_TEXT_NOT_STRING")

    def test_table_row_number_invalid_zero(self):
        container = self._valid_table_container(
            row_data={"cells": ["a"], "row_number": 0}
        )
        warnings = _validator().validate(container)
        assert "TABLE_ROW_NUMBER_INVALID" in _warning_codes(warnings)

    def test_table_row_number_invalid_string(self):
        container = self._valid_table_container(
            row_data={"cells": ["a"], "row_number": "1"}
        )
        warnings = _validator().validate(container)
        assert "TABLE_ROW_NUMBER_INVALID" in _warning_codes(warnings)


# ---------------------------------------------------------------------------
# Batch 6: TABLE groups (_check_table_groups)
# ---------------------------------------------------------------------------


class TestTableGroups:
    def test_valid_table_group(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)])
        groups = [
            _table_group(
                0,
                children=children,
                table_metadata=TableMetadata(num_of_cells=2),
            )
        ]
        blocks = [_table_row_block(0, data={"cells": ["a", "b"]}, parent_index=0)]
        assert _validator().validate(_container(blocks=blocks, block_groups=groups)) == []

    def test_table_child_not_table_row(self):
        children = BlockGroupChildren(block_ranges=[IndexRange(start=0, end=0)])
        groups = [_table_group(0, children=children, table_metadata=TableMetadata(num_of_cells=1))]
        blocks = [_text_block(0)]
        _assert_raises_with_codes(
            _container(blocks=blocks, block_groups=groups),
            "TABLE_CHILD_NOT_TABLE_ROW",
        )

    def test_table_metadata_missing_warns(self):
        groups = [_table_group(0, table_metadata=None)]
        warnings = _validator().validate(_container(block_groups=groups))
        assert "TABLE_METADATA_MISSING" in _warning_codes(warnings)

    def test_table_metadata_num_cells_missing_warns(self):
        groups = [_table_group(0, table_metadata=TableMetadata())]
        warnings = _validator().validate(_container(block_groups=groups))
        assert "TABLE_METADATA_NUM_CELLS_MISSING" in _warning_codes(warnings)

    def test_table_metadata_num_cells_invalid_negative(self):
        groups = [_table_group(0, table_metadata=TableMetadata(num_of_cells=-1))]
        _assert_raises_with_codes(
            _container(block_groups=groups),
            "TABLE_METADATA_NUM_CELLS_INVALID",
        )

    def test_table_metadata_num_cells_invalid_string(self):
        groups = [
            _table_group(
                0,
                table_metadata=TableMetadata.model_construct(num_of_cells="5"),
            )
        ]
        _assert_raises_with_codes(
            _container(block_groups=groups),
            "TABLE_METADATA_NUM_CELLS_INVALID",
        )


# ---------------------------------------------------------------------------
# Batch 7: Integration (validate(), exception, logging, record context)
# ---------------------------------------------------------------------------


class TestValidateIntegration:
    def test_collects_multiple_errors_before_raising(self):
        container = _container(
            blocks=[
                _text_block(0, data=123),
                _text_block(1, data=None),
            ]
        )
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(container)
        assert len(exc_info.value.errors) == 2
        assert all(e.severity == Severity.ERROR for e in exc_info.value.errors)

    def test_exception_includes_record_context(self):
        container = _container(blocks=[_text_block(0, data=123)])
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator(
                record_id="rec-1",
                virtual_record_id="vr-1",
                record_name="Report.pdf",
            ).validate(container)
        msg = str(exc_info.value)
        assert "record_id=rec-1" in msg
        assert "vrid=vr-1" in msg
        assert "name='Report.pdf'" in msg
        assert exc_info.value.record_id == "rec-1"
        assert exc_info.value.virtual_record_id == "vr-1"
        assert exc_info.value.record_name == "Report.pdf"

    def test_warnings_logged_and_returned(self):
        logger = MagicMock()
        container = _container(
            blocks=[_block_construct(data="hello", format=None)]
        )
        warnings = _validator(
            logger=logger,
            record_id="rec-1",
        ).validate(container)
        assert len(warnings) == 1
        assert warnings[0].code == "TEXT_FORMAT_MISSING"
        logger.warning.assert_called_once()
        call_args = logger.warning.call_args[0]
        assert "record_id=rec-1" in call_args[1]

    def test_empty_container_only_groups_passes(self):
        assert _validator().validate(_container(block_groups=[_block_group(0)])) == []

    def test_empty_container_only_blocks_passes(self):
        assert _validator().validate(_container(blocks=[_block(0)])) == []


# ---------------------------------------------------------------------------
# Batch 8: Helper methods
# ---------------------------------------------------------------------------


class TestValidatorHelpers:
    def test_format_record_context_all_fields(self):
        v = _validator(record_id="r1", virtual_record_id="v1", record_name="Doc")
        assert v._format_record_context() == " [record_id=r1, vrid=v1, name='Doc']"

    def test_format_record_context_empty(self):
        assert _validator()._format_record_context() == ""

    @pytest.mark.parametrize(
        "uri,expected",
        [
            ("https://example.com/a.png", False),
            ("http://example.com/a.png", False),
            ("data:image/png;base64,abc", True),
            ("ftp://example.com/a.png", False),
            ("", False),
            ("data:image/png", False),
        ],
    )
    def test_is_valid_image_uri(self, uri, expected):
        assert BlockContainerValidator._is_valid_image_uri(uri) is expected

    def test_block_type_enum(self):
        assert BlockContainerValidator._block_type(_text_block(0)) == "text"

    def test_block_type_none(self):
        assert BlockContainerValidator._block_type(_block_construct(type=None)) is None

    def test_block_type_plain_string(self):
        assert BlockContainerValidator._block_type(_block_construct(type="text")) == "text"

    def test_group_type_enum(self):
        assert BlockContainerValidator._group_type(_table_group(0)) == "table"

    def test_group_type_none(self):
        assert BlockContainerValidator._group_type(
            BlockGroup.model_construct(index=0, type=None)
        ) is None

    def test_group_type_plain_string(self):
        assert BlockContainerValidator._group_type(
            BlockGroup.model_construct(index=0, type="table")
        ) == "table"

    def test_format_enum(self):
        assert BlockContainerValidator._format(DataFormat.JSON) == "json"

    def test_format_none(self):
        assert BlockContainerValidator._format(None) is None


# ---------------------------------------------------------------------------
# Batch 9: base64 image helpers + remaining uncovered branches
# ---------------------------------------------------------------------------


class TestIsImageData:
    def test_too_short(self):
        assert _is_image_data(b"PNG") is False

    def test_png_jpeg_gif(self):
        assert _is_image_data(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8) is True
        assert _is_image_data(b"\xff\xd8\xff" + b"\x00" * 8) is True
        assert _is_image_data(b"GIF87a" + b"\x00" * 8) is True
        assert _is_image_data(b"GIF89a" + b"\x00" * 8) is True

    def test_webp_requires_secondary_marker(self):
        assert _is_image_data(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00") is True
        assert _is_image_data(b"RIFF" + b"\x00" * 4 + b"WAVE") is False
        assert _is_image_data(b"RIFFSHORT") is False

    def test_tiff_ico_cur(self):
        assert _is_image_data(b"II\x2a\x00" + b"\x00" * 4) is True
        assert _is_image_data(b"MM\x00\x2a" + b"\x00" * 4) is True
        assert _is_image_data(b"\x00\x00\x01\x00" + b"\x00" * 4) is True
        assert _is_image_data(b"\x00\x00\x02\x00" + b"\x00" * 4) is True

    def test_heic_avif_ftyp_brands(self):
        for brand in (b"avif", b"avis", b"heic", b"heix", b"mif1"):
            data = b"\x00\x00\x00\x18ftyp" + brand + b"\x00" * 4
            assert _is_image_data(data) is True
        assert _is_image_data(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4) is False

    def test_unknown_bytes(self):
        assert _is_image_data(b"NOTANIMAGE!!!!") is False


class TestDecodeB64Prefix:
    def test_too_short_returns_none(self):
        assert _decode_b64_prefix("abc") is None

    def test_decodes_png_prefix(self):
        import base64

        raw = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        decoded = _decode_b64_prefix(raw)
        assert decoded is not None
        assert decoded.startswith(b"\x89PNG")

    def test_base64url_and_whitespace(self):
        import base64

        raw = base64.urlsafe_b64encode(b"\xff\xd8\xff" + b"\x00" * 20).decode()
        # insert whitespace + use urlsafe alphabet
        spaced = raw[:8] + "\n" + raw[8:]
        decoded = _decode_b64_prefix(spaced)
        assert decoded is not None
        assert decoded.startswith(b"\xff\xd8\xff")

    def test_both_decode_paths_fail_returns_none(self):
        from unittest.mock import patch

        with patch(
            "app.modules.transformers.block_container_validator.base64.b64decode",
            side_effect=Exception("bad b64"),
        ):
            assert _decode_b64_prefix("ABCDEFGH") is None

    def test_validate_false_fallback(self):
        import base64

        payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        # Non-alphabet char fails strict validate=True; validate=False still decodes
        messy = payload[:10] + "!" + payload[10:]
        decoded = _decode_b64_prefix(messy)
        assert decoded is not None
        assert _is_image_data(decoded)


class TestContainsBase64Image:
    def test_data_uri_png(self):
        import base64

        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        assert contains_base64_image(f"prefix data:image/png;base64,{png_b64} suffix")

    def test_raw_base64_jpeg(self):
        import base64

        jpeg_b64 = base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 100).decode()
        assert contains_base64_image(jpeg_b64)

    def test_non_image_base64_false(self):
        import base64

        text_b64 = base64.b64encode(b"hello world!!!!!!").decode()
        assert contains_base64_image(text_b64) is False

    def test_data_uri_non_image_payload_continues(self):
        import base64

        # Valid data-URI shape whose payload is not an image → keep scanning
        payload = base64.b64encode(b"not-an-image-bytes!!").decode()
        assert contains_base64_image(f"data:image/png;base64,{payload}") is False

    def test_max_candidates_stops_raw_scanning(self):
        import base64

        # Separate with '.' so the raw regex cannot merge candidates across whitespace
        filler = base64.b64encode(b"x" * 24).decode()
        text = ".".join([filler] * (_MAX_CANDIDATES + 5))
        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        assert contains_base64_image(text + "." + png_b64) is False

    def test_max_candidates_stops_data_uri_scanning(self):
        import base64

        filler = base64.b64encode(b"x" * 24).decode()
        uris = ".".join(
            [f"data:image/png;base64,{filler}" for _ in range(_MAX_CANDIDATES + 3)]
        )
        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20).decode()
        assert contains_base64_image(uris + f".data:image/png;base64,{png_b64}") is False


class TestTextContainsBase64Image:
    def test_text_block_with_embedded_png_errors(self):
        import base64

        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        container = _container(
            blocks=[_text_block(0, data=f"see data:image/png;base64,{png_b64}")]
        )
        _assert_raises_with_codes(container, "TEXT_DATA_CONTAINS_BASE64_IMAGE")


class TestQuotedBase64IsNotAnImage:
    """A truncated example quoted in a doc or chat starts like an image but is
    a few characters of text; only a payload big enough to be an image counts."""

    # A valid 1x1 PNG, 70 bytes.
    _ONE_PIXEL_PNG = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )

    def test_quoted_truncated_example_passes(self) -> None:
        text = 'request body: {"args": {"image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."}}'
        assert contains_base64_image(text) is False
        BlockContainerValidator().validate(_container(blocks=[_text_block(0, data=text)]))

    def test_quoted_header_followed_by_prose_passes(self) -> None:
        text = (
            "Every PNG starts with iVBORw0KGgoAAAANSUhEUg and the rest of the "
            "payload follows the IHDR chunk in the request body we send upstream"
        )
        assert contains_base64_image(text) is False
        assert contains_base64_image(f"data:image/png;base64,{text.split(' with ')[1]}") is False

    def test_smallest_real_png_is_still_an_image(self) -> None:
        assert contains_base64_image(f"see data:image/png;base64,{self._ONE_PIXEL_PNG}")
        assert contains_base64_image(self._ONE_PIXEL_PNG)

    def test_line_wrapped_image_is_still_an_image(self) -> None:
        import base64
        import textwrap

        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        assert contains_base64_image("\n".join(textwrap.wrap(png_b64, 76)))
        assert contains_base64_image("data:image/png;base64," + "\r\n".join(textwrap.wrap(png_b64, 64)))


    def test_column_wrapped_data_uri_is_still_an_image(self) -> None:
        import base64
        import textwrap

        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8).decode()
        wrapped = "\n".join(textwrap.wrap("data:image/png;base64," + png_b64, 76, break_long_words=True))
        assert contains_base64_image(f"Attached screenshot:\n{wrapped}\n")

    def test_prose_word_after_a_quoted_fragment_passes(self) -> None:
        fragment = self._ONE_PIXEL_PNG[:80]
        assert contains_base64_image(f"the payload {fragment} example") is False
        assert contains_base64_image(f"data:image/png;base64,{fragment} example") is False

    def test_image_after_a_quoted_fragment_in_the_same_run_is_found(self) -> None:
        import base64
        import textwrap

        png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8).decode()
        text = "PNG payloads start iVBORw0KGgoAAAANSUhEUg and look like\n" + "\n".join(textwrap.wrap(png_b64, 76))
        assert contains_base64_image(text)


    def test_wrapped_smallest_png_with_padded_last_line_is_still_an_image(self) -> None:
        import textwrap

        raw = "\n".join(textwrap.wrap(self._ONE_PIXEL_PNG, 76))
        uri = "\n".join(textwrap.wrap("data:image/png;base64," + self._ONE_PIXEL_PNG, 76, break_long_words=True))
        for text in (raw, uri):
            assert contains_base64_image(f"Logo:\n{text}\nThanks")
        _assert_raises_with_codes(
            _container(blocks=[_text_block(0, data=raw)]), "TEXT_DATA_CONTAINS_BASE64_IMAGE"
        )


class TestParentChainOutOfRangeBreak:
    def test_walk_breaks_on_out_of_range_ancestor(self):
        """Line 320: chain walk stops when an ancestor parent_index is OOB."""
        container = _container(
            block_groups=[
                _block_group(0, parent_index=1),
                _block_group(1, parent_index=99),
            ]
        )
        # group[1] → PARENT_INDEX_OUT_OF_RANGE; group[0] walk hits break at 320
        _assert_raises_with_codes(container, "PARENT_INDEX_OUT_OF_RANGE")


class TestTableRowParentOutOfRangeExit:
    def test_parent_index_out_of_range_skips_type_check(self):
        """637→exit: parent_index set but out of range — no TABLE_ROW_PARENT_* issue."""
        issues: list[ValidationIssue] = []
        block = _table_row_block(0, data={"cells": ["a"]}, parent_index=5)
        BlockContainerValidator._check_table_row_parent(
            block, [_table_group(0)], 1, 0, "block[0]", issues
        )
        assert issues == []

    def test_parent_is_table_exits_cleanly(self):
        issues: list[ValidationIssue] = []
        block = _table_row_block(0, data={"cells": ["a"]}, parent_index=0)
        BlockContainerValidator._check_table_row_parent(
            block, [_table_group(0)], 1, 0, "block[0]", issues
        )
        assert issues == []


class TestTableGroupOutOfBoundsChildRangeContinue:
    def test_oob_child_range_continued(self):
        """Line 668: OOB children ranges are skipped in _check_table_groups."""
        # Also triggers structural CHILDREN_BLOCK_INDEX_OUT_OF_RANGE
        children = BlockGroupChildren(block_ranges=[IndexRange(start=5, end=5)])
        groups = [
            _table_group(0, children=children, table_metadata=TableMetadata(num_of_cells=1))
        ]
        with pytest.raises(BlockContainerValidationError) as exc_info:
            _validator().validate(_container(blocks=[], block_groups=groups))
        codes = _error_codes(exc_info.value)
        assert "CHILDREN_BLOCK_INDEX_OUT_OF_RANGE" in codes
        assert "TABLE_CHILD_NOT_TABLE_ROW" not in codes


class TestStripBase64Images:
    """Text that kept an image payload loses the payload and nothing else."""

    @staticmethod
    def _png_b64() -> str:
        import base64

        return base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8).decode()

    def test_an_image_in_code_keeps_the_code(self) -> None:
        text = f'+export default "data:image/png;base64,{self._png_b64()}"'
        stripped = strip_base64_images(text)
        assert stripped == '+export default "data:image/png;base64,[image data omitted]"'
        assert contains_base64_image(stripped) is False

    def test_markdown_image_syntax_keeps_its_alt_text(self) -> None:
        text = f"See ![Login screen](data:image/png;base64,{self._png_b64()}) for the error."
        assert strip_base64_images(text) == (
            "See ![Login screen](data:image/png;base64,[image data omitted]) for the error."
        )

    def test_a_wrapped_raw_payload_is_replaced_and_the_prose_around_it_kept(self) -> None:
        import textwrap

        text = "Logo:\n" + "\n".join(textwrap.wrap(self._png_b64(), 76)) + "\nThanks"
        assert strip_base64_images(text) == "Logo:\n[image data omitted]\nThanks"

    def test_a_column_wrapped_data_uri_is_replaced(self) -> None:
        import textwrap

        wrapped = "\n".join(
            textwrap.wrap("data:image/png;base64," + self._png_b64(), 76, break_long_words=True)
        )
        stripped = strip_base64_images(f"Attached screenshot:\n{wrapped}\nDone")
        assert stripped == "Attached screenshot:\ndata:image/png;base64,[image data omitted]\nDone"

    def test_several_images_are_all_replaced(self) -> None:
        png = self._png_b64()
        text = f"a data:image/png;base64,{png} b\n\n{png}\n\nc"
        stripped = strip_base64_images(text)
        assert stripped == (
            "a data:image/png;base64,[image data omitted] b\n\n[image data omitted]\n\nc"
        )

    def test_the_lines_after_a_one_line_payload_are_kept(self) -> None:
        # A hash is 64 base64-alphabet characters and "width=" ends in padding;
        # neither is part of the image before them.
        after = "\n" + "a" * 64 + "\nwidth=\nkeep"
        stripped = strip_base64_images(self._png_b64() + after)
        assert stripped == "[image data omitted]" + after
        assert contains_base64_image(stripped) is False

    def test_the_line_after_a_data_uri_is_kept(self) -> None:
        stripped = strip_base64_images(f"data:image/png;base64,{self._png_b64()}\n" + "a" * 64)
        assert stripped == "data:image/png;base64,[image data omitted]\n" + "a" * 64

    def test_a_wrapped_payload_stops_at_its_padded_last_line(self) -> None:
        import textwrap

        wrapped = "\n".join(textwrap.wrap(self._png_b64(), 76))
        assert wrapped.endswith("=")
        stripped = strip_base64_images(wrapped + "\nwidth=\n" + "b" * 76 + "\nkeep")
        assert stripped == "[image data omitted]\nwidth=\n" + "b" * 76 + "\nkeep"

    def test_a_blank_line_ends_a_wrapped_payload(self) -> None:
        import textwrap

        lines = textwrap.wrap(self._png_b64(), 76)
        body = "\n".join(lines[:-1])  # no padded last line, so only the gap can end it
        stripped = strip_base64_images(body + "\n\n" + "c" * 76)
        assert stripped == "[image data omitted]\n\n" + "c" * 76

    @pytest.mark.parametrize("width", [80, 84])
    def test_a_payload_wrapped_wider_than_mime_is_replaced(self, width: int) -> None:
        import textwrap

        stripped = strip_base64_images("\n".join(textwrap.wrap(self._png_b64(), width)))
        assert stripped == "[image data omitted]"
        assert contains_base64_image(stripped) is False

    def test_a_data_uri_folded_at_84_columns_is_replaced(self) -> None:
        import textwrap

        folded = "\n".join(
            textwrap.wrap("data:image/png;base64," + self._png_b64(), 84, break_long_words=True)
        )
        stripped = strip_base64_images(folded + "\nDone")
        assert stripped == "data:image/png;base64,[image data omitted]\nDone"
        assert contains_base64_image(stripped) is False

    # Base64 lines of 64 and 76 characters hold 48 and 57 bytes, so an image
    # of a multiple of that many bytes ends on a full line, with no padding.
    _BYTES_PER_LINE = {64: 48, 76: 57}
    _AFTER = "NextLineInTheFileHasMixedCaseLetters" * 3

    @staticmethod
    def _fitted(build: Callable[[int], bytes], per_line: int) -> bytes:
        """The image *build* makes with the filler size that ends it on a full
        line, two lines or more, so it is long enough to count as an image."""
        for size in range(per_line * 4):
            image = build(size)
            if len(image) % per_line == 0 and len(image) >= 2 * per_line:
                return image
        raise AssertionError("no filler size fits")

    @classmethod
    def _png(cls, filler: int) -> bytes:
        import struct
        import zlib

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"tEXt", b"k\x00" + b"v" * filler)
            + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
            + chunk(b"IEND", b"")
        )

    @staticmethod
    def _jpeg(comment: bytes, *, thumbnail_at: int | None = None) -> bytes:
        """SOI, an optional EXIF APP1 holding a thumbnail with its own EOI, a COM
        segment, one scan whose data has a stuffed byte and a restart marker,
        and EOI."""
        import struct

        out = b"\xff\xd8"
        if thumbnail_at is not None:
            thumbnail = b"\xff\xd8\xff\xfe\x00\x04hi\xff\xd9"
            body = b"Exif\x00\x00" + b"\x11" * thumbnail_at + thumbnail + b"\x22" * 30
            out += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
        out += b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
        out += b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
        out += b"\x12\x34\xff\x00\x56\xff\xd0\x78\x9a"
        return out + b"\xff\xd9"

    @staticmethod
    def _wrap(image: bytes, width: int) -> str:
        import base64
        import textwrap

        return "\n".join(textwrap.wrap(base64.b64encode(image).decode(), width))

    @pytest.mark.parametrize("width", [64, 76])
    def test_a_digest_after_a_finished_image_is_kept(self, width: int) -> None:
        import hashlib

        image = self._fitted(self._png, self._BYTES_PER_LINE[width])
        digest = (hashlib.sha256(b"x").hexdigest() * 2)[:width]
        stripped = strip_base64_images(self._wrap(image, width) + "\n" + digest)
        assert stripped == "[image data omitted]\n" + digest

    def test_a_png_that_ends_on_a_full_line_ends_the_run(self) -> None:
        image = self._fitted(self._png, 48)
        after = self._AFTER[:64]
        stripped = strip_base64_images(self._wrap(image, 64) + "\n" + after)
        assert stripped == "[image data omitted]\n" + after

    @pytest.mark.parametrize("width", [64, 76])
    def test_an_icon_of_zero_pixels_is_replaced_whole(self, width: int) -> None:
        import struct

        # Zero bytes encode as lines of "A", which are still the image.
        dib = struct.pack("<IiiHHIIiiII", 40, 16, 32, 1, 32, 0, 0, 0, 0, 0, 0)
        image = dib + b"\x00" * (16 * 16 * 4 + 16 * 16 // 8)
        ico = (
            struct.pack("<HHH", 0, 1, 1)
            + struct.pack("<BBBBHHII", 16, 16, 0, 0, 1, 32, len(image), 22)
            + image
        )
        stripped = strip_base64_images(self._wrap(ico, width) + "\nkeep")
        assert stripped == "[image data omitted]\nkeep"
        assert contains_base64_image(stripped) is False

    @pytest.mark.parametrize("width", [64, 76])
    def test_a_jpeg_with_a_comment_of_zeros_is_replaced_whole(self, width: int) -> None:
        stripped = strip_base64_images(self._wrap(self._jpeg(b"\x00" * 200), width) + "\nkeep")
        assert stripped == "[image data omitted]\nkeep"
        assert contains_base64_image(stripped) is False

    @pytest.mark.parametrize(("width", "size"), [(76, 114), (64, 96)])
    def test_the_line_after_a_jpeg_that_ends_on_a_full_line_is_kept(self, width: int, size: int) -> None:
        image = self._jpeg(bytes((i * 17 + 65) % 256 for i in range(size - len(self._jpeg(b"")))))
        assert len(image) == size
        after = self._AFTER[:width]
        stripped = strip_base64_images(self._wrap(image, width) + "\n" + after + "\nkeep")
        assert stripped == "[image data omitted]\n" + after + "\nkeep"

    def test_a_jpeg_does_not_end_at_its_thumbnail(self) -> None:
        # The thumbnail's own EOI lands on a line end; the image goes on after it.
        prefix = 2 + 4 + 6  # SOI, the APP1 marker and length, "Exif\0\0"
        thumbnail_at = -(prefix + 10) % 57  # the thumbnail is 10 bytes
        image = self._fitted(
            lambda filler: self._jpeg(b"c" * filler, thumbnail_at=thumbnail_at), 57,
        )
        after = self._AFTER[:76]
        stripped = strip_base64_images(self._wrap(image, 76) + "\n" + after)
        assert stripped == "[image data omitted]\n" + after
        assert contains_base64_image(stripped) is False

    def test_an_unpadded_short_last_line_that_completes_the_image_goes_with_it(self) -> None:
        # A multiple of 3 bytes has no padding, so only the image's end shows that
        # its short last line belongs to it.
        image = next(
            png for png in (self._png(filler) for filler in range(200))
            if len(png) % 3 == 0 and len(png) % 57
        )
        stripped = strip_base64_images(self._wrap(image, 76) + "\nkeep")
        assert stripped == "[image data omitted]\nkeep"
        assert contains_base64_image(stripped) is False

    def test_the_line_after_a_gif_that_ends_on_a_full_line_is_kept(self) -> None:
        def gif(filler: int) -> bytes:
            comment = b"x" * filler
            blocks = b"".join(bytes([len(comment[i:i + 255])]) + comment[i:i + 255] for i in range(0, len(comment), 255))
            return (
                b"GIF89a" + b"\x01\x00\x01\x00\x80\x00\x00" + b"\x00\x00\x00\xff\xff\xff"
                + b"\x21\xfe" + blocks + b"\x00"
                + b"\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00" + b"\x02\x02\x44\x01\x00"
                + b"\x3b"
            )

        after = self._AFTER[:76]
        stripped = strip_base64_images(self._wrap(self._fitted(gif, 57), 76) + "\n" + after)
        assert stripped == "[image data omitted]\n" + after

    def test_the_line_after_a_webp_that_ends_on_a_full_line_is_kept(self) -> None:
        import struct

        def webp(filler: int) -> bytes:
            data = b"\x2f" + b"\x00" * (filler * 2 + 1)  # even, so the chunk needs no pad byte
            body = b"WEBP" + b"VP8L" + struct.pack("<I", len(data)) + data
            return b"RIFF" + struct.pack("<I", len(body)) + body

        after = self._AFTER[:64]
        stripped = strip_base64_images(self._wrap(self._fitted(webp, 48), 64) + "\n" + after)
        assert stripped == "[image data omitted]\n" + after

    def test_an_image_past_the_detection_cap_is_replaced_too(self) -> None:
        # Commas end each run, so every identifier is a candidate of its own.
        identifiers = ", ".join(f"identifier_number_{i:04d}" for i in range(_MAX_CANDIDATES + 5))
        text = f"{identifiers}\n{self._png_b64()}\nend"
        assert contains_base64_image(text) is False  # the validator stops at its cap
        stripped = strip_base64_images(text)
        assert stripped == f"{identifiers}\n[image data omitted]\nend"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Plain prose with no payload in it at all.",
            'request body: {"image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg..."}',
            "Every PNG starts with iVBORw0KGgoAAAANSUhEUg and the rest follows the IHDR chunk",
            # SVG is text, not a raster image the validator looks for.
            "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMCIgaGVpZ2h0PSIxMCI+PC9zdmc+",
            "sha256:" + "a" * 64 + " and AKIAIOSFODNN7EXAMPLE_and_a_long_identifier_name_here",
        ],
    )
    def test_text_without_an_image_is_unchanged(self, text: str) -> None:
        assert strip_base64_images(text) == text

    def test_every_shape_the_validator_flags_is_cleared(self) -> None:
        import textwrap

        png = self._png_b64()
        one_pixel = TestQuotedBase64IsNotAnImage._ONE_PIXEL_PNG
        shapes = [
            f"see data:image/png;base64,{png}",
            png,
            "\n".join(textwrap.wrap(png, 76)),
            "data:image/png;base64," + "\r\n".join(textwrap.wrap(png, 64)),
            f"see data:image/png;base64,{one_pixel}",
            "Logo:\n" + "\n".join(textwrap.wrap(one_pixel, 76)) + "\nThanks",
            "PNG payloads start iVBORw0KGgoAAAANSUhEUg and look like\n" + "\n".join(textwrap.wrap(png, 76)),
        ]
        for text in shapes:
            assert contains_base64_image(text), text[:40]
            stripped = strip_base64_images(text)
            assert contains_base64_image(stripped) is False, text[:40]
            assert "[image data omitted]" in stripped
            BlockContainerValidator().validate(_container(blocks=[_text_block(0, data=stripped)]))
