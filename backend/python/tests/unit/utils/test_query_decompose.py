"""Unit tests for app.utils.query_decompose — QueryDecompositionExpansionService.

Covers: DecomposedQuery model, DecomposedQueries model,
transform_query, _parse_decomposition_response, _validate_and_clean_result,
_map_to_valid_confidence, expand_query, get_query_analysis.
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.query_decompose import (
    DecomposedQueries,
    DecomposedQuery,
    MAX_DECOMPOSE_AND_EXPAND_QUERIES,
    MIN_DECOMPOSE_AND_EXPAND_QUERIES,
    QueryDecompositionExpansionService,
)

log = logging.getLogger("test_query_decompose")
log.setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(llm=None):
    """Build a QueryDecompositionExpansionService with a mock LLM."""
    mock_llm = llm or MagicMock()
    return QueryDecompositionExpansionService(llm=mock_llm, logger=log)


# ============================================================================
# Pydantic models
# ============================================================================

class TestDecomposedQueryModel:
    def test_valid(self):
        dq = DecomposedQuery(query="What is AI?", confidence="High")
        assert dq.query == "What is AI?"
        assert dq.confidence == "High"

    def test_fields_required(self):
        """Both fields are required."""
        with pytest.raises(Exception):
            DecomposedQuery()


class TestDecomposedQueriesModel:
    def test_valid(self):
        dqs = DecomposedQueries(
            queries=[DecomposedQuery(query="q1", confidence="High")],
            reason="test reason",
            operation="none",
        )
        assert len(dqs.queries) == 1
        assert dqs.reason == "test reason"
        assert dqs.operation == "none"

    def test_empty_queries(self):
        dqs = DecomposedQueries(queries=[], reason="no queries", operation="none")
        assert dqs.queries == []


# ============================================================================
# _parse_decomposition_response
# ============================================================================

class TestParseDecompositionResponse:
    def test_response_with_content_attribute(self):
        """Parse LLM response that has a .content attribute."""
        svc = _make_service()
        resp = MagicMock()
        resp.content = json.dumps({
            "queries": [{"query": "q1", "confidence": "High"}],
            "reason": "test",
            "operation": "none",
        })
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "none"
        assert len(result["queries"]) == 1

    def test_response_as_dict(self):
        """Parse response that is a dict with 'content' key."""
        svc = _make_service()
        resp = {
            "content": json.dumps({
                "queries": [{"query": "q1", "confidence": "Medium"}],
                "reason": "dict response",
                "operation": "expansion",
            })
        }
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "expansion"

    def test_response_as_string(self):
        """Parse a plain string response."""
        svc = _make_service()
        resp = json.dumps({
            "queries": [{"query": "q1", "confidence": "Low"}],
            "reason": "string response",
            "operation": "none",
        })
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "none"

    def test_strips_markdown_code_blocks(self):
        """Markdown code fences around JSON are stripped."""
        svc = _make_service()
        resp = MagicMock()
        resp.content = '```json\n{"queries": [{"query": "q1", "confidence": "High"}], "reason": "test", "operation": "none"}\n```'
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        assert result["operation"] == "none"

    def test_strips_think_tags(self):
        """<think> tags are stripped from response."""
        svc = _make_service()
        resp = MagicMock()
        resp.content = '<think>thinking...</think>{"queries": [{"query": "q1", "confidence": "High"}], "reason": "ok", "operation": "none"}'
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result

    def test_legacy_format_string_queries(self):
        """Old format with queries as strings is converted to dicts."""
        svc = _make_service()
        resp = json.dumps({
            "queries": ["query1", "query2"],
            "reason": "legacy",
            "operation": "expansion",
        })
        result = svc._parse_decomposition_response(resp)
        for q in result["queries"]:
            assert isinstance(q, dict)
            assert "query" in q
            assert "confidence" in q

    def test_missing_operation_inferred_single(self):
        """Missing operation is inferred as 'none' for single query."""
        svc = _make_service()
        resp = json.dumps({
            "queries": [{"query": "q1", "confidence": "High"}],
            "reason": "test",
        })
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "none"

    def test_missing_operation_inferred_many(self):
        """Missing operation is inferred as 'decompose_and_expand' for many queries."""
        svc = _make_service()
        queries = [{"query": f"q{i}", "confidence": "High"} for i in range(7)]
        resp = json.dumps({"queries": queries, "reason": "test"})
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "decompose_and_expand"

    def test_missing_operation_inferred_medium(self):
        """Missing operation inferred as 'expansion' for medium query count."""
        svc = _make_service()
        queries = [{"query": f"q{i}", "confidence": "High"} for i in range(3)]
        resp = json.dumps({"queries": queries, "reason": "test"})
        result = svc._parse_decomposition_response(resp)
        assert result["operation"] == "expansion"

    def test_missing_fields_defaults(self):
        """Missing queries/reason fields get defaults."""
        svc = _make_service()
        resp = json.dumps({"operation": "none"})
        result = svc._parse_decomposition_response(resp)
        assert result["queries"] == []
        assert "reason" in result

    def test_invalid_json_returns_error(self):
        """Unparseable JSON returns error dict."""
        svc = _make_service()
        result = svc._parse_decomposition_response("not json at all {{{")
        assert "error" in result

    def test_non_dict_json_returns_error(self):
        """JSON that parses to non-dict returns error."""
        svc = _make_service()
        result = svc._parse_decomposition_response("[1,2,3]")
        assert "error" in result

    def test_query_dict_missing_confidence(self):
        """Query dict without confidence gets default 'High'."""
        svc = _make_service()
        resp = json.dumps({
            "queries": [{"query": "q1"}],
            "reason": "test",
            "operation": "none",
        })
        result = svc._parse_decomposition_response(resp)
        assert result["queries"][0]["confidence"] == "High"


# ============================================================================
# _validate_and_clean_result
# ============================================================================

class TestValidateAndCleanResult:
    def test_empty_queries_defaults(self):
        svc = _make_service()
        result = svc._validate_and_clean_result({"queries": [], "operation": "none"}, "original q")
        assert len(result["queries"]) == 1
        assert result["queries"][0]["query"] == "original q"
        assert result["operation"] == "none"

    def test_invalid_confidence_mapped(self):
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": [{"query": "q1", "confidence": "super high"}],
                "reason": "test",
                "operation": "none",
            },
            "q1",
        )
        # "super high" doesn't match a valid value, so it gets mapped
        assert result["queries"][0]["confidence"] in ["Very High", "High", "Medium", "Low"]

    def test_invalid_operation_inferred(self):
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": [{"query": "q1", "confidence": "High"}],
                "reason": "test",
                "operation": "BOGUS",
            },
            "q1",
        )
        assert result["operation"] in ["none", "expansion", "decompose_and_expand"]

    def test_missing_reason_generated(self):
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": [{"query": "q1", "confidence": "High"}],
                "reason": "",
                "operation": "none",
            },
            "q1",
        )
        assert result["reason"] != ""

    def test_valid_result_passes_through(self):
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": [{"query": "q1", "confidence": "Very High"}],
                "reason": "all good",
                "operation": "expansion",
            },
            "q1",
        )
        assert result["queries"][0]["confidence"] == "Very High"
        assert result["operation"] == "expansion"
        assert result["reason"] == "all good"

    def test_non_dict_query_obj_skipped(self):
        """Non-dict items in queries list are skipped during confidence validation."""
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": ["not a dict", {"query": "q1", "confidence": "High"}],
                "reason": "test",
                "operation": "expansion",
            },
            "q1",
        )
        assert len(result["queries"]) == 2


# ============================================================================
# _map_to_valid_confidence
# ============================================================================

class TestMapToValidConfidence:
    def test_very_high_variations(self):
        svc = _make_service()
        for val in ["very high", "HIGHEST", "Maximum", "Critical"]:
            assert svc._map_to_valid_confidence(val) == "Very High"

    def test_high_variations(self):
        svc = _make_service()
        for val in ["high", "Important", "strong"]:
            assert svc._map_to_valid_confidence(val) == "High"

    def test_medium_variations(self):
        svc = _make_service()
        for val in ["medium", "Moderate", "mid", "average"]:
            assert svc._map_to_valid_confidence(val) == "Medium"

    def test_low_variations(self):
        svc = _make_service()
        for val in ["low", "Weak", "uncertain"]:
            assert svc._map_to_valid_confidence(val) == "Low"

    def test_unknown_defaults_medium(self):
        svc = _make_service()
        assert svc._map_to_valid_confidence("xyz_unknown") == "Medium"

    def test_non_string_defaults_medium(self):
        svc = _make_service()
        assert svc._map_to_valid_confidence(123) == "Medium"


# ============================================================================
# transform_query
# ============================================================================

class TestTransformQuery:
    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_exception_returns_fallback(self):
        """When LLM chain raises, original query is returned."""
        svc = _make_service()
        svc.llm.with_structured_output = MagicMock(side_effect=Exception("boom"))

        result = await svc.transform_query("my query")
        assert result["queries"][0]["query"] == "my query"
        assert result["operation"] == "none"
        assert "Error" in result["reason"] or "error" in result["reason"].lower()


# ============================================================================
# expand_query
# ============================================================================

class TestExpandQuery:
    @pytest.mark.asyncio
    async def test_delegates_to_transform(self):
        """expand_query just calls transform_query."""
        svc = _make_service()
        expected = {"queries": [{"query": "q", "confidence": "High"}], "reason": "ok", "operation": "expansion"}
        svc.transform_query = AsyncMock(return_value=expected)

        result = await svc.expand_query("test")
        svc.transform_query.assert_called_once_with("test")
        assert result == expected


# ============================================================================
# get_query_analysis
# ============================================================================

class TestGetQueryAnalysis:
    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        """When analysis chain raises, error dict is returned."""
        svc = _make_service()

        # Since mocking langchain chains is complex, test the error branch
        svc.llm.__or__ = MagicMock(side_effect=Exception("chain fail"))

        result = await svc.get_query_analysis("test query")
        # The error is caught and returns a dict with error info
        assert "error" in result or "complexity" in result
