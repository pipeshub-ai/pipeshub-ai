"""Unit tests for app.modules.parsers.json.structured_data_utils."""

from app.modules.parsers.json.structured_data_utils import (
    classify_list,
    classify_value,
    flatten_leaf_dict,
    flatten_to_text,
    is_flat_dict,
    object_to_sentence,
    stringify_scalar_array,
)


class TestClassifyList:

    def test_all_dicts_is_object_array(self):
        assert classify_list([{"a": 1}, {"b": 2}]) == "object_array"

    def test_all_scalars_is_scalar_array(self):
        assert classify_list([1, 2, 3]) == "scalar_array"

    def test_majority_dicts_still_object_array(self):
        assert classify_list([{"a": 1}, {"b": 2}, None]) == "object_array"

    def test_minority_dicts_is_mixed(self):
        assert classify_list([{"a": 1}, "x", "y", "z"]) == "mixed_array"

    def test_nested_list_scalars_not_scalar_array(self):
        # A list containing a nested list is not a "scalar" array since a
        # nested list is neither a dict nor a plain scalar.
        assert classify_list([[1, 2], [3, 4]]) == "mixed_array"


class TestIsFlatDict:

    def test_all_scalars(self):
        assert is_flat_dict({"a": 1, "b": "x"}) is True

    def test_scalar_array_value_is_flat(self):
        assert is_flat_dict({"a": [1, 2, 3]}) is True

    def test_nested_dict_value_not_flat(self):
        assert is_flat_dict({"a": {"b": 1}}) is False

    def test_empty_nested_dict_is_flat(self):
        assert is_flat_dict({"a": {}}) is True

    def test_object_array_value_not_flat(self):
        assert is_flat_dict({"a": [{"b": 1}]}) is False

    def test_scalar_array_of_dicts_empty_is_flat(self):
        assert is_flat_dict({"a": []}) is True


class TestClassifyValue:

    def test_scalar_types(self):
        for value in ["text", 1, 1.5, True, None]:
            assert classify_value(value) == "scalar"

    def test_empty_dict(self):
        assert classify_value({}) == "empty"

    def test_empty_list(self):
        assert classify_value([]) == "empty"

    def test_flat_object(self):
        assert classify_value({"a": 1, "b": "x"}) == "flat_object"

    def test_nested_object(self):
        assert classify_value({"a": {"b": 1}}) == "nested_object"

    def test_object_array(self):
        assert classify_value([{"a": 1}, {"b": 2}]) == "object_array"

    def test_scalar_array(self):
        assert classify_value([1, 2, 3]) == "scalar_array"

    def test_mixed_array(self):
        assert classify_value([{"a": 1}, "x", "y", "z"]) == "mixed_array"


class TestFlattenLeafDict:

    def test_flat_dict_unchanged_keys(self):
        result = flatten_leaf_dict({"a": 1, "b": "x"})
        assert result == {"a": 1, "b": "x"}

    def test_nested_dict_dot_path(self):
        result = flatten_leaf_dict({"a": {"b": 1, "c": 2}})
        assert result == {"a.b": 1, "a.c": 2}

    def test_deeply_nested_dict(self):
        result = flatten_leaf_dict({"a": {"b": {"c": 1}}})
        assert result == {"a.b.c": 1}

    def test_empty_nested_dict(self):
        result = flatten_leaf_dict({"a": {}})
        assert result == {"a": "{}"}

    def test_list_value_stringified(self):
        result = flatten_leaf_dict({"a": [1, 2, 3]})
        assert result == {"a": "1, 2, 3"}

    def test_prefix_applied(self):
        result = flatten_leaf_dict({"b": 1}, prefix="a")
        assert result == {"a.b": 1}


class TestStringifyScalarArray:

    def test_empty_list(self):
        assert stringify_scalar_array([]) == "[]"

    def test_scalar_items(self):
        assert stringify_scalar_array(["a", "b", "c"]) == "a, b, c"

    def test_numeric_items(self):
        assert stringify_scalar_array([1, 2, 3]) == "1, 2, 3"

    def test_dict_items_json_encoded(self):
        result = stringify_scalar_array([{"x": 1}])
        assert result == '{"x": 1}'


class TestFlattenToText:

    def test_basic(self):
        text = flatten_to_text({"a": 1, "b": "x"})
        assert text == "a: 1, b: x"

    def test_none_value_rendered_as_null(self):
        text = flatten_to_text({"a": None})
        assert text == "a: null"


class TestObjectToSentence:

    def test_flat_dict(self):
        assert object_to_sentence({"name": "Alice", "age": 30}) == "name: Alice, age: 30"

    def test_nested_dict_flattened(self):
        result = object_to_sentence({"name": "Alice", "address": {"city": "NYC"}})
        assert result == "name: Alice, address.city: NYC"

    def test_non_dict_returns_str(self):
        assert object_to_sentence("plain-value") == "plain-value"
