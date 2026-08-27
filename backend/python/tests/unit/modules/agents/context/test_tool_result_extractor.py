"""Tests for ToolResultExtractor — success detection, data extraction, and field navigation."""

import json

import pytest

from app.modules.agents.context.tool_result_extractor import ToolResultExtractor


# ---------------------------------------------------------------------------
# extract_success_status
# ---------------------------------------------------------------------------

class TestExtractSuccessStatus:

    def test_none_returns_false(self):
        assert ToolResultExtractor.extract_success_status(None) is False

    # -- tuple format --

    def test_tuple_true(self):
        assert ToolResultExtractor.extract_success_status((True, {"data": 1})) is True

    def test_tuple_false(self):
        assert ToolResultExtractor.extract_success_status((False, "err")) is False

    def test_tuple_single_element(self):
        assert ToolResultExtractor.extract_success_status((True,)) is True

    def test_tuple_single_element_false(self):
        assert ToolResultExtractor.extract_success_status((False,)) is False

    # -- dict format: success key --

    def test_dict_success_true(self):
        assert ToolResultExtractor.extract_success_status({"success": True}) is True

    def test_dict_success_false(self):
        assert ToolResultExtractor.extract_success_status({"success": False}) is False

    # -- dict format: ok key --

    def test_dict_ok_true(self):
        assert ToolResultExtractor.extract_success_status({"ok": True, "data": []}) is True

    def test_dict_ok_false(self):
        assert ToolResultExtractor.extract_success_status({"ok": False}) is False

    # -- dict format: error key --

    def test_dict_error_present(self):
        assert ToolResultExtractor.extract_success_status({"error": "something broke"}) is False

    def test_dict_error_none(self):
        assert ToolResultExtractor.extract_success_status({"error": None}) is True

    def test_dict_error_empty_string(self):
        assert ToolResultExtractor.extract_success_status({"error": ""}) is True

    def test_dict_error_null_string(self):
        assert ToolResultExtractor.extract_success_status({"error": "null"}) is True

    # -- dict format: status key --

    def test_dict_status_500(self):
        assert ToolResultExtractor.extract_success_status({"status": 500}) is False

    def test_dict_status_404(self):
        assert ToolResultExtractor.extract_success_status({"status": 404}) is False

    def test_dict_status_200(self):
        assert ToolResultExtractor.extract_success_status({"status": 200, "body": "ok"}) is True

    def test_dict_status_error_string(self):
        assert ToolResultExtractor.extract_success_status({"status": "error"}) is False

    def test_dict_status_failed_string(self):
        assert ToolResultExtractor.extract_success_status({"status": "failed"}) is False

    def test_dict_status_failure_string(self):
        assert ToolResultExtractor.extract_success_status({"status": "failure"}) is False

    def test_dict_status_ok_string(self):
        assert ToolResultExtractor.extract_success_status({"status": "ok"}) is True

    # -- dict format: status_code key --

    def test_dict_status_code_400(self):
        assert ToolResultExtractor.extract_success_status({"status_code": 400}) is False

    def test_dict_status_code_200(self):
        assert ToolResultExtractor.extract_success_status({"status_code": 200}) is True

    # -- dict with no marker → True --

    def test_dict_no_marker(self):
        assert ToolResultExtractor.extract_success_status({"items": [1, 2]}) is True

    def test_dict_empty(self):
        assert ToolResultExtractor.extract_success_status({}) is True

    # -- JSON string --

    def test_json_string_success_true(self):
        assert ToolResultExtractor.extract_success_status('{"success": true}') is True

    def test_json_string_success_false(self):
        assert ToolResultExtractor.extract_success_status('{"success": false}') is False

    def test_json_string_error_null(self):
        assert ToolResultExtractor.extract_success_status('{"error": null}') is True

    def test_json_string_status_500(self):
        assert ToolResultExtractor.extract_success_status('{"status": 500}') is False

    # -- plain string --

    def test_plain_string_with_error_indicator(self):
        assert ToolResultExtractor.extract_success_status("error: connection refused") is False

    def test_plain_string_with_failed(self):
        assert ToolResultExtractor.extract_success_status("request failed") is False

    def test_plain_string_with_exception(self):
        assert ToolResultExtractor.extract_success_status("an exception occurred") is False

    def test_plain_string_with_traceback(self):
        assert ToolResultExtractor.extract_success_status("traceback (most recent call last)") is False

    def test_plain_string_clean(self):
        assert ToolResultExtractor.extract_success_status("All done successfully") is True

    def test_string_error_null_json_pattern(self):
        assert ToolResultExtractor.extract_success_status('"error": null') is True

    def test_string_error_none_pattern(self):
        assert ToolResultExtractor.extract_success_status("'error': none") is True

    def test_string_status_code_4xx(self):
        assert ToolResultExtractor.extract_success_status("status_code: 401") is False

    def test_string_status_code_5xx(self):
        assert ToolResultExtractor.extract_success_status("status_code: 503") is False


# ---------------------------------------------------------------------------
# extract_data_from_result
# ---------------------------------------------------------------------------

class TestExtractDataFromResult:

    def test_none(self):
        assert ToolResultExtractor.extract_data_from_result(None) is None

    def test_tuple_unwrap(self):
        result = ToolResultExtractor.extract_data_from_result((True, {"key": "val"}))
        assert result == {"key": "val"}

    def test_nested_tuple_unwrap(self):
        result = ToolResultExtractor.extract_data_from_result((True, '{"a": 1}'))
        assert result == {"a": 1}

    def test_json_string(self):
        result = ToolResultExtractor.extract_data_from_result('{"items": [1, 2]}')
        assert result == {"items": [1, 2]}

    def test_invalid_json_string(self):
        result = ToolResultExtractor.extract_data_from_result("plain text result")
        assert result == "plain text result"

    def test_dict_passthrough(self):
        d = {"a": 1}
        assert ToolResultExtractor.extract_data_from_result(d) is d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert ToolResultExtractor.extract_data_from_result(lst) is lst

    def test_tuple_false_still_extracts(self):
        result = ToolResultExtractor.extract_data_from_result((False, "error msg"))
        assert result == "error msg"

    def test_longer_tuple_not_unwrapped(self):
        t = (True, "data", "extra")
        assert ToolResultExtractor.extract_data_from_result(t) is t


# ---------------------------------------------------------------------------
# extract_field_from_data
# ---------------------------------------------------------------------------

class TestExtractFieldFromData:
    """Tests for the complex field-path navigator."""

    # -- basic dict access --

    def test_single_field(self):
        assert ToolResultExtractor.extract_field_from_data({"name": "Alice"}, ["name"]) == "Alice"

    def test_nested_dict(self):
        data = {"user": {"profile": {"email": "a@b.com"}}}
        assert ToolResultExtractor.extract_field_from_data(data, ["user", "profile", "email"]) == "a@b.com"

    def test_missing_field_returns_none(self):
        assert ToolResultExtractor.extract_field_from_data({"a": 1}, ["b"]) is None

    def test_none_data(self):
        assert ToolResultExtractor.extract_field_from_data(None, ["x"]) is None

    def test_empty_path(self):
        data = {"x": 1}
        assert ToolResultExtractor.extract_field_from_data(data, []) == data

    # -- list indexing --

    def test_list_numeric_index(self):
        data = {"items": [{"id": "a"}, {"id": "b"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["items", "1", "id"]) == "b"

    def test_list_index_out_of_bounds(self):
        data = {"items": [{"id": "a"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["items", "5", "id"]) is None

    def test_top_level_list_index(self):
        data = [{"name": "first"}, {"name": "second"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["0", "name"]) == "first"

    def test_top_level_list_field_on_first(self):
        data = [{"name": "first"}, {"name": "second"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["name"]) == "first"

    # -- empty list handling --

    def test_empty_list_with_index(self):
        data = {"items": []}
        assert ToolResultExtractor.extract_field_from_data(data, ["items", "0", "id"]) is None

    def test_empty_list_at_end_of_path(self):
        data = {"items": []}
        assert ToolResultExtractor.extract_field_from_data(data, ["items"]) is None

    # -- list returned at end of path --

    def test_list_returned_when_end_of_path(self):
        data = {"tags": ["a", "b", "c"]}
        assert ToolResultExtractor.extract_field_from_data(data, ["tags"]) == ["a", "b", "c"]

    # -- auto-extract from first item when list mid-path --

    def test_auto_first_item_from_list(self):
        data = {"users": [{"id": 1}, {"id": 2}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["users", "id"]) == 1

    # -- wildcard handling --

    def test_wildcard_question_mark(self):
        data = [{"id": "x"}, {"id": "y"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["?", "id"]) == "x"

    def test_wildcard_star(self):
        data = [{"val": 10}]
        assert ToolResultExtractor.extract_field_from_data(data, ["*", "val"]) == 10

    def test_wildcard_on_empty_list(self):
        assert ToolResultExtractor.extract_field_from_data([], ["?", "id"]) is None

    # -- "data" prefix skip --

    def test_data_prefix_skip(self):
        data = {"name": "test"}
        assert ToolResultExtractor.extract_field_from_data(data, ["data", "name"]) == "test"

    def test_data_prefix_with_numeric_index_finds_list(self):
        data = {"items": [{"id": "a"}, {"id": "b"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["data", "0", "id"]) == "a"

    def test_data_prefix_with_numeric_index_results_key(self):
        data = {"results": [{"id": "r1"}, {"id": "r2"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["data", "0", "id"]) == "r1"

    def test_data_prefix_with_numeric_index_fallback_any_list(self):
        data = {"stuff": [{"id": "s1"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["data", "0", "id"]) == "s1"

    def test_data_prefix_numeric_out_of_bounds(self):
        data = {"items": [{"id": "a"}]}
        result = ToolResultExtractor.extract_field_from_data(data, ["data", "5", "id"])
        assert result is None

    # -- "results" fallback to *_results keys --

    def test_results_fallback_to_prefixed_key(self):
        data = {"web_results": [{"url": "http://example.com"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results", "0", "url"]) == "http://example.com"

    def test_results_no_fallback_returns_none(self):
        data = {"items": [1, 2]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results"]) is None

    def test_results_fallback_with_data_redirect(self):
        data = {"data": [{"id": 1}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results", "0", "id"]) == 1

    # -- content ↔ body alias --

    def test_content_alias_to_body(self):
        data = {"body": "hello"}
        assert ToolResultExtractor.extract_field_from_data(data, ["content"]) == "hello"

    def test_body_alias_to_content(self):
        data = {"content": "world"}
        assert ToolResultExtractor.extract_field_from_data(data, ["body"]) == "world"

    def test_content_alias_in_list_first_item(self):
        data = [{"body": "text"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["content"]) == "text"

    def test_body_alias_in_list_first_item(self):
        data = [{"content": "text"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["body"]) == "text"

    # -- url ↔ link alias --

    def test_url_alias_to_link(self):
        data = {"link": "http://example.com"}
        assert ToolResultExtractor.extract_field_from_data(data, ["url"]) == "http://example.com"

    def test_link_alias_to_url(self):
        data = {"url": "http://example.com"}
        assert ToolResultExtractor.extract_field_from_data(data, ["link"]) == "http://example.com"

    # -- JSON string mid-navigation --

    def test_json_string_field(self):
        data = {"payload": '{"key": "value"}'}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "key"]) == "value"

    def test_json_string_content_body_alias(self):
        data = {"payload": '{"body": "text"}'}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "content"]) == "text"

    def test_json_string_body_content_alias(self):
        data = {"payload": '{"content": "text"}'}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "body"]) == "text"

    def test_json_string_missing_field(self):
        data = {"payload": '{"a": 1}'}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "b"]) is None

    def test_invalid_json_string_returns_none(self):
        data = {"payload": "not json"}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "key"]) is None

    def test_json_string_non_dict_returns_none(self):
        data = {"payload": "[1,2,3]"}
        assert ToolResultExtractor.extract_field_from_data(data, ["payload", "key"]) is None

    # -- Confluence storage format auto-extract --

    def test_confluence_storage_auto_extract(self):
        data = {"body": {"storage": {"value": "<p>Hello</p>"}}}
        assert ToolResultExtractor.extract_field_from_data(data, ["body"]) == "<p>Hello</p>"

    def test_dict_single_value_key_auto_extract(self):
        data = {"field": {"value": "unwrapped"}}
        assert ToolResultExtractor.extract_field_from_data(data, ["field"]) == "unwrapped"

    def test_dict_value_key_with_other_keys_no_unwrap(self):
        data = {"field": {"value": "x", "extra": "y"}}
        result = ToolResultExtractor.extract_field_from_data(data, ["field"])
        assert result == {"value": "x", "extra": "y"}

    # -- "id" → "key" fallback for Confluence spaces --

    def test_id_fallback_to_key(self):
        data = {"space": {"id": None, "key": "DEV"}}
        assert ToolResultExtractor.extract_field_from_data(data, ["space", "id"]) == "DEV"

    def test_id_no_fallback_when_id_present(self):
        data = {"space": {"id": 123, "key": "DEV"}}
        assert ToolResultExtractor.extract_field_from_data(data, ["space", "id"]) == 123

    def test_id_fallback_nested(self):
        data = {"a": {"b": {"id": None, "key": "K1"}}}
        assert ToolResultExtractor.extract_field_from_data(data, ["a", "b", "id"]) == "K1"

    def test_id_fallback_no_key_either(self):
        data = {"space": {"id": None}}
        assert ToolResultExtractor.extract_field_from_data(data, ["space", "id"]) is None

    def test_id_fallback_parent_is_list(self):
        data = [{"id": None, "key": "PROJ"}]
        assert ToolResultExtractor.extract_field_from_data(data, ["0", "id"]) == "PROJ"

    # -- non-dict non-list non-string mid-path --

    def test_int_mid_path_returns_none(self):
        data = {"count": 42}
        assert ToolResultExtractor.extract_field_from_data(data, ["count", "value"]) is None

    # -- complex multi-hop --

    def test_complex_navigation(self):
        data = {
            "response": {
                "results": [
                    {"id": "r1", "attrs": {"score": 0.9}},
                    {"id": "r2", "attrs": {"score": 0.8}},
                ]
            }
        }
        assert ToolResultExtractor.extract_field_from_data(
            data, ["response", "results", "1", "attrs", "score"]
        ) == 0.8

    def test_list_auto_first_then_nested(self):
        data = {"records": [{"meta": {"title": "Doc A"}}]}
        assert ToolResultExtractor.extract_field_from_data(
            data, ["records", "meta", "title"]
        ) == "Doc A"

    # -- string returned directly --

    def test_string_at_end_of_path(self):
        data = {"msg": "hello"}
        assert ToolResultExtractor.extract_field_from_data(data, ["msg"]) == "hello"

    # -- list on list with field access --

    def test_list_non_numeric_non_wildcard_no_dict_returns_none(self):
        data = ["a", "b", "c"]
        assert ToolResultExtractor.extract_field_from_data(data, ["name"]) is None

    # -- negative index --

    def test_negative_index_out_of_bounds(self):
        data = [{"id": 1}]
        assert ToolResultExtractor.extract_field_from_data(data, ["-5"]) is None

    def test_valid_negative_index(self):
        data = [{"id": 1}, {"id": 2}, {"id": 3}]
        assert ToolResultExtractor.extract_field_from_data(data, ["-1"]) is None

    # -- results with data redirect --

    def test_results_redirect_to_data_list(self):
        data = {"data": [{"id": "d1"}, {"id": "d2"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results", "0", "id"]) == "d1"

    def test_results_redirect_to_data_list_out_of_bounds(self):
        data = {"data": [{"id": "d1"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results", "5", "id"]) is None

    def test_results_redirect_to_data_non_numeric_next(self):
        data = {"data": [{"name": "x"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["results", "name"]) is None

    # -- data prefix numeric with no list found --

    def test_data_prefix_numeric_no_list_in_dict(self):
        data = {"count": 5, "label": "test"}
        assert ToolResultExtractor.extract_field_from_data(data, ["data", "0", "id"]) is None

    # -- empty list with non-numeric next field --

    def test_empty_list_with_non_numeric_next(self):
        data = {"items": []}
        assert ToolResultExtractor.extract_field_from_data(data, ["items", "name"]) is None

    # -- list of non-dicts with non-numeric non-wildcard --

    def test_list_of_strings_with_field(self):
        data = ["a", "b", "c"]
        assert ToolResultExtractor.extract_field_from_data(data, ["x"]) is None

    # -- id fallback with list parent ValueError --

    def test_id_fallback_with_list_parent_value_error(self):
        data = {"space": [{"id": None, "key": "K1"}]}
        assert ToolResultExtractor.extract_field_from_data(data, ["space", "abc", "id"]) is None

    # -- Confluence storage with non-dict storage --

    def test_confluence_storage_non_dict(self):
        data = {"body": {"storage": "not a dict"}}
        result = ToolResultExtractor.extract_field_from_data(data, ["body"])
        assert result == {"storage": "not a dict"}

    # -- data prefix skip at end of path --

    def test_data_prefix_skip_at_end_of_path(self):
        data = {"name": "test"}
        result = ToolResultExtractor.extract_field_from_data(data, ["data"])
        assert result == {"name": "test"}
