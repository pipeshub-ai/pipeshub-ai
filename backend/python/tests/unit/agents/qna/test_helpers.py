"""Tests for app.modules.agents.qna.helpers."""

import json

from app.modules.agents.qna.helpers import (
    REMOVE_FIELDS,
    _tool_names_and_results_from_state,
    clean_tool_result,
)


class TestCleanToolResult:
    def test_primitive_passthrough(self):
        assert clean_tool_result(42) == 42
        assert clean_tool_result(None) is None
        assert clean_tool_result(True) is True

    def test_string_passthrough(self):
        assert clean_tool_result("hello") == "hello"

    def test_dict_removes_blacklisted_fields(self):
        data = {"name": "Alice", "avatarUrls": "http://...", "debug": {}}
        result = clean_tool_result(data)
        assert result == {"name": "Alice"}

    def test_dict_removes_underscore_prefix_keys(self):
        data = {"name": "x", "_links": {}, "$schema": "v1"}
        result = clean_tool_result(data)
        assert result == {"name": "x"}

    def test_dict_removes_case_insensitive(self):
        data = {"name": "x", "Self": "ref"}
        result = clean_tool_result(data)
        assert result == {"name": "x"}

    def test_dict_recursion(self):
        data = {"project": {"name": "P", "avatarUrls": "..."}}
        result = clean_tool_result(data)
        assert result == {"project": {"name": "P"}}

    def test_empty_nested_dict_removed(self):
        data = {"meta": {"_links": "...", "$ref": "..."}}
        result = clean_tool_result(data)
        assert "meta" not in result

    def test_list_recursion(self):
        data = [{"name": "A", "debug": True}, {"name": "B"}]
        result = clean_tool_result(data)
        assert result == [{"name": "A"}, {"name": "B"}]

    def test_list_in_dict(self):
        data = {"items": [{"name": "A", "avatarUrls": "..."}]}
        result = clean_tool_result(data)
        assert result == {"items": [{"name": "A"}]}

    def test_tuple_result(self):
        result = clean_tool_result((True, {"name": "x", "debug": "y"}))
        assert result == (True, {"name": "x"})

    def test_json_string_parsed_and_cleaned(self):
        payload = json.dumps({"name": "x", "avatarUrls": "..."})
        result = clean_tool_result(payload)
        parsed = json.loads(result)
        assert parsed == {"name": "x"}

    def test_non_json_string_returned_as_is(self):
        assert clean_tool_result("just text") == "just text"

    def test_all_remove_fields_covered(self):
        data = {field: "val" for field in REMOVE_FIELDS}
        data["keep_me"] = "yes"
        result = clean_tool_result(data)
        assert result == {"keep_me": "yes"}


class TestToolNamesAndResultsFromState:
    def test_empty_state(self):
        result = _tool_names_and_results_from_state({})
        assert result["succeeded_tool_names"] == []
        assert result["failed_tool_names"] == []
        assert result["tool_results"] == []

    def test_extracts_succeeded_and_failed(self):
        state = {
            "all_tool_results": [
                {"tool_name": "search", "status": "success"},
                {"tool_name": "write", "status": "error"},
                {"tool_name": "read", "status": "success"},
            ]
        }
        result = _tool_names_and_results_from_state(state)
        assert result["succeeded_tool_names"] == ["search", "read"]
        assert result["failed_tool_names"] == ["write"]

    def test_falls_back_to_tool_results_key(self):
        state = {
            "tool_results": [
                {"tool_name": "t1", "status": "success"},
            ]
        }
        result = _tool_names_and_results_from_state(state)
        assert result["succeeded_tool_names"] == ["t1"]

    def test_skips_entries_without_tool_name(self):
        state = {
            "all_tool_results": [
                {"status": "success"},
                {"tool_name": "t1", "status": "success"},
            ]
        }
        result = _tool_names_and_results_from_state(state)
        assert result["succeeded_tool_names"] == ["t1"]

    def test_unrecognized_status_is_neither(self):
        state = {
            "all_tool_results": [
                {"tool_name": "t1", "status": "pending"},
            ]
        }
        result = _tool_names_and_results_from_state(state)
        assert result["succeeded_tool_names"] == []
        assert result["failed_tool_names"] == []
