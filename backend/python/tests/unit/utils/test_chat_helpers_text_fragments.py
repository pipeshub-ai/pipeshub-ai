import pytest

from app.models.blocks import BlockType, DataFormat
from app.utils.chat_helpers import (
    _NO_TEXT_FRAGMENT_BLOCK_TYPES,
    _fragment_source_format,
)
from app.utils.text_fragments import SourceFormat


class TestFragmentSourceFormat:
    @pytest.mark.parametrize("block_type", [BlockType.CODE.value, BlockType.TABLE_ROW.value])
    def test_code_and_table_rows_are_plain_whatever_the_recorded_format(self, block_type: str) -> None:
        assert _fragment_source_format({"format": "markdown"}, block_type) is SourceFormat.PLAIN

    def test_follows_recorded_format(self) -> None:
        assert _fragment_source_format({"format": DataFormat.HTML}, BlockType.TEXT.value) is SourceFormat.HTML
        assert _fragment_source_format({"format": "txt"}, BlockType.TEXT.value) is SourceFormat.PLAIN

    def test_unknown_or_missing_format_defers_to_generator_default(self) -> None:
        assert _fragment_source_format({"format": "bin"}, BlockType.TEXT.value) is None
        assert _fragment_source_format({}, BlockType.TEXT.value) is None
        assert _fragment_source_format(None, None) is None


def test_summary_and_image_blocks_get_no_fragment() -> None:
    assert BlockType.RECORD_SUMMARY.value in _NO_TEXT_FRAGMENT_BLOCK_TYPES
    assert BlockType.IMAGE.value in _NO_TEXT_FRAGMENT_BLOCK_TYPES
    assert BlockType.TEXT.value not in _NO_TEXT_FRAGMENT_BLOCK_TYPES
