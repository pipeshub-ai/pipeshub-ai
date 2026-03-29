"""
Unit tests for QdrantUtils and QdrantService filter-related functionality.

Tests cover:
- QdrantUtils.build_conditions: dict to FieldCondition list conversion
- QdrantUtils._is_valid_value: value validation logic
- QdrantService.filter_collection: mode dispatch, kwargs routing, empty filters
- QdrantUtils translation functions: VectorPoint, FilterExpression, SearchRequest conversions
"""

import pytest
from unittest.mock import MagicMock

from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
)

from app.services.vector_db.qdrant.utils import QdrantUtils
from app.services.vector_db.qdrant.qdrant import QdrantService
from app.services.vector_db.models import (
    FieldCondition as GenericFieldCondition,
    FilterExpression,
    FilterMode,
    FusionMethod,
    HybridSearchRequest,
    SearchResult,
    SparseVector,
    VectorPoint,
)


# ---------------------------------------------------------------------------
# QdrantUtils._is_valid_value
# ---------------------------------------------------------------------------

class TestIsValidValue:

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
        assert QdrantUtils._is_valid_value(["a", "b"]) is True

    def test_empty_list_is_valid(self):
        assert QdrantUtils._is_valid_value([]) is True


# ---------------------------------------------------------------------------
# QdrantUtils.build_conditions
# ---------------------------------------------------------------------------

class TestBuildConditions:

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

    def test_zero_integer_is_valid_condition(self):
        result = QdrantUtils.build_conditions({"count": 0})
        assert len(result) == 1
        assert result[0].match == MatchValue(value=0)


# ---------------------------------------------------------------------------
# QdrantUtils.filter_expression_to_qdrant
# ---------------------------------------------------------------------------

class TestFilterExpressionToQdrant:

    def test_empty_expression(self):
        expr = FilterExpression()
        result = QdrantUtils.filter_expression_to_qdrant(expr)
        assert isinstance(result, Filter)
        assert result.should == []

    def test_must_conditions(self):
        expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org-123")]
        )
        result = QdrantUtils.filter_expression_to_qdrant(expr)
        assert result.must is not None
        assert len(result.must) == 1
        assert result.must[0].key == "metadata.orgId"

    def test_should_conditions(self):
        expr = FilterExpression(
            should=[GenericFieldCondition(key="metadata.role", values=["admin", "user"])]
        )
        result = QdrantUtils.filter_expression_to_qdrant(expr)
        assert result.should is not None
        assert len(result.should) == 1

    def test_must_not_conditions(self):
        expr = FilterExpression(
            must_not=[GenericFieldCondition(key="metadata.status", value="deleted")]
        )
        result = QdrantUtils.filter_expression_to_qdrant(expr)
        assert result.must_not is not None
        assert len(result.must_not) == 1

    def test_combined_conditions(self):
        expr = FilterExpression(
            must=[GenericFieldCondition(key="metadata.orgId", value="org-123")],
            should=[GenericFieldCondition(key="metadata.role", values=["admin"])],
            must_not=[GenericFieldCondition(key="metadata.banned", value=True)],
        )
        result = QdrantUtils.filter_expression_to_qdrant(expr)
        assert len(result.must) == 1
        assert len(result.should) == 1
        assert len(result.must_not) == 1


# ---------------------------------------------------------------------------
# QdrantUtils.vector_point_to_qdrant
# ---------------------------------------------------------------------------

class TestVectorPointToQdrant:

    def test_dense_only(self):
        point = VectorPoint(
            id="abc-123",
            dense_vector=[0.1, 0.2, 0.3],
            payload={"metadata": {"orgId": "org1"}, "page_content": "hello"},
        )
        result = QdrantUtils.vector_point_to_qdrant(point)
        assert result.id == "abc-123"
        assert "dense" in result.vector
        assert result.payload["page_content"] == "hello"

    def test_dense_and_sparse(self):
        point = VectorPoint(
            id="abc-123",
            dense_vector=[0.1, 0.2],
            sparse_vector=SparseVector(indices=[0, 5], values=[1.0, 0.5]),
            payload={"metadata": {}, "page_content": "test"},
        )
        result = QdrantUtils.vector_point_to_qdrant(point)
        assert "dense" in result.vector
        assert "sparse" in result.vector

    def test_empty_vectors(self):
        point = VectorPoint(id="abc-123", payload={"metadata": {}})
        result = QdrantUtils.vector_point_to_qdrant(point)
        assert result.vector == {}


# ---------------------------------------------------------------------------
# QdrantUtils.to_generic_sparse_vector
# ---------------------------------------------------------------------------

class TestToGenericSparseVector:

    def test_already_sparse_vector(self):
        sv = SparseVector(indices=[1, 2], values=[0.5, 0.7])
        result = QdrantUtils.to_generic_sparse_vector(sv)
        assert result is sv

    def test_dict_sparse(self):
        d = {"indices": [1, 2], "values": [0.5, 0.7]}
        result = QdrantUtils.to_generic_sparse_vector(d)
        assert result.indices == [1, 2]
        assert result.values == [0.5, 0.7]

    def test_object_with_attrs(self):
        obj = type("Obj", (), {"indices": [3], "values": [0.9]})()
        result = QdrantUtils.to_generic_sparse_vector(obj)
        assert result.indices == [3]

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            QdrantUtils.to_generic_sparse_vector("invalid")


# ---------------------------------------------------------------------------
# QdrantService.filter_collection (returns FilterExpression)
# ---------------------------------------------------------------------------

class TestFilterCollection:

    def _make_service(self):
        service = QdrantService.__new__(QdrantService)
        service.client = MagicMock()
        service.config_service = MagicMock()
        service.is_async = False
        return service

    @pytest.mark.asyncio
    async def test_raises_when_client_not_connected(self):
        service = QdrantService.__new__(QdrantService)
        service.client = None
        service.config_service = MagicMock()
        service.is_async = False
        with pytest.raises(RuntimeError, match="Client not connected"):
            await service.filter_collection(must={"orgId": "123"})

    @pytest.mark.asyncio
    async def test_empty_filters_returns_empty_filter_expression(self):
        service = self._make_service()
        result = await service.filter_collection()
        assert isinstance(result, FilterExpression)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_default_mode_is_must(self):
        service = self._make_service()
        result = await service.filter_collection(orgId="org-1", status="active")
        assert len(result.must) == 2

    @pytest.mark.asyncio
    async def test_explicit_must_dict(self):
        service = self._make_service()
        result = await service.filter_collection(must={"orgId": "123"})
        assert len(result.must) == 1
        assert result.must[0].key == "metadata.orgId"

    @pytest.mark.asyncio
    async def test_should_mode_with_kwargs(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=FilterMode.SHOULD,
            department="IT",
            role="admin",
        )
        assert len(result.should) == 2

    @pytest.mark.asyncio
    async def test_must_not_mode_with_kwargs(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode=FilterMode.MUST_NOT,
            status="deleted",
        )
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_string_mode_conversion(self):
        service = self._make_service()
        result = await service.filter_collection(
            filter_mode="should", department="IT"
        )
        assert len(result.should) == 1

    @pytest.mark.asyncio
    async def test_invalid_string_mode(self):
        service = self._make_service()
        with pytest.raises(ValueError, match="Invalid mode"):
            await service.filter_collection(
                filter_mode="invalid_mode", field="value"
            )

    @pytest.mark.asyncio
    async def test_combined_must_should_must_not(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"orgId": "123", "active": True},
            should={"roles": ["admin", "user"]},
            must_not={"banned": True},
        )
        assert len(result.must) == 2
        assert len(result.should) == 1
        assert len(result.must_not) == 1

    @pytest.mark.asyncio
    async def test_list_value_in_must(self):
        service = self._make_service()
        result = await service.filter_collection(
            must={"roles": ["admin", "user"]},
        )
        assert len(result.must) == 1
        assert result.must[0].values == ["admin", "user"]
