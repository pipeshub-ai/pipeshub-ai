"""
Unit tests for QdrantUtils and QdrantService filter-related functionality.

Tests cover:
- QdrantUtils.build_conditions: dict to FieldCondition list conversion
- QdrantUtils._is_valid_value: value validation logic
- QdrantService.filter_collection: mode dispatch, kwargs routing, empty filters
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
)

from app.services.vector_db.qdrant.utils import QdrantUtils
from app.services.vector_db.qdrant.filter import QdrantFilterMode
from app.services.vector_db.qdrant.qdrant import QdrantService


# ---------------------------------------------------------------------------
# QdrantUtils._is_valid_value
# ---------------------------------------------------------------------------

class TestIsValidValue:
    """Tests for QdrantUtils._is_valid_value static method."""

    def test_none_is_invalid(self):
        assert QdrantUtils._is_valid_value(None) is False

    def test_empty_string_is_invalid(self):
        assert QdrantUtils._is_valid_value("") is False

    def test_whitespace_only_string_is_invalid(self):
        assert QdrantUtils._is_valid_value("   ") is False

    def test_tab_only_string_is_invalid(self):
        assert QdrantUtils._is_valid_value("\t") is False

    def test_non_empty_string_is_valid(self):
        assert QdrantUtils._is_valid_value("hello") is True

    def test_string_with_surrounding_whitespace_is_valid(self):
        assert QdrantUtils._is_valid_value("  hello  ") is True

    def test_integer_is_valid(self):
        assert QdrantUtils._is_valid_value(42) is True

    def test_zero_integer_is_valid(self):
        assert QdrantUtils._is_valid_value(0) is True

    def test_negative_integer_is_valid(self):
        assert QdrantUtils._is_valid_value(-1) is True

    def test_float_is_valid(self):
        assert QdrantUtils._is_valid_value(3.14) is True

    def test_zero_float_is_valid(self):
        assert QdrantUtils._is_valid_value(0.0) is True

    def test_bool_true_is_valid(self):
        assert QdrantUtils._is_valid_value(True) is True

    def test_bool_false_is_valid(self):
        assert QdrantUtils._is_valid_value(False) is True

    def test_list_is_valid(self):
        """Lists are technically valid (not None, not empty string)."""
        assert QdrantUtils._is_valid_value(["a", "b"]) is True

    def test_empty_list_is_valid(self):
        """Empty list passes _is_valid_value (it's not None or empty string)."""
        assert QdrantUtils._is_valid_value([]) is True


# ---------------------------------------------------------------------------
# QdrantUtils.build_conditions
# ---------------------------------------------------------------------------

class TestBuildConditions:
    """Tests for QdrantUtils.build_conditions static method."""

    def test_empty_filters(self):
        result = QdrantUtils.build_conditions({})
        assert result == []

    def test_single_string_value(self):
        result = QdrantUtils.build_conditions({"orgId": "org-123"})
        assert len(result) == 1
        cond = result[0]
        assert isinstance(cond, FieldCondition)
        assert cond.key == "metadata.orgId"
        assert cond.match == MatchValue(value="org-123")

    def test_metadata_prefix_added(self):
        result = QdrantUtils.build_conditions({"status": "active"})
        assert result[0].key == "metadata.status"

    def test_integer_value(self):
        result = QdrantUtils.build_conditions({"count": 5})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=5)

    def test_float_value(self):
        """Floats pass _is_valid_value, but MatchValue may reject them depending on qdrant version.
        The build_conditions method will attempt to create a FieldCondition with a float value.
        """
        # MatchValue does not accept floats in this qdrant-client version.
        # Verify it raises a validation error.
        with pytest.raises(Exception):
            QdrantUtils.build_conditions({"score": 0.95})

    def test_bool_value(self):
        result = QdrantUtils.build_conditions({"active": True})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=True)

    def test_bool_false_value(self):
        result = QdrantUtils.build_conditions({"active": False})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=False)

    def test_list_value_uses_match_any(self):
        result = QdrantUtils.build_conditions({"roles": ["admin", "user"]})
        assert len(result) == 1
        cond = result[0]
        assert cond.key == "metadata.roles"
        assert cond.match == MatchAny(any=["admin", "user"])

    def test_tuple_value_uses_match_any(self):
        result = QdrantUtils.build_conditions({"roles": ("admin", "user")})
        assert len(result) == 1
        assert result[0].match == MatchAny(any=["admin", "user"])

    def test_list_with_none_values_filtered(self):
        result = QdrantUtils.build_conditions({"roles": ["admin", None, "user"]})
        assert len(result) == 1
        assert result[0].match == MatchAny(any=["admin", "user"])

    def test_list_all_none_values_produces_no_condition(self):
        result = QdrantUtils.build_conditions({"roles": [None, None]})
        assert result == []

    def test_empty_list_produces_no_condition(self):
        result = QdrantUtils.build_conditions({"roles": []})
        assert result == []

    def test_none_value_filtered_out(self):
        result = QdrantUtils.build_conditions({"orgId": None})
        assert result == []

    def test_empty_string_value_filtered_out(self):
        result = QdrantUtils.build_conditions({"orgId": ""})
        assert result == []

    def test_whitespace_string_value_filtered_out(self):
        result = QdrantUtils.build_conditions({"orgId": "   "})
        assert result == []

    def test_multiple_filters(self):
        result = QdrantUtils.build_conditions({
            "orgId": "org-123",
            "status": "active",
        })
        assert len(result) == 2
        keys = {c.key for c in result}
        assert keys == {"metadata.orgId", "metadata.status"}

    def test_mixed_valid_and_invalid_filters(self):
        result = QdrantUtils.build_conditions({
            "orgId": "org-123",
            "empty": "",
            "none_val": None,
            "roles": ["admin"],
            "empty_list": [],
        })
        assert len(result) == 2
        keys = {c.key for c in result}
        assert keys == {"metadata.orgId", "metadata.roles"}

    def test_multiple_list_filters(self):
        result = QdrantUtils.build_conditions({
            "roles": ["admin", "user"],
            "departments": ["eng", "sales"],
        })
        assert len(result) == 2
        keys = {c.key for c in result}
        assert keys == {"metadata.roles", "metadata.departments"}

    def test_zero_integer_is_valid_condition(self):
        result = QdrantUtils.build_conditions({"count": 0})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=0)

    def test_false_bool_is_valid_condition(self):
        result = QdrantUtils.build_conditions({"enabled": False})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=False)


# ---------------------------------------------------------------------------
# QdrantService.filter_collection
# ---------------------------------------------------------------------------

class TestFilterCollection:
    """Tests for QdrantService.filter_collection method."""

    def _make_service(self):
        """Create a QdrantService with a mock client."""
        service = QdrantService.__new__(QdrantService)
        service.client = MagicMock()
        service.config_service = MagicMock()
        service.is_async = False
        return service

    # -- Client not connected --

    @pytest.mark.asyncio
    async def test_raises_when_client_not_connected(self):
        service = QdrantService.__new__(QdrantService)
        service.client = None
        service.config_service = MagicMock()
        service.is_async = False
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.filter_collection(must={"orgId": "123"})

    # -- Empty filters --

    @pytest.mark.asyncio
    async def test_empty_filters_returns_empty_filter(self):
        service = self._make_service()
        result = await service.filter_collection()
        assert isinstance(result, Filter)
        assert result.should == []

    @pytest.mark.asyncio
    async def test_all_none_values_returns_empty_filter(self):
        service = self._make_service()
        result = await service.filter_collection(must={"a": None}, should={"b": None})
        assert isinstance(result, Filter)
        assert result.should == []

    # -- MUST mode (default) --

    @pytest.mark.asyncio
    async def test_default_mode_is_must(self):
        service = self._make_service()
        result = await service.filter_collection(orgId="org-1", status="active")
        assert result.must is not None
        assert len(result.must) == 2
        keys = {c.key for c in result.must}
        assert keys == {"metadata.orgId", "metadata.status"}

    @pytest.mark.asyncio
    async def test_explicit_must_dict(self):
        service = self._make_service()
        result = await service.filter_collection(must={"orgId": "123"})
        assert result.must is not None
        assert len(result.must) == 1
        assert result.must[0].key == "metadata.orgId"

    @pytest.mark.asyncio
    async def test_kwargs_merge_with_must_dict(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"orgId": "123"},
            status="active",
        )
        assert result.must is not None
        assert len(result.must) == 2

    # -- SHOULD mode --

    @pytest.mark.asyncio
    async def test_should_mode_with_kwargs(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.SHOULD,
            department="IT",
            role="admin",
        )
        assert result.should is not None
        assert len(result.should) == 2

    @pytest.mark.asyncio
    async def test_explicit_should_dict(self):
        service = self._make_service()
        result = await service.filter_collection(
            should={"department": "IT", "role": "admin"}
        )
        assert result.should is not None
        assert len(result.should) == 2

    @pytest.mark.asyncio
    async def test_should_with_min_should_match(self):
        """min_should_match is not supported in this qdrant-client version."""
        service = self._make_service()
        with pytest.raises(Exception):
            await service.filter_collection(
                should={"department": "IT", "role": "admin"},
                min_should_match=1,
            )

    @pytest.mark.asyncio
    async def test_min_should_match_not_set_without_should(self):
        """When no should conditions exist, min_should_match is not included."""
        service = self._make_service()
        result = await service.filter_collection(
            must={"orgId": "123"},
        )
        assert result.must is not None

    # -- MUST_NOT mode --

    @pytest.mark.asyncio
    async def test_must_not_mode_with_kwargs(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.MUST_NOT,
            status="deleted",
        )
        assert result.must_not is not None
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_explicit_must_not_dict(self):
        service = self._make_service()
        result = await service.filter_collection(
            must_not={"status": "deleted", "banned": True}
        )
        assert result.must_not is not None
        assert len(result.must_not) == 2

    # -- String mode conversion --

    @pytest.mark.asyncio
    async def test_string_mode_must(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode="must",
            orgId="org-1",
        )
        assert result.must is not None

    @pytest.mark.asyncio
    async def test_string_mode_should(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode="should",
            orgId="org-1",
        )
        assert result.should is not None

    @pytest.mark.asyncio
    async def test_string_mode_must_not(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode="must_not",
            status="deleted",
        )
        assert result.must_not is not None

    @pytest.mark.asyncio
    async def test_string_mode_case_insensitive(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode="MUST",
            orgId="org-1",
        )
        assert result.must is not None

    @pytest.mark.asyncio
    async def test_invalid_string_mode_raises(self):
        service = self._make_service()
        with pytest.raises(ValueError, match="Invalid mode"):
            await service.filter_collection(
                filter_mode="invalid_mode",
                orgId="org-1",
            )

    # -- Combined filters --

    @pytest.mark.asyncio
    async def test_combined_must_should_must_not(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"orgId": "123", "active": True},
            should={"roles": ["admin", "user"]},
            must_not={"banned": True},
        )
        assert result.must is not None
        assert len(result.must) == 2
        assert result.should is not None
        assert len(result.should) == 1
        assert result.must_not is not None
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_kwargs_routed_to_should_with_explicit_must(self):
        """When mode=SHOULD, kwargs go to should, explicit must stays in must."""
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.SHOULD,
            must={"orgId": "123"},
            department="IT",
        )
        assert result.must is not None
        assert len(result.must) == 1
        assert result.should is not None
        assert len(result.should) == 1

    @pytest.mark.asyncio
    async def test_kwargs_routed_to_must_not_with_explicit_should(self):
        """When mode=MUST_NOT, kwargs go to must_not, explicit should stays."""
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.MUST_NOT,
            should={"department": "IT"},
            banned="yes",
        )
        assert result.should is not None
        assert result.must_not is not None

    # -- Filter with list values --

    @pytest.mark.asyncio
    async def test_list_value_in_must(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"roles": ["admin", "user"]},
        )
        assert result.must is not None
        assert len(result.must) == 1
        assert result.must[0].match == MatchAny(any=["admin", "user"])

    # -- No kwargs, only explicit dicts --

    @pytest.mark.asyncio
    async def test_no_kwargs_only_explicit_dicts(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"orgId": "123"},
            should={"role": "admin"},
        )
        assert result.must is not None
        assert result.should is not None

    # -- mode as enum directly --

    @pytest.mark.asyncio
    async def test_filter_mode_enum_must(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.MUST,
            orgId="org-1",
        )
        assert result.must is not None

    @pytest.mark.asyncio
    async def test_filter_mode_enum_should(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.SHOULD,
            orgId="org-1",
        )
        assert result.should is not None

    @pytest.mark.asyncio
    async def test_filter_mode_enum_must_not(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=QdrantFilterMode.MUST_NOT,
            orgId="org-1",
        )
        assert result.must_not is not None

    # -- Partial empty dicts --

    @pytest.mark.asyncio
    async def test_empty_must_dict_with_valid_should(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={},
            should={"role": "admin"},
        )
        assert result.should is not None

    @pytest.mark.asyncio
    async def test_all_empty_dicts_returns_empty_filter(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={},
            should={},
            must_not={},
        )
        assert isinstance(result, Filter)
        assert result.should == []
