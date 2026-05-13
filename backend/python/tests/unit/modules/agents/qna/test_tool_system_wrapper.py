"""Regression tests for the tool-result wrapper helpers in tool_system.

The wrapper is what LangChain invokes for every tool call. If it does the
wrong thing with the tool's return value, the LLM sees garbled
``ToolMessage`` content (e.g. Python ``repr`` of a dict, a stringified
tuple, or a stray ``False``). These tests lock in the intended contract:

1. ``(success: bool, data)`` tuples are FLATTENED into a single JSON string
   with ``success`` injected as a top-level key — never returned as a tuple
   to LangChain (which would either ``str()``-ify it into
   ``"(True, '{...}')"`` or fail Pydantic validation on the ``ToolMessage``).
2. Already-string non-tuple results pass through untouched.
3. Dict / list non-tuple results are JSON-encoded (never Python repr).
4. Lists are NOT unwrapped as tuples, even if they happen to have length 2.
5. Non-JSON-serializable members fall back to ``default=str`` JSON encoding.
6. Primitives (int, bool) are stringified via ``_normalise_tool_result``.

The helpers are imported directly — earlier the file used AST extraction to
avoid heavy transitive imports, but lifting the helpers to module-level made
that workaround unnecessary.
"""

from __future__ import annotations

import json

import pytest

from app.modules.agents.qna.tool_system import (
    _flatten_success_into_payload,
    _normalise_tool_result,
)


# ---------------------------------------------------------------------------
# _normalise_tool_result — non-tuple normalisation
# ---------------------------------------------------------------------------

class TestNormaliseToolResult:
    def test_string_passes_through(self):
        assert _normalise_tool_result("already a string") == "already a string"

    def test_dict_is_json_encoded(self):
        out = _normalise_tool_result({"alpha": 1, "beta": "two"})
        assert json.loads(out) == {"alpha": 1, "beta": "two"}

    def test_list_is_json_encoded(self):
        out = _normalise_tool_result(["first", "second"])
        assert json.loads(out) == ["first", "second"]

    def test_nonserializable_dict_falls_back_to_str(self):
        class _X:
            def __repr__(self) -> str:
                return "X()"

        out = _normalise_tool_result({"obj": _X()})
        parsed = json.loads(out)
        assert "obj" in parsed
        # default=str coerces non-JSON objects to their str() form.
        assert isinstance(parsed["obj"], str)

    def test_primitive_bool_is_stringified(self):
        assert _normalise_tool_result(True) == "True"

    def test_primitive_int_is_stringified(self):
        assert _normalise_tool_result(42) == "42"


# ---------------------------------------------------------------------------
# _flatten_success_into_payload — tuple flattening contract
# ---------------------------------------------------------------------------

class TestFlattenSuccessIntoPayload:
    """Every tuple shape the action layer can produce must flatten to a JSON
    string that carries the success bool as a top-level ``success`` key."""

    def test_success_with_dict_payload_merges(self):
        out = _flatten_success_into_payload(True, {"message": "ok", "count": 3})
        parsed = json.loads(out)
        assert parsed == {"success": True, "message": "ok", "count": 3}

    def test_failure_with_dict_payload_merges(self):
        out = _flatten_success_into_payload(False, {"error": "HTTP 400", "details": "bad"})
        parsed = json.loads(out)
        assert parsed == {"success": False, "error": "HTTP 400", "details": "bad"}

    def test_success_with_json_string_payload_merges(self):
        out = _flatten_success_into_payload(True, '{"results": [1, 2], "total": 2}')
        parsed = json.loads(out)
        assert parsed == {"success": True, "results": [1, 2], "total": 2}

    def test_failure_with_json_string_payload_merges(self):
        out = _flatten_success_into_payload(False, '{"error": "boom"}')
        parsed = json.loads(out)
        assert parsed == {"success": False, "error": "boom"}

    def test_non_json_string_payload_wrapped_in_content(self):
        """When the data isn't JSON-parseable to a dict, fall back to wrapping."""
        out = _flatten_success_into_payload(True, "plain text content")
        parsed = json.loads(out)
        assert parsed == {"success": True, "content": "plain text content"}

    def test_list_payload_wrapped_in_content(self):
        # Lists CAN'T be merged at the top level (no key/value structure).
        out = _flatten_success_into_payload(True, [1, 2, 3])
        parsed = json.loads(out)
        assert parsed["success"] is True
        # Content stringified; we don't try to preserve list structure here.
        assert "content" in parsed

    def test_success_value_is_coerced_to_bool(self):
        """Truthy/falsy non-bool values are coerced to a real bool."""
        out_truthy = _flatten_success_into_payload(1, {"x": 1})  # type: ignore[arg-type]
        assert json.loads(out_truthy)["success"] is True

        out_falsy = _flatten_success_into_payload(0, {"x": 1})  # type: ignore[arg-type]
        assert json.loads(out_falsy)["success"] is False

    def test_success_key_in_payload_is_overridden_to_authoritative_value(self):
        """If the data dict already has its own `success` key, the authoritative
        bool from the tuple's first element wins. (`{**data, "success": ...}`
        order in the helper guarantees this.)"""
        out = _flatten_success_into_payload(False, {"success": True, "error": "really failed"})
        parsed = json.loads(out)
        # Authoritative value is False — not the misleading inner True.
        assert parsed["success"] is False
        assert parsed["error"] == "really failed"

    def test_nonserializable_dict_payload_uses_default_str(self):
        class _X:
            def __repr__(self) -> str:
                return "X()"

        out = _flatten_success_into_payload(True, {"obj": _X()})
        parsed = json.loads(out)
        assert parsed["success"] is True
        assert "obj" in parsed
        assert isinstance(parsed["obj"], str)


# ---------------------------------------------------------------------------
# Integration — the whole flow as the LangChain wrapper sees it
# ---------------------------------------------------------------------------

class TestFlattenedShapeIsConsumerCompatible:
    """The flattened JSON must be readable by both downstream consumers
    without further work — that's the point of flattening."""

    def test_extract_success_status_reads_flattened_success_true(self):
        """``ToolResultExtractor.extract_success_status`` (QnA path) reads
        the ``success`` key from the parsed JSON."""
        from app.modules.agents.qna.nodes import ToolResultExtractor

        out = _flatten_success_into_payload(True, {"message": "ok"})
        # Mimics the consumer path: arrives as a string, gets parsed, is read
        # by the dict branch which short-circuits on `success`.
        assert ToolResultExtractor.extract_success_status(out) is True

    def test_extract_success_status_reads_flattened_success_false(self):
        from app.modules.agents.qna.nodes import ToolResultExtractor

        out = _flatten_success_into_payload(False, {"error": "boom"})
        assert ToolResultExtractor.extract_success_status(out) is False

    def test_detect_tool_result_status_reads_flattened_success(self):
        """``_detect_tool_result_status`` (ReAct path) also reads the
        flattened ``success`` key after JSON-parsing."""
        from app.modules.agents.qna.nodes import _detect_tool_result_status

        # Note: `_detect_tool_result_status` accepts either string-content or
        # already-parsed dict. The ReAct path calls it post-JSON-parse; we
        # test both surfaces here.
        out_str = _flatten_success_into_payload(False, {"error": "boom"})
        assert _detect_tool_result_status(out_str) == "error"

        out_dict = json.loads(_flatten_success_into_payload(True, {"x": 1}))
        assert _detect_tool_result_status(out_dict) == "success"
