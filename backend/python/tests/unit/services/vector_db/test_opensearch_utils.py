"""
Unit tests for OpenSearchUtils and OpenSearchService filter-related functionality.

Tests cover:
- OpenSearchUtils.build_conditions: dict to FieldCondition list conversion
- OpenSearchUtils._is_valid_value: value validation logic
- OpenSearchUtils.filter_expression_to_bool_query: FilterExpression to OpenSearch bool query
- OpenSearchUtils._field_condition_to_clause: single FieldCondition to OpenSearch clause
- OpenSearchUtils.vector_point_to_document: VectorPoint to OpenSearch document
- OpenSearchUtils.hit_to_search_result: OpenSearch hit to SearchResult
- OpenSearchUtils.build_hybrid_query: HybridSearchRequest to OpenSearch query body
- OpenSearchService.filter_collection: mode dispatch, kwargs routing, empty filters
"""

import pytest
from unittest.mock import MagicMock

from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.services.vector_db.opensearch.opensearch import OpenSearchService
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    FilterMode,
    FusionMethod,
    HybridSearchRequest,
    SearchResult,
    SparseVector,
    VectorPoint,
)


# ---------------------------------------------------------------------------
# OpenSearchUtils._is_valid_value
# ---------------------------------------------------------------------------

class TestIsValidValue:

    def test_none_is_invalid(self):
        assert OpenSearchUtils._is_valid_value(None) is False

    def test_empty_string_is_invalid(self):
        assert OpenSearchUtils._is_valid_value("") is False

    def test_whitespace_only_string_is_invalid(self):
        assert OpenSearchUtils._is_valid_value("   ") is False

    def test_tab_only_string_is_invalid(self):
        assert OpenSearchUtils._is_valid_value("\t") is False

    def test_non_empty_string_is_valid(self):
        assert OpenSearchUtils._is_valid_value("hello") is True

    def test_string_with_surrounding_whitespace_is_valid(self):
        assert OpenSearchUtils._is_valid_value("  hello  ") is True

    def test_integer_is_valid(self):
        assert OpenSearchUtils._is_valid_value(42) is True

    def test_zero_integer_is_valid(self):
        assert OpenSearchUtils._is_valid_value(0) is True

    def test_negative_integer_is_valid(self):
        assert OpenSearchUtils._is_valid_value(-1) is True

    def test_float_is_valid(self):
        assert OpenSearchUtils._is_valid_value(3.14) is True

    def test_zero_float_is_valid(self):
        assert OpenSearchUtils._is_valid_value(0.0) is True

    def test_bool_true_is_valid(self):
        assert OpenSearchUtils._is_valid_value(True) is True

    def test_bool_false_is_valid(self):
        assert OpenSearchUtils._is_valid_value(False) is True

    def test_list_is_valid(self):
        assert OpenSearchUtils._is_valid_value(["a", "b"]) is True

    def test_empty_list_is_valid(self):
        assert OpenSearchUtils._is_valid_value([]) is True


# ---------------------------------------------------------------------------
# OpenSearchUtils.build_conditions
# ---------------------------------------------------------------------------

class TestBuildConditions:

    def test_empty_filters(self):
        result = OpenSearchUtils.build_conditions({})
        assert result == []

    def test_single_string_value(self):
        result = OpenSearchUtils.build_conditions({"orgId": "org-123"})
        assert len(result) == 1
        assert result[0].key == "metadata.orgId"
        assert result[0].value == "org-123"

    def test_metadata_prefix_added(self):
        result = OpenSearchUtils.build_conditions({"status": "active"})
        assert result[0].key == "metadata.status"

    def test_integer_value(self):
        result = OpenSearchUtils.build_conditions({"count": 5})
        assert len(result) == 1
        assert result[0].value == 5

    def test_float_value(self):
        result = OpenSearchUtils.build_conditions({"score": 0.95})
        assert len(result) == 1
        assert result[0].value == 0.95

    def test_bool_value(self):
        result = OpenSearchUtils.build_conditions({"active": True})
        assert len(result) == 1
        assert result[0].value is True

    def test_bool_false_value(self):
        result = OpenSearchUtils.build_conditions({"active": False})
        assert len(result) == 1
        assert result[0].value is False

    def test_list_value_uses_values_field(self):
        result = OpenSearchUtils.build_conditions({"roles": ["admin", "user"]})
        assert len(result) == 1
        cond = result[0]
        assert cond.key == "metadata.roles"
        assert cond.values == ["admin", "user"]

    def test_tuple_value_uses_values_field(self):
        result = OpenSearchUtils.build_conditions({"roles": ("admin", "user")})
        assert len(result) == 1
        assert result[0].values == ["admin", "user"]

    def test_list_with_none_values_filtered(self):
        result = OpenSearchUtils.build_conditions({"roles": ["admin", None, "user"]})
        assert len(result) == 1
        assert result[0].values == ["admin", "user"]

    def test_list_all_none_values_produces_no_condition(self):
        result = OpenSearchUtils.build_conditions({"roles": [None, None]})
        assert result == []

    def test_empty_list_produces_no_condition(self):
        result = OpenSearchUtils.build_conditions({"roles": []})
        assert result == []

    def test_none_value_filtered_out(self):
        result = OpenSearchUtils.build_conditions({"orgId": None})
        assert result == []

    def test_empty_string_value_filtered_out(self):
        result = OpenSearchUtils.build_conditions({"orgId": ""})
        assert result == []

    def test_whitespace_string_value_filtered_out(self):
        result = OpenSearchUtils.build_conditions({"orgId": "   "})
        assert result == []

    def test_multiple_filters(self):
        result = OpenSearchUtils.build_conditions({
            "orgId": "org-123",
            "status": "active",
        })
        assert len(result) == 2
        keys = {c.key for c in result}
        assert keys == {"metadata.orgId", "metadata.status"}

    def test_mixed_valid_and_invalid_filters(self):
        result = OpenSearchUtils.build_conditions({
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
        result = OpenSearchUtils.build_conditions({"count": 0})
        assert len(result) == 1
        assert result[0].value == 0


# ---------------------------------------------------------------------------
# OpenSearchUtils._field_condition_to_clause
# ---------------------------------------------------------------------------

class TestFieldConditionToClause:

    def test_single_value_produces_term(self):
        cond = FieldCondition(key="metadata.orgId", value="org-123")
        result = OpenSearchUtils._field_condition_to_clause(cond)
        assert result == {"term": {"metadata.orgId": "org-123"}}

    def test_list_values_produces_terms(self):
        cond = FieldCondition(key="metadata.roles", values=["admin", "user"])
        result = OpenSearchUtils._field_condition_to_clause(cond)
        assert result == {"terms": {"metadata.roles": ["admin", "user"]}}

    def test_bool_value_produces_term(self):
        cond = FieldCondition(key="metadata.active", value=True)
        result = OpenSearchUtils._field_condition_to_clause(cond)
        assert result == {"term": {"metadata.active": True}}

    def test_integer_value_produces_term(self):
        cond = FieldCondition(key="metadata.count", value=42)
        result = OpenSearchUtils._field_condition_to_clause(cond)
        assert result == {"term": {"metadata.count": 42}}


# ---------------------------------------------------------------------------
# OpenSearchUtils.filter_expression_to_bool_query
# ---------------------------------------------------------------------------

class TestFilterExpressionToBoolQuery:

    def test_empty_expression(self):
        expr = FilterExpression()
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert result == {"match_all": {}}

    def test_must_conditions(self):
        expr = FilterExpression(
            must=[FieldCondition(key="metadata.orgId", value="org-123")]
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert "bool" in result
        assert result["bool"]["must"] is not None
        assert len(result["bool"]["must"]) == 1
        assert result["bool"]["must"][0] == {"term": {"metadata.orgId": "org-123"}}

    def test_should_conditions(self):
        expr = FilterExpression(
            should=[FieldCondition(key="metadata.role", values=["admin", "user"])]
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert result["bool"]["should"] is not None
        assert len(result["bool"]["should"]) == 1
        assert result["bool"]["should"][0] == {"terms": {"metadata.role": ["admin", "user"]}}

    def test_should_with_min_should_match(self):
        expr = FilterExpression(
            should=[
                FieldCondition(key="metadata.role", values=["admin", "user"]),
            ],
            min_should_match=1,
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert result["bool"]["minimum_should_match"] == 1

    def test_should_without_min_should_match(self):
        expr = FilterExpression(
            should=[FieldCondition(key="metadata.role", value="admin")]
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert "minimum_should_match" not in result["bool"]

    def test_must_not_conditions(self):
        expr = FilterExpression(
            must_not=[FieldCondition(key="metadata.status", value="deleted")]
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert result["bool"]["must_not"] is not None
        assert len(result["bool"]["must_not"]) == 1

    def test_combined_conditions(self):
        expr = FilterExpression(
            must=[FieldCondition(key="metadata.orgId", value="org-123")],
            should=[FieldCondition(key="metadata.role", values=["admin"])],
            must_not=[FieldCondition(key="metadata.banned", value=True)],
        )
        result = OpenSearchUtils.filter_expression_to_bool_query(expr)
        assert len(result["bool"]["must"]) == 1
        assert len(result["bool"]["should"]) == 1
        assert len(result["bool"]["must_not"]) == 1


# ---------------------------------------------------------------------------
# OpenSearchUtils.vector_point_to_document
# ---------------------------------------------------------------------------

class TestVectorPointToDocument:

    def test_basic(self):
        point = VectorPoint(
            id="abc-123",
            dense_vector=[0.1, 0.2, 0.3],
            payload={
                "metadata": {"orgId": "org1"},
                "page_content": "hello world",
            },
        )
        doc = OpenSearchUtils.vector_point_to_document(point)
        assert doc["metadata"] == {"orgId": "org1"}
        assert doc["page_content"] == "hello world"
        assert doc["dense_embedding"] == [0.1, 0.2, 0.3]

    def test_no_dense_vector(self):
        point = VectorPoint(
            id="abc-123",
            payload={"metadata": {}, "page_content": "test"},
        )
        doc = OpenSearchUtils.vector_point_to_document(point)
        assert "dense_embedding" not in doc

    def test_empty_payload(self):
        point = VectorPoint(id="abc-123")
        doc = OpenSearchUtils.vector_point_to_document(point)
        assert doc["metadata"] == {}
        assert doc["page_content"] == ""

    def test_with_sparse_vector_ignored(self):
        point = VectorPoint(
            id="abc-123",
            dense_vector=[0.1, 0.2],
            sparse_vector=SparseVector(indices=[0, 5], values=[1.0, 0.5]),
            payload={"metadata": {}, "page_content": "test"},
        )
        doc = OpenSearchUtils.vector_point_to_document(point)
        assert "dense_embedding" in doc
        assert "sparse" not in doc


# ---------------------------------------------------------------------------
# OpenSearchUtils.hit_to_search_result
# ---------------------------------------------------------------------------

class TestHitToSearchResult:

    def test_basic(self):
        hit = {
            "_id": "doc-1",
            "_score": 0.95,
            "_source": {
                "metadata": {"orgId": "org1"},
                "page_content": "hello",
            },
        }
        result = OpenSearchUtils.hit_to_search_result(hit)
        assert isinstance(result, SearchResult)
        assert result.id == "doc-1"
        assert result.score == 0.95
        assert result.payload["metadata"]["orgId"] == "org1"
        assert result.payload["page_content"] == "hello"

    def test_missing_fields(self):
        hit = {"_id": "doc-1"}
        result = OpenSearchUtils.hit_to_search_result(hit)
        assert result.id == "doc-1"
        assert result.score == 0.0
        assert result.payload["metadata"] == {}
        assert result.payload["page_content"] == ""

    def test_missing_id(self):
        hit = {"_score": 0.5, "_source": {"metadata": {}, "page_content": "x"}}
        result = OpenSearchUtils.hit_to_search_result(hit)
        assert result.id == ""

    def test_none_score(self):
        hit = {"_id": "doc-1", "_score": None, "_source": {"metadata": {}, "page_content": ""}}
        result = OpenSearchUtils.hit_to_search_result(hit)
        assert result.score == 0.0

    def test_integer_id(self):
        hit = {"_id": 42, "_score": 0.8, "_source": {"metadata": {}, "page_content": ""}}
        result = OpenSearchUtils.hit_to_search_result(hit)
        assert result.id == "42"


# ---------------------------------------------------------------------------
# OpenSearchUtils.build_hybrid_query
# ---------------------------------------------------------------------------

class TestBuildHybridQuery:

    def test_full_hybrid(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            text_query="hello world",
            limit=20,
        )
        filter_query = {"bool": {"must": [{"term": {"metadata.orgId": "org1"}}]}}
        body = OpenSearchUtils.build_hybrid_query(req, filter_query)

        assert body["size"] == 20
        assert "hybrid" in body["query"]
        assert len(body["query"]["hybrid"]["queries"]) == 2
        assert "post_filter" in body

    def test_dense_only(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2, 0.3],
            limit=10,
        )
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert "knn" in body["query"]
        assert body["query"]["knn"]["dense_embedding"]["vector"] == [0.1, 0.2, 0.3]
        assert body["query"]["knn"]["dense_embedding"]["k"] == 10

    def test_text_only(self):
        req = HybridSearchRequest(
            text_query="hello",
            limit=10,
        )
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert "match" in body["query"]
        assert body["query"]["match"]["page_content"]["query"] == "hello"

    def test_no_queries(self):
        req = HybridSearchRequest(limit=10)
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert body["query"] == {"match_all": {}}

    def test_no_filter(self):
        req = HybridSearchRequest(
            dense_query=[0.1],
            text_query="hello",
            limit=5,
        )
        body = OpenSearchUtils.build_hybrid_query(req, {"match_all": {}})
        assert "post_filter" not in body

    def test_empty_filter(self):
        req = HybridSearchRequest(
            dense_query=[0.1],
            limit=5,
        )
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert "post_filter" not in body

    def test_sparse_query_without_text_skips_bm25(self):
        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            sparse_query=SparseVector(indices=[0, 1], values=[1.0, 0.5]),
            limit=10,
        )
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert "knn" in body["query"]
        # Should be a single knn query, no hybrid wrapper needed
        assert "hybrid" not in body["query"]

    def test_source_excludes_dense_embedding(self):
        req = HybridSearchRequest(dense_query=[0.1], limit=5)
        body = OpenSearchUtils.build_hybrid_query(req, {})
        assert body["_source"]["exclude"] == ["dense_embedding"]


# ---------------------------------------------------------------------------
# OpenSearchService.filter_collection (returns FilterExpression)
# ---------------------------------------------------------------------------

class TestFilterCollection:

    def _make_service(self):
        service = OpenSearchService.__new__(OpenSearchService)
        service.client = MagicMock()
        service.config_service = MagicMock()
        service.is_async = False
        return service

    @pytest.mark.asyncio
    async def test_raises_when_client_not_connected(self):
        service = OpenSearchService.__new__(OpenSearchService)
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

    @pytest.mark.asyncio
    async def test_min_should_match_passed_through(self):
        service = self._make_service()
        result = await service.filter_collection(
            should={"department": "IT"},
            min_should_match=1,
        )
        assert result.min_should_match == 1
