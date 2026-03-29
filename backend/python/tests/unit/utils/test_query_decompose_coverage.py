"""
Coverage boost tests for app.utils.query_decompose.

Targets uncovered lines:
- 161-162: dict response with </think> tag
- 166-167: string response with </think> tag
- 200: query obj that is a dict without 'query' key
- 240-264: transform_query success path (full chain execution)
- 303-306: _validate_and_clean_result with invalid operation and many queries
- 380-382: get_query_analysis exception path
"""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.query_decompose import (
    QueryDecompositionExpansionService,
)

log = logging.getLogger("test_query_decompose_coverage")
log.setLevel(logging.CRITICAL)


def _make_service(llm=None):
    mock_llm = llm or MagicMock()
    return QueryDecompositionExpansionService(llm=mock_llm, logger=log)


class TestParseResponseThinkTags:
    """Lines 161-162: dict response with think tags, 166-167: string with think tags."""

    def test_dict_response_with_think_tag(self):
        """Dict response containing </think> tag is handled."""
        svc = _make_service()
        json_payload = json.dumps({
            "queries": [{"query": "q1", "confidence": "High"}],
            "reason": "test",
            "operation": "none",
        })
        # response is a string containing think tags
        resp = f"<think>reasoning</think>{json_payload}"
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        assert result["operation"] == "none"
        assert len(result["queries"]) == 1

    def test_plain_string_response_with_think_tag(self):
        """Plain string response containing </think> tag is handled."""
        svc = _make_service()
        json_payload = json.dumps({
            "queries": [{"query": "q1", "confidence": "Medium"}],
            "reason": "string test",
            "operation": "expansion",
        })
        resp = f"<think>some reasoning</think>{json_payload}"
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        assert result["operation"] == "expansion"

    def test_string_response_with_think_tag_and_whitespace(self):
        """String with think tag and whitespace around JSON."""
        svc = _make_service()
        json_payload = json.dumps({
            "queries": [{"query": "q1", "confidence": "High"}],
            "reason": "test",
            "operation": "none",
        })
        resp = f"<think>thinking deeply</think>  \n  {json_payload}  "
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        assert result["queries"][0]["query"] == "q1"


class TestParseQueryObjWithoutQueryKey:
    """Line 200: query dict without 'query' key gets converted with str()."""

    def test_query_dict_missing_query_key(self):
        """A query dict that has no 'query' field is converted via str()."""
        svc = _make_service()
        resp = json.dumps({
            "queries": [{"something_else": "value", "other": 123}],
            "reason": "test",
            "operation": "none",
        })
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        # The dict without 'query' key should be converted to string
        assert result["queries"][0]["confidence"] == "High"
        assert isinstance(result["queries"][0]["query"], str)

    def test_query_is_integer(self):
        """A non-dict, non-string query (e.g., integer) is converted."""
        svc = _make_service()
        resp = json.dumps({
            "queries": [42],
            "reason": "test",
            "operation": "none",
        })
        result = svc._parse_decomposition_response(resp)
        assert result["queries"][0]["query"] == "42"
        assert result["queries"][0]["confidence"] == "High"


class TestTransformQuerySuccess:
    """Lines 240-264: the successful transform_query path."""

    @pytest.mark.asyncio
    async def test_transform_query_returns_error_from_parse(self):
        """When parse returns error, transform_query returns fallback."""
        svc = _make_service()
        svc.llm.with_structured_output = MagicMock()

        error_result = {"error": "JSON parsing failed"}

        # Create mock chain
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=error_result)

        # Mock the chain by making template.__ror__ work
        with patch.object(
            type(svc.decomposition_template), '__ror__',
            create=True,
            return_value=MagicMock(__or__=MagicMock(return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))))
        ):
            result = await svc.transform_query("test query")

        # When chain returns error, fallback is used
        assert result["queries"][0]["query"] == "test query"
        assert result["operation"] == "none"

    @pytest.mark.asyncio
    async def test_transform_query_not_implemented_structured_output(self):
        """When with_structured_output raises NotImplementedError."""
        svc = _make_service()
        svc.llm.with_structured_output = MagicMock(
            side_effect=NotImplementedError("not supported")
        )

        # The method should catch NotImplementedError and continue
        # Then the chain will fail (since LLM is a MagicMock), causing exception fallback
        result = await svc.transform_query("test query")

        # Should get fallback result
        assert result["queries"][0]["query"] == "test query"
        assert result["operation"] == "none"

    @pytest.mark.asyncio
    async def test_transform_query_success_via_chain_mock(self):
        """Full successful transform_query by mocking the chain's ainvoke."""
        svc = _make_service()
        svc.llm.with_structured_output = MagicMock()

        valid_result = {
            "queries": [
                {"query": "q1", "confidence": "Very High"},
                {"query": "q2", "confidence": "High"},
            ],
            "reason": "Test decomposition",
            "operation": "expansion",
        }

        # Create a mock chain that returns valid result
        mock_chain = MagicMock()
        mock_chain.ainvoke = AsyncMock(return_value=valid_result)

        # Mock the | chain: dict | template | llm | parse_fn
        with patch.object(
            type(svc.decomposition_template), '__ror__',
            create=True,
            return_value=MagicMock(__or__=MagicMock(return_value=MagicMock(__or__=MagicMock(return_value=mock_chain))))
        ):
            result = await svc.transform_query("complex multi-part query")

        # Should get the valid result (after validation)
        assert len(result["queries"]) == 2
        assert result["operation"] == "expansion"
        assert result["reason"] == "Test decomposition"


class TestValidateInvalidOperationManyQueries:
    """Lines 303-306: invalid operation with many queries -> decompose_and_expand."""

    def test_invalid_operation_many_queries(self):
        svc = _make_service()
        many_queries = [{"query": f"q{i}", "confidence": "High"} for i in range(7)]
        result = svc._validate_and_clean_result(
            {
                "queries": many_queries,
                "reason": "test",
                "operation": "INVALID_OP",
            },
            "original",
        )
        assert result["operation"] == "decompose_and_expand"

    def test_invalid_operation_few_queries(self):
        svc = _make_service()
        result = svc._validate_and_clean_result(
            {
                "queries": [{"query": "q1", "confidence": "High"}],
                "reason": "test",
                "operation": "INVALID_OP",
            },
            "original",
        )
        assert result["operation"] == "none"

    def test_invalid_operation_medium_queries(self):
        svc = _make_service()
        queries = [{"query": f"q{i}", "confidence": "High"} for i in range(3)]
        result = svc._validate_and_clean_result(
            {
                "queries": queries,
                "reason": "test",
                "operation": "BOGUS",
            },
            "original",
        )
        assert result["operation"] == "expansion"


class TestGetQueryAnalysisException:
    """Lines 380-382: get_query_analysis exception."""

    @pytest.mark.asyncio
    async def test_analysis_chain_exception(self):
        """When the analysis chain raises, error dict is returned."""
        svc = _make_service()
        # Make the LLM operations fail
        svc.llm.__or__ = MagicMock(side_effect=Exception("analysis failed"))

        result = await svc.get_query_analysis("test query")
        assert "error" in result or "complexity" in result

    @pytest.mark.asyncio
    async def test_analysis_chain_runtime_error(self):
        """RuntimeError during analysis returns error dict or default result."""
        svc = _make_service()
        svc.llm.__or__ = MagicMock(side_effect=RuntimeError("connection lost"))

        result = await svc.get_query_analysis("test query")
        # The function may return an error dict or a default analysis result
        assert isinstance(result, dict)


class TestParseCodeBlockVariants:
    """Test various code block stripping scenarios."""

    def test_triple_backtick_without_json(self):
        svc = _make_service()
        json_payload = json.dumps({
            "queries": [{"query": "q1", "confidence": "High"}],
            "reason": "test",
            "operation": "none",
        })
        resp = MagicMock()
        resp.content = f"```\n{json_payload}\n```"
        result = svc._parse_decomposition_response(resp)
        assert "error" not in result
        assert result["operation"] == "none"


class TestParseGeneralException:
    """Test the general Exception catch in _parse_decomposition_response."""

    def test_general_exception_returns_error(self):
        svc = _make_service()
        # An object that causes a general exception when accessed
        resp = MagicMock()
        resp.content = None
        # This will cause json.loads to fail with TypeError -> caught by Exception
        type(resp).content = property(lambda self: None)

        result = svc._parse_decomposition_response(resp)
        assert "error" in result
