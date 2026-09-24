"""Unit tests for app.modules.agents.qna.tool_system."""

import json

import pytest

from app.modules.agents.qna.tool_system import (
    _flatten_success_into_payload,
    _normalise_tool_result,
    code_execution_enabled,
    get_tool_results_summary,
)


class TestNormaliseToolResult:
    def test_string_passthrough(self):
        assert _normalise_tool_result("hello") == "hello"

    def test_dict_to_json(self):
        result = _normalise_tool_result({"key": "val"})
        assert json.loads(result) == {"key": "val"}

    def test_list_to_json(self):
        result = _normalise_tool_result([1, 2, 3])
        assert json.loads(result) == [1, 2, 3]

    def test_int_to_str(self):
        assert _normalise_tool_result(42) == "42"

    def test_none_to_str(self):
        assert _normalise_tool_result(None) == "None"

    def test_bool_to_str(self):
        assert _normalise_tool_result(True) == "True"


class TestFlattenSuccessIntoPayload:
    def test_dict_data(self):
        result = _flatten_success_into_payload(True, {"key": "val"})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["key"] == "val"

    def test_dict_data_failure(self):
        result = _flatten_success_into_payload(False, {"key": "val"})
        parsed = json.loads(result)
        assert parsed["success"] is False

    def test_json_string_data(self):
        result = _flatten_success_into_payload(True, '{"key": "val"}')
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["key"] == "val"

    def test_plain_string_data(self):
        result = _flatten_success_into_payload(True, "hello")
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["content"] == "hello"

    def test_list_data(self):
        result = _flatten_success_into_payload(True, [1, 2])
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert "content" in parsed


class TestCodeExecutionEnabled:
    def test_state_flag_true(self):
        assert code_execution_enabled({"enable_code_execution": True}) is True

    def test_state_flag_false(self):
        assert code_execution_enabled({"enable_code_execution": False}) is False

    def test_env_true(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "true")
        assert code_execution_enabled({}) is True

    def test_env_false(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "false")
        assert code_execution_enabled({}) is False

    def test_env_one(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "1")
        assert code_execution_enabled({}) is True

    def test_env_zero(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "0")
        assert code_execution_enabled({}) is False

    def test_default_true(self, monkeypatch):
        monkeypatch.delenv("PIPESHUB_ENABLE_CODE_EXECUTION", raising=False)
        assert code_execution_enabled({}) is True

    def test_state_overrides_env(self, monkeypatch):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "false")
        assert code_execution_enabled({"enable_code_execution": True}) is True


class TestGetToolResultsSummary:
    def test_empty_results(self):
        result = get_tool_results_summary({})
        assert "No tools executed" in result

    def test_empty_list(self):
        result = get_tool_results_summary({"all_tool_results": []})
        assert "No tools executed" in result

    def test_single_success(self):
        state = {"all_tool_results": [
            {"tool_name": "slack.search", "status": "success"},
        ]}
        result = get_tool_results_summary(state)
        assert "Total: 1" in result
        assert "slack" in result.lower()

    def test_multiple_categories(self):
        state = {"all_tool_results": [
            {"tool_name": "slack.search", "status": "success"},
            {"tool_name": "slack.post", "status": "error"},
            {"tool_name": "jira.list", "status": "success"},
        ]}
        result = get_tool_results_summary(state)
        assert "Total: 3" in result

    def test_utility_category_for_dotless_name(self):
        state = {"all_tool_results": [
            {"tool_name": "search", "status": "success"},
        ]}
        result = get_tool_results_summary(state)
        assert "Utility" in result
