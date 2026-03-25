"""Extended tests for knowledge_hub_router utility functions and kb_router utilities."""

import pytest
from fastapi import HTTPException

from app.connectors.sources.localKB.api.knowledge_hub_router import (
    _get_enum_values,
    _parse_comma_separated_str,
    _parse_date_range,
    _parse_size_range,
    _validate_enum_values,
    _validate_uuid_format,
)
from app.connectors.sources.localKB.api.kb_router import (
    _parse_comma_separated_str as kb_parse_comma,
)


# ===========================================================================
# _get_enum_values
# ===========================================================================

class TestGetEnumValues:
    def test_basic_enum(self):
        from enum import Enum

        class Color(Enum):
            RED = "red"
            GREEN = "green"

        result = _get_enum_values(Color)
        assert result == {"red", "green"}


# ===========================================================================
# _validate_enum_values
# ===========================================================================

class TestValidateEnumValues:
    def test_none_returns_none(self):
        assert _validate_enum_values(None, {"a", "b"}, "test") is None

    def test_empty_list_returns_none(self):
        assert _validate_enum_values([], {"a", "b"}, "test") is None

    def test_filters_invalid(self):
        result = _validate_enum_values(["valid", "invalid"], {"valid"}, "test")
        assert result == ["valid"]

    def test_all_invalid_returns_none(self):
        assert _validate_enum_values(["x", "y"], {"a", "b"}, "test") is None

    def test_all_valid(self):
        result = _validate_enum_values(["a", "b"], {"a", "b", "c"}, "test")
        assert result == ["a", "b"]


# ===========================================================================
# _parse_comma_separated_str (knowledge_hub_router)
# ===========================================================================

class TestParseCommaSeparatedStr:
    def test_none(self):
        assert _parse_comma_separated_str(None) is None

    def test_empty(self):
        assert _parse_comma_separated_str("") is None

    def test_single(self):
        assert _parse_comma_separated_str("value") == ["value"]

    def test_multiple(self):
        result = _parse_comma_separated_str("a, b, c")
        assert result == ["a", "b", "c"]

    def test_empty_items_filtered(self):
        result = _parse_comma_separated_str("a,,b, ,c")
        assert result == ["a", "b", "c"]

    def test_too_many_items(self):
        items = ",".join([str(i) for i in range(101)])
        with pytest.raises(HTTPException):
            _parse_comma_separated_str(items)


# ===========================================================================
# _parse_comma_separated_str (kb_router)
# ===========================================================================

class TestKBParseCommaSeparatedStr:
    def test_none(self):
        assert kb_parse_comma(None) is None

    def test_basic(self):
        assert kb_parse_comma("a,b,c") == ["a", "b", "c"]

    def test_empty_string(self):
        assert kb_parse_comma("") is None


# ===========================================================================
# _validate_uuid_format
# ===========================================================================

class TestValidateUuidFormat:
    def test_none_passes(self):
        _validate_uuid_format(None, "test")  # Should not raise

    def test_valid_uuid(self):
        _validate_uuid_format("550e8400-e29b-41d4-a716-446655440000", "test")

    def test_knowledge_base_app_id(self):
        _validate_uuid_format("knowledgeBase_org-123", "test")

    def test_invalid_format(self):
        with pytest.raises(HTTPException):
            _validate_uuid_format("not-a-uuid", "test")

    def test_partial_uuid(self):
        with pytest.raises(HTTPException):
            _validate_uuid_format("550e8400-e29b-41d4", "test")


# ===========================================================================
# _parse_date_range
# ===========================================================================

class TestParseDateRange:
    def test_none(self):
        assert _parse_date_range(None) is None

    def test_empty(self):
        assert _parse_date_range("") is None

    def test_gte_only(self):
        result = _parse_date_range("gte:1000000000000")
        assert result == {"gte": 1000000000000}

    def test_lte_only(self):
        result = _parse_date_range("lte:2000000000000")
        assert result == {"lte": 2000000000000}

    def test_both(self):
        result = _parse_date_range("gte:1000000000000,lte:2000000000000")
        assert result == {"gte": 1000000000000, "lte": 2000000000000}

    def test_invalid_timestamp(self):
        with pytest.raises(HTTPException):
            _parse_date_range("gte:not_a_number")

    def test_negative_timestamp(self):
        with pytest.raises(HTTPException):
            _parse_date_range("gte:-1")

    def test_too_large_timestamp(self):
        with pytest.raises(HTTPException):
            _parse_date_range("gte:99999999999999")

    def test_gte_greater_than_lte(self):
        with pytest.raises(HTTPException):
            _parse_date_range("gte:2000000000000,lte:1000000000000")

    def test_no_valid_keys(self):
        result = _parse_date_range("invalid:123")
        assert result is None


# ===========================================================================
# _parse_size_range
# ===========================================================================

class TestParseSizeRange:
    def test_none(self):
        assert _parse_size_range(None) is None

    def test_empty(self):
        assert _parse_size_range("") is None

    def test_gte_only(self):
        result = _parse_size_range("gte:1024")
        assert result == {"gte": 1024}

    def test_lte_only(self):
        result = _parse_size_range("lte:1048576")
        assert result == {"lte": 1048576}

    def test_both(self):
        result = _parse_size_range("gte:1024,lte:1048576")
        assert result == {"gte": 1024, "lte": 1048576}

    def test_negative_size(self):
        with pytest.raises(HTTPException):
            _parse_size_range("gte:-1")

    def test_too_large_size(self):
        with pytest.raises(HTTPException):
            _parse_size_range("gte:2099511627776")  # > 1TB

    def test_invalid_value(self):
        with pytest.raises(HTTPException):
            _parse_size_range("gte:not_number")

    def test_gte_greater_than_lte(self):
        with pytest.raises(HTTPException):
            _parse_size_range("gte:2000,lte:1000")

    def test_no_valid_keys(self):
        result = _parse_size_range("invalid:123")
        assert result is None
