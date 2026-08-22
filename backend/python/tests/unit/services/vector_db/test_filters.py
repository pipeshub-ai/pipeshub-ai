"""Unit tests for shared filter-building helpers."""

import pytest

from app.services.vector_db.filters import build_filter_expression, is_valid_filter_value
from app.services.vector_db.models import FieldCondition, FilterMode
from app.services.vector_db.qdrant.utils import QdrantUtils


class TestIsValidFilterValue:
    def test_none_is_invalid(self):
        assert is_valid_filter_value(None) is False

    def test_blank_string_is_invalid(self):
        assert is_valid_filter_value("   ") is False

    def test_empty_list_is_invalid(self):
        assert is_valid_filter_value([]) is False

    def test_scalar_is_valid(self):
        assert is_valid_filter_value("org-1") is True


class TestBuildFilterExpression:
    def test_must_mode_merges_kwargs(self):
        expr = build_filter_expression(
            FilterMode.MUST,
            must={"orgId": "org1"},
            extra_kwargs={"virtualRecordId": "vr1"},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert len(expr.must) == 2
        keys = {c.key for c in expr.must}
        assert keys == {"metadata.orgId", "metadata.virtualRecordId"}

    def test_should_list_becomes_values(self):
        expr = build_filter_expression(
            FilterMode.SHOULD,
            should={"virtualRecordId": ["a", "b"]},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert len(expr.should) == 1
        assert expr.should[0].values == ["a", "b"]

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid mode"):
            build_filter_expression(
                "invalid",
                build_conditions=QdrantUtils.build_conditions_generic,
            )

    def test_blank_values_dropped(self):
        expr = build_filter_expression(
            FilterMode.MUST,
            must={"orgId": "", "virtualRecordId": "vr1"},
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert len(expr.must) == 1
        assert expr.must[0].key == "metadata.virtualRecordId"

    def test_min_should_match_preserved(self):
        expr = build_filter_expression(
            FilterMode.SHOULD,
            should={"a": 1, "b": 2},
            min_should_match=2,
            build_conditions=QdrantUtils.build_conditions_generic,
        )
        assert expr.min_should_match == 2
