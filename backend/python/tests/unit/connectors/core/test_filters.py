"""Tests for app.connectors.core.registry.filters."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.registry.filters import (
    BooleanOperator,
    DatetimeOperator,
    Filter,
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    FilterValue,
    IndexingFilterKey,
    ListOperator,
    MultiselectOperator,
    NumberOperator,
    OptionSourceType,
    StringOperator,
    SyncFilterKey,
    TYPE_OPERATORS,
    get_operator_enum_class,
    get_operators_for_type,
    load_connector_filters,
)


# ===========================================================================
# FilterType, FilterCategory, OptionSourceType enums
# ===========================================================================


class TestFilterEnums:
    """Basic enum value tests."""

    def test_filter_type_values(self):
        assert FilterType.STRING.value == "string"
        assert FilterType.BOOLEAN.value == "boolean"
        assert FilterType.DATETIME.value == "datetime"
        assert FilterType.LIST.value == "list"
        assert FilterType.NUMBER.value == "number"
        assert FilterType.MULTISELECT.value == "multiselect"

    def test_filter_category_values(self):
        assert FilterCategory.SYNC.value == "sync"
        assert FilterCategory.INDEXING.value == "indexing"

    def test_option_source_type_values(self):
        assert OptionSourceType.MANUAL.value == "manual"
        assert OptionSourceType.STATIC.value == "static"
        assert OptionSourceType.DYNAMIC.value == "dynamic"


# ===========================================================================
# FilterOperator
# ===========================================================================


class TestFilterOperator:
    """Tests for FilterOperator constants."""

    def test_string_operators(self):
        assert FilterOperator.IS == "is"
        assert FilterOperator.IS_NOT == "is_not"
        assert FilterOperator.CONTAINS == "contains"
        assert FilterOperator.DOES_NOT_CONTAIN == "does_not_contain"
        assert FilterOperator.IS_EMPTY == "is_empty"
        assert FilterOperator.IS_NOT_EMPTY == "is_not_empty"

    def test_datetime_operators(self):
        assert FilterOperator.LAST_7_DAYS == "last_7_days"
        assert FilterOperator.IS_AFTER == "is_after"
        assert FilterOperator.IS_BEFORE == "is_before"
        assert FilterOperator.IS_BETWEEN == "is_between"

    def test_list_operators(self):
        assert FilterOperator.IN == "in"
        assert FilterOperator.NOT_IN == "not_in"

    def test_number_operators(self):
        assert FilterOperator.GREATER_THAN == "greater_than"
        assert FilterOperator.LESS_THAN == "less_than"
        assert FilterOperator.EQUAL == "equal"


# ===========================================================================
# Operator Enum Classes
# ===========================================================================


class TestOperatorEnumClasses:
    """Tests for type-specific operator enums."""

    def test_string_operator_values_match(self):
        assert StringOperator.IS.value == FilterOperator.IS
        assert StringOperator.IS_NOT.value == FilterOperator.IS_NOT

    def test_boolean_operator_values_match(self):
        assert BooleanOperator.IS.value == FilterOperator.IS
        assert BooleanOperator.IS_NOT.value == FilterOperator.IS_NOT

    def test_datetime_operator_values_match(self):
        assert DatetimeOperator.IS_AFTER.value == FilterOperator.IS_AFTER
        assert DatetimeOperator.IS_BETWEEN.value == FilterOperator.IS_BETWEEN

    def test_list_operator_values_match(self):
        assert ListOperator.IN.value == FilterOperator.IN
        assert ListOperator.NOT_IN.value == FilterOperator.NOT_IN

    def test_multiselect_operator_values_match(self):
        assert MultiselectOperator.IN.value == FilterOperator.IN
        assert MultiselectOperator.NOT_IN.value == FilterOperator.NOT_IN

    def test_number_operator_values_match(self):
        assert NumberOperator.EQUAL.value == FilterOperator.EQUAL
        assert NumberOperator.GREATER_THAN.value == FilterOperator.GREATER_THAN


# ===========================================================================
# TYPE_OPERATORS mapping
# ===========================================================================


class TestTypeOperators:
    """Tests for the TYPE_OPERATORS mapping."""

    def test_all_filter_types_present(self):
        for ft in FilterType:
            assert ft in TYPE_OPERATORS

    def test_string_operators_correct(self):
        ops = TYPE_OPERATORS[FilterType.STRING]
        assert "is" in ops
        assert "is_not" in ops
        assert "contains" in ops

    def test_boolean_operators_correct(self):
        ops = TYPE_OPERATORS[FilterType.BOOLEAN]
        assert "is" in ops
        assert "is_not" in ops
        assert len(ops) == 2

    def test_datetime_operators_correct(self):
        ops = TYPE_OPERATORS[FilterType.DATETIME]
        assert "is_after" in ops
        assert "is_before" in ops
        assert "is_between" in ops

    def test_list_operators_correct(self):
        ops = TYPE_OPERATORS[FilterType.LIST]
        assert "in" in ops
        assert "not_in" in ops

    def test_number_operators_correct(self):
        ops = TYPE_OPERATORS[FilterType.NUMBER]
        assert "equal" in ops
        assert "greater_than" in ops
        assert "less_than" in ops


# ===========================================================================
# get_operators_for_type & get_operator_enum_class
# ===========================================================================


class TestHelperFunctions:
    """Tests for get_operators_for_type and get_operator_enum_class."""

    def test_get_operators_for_type(self):
        ops = get_operators_for_type(FilterType.STRING)
        assert isinstance(ops, list)
        assert "is" in ops

    def test_get_operators_for_unknown_type(self):
        result = get_operators_for_type("unknown")
        assert result == []

    def test_get_operator_enum_class_string(self):
        assert get_operator_enum_class(FilterType.STRING) is StringOperator

    def test_get_operator_enum_class_boolean(self):
        assert get_operator_enum_class(FilterType.BOOLEAN) is BooleanOperator

    def test_get_operator_enum_class_datetime(self):
        assert get_operator_enum_class(FilterType.DATETIME) is DatetimeOperator

    def test_get_operator_enum_class_list(self):
        assert get_operator_enum_class(FilterType.LIST) is ListOperator

    def test_get_operator_enum_class_multiselect(self):
        assert get_operator_enum_class(FilterType.MULTISELECT) is MultiselectOperator

    def test_get_operator_enum_class_number(self):
        assert get_operator_enum_class(FilterType.NUMBER) is NumberOperator

    def test_get_operator_enum_class_unknown(self):
        result = get_operator_enum_class("bogus")
        assert result is None


# ===========================================================================
# FilterOption & FilterOptionsResponse
# ===========================================================================


class TestFilterOption:
    """Tests for FilterOption dataclass."""

    def test_to_dict(self):
        opt = FilterOption(id="sp-1", label="Engineering")
        d = opt.to_dict()
        assert d == {"id": "sp-1", "label": "Engineering"}


class TestFilterOptionsResponse:
    """Tests for FilterOptionsResponse dataclass."""

    def test_to_dict_basic(self):
        resp = FilterOptionsResponse(
            success=True,
            options=[FilterOption(id="1", label="One")],
            page=1,
            limit=10,
            has_more=False,
        )
        d = resp.to_dict()
        assert d["success"] is True
        assert d["page"] == 1
        assert d["hasMore"] is False
        assert len(d["options"]) == 1
        assert d["options"][0]["id"] == "1"

    def test_to_dict_with_cursor_and_message(self):
        resp = FilterOptionsResponse(
            success=False,
            options=[],
            page=2,
            limit=5,
            has_more=True,
            cursor="abc123",
            message="Rate limited",
        )
        d = resp.to_dict()
        assert d["cursor"] == "abc123"
        assert d["message"] == "Rate limited"

    def test_to_dict_without_optional_fields(self):
        resp = FilterOptionsResponse(
            success=True, options=[], page=1, limit=10, has_more=False
        )
        d = resp.to_dict()
        assert "cursor" not in d
        assert "message" not in d


# ===========================================================================
# FilterField
# ===========================================================================


class TestFilterField:
    """Tests for the FilterField dataclass."""

    def test_basic_creation(self):
        ff = FilterField(
            name="space_keys",
            display_name="Spaces",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
        )
        assert ff.name == "space_keys"
        assert ff.filter_type == FilterType.LIST

    def test_auto_detect_static_option_source(self):
        """If options are provided with MANUAL source, auto-detects as STATIC."""
        ff = FilterField(
            name="status",
            display_name="Status",
            filter_type=FilterType.MULTISELECT,
            options=["open", "closed"],
        )
        assert ff.option_source_type == OptionSourceType.STATIC

    def test_dynamic_source_requires_list_or_multiselect(self):
        """DYNAMIC source type with non-LIST/MULTISELECT raises ValueError."""
        with pytest.raises(ValueError, match="DYNAMIC only supported"):
            FilterField(
                name="x",
                display_name="X",
                filter_type=FilterType.STRING,
                option_source_type=OptionSourceType.DYNAMIC,
            )

    def test_dynamic_source_valid_with_multiselect(self):
        """DYNAMIC source with MULTISELECT does not raise."""
        ff = FilterField(
            name="x",
            display_name="X",
            filter_type=FilterType.MULTISELECT,
            option_source_type=OptionSourceType.DYNAMIC,
        )
        assert ff.option_source_type == OptionSourceType.DYNAMIC

    def test_dynamic_source_valid_with_list(self):
        """DYNAMIC source with LIST does not raise."""
        ff = FilterField(
            name="x",
            display_name="X",
            filter_type=FilterType.LIST,
            option_source_type=OptionSourceType.DYNAMIC,
        )
        assert ff.option_source_type == OptionSourceType.DYNAMIC

    def test_static_source_requires_options(self):
        """STATIC source without options raises ValueError."""
        with pytest.raises(ValueError, match="requires options list"):
            FilterField(
                name="x",
                display_name="X",
                filter_type=FilterType.MULTISELECT,
                option_source_type=OptionSourceType.STATIC,
                options=[],
            )

    def test_operators_property(self):
        """operators returns correct operators for the type."""
        ff = FilterField(
            name="x", display_name="X", filter_type=FilterType.BOOLEAN
        )
        assert "is" in ff.operators
        assert "is_not" in ff.operators

    def test_to_schema_dict(self):
        """to_schema_dict returns complete schema."""
        ff = FilterField(
            name="modified",
            display_name="Modified Date",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            description="Date filter",
            required=True,
        )
        schema = ff.to_schema_dict()
        assert schema["name"] == "modified"
        assert schema["displayName"] == "Modified Date"
        assert schema["filterType"] == "datetime"
        assert schema["category"] == "sync"
        assert schema["required"] is True
        assert "operators" in schema
        assert "is_after" in schema["operators"]
        assert "noImplicitOperatorDefault" not in schema

    def test_to_schema_dict_no_implicit_operator_default(self):
        ff = FilterField(
            name="modified",
            display_name="Modified Date",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            no_implicit_operator_default=True,
        )
        schema = ff.to_schema_dict()
        assert schema.get("noImplicitOperatorDefault") is True
        """Schema includes options when present."""
        ff = FilterField(
            name="status",
            display_name="Status",
            filter_type=FilterType.MULTISELECT,
            options=["open", "closed"],
        )
        schema = ff.to_schema_dict()
        assert schema["options"] == ["open", "closed"]

    def test_get_default_for_type(self):
        """_get_default_for_type returns correct defaults."""
        ff_str = FilterField(name="x", display_name="X", filter_type=FilterType.STRING)
        assert ff_str._get_default_for_type() == ""

        ff_bool = FilterField(name="x", display_name="X", filter_type=FilterType.BOOLEAN)
        assert ff_bool._get_default_for_type() is True

        ff_list = FilterField(name="x", display_name="X", filter_type=FilterType.LIST)
        assert ff_list._get_default_for_type() == []

        ff_dt = FilterField(name="x", display_name="X", filter_type=FilterType.DATETIME)
        assert ff_dt._get_default_for_type() is None

        ff_num = FilterField(name="x", display_name="X", filter_type=FilterType.NUMBER)
        assert ff_num._get_default_for_type() is None

    def test_get_default_operator(self):
        """_get_default_operator returns correct defaults."""
        ff = FilterField(name="x", display_name="X", filter_type=FilterType.STRING)
        assert ff._get_default_operator() == "is"

        ff = FilterField(name="x", display_name="X", filter_type=FilterType.BOOLEAN)
        assert ff._get_default_operator() == "is"

        ff = FilterField(name="x", display_name="X", filter_type=FilterType.DATETIME)
        assert ff._get_default_operator() == "is_after"

        ff = FilterField(name="x", display_name="X", filter_type=FilterType.LIST)
        assert ff._get_default_operator() == "in"

        ff = FilterField(name="x", display_name="X", filter_type=FilterType.NUMBER)
        assert ff._get_default_operator() == "equal"


# ===========================================================================
# SyncFilterKey & IndexingFilterKey enums
# ===========================================================================


class TestFilterKeys:
    """Tests for SyncFilterKey and IndexingFilterKey."""

    def test_sync_filter_key_values(self):
        assert SyncFilterKey.MODIFIED.value == "modified"
        assert SyncFilterKey.SPACE_KEYS.value == "space_keys"
        assert SyncFilterKey.FOLDER_IDS.value == "folder_ids"

    def test_indexing_filter_key_values(self):
        assert IndexingFilterKey.PAGES.value == "pages"
        assert IndexingFilterKey.FILES.value == "files"
        assert IndexingFilterKey.EMAILS.value == "emails"
        assert IndexingFilterKey.ENABLE_MANUAL_SYNC.value == "enable_manual_sync"
        assert IndexingFilterKey.TABLES.value == "index_stage_tables"
        assert IndexingFilterKey.VIEWS.value == "index_stage_views"
        assert IndexingFilterKey.STAGE_FILES.value == "index_stage_files"
        assert IndexingFilterKey.MAX_ROWS_PER_TABLE.value == "max_rows_per_table"


# ===========================================================================
# Filter (Pydantic model)
# ===========================================================================


class TestFilter:
    """Tests for the Filter Pydantic model."""

    def test_create_string_filter(self):
        f = Filter(key="name", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.key == "name"
        assert f.value == "test"

    def test_create_boolean_filter(self):
        f = Filter(key="active", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS)
        assert f.value is True

    def test_create_list_filter(self):
        f = Filter(
            key="spaces",
            value=["DOCS", "ENG"],
            type=FilterType.LIST,
            operator=ListOperator.IN,
        )
        assert f.value == ["DOCS", "ENG"]

    def test_create_datetime_filter(self):
        """Datetime filters are created via dict value (start/end) which is converted to tuple."""
        f = Filter.model_validate({
            "key": "modified",
            "value": {"start": 1000, "end": 2000},
            "type": "datetime",
            "operator": "is_between",
        })
        assert f.value == (1000, 2000)

    def test_create_number_filter(self):
        f = Filter(
            key="size",
            value=100,
            type=FilterType.NUMBER,
            operator=NumberOperator.GREATER_THAN,
        )
        assert f.value == 100

    def test_empty_key_raises(self):
        """Empty key raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            Filter(key="", value="x", type=FilterType.STRING, operator=StringOperator.IS)

    def test_whitespace_key_raises(self):
        """Whitespace-only key raises ValueError."""
        with pytest.raises(ValueError, match="cannot be empty"):
            Filter(key="  ", value="x", type=FilterType.STRING, operator=StringOperator.IS)

    def test_invalid_operator_for_type_raises(self):
        """Using a datetime operator for a string filter raises."""
        with pytest.raises(ValueError):
            Filter(
                key="name",
                value="test",
                type=FilterType.STRING,
                operator=DatetimeOperator.IS_AFTER,
            )

    def test_invalid_value_type_raises(self):
        """Wrong value type raises ValueError."""
        with pytest.raises(ValueError):
            Filter(
                key="name",
                value=123,  # should be str
                type=FilterType.STRING,
                operator=StringOperator.IS,
            )

    def test_datetime_non_dict_value_becomes_none(self):
        """Datetime filter with non-dict value is converted to None by the model validator."""
        f = Filter.model_validate({
            "key": "date",
            "value": "not-a-dict",
            "type": "datetime",
            "operator": "is_after",
        })
        assert f.value is None

    def test_datetime_dict_wrong_fields_yields_none_elements(self):
        """Datetime dict missing start/end yields (None, None) tuple."""
        f = Filter.model_validate({
            "key": "date",
            "value": {},
            "type": "datetime",
            "operator": "is_after",
        })
        assert f.value == (None, None)

    def test_datetime_non_int_element_raises(self):
        """Datetime dict with non-int element raises during int() conversion."""
        from pydantic import ValidationError as PydanticValidationError
        with pytest.raises((ValueError, PydanticValidationError)):
            Filter.model_validate({
                "key": "date",
                "value": {"start": 1000, "end": "bad"},
                "type": "datetime",
                "operator": "is_after",
            })

    def test_datetime_none_elements_allowed(self):
        """Datetime dict with None elements is valid."""
        f = Filter.model_validate({
            "key": "date",
            "value": {"start": 1000, "end": None},
            "type": "datetime",
            "operator": "is_after",
        })
        assert f.value == (1000, None)

    def test_list_with_non_string_elements_converted(self):
        """List filter with non-string items converts them via str() in model_validator."""
        f = Filter(
            key="tags",
            value=["ok", 123],
            type=FilterType.LIST,
            operator=ListOperator.IN,
        )
        # The model_validator converts non-string items to strings
        assert f.value == ["ok", "123"]

    def test_none_value_is_valid(self):
        """None value passes validation."""
        f = Filter(key="x", value=None, type=FilterType.STRING, operator=StringOperator.IS)
        assert f.value is None

    # --- Operator conversion from string (model_validator) ---

    def test_string_operator_from_dict(self):
        """Operator string is converted to enum from dict."""
        f = Filter.model_validate({
            "key": "name", "value": "test", "type": "string", "operator": "is"
        })
        assert isinstance(f.operator, StringOperator)
        assert f.operator == StringOperator.IS

    def test_legacy_equals_operator_migrated(self):
        """Legacy 'equals' operator is migrated based on type."""
        f = Filter.model_validate({
            "key": "active", "value": True, "type": "boolean", "operator": "equals"
        })
        assert f.operator == BooleanOperator.IS

    def test_datetime_value_from_dict(self):
        """Datetime value from {start, end} dict is converted to tuple."""
        f = Filter.model_validate({
            "key": "date", "value": {"start": 1000, "end": 2000},
            "type": "datetime", "operator": "is_between"
        })
        assert f.value == (1000, 2000)

    def test_datetime_value_null_start(self):
        """Datetime with null start."""
        f = Filter.model_validate({
            "key": "date", "value": {"start": None, "end": 2000},
            "type": "datetime", "operator": "is_before"
        })
        assert f.value == (None, 2000)

    def test_list_value_extracts_ids_from_objects(self):
        """List value with {id, label} objects extracts IDs."""
        f = Filter.model_validate({
            "key": "spaces",
            "value": [{"id": "sp1", "label": "Space 1"}, {"id": "sp2", "label": "Space 2"}],
            "type": "list",
            "operator": "in",
        })
        assert f.value == ["sp1", "sp2"]

    def test_list_value_backward_compat_strings(self):
        """List value with plain strings is preserved."""
        f = Filter.model_validate({
            "key": "spaces", "value": ["sp1", "sp2"], "type": "list", "operator": "in"
        })
        assert f.value == ["sp1", "sp2"]

    # --- Filter methods ---

    def test_is_empty_none(self):
        f = Filter(key="x", value=None, type=FilterType.STRING, operator=StringOperator.IS)
        assert f.is_empty() is True

    def test_is_empty_empty_string(self):
        f = Filter(key="x", value="", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.is_empty() is True

    def test_is_empty_empty_list(self):
        f = Filter(key="x", value=[], type=FilterType.LIST, operator=ListOperator.IN)
        assert f.is_empty() is True

    def test_is_empty_non_empty(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.is_empty() is False

    def test_as_list_from_list(self):
        f = Filter(key="x", value=["a", "b"], type=FilterType.LIST, operator=ListOperator.IN)
        assert f.as_list() == ["a", "b"]

    def test_as_list_from_single(self):
        f = Filter(key="x", value="a", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.as_list() == ["a"]

    def test_as_list_from_none(self):
        f = Filter(key="x", value=None, type=FilterType.STRING, operator=StringOperator.IS)
        assert f.as_list() == []

    def test_get_value_returns_value(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.get_value() == "test"

    def test_get_value_returns_default_when_empty(self):
        f = Filter(key="x", value=None, type=FilterType.STRING, operator=StringOperator.IS)
        assert f.get_value(default="fallback") == "fallback"

    def test_get_operator(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.get_operator() == StringOperator.IS

    def test_operator_value_property(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.CONTAINS)
        assert f.operator_value == "contains"

    def test_get_datetime_start(self):
        f = Filter.model_validate({
            "key": "d", "value": {"start": 1000, "end": 2000},
            "type": "datetime", "operator": "is_between"
        })
        assert f.get_datetime_start() == 1000

    def test_get_datetime_end(self):
        f = Filter.model_validate({
            "key": "d", "value": {"start": 1000, "end": 2000},
            "type": "datetime", "operator": "is_between"
        })
        assert f.get_datetime_end() == 2000

    def test_get_datetime_start_non_datetime(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.get_datetime_start() is None

    def test_get_datetime_end_non_datetime(self):
        f = Filter(key="x", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        assert f.get_datetime_end() is None

    def test_get_datetime_iso(self):
        # 1704067200000ms = 2024-01-01T00:00:00 UTC
        f = Filter.model_validate({
            "key": "d",
            "value": {"start": 1704067200000, "end": 1704153600000},
            "type": "datetime",
            "operator": "is_between",
        })
        start_iso, end_iso = f.get_datetime_iso()
        assert start_iso == "2024-01-01T00:00:00"
        assert end_iso == "2024-01-02T00:00:00"

    def test_get_datetime_iso_with_none_values(self):
        f = Filter.model_validate({
            "key": "d",
            "value": {"start": None, "end": None},
            "type": "datetime",
            "operator": "is_between",
        })
        start_iso, end_iso = f.get_datetime_iso()
        assert start_iso is None
        assert end_iso is None

    def test_epoch_to_iso_static(self):
        result = Filter._epoch_to_iso(1704067200000)
        assert result == "2024-01-01T00:00:00"

    def test_epoch_to_iso_none(self):
        assert Filter._epoch_to_iso(None) is None

    def test_get_datetime_iso_empty_value(self):
        f = Filter(key="d", value=None, type=FilterType.DATETIME, operator=DatetimeOperator.IS_AFTER)
        assert f.get_datetime_iso() == (None, None)

    def test_number_float_value(self):
        """Number filter accepts float values."""
        f = Filter(
            key="size",
            value=3.14,
            type=FilterType.NUMBER,
            operator=NumberOperator.GREATER_THAN,
        )
        assert f.value == 3.14

    def test_multiselect_filter(self):
        """Multiselect filter with IN operator."""
        f = Filter(
            key="channels",
            value=["ch-1", "ch-2"],
            type=FilterType.MULTISELECT,
            operator=MultiselectOperator.IN,
        )
        assert f.value == ["ch-1", "ch-2"]


# ===========================================================================
# FilterCollection
# ===========================================================================


class TestFilterCollection:
    """Tests for FilterCollection."""

    def test_empty_collection(self):
        fc = FilterCollection()
        assert len(fc) == 0
        assert bool(fc) is False

    def test_collection_with_filters(self):
        fc = FilterCollection(filters=[
            Filter(key="name", value="test", type=FilterType.STRING, operator=StringOperator.IS),
        ])
        assert len(fc) == 1
        assert bool(fc) is True

    def test_get_existing_filter(self):
        f = Filter(key="name", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        fc = FilterCollection(filters=[f])
        assert fc.get("name") is f

    def test_get_missing_filter(self):
        fc = FilterCollection()
        assert fc.get("missing") is None

    def test_get_with_enum_key(self):
        f = Filter(key="pages", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS)
        fc = FilterCollection(filters=[f])
        assert fc.get(IndexingFilterKey.PAGES) is f

    def test_get_value_existing(self):
        f = Filter(key="name", value="test", type=FilterType.STRING, operator=StringOperator.IS)
        fc = FilterCollection(filters=[f])
        assert fc.get_value("name") == "test"

    def test_get_value_missing(self):
        fc = FilterCollection()
        assert fc.get_value("missing") is None

    def test_get_value_default(self):
        fc = FilterCollection()
        assert fc.get_value("missing", default="fallback") == "fallback"

    def test_get_value_empty_returns_default(self):
        f = Filter(key="name", value=None, type=FilterType.STRING, operator=StringOperator.IS)
        fc = FilterCollection(filters=[f])
        assert fc.get_value("name", default="default") == "default"

    def test_keys(self):
        fc = FilterCollection(filters=[
            Filter(key="a", value="x", type=FilterType.STRING, operator=StringOperator.IS),
            Filter(key="b", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        assert fc.keys() == ["a", "b"]

    # --- is_enabled ---

    def test_is_enabled_true_for_boolean_true(self):
        fc = FilterCollection(filters=[
            Filter(key="pages", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        assert fc.is_enabled("pages") is True

    def test_is_enabled_false_for_boolean_false(self):
        fc = FilterCollection(filters=[
            Filter(key="pages", value=False, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        assert fc.is_enabled("pages") is False

    def test_is_enabled_default_for_missing(self):
        fc = FilterCollection()
        assert fc.is_enabled("missing") is True
        assert fc.is_enabled("missing", default=False) is False

    def test_is_enabled_non_boolean_non_empty_is_true(self):
        """Non-boolean, non-empty filter returns True."""
        fc = FilterCollection(filters=[
            Filter(key="spaces", value=["sp1"], type=FilterType.LIST, operator=ListOperator.IN),
        ])
        assert fc.is_enabled("spaces") is True

    def test_is_enabled_empty_returns_default(self):
        fc = FilterCollection(filters=[
            Filter(key="spaces", value=[], type=FilterType.LIST, operator=ListOperator.IN),
        ])
        assert fc.is_enabled("spaces") is True  # default=True

    def test_is_enabled_manual_sync_overrides(self):
        """When enable_manual_sync is True, other filters return False."""
        fc = FilterCollection(filters=[
            Filter(
                key="enable_manual_sync",
                value=True,
                type=FilterType.BOOLEAN,
                operator=BooleanOperator.IS,
            ),
            Filter(key="pages", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        # enable_manual_sync itself returns True
        assert fc.is_enabled("enable_manual_sync") is True
        # Other filters return False because manual sync is on
        assert fc.is_enabled("pages") is False

    def test_is_enabled_manual_sync_false_no_override(self):
        """When enable_manual_sync is False, other filters are not overridden."""
        fc = FilterCollection(filters=[
            Filter(
                key="enable_manual_sync",
                value=False,
                type=FilterType.BOOLEAN,
                operator=BooleanOperator.IS,
            ),
            Filter(key="pages", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        assert fc.is_enabled("pages") is True

    def test_is_enabled_manual_sync_none_no_override(self):
        """When enable_manual_sync is None/empty, no override."""
        fc = FilterCollection(filters=[
            Filter(
                key="enable_manual_sync",
                value=None,
                type=FilterType.BOOLEAN,
                operator=BooleanOperator.IS,
            ),
            Filter(key="pages", value=True, type=FilterType.BOOLEAN, operator=BooleanOperator.IS),
        ])
        assert fc.is_enabled("pages") is True

    def test_is_enabled_enable_manual_sync_itself_returns_default_when_missing(self):
        """enable_manual_sync key returns default when not in collection."""
        fc = FilterCollection()
        assert fc.is_enabled("enable_manual_sync") is True
        assert fc.is_enabled("enable_manual_sync", default=False) is False

    # --- from_dict ---

    def test_from_dict_valid(self):
        fc = FilterCollection.from_dict({
            "name": {"value": "test", "type": "string", "operator": "is"},
            "active": {"value": True, "type": "boolean", "operator": "is"},
        })
        assert len(fc) == 2

    def test_from_dict_empty(self):
        fc = FilterCollection.from_dict({})
        assert len(fc) == 0

    def test_from_dict_none(self):
        fc = FilterCollection.from_dict(None)
        assert len(fc) == 0

    def test_from_dict_skips_invalid_type(self):
        """Non-dict filter value is skipped."""
        logger = MagicMock()
        fc = FilterCollection.from_dict({"bad": "not-a-dict"}, logger)
        assert len(fc) == 0
        logger.warning.assert_called()

    def test_from_dict_skips_missing_required_fields(self):
        """Missing operator or type is skipped."""
        logger = MagicMock()
        fc = FilterCollection.from_dict({"bad": {"value": "test"}}, logger)
        assert len(fc) == 0
        logger.warning.assert_called()

    def test_from_dict_skips_invalid_filter(self):
        """Invalid filter data is skipped with warning."""
        logger = MagicMock()
        fc = FilterCollection.from_dict({
            "bad": {"value": 123, "type": "string", "operator": "is"},  # wrong value type
        }, logger)
        assert len(fc) == 0
        logger.warning.assert_called()

    def test_from_dict_mixed_valid_and_invalid(self):
        """Valid filters are kept, invalid ones are skipped."""
        fc = FilterCollection.from_dict({
            "good": {"value": "test", "type": "string", "operator": "is"},
            "bad": {"value": 123, "type": "string", "operator": "is"},
        })
        assert len(fc) == 1
        assert fc.get("good") is not None


# ===========================================================================
# load_connector_filters
# ===========================================================================


class TestLoadConnectorFilters:
    """Tests for the load_connector_filters async function."""

    @pytest.mark.asyncio
    async def test_loads_sync_and_indexing_filters(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "enabled": True,
            "filters": {
                "sync": {
                    "values": {
                        "space_keys": {"value": ["DOCS"], "type": "list", "operator": "in"},
                    }
                },
                "indexing": {
                    "values": {
                        "pages": {"value": True, "type": "boolean", "operator": "is"},
                    }
                },
            },
        }

        sync_filters, indexing_filters = await load_connector_filters(
            config_service, "confluence", "conf-1"
        )

        assert len(sync_filters) == 1
        assert len(indexing_filters) == 1
        assert sync_filters.get_value("space_keys") == ["DOCS"]
        assert indexing_filters.get_value("pages") is True

    @pytest.mark.asyncio
    async def test_returns_empty_on_config_error(self):
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("connection refused")

        sync_filters, indexing_filters = await load_connector_filters(
            config_service, "slack", "sl-1"
        )

        assert len(sync_filters) == 0
        assert len(indexing_filters) == 0

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_config(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = None

        sync_filters, indexing_filters = await load_connector_filters(
            config_service, "slack", "sl-2"
        )

        assert len(sync_filters) == 0
        assert len(indexing_filters) == 0

    @pytest.mark.asyncio
    async def test_returns_empty_when_disabled(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = {"enabled": False}

        sync_filters, indexing_filters = await load_connector_filters(
            config_service, "slack", "sl-3"
        )

        assert len(sync_filters) == 0
        assert len(indexing_filters) == 0

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_filters_section(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = {"enabled": True}

        sync_filters, indexing_filters = await load_connector_filters(
            config_service, "slack", "sl-4"
        )

        assert len(sync_filters) == 0
        assert len(indexing_filters) == 0

    @pytest.mark.asyncio
    async def test_correct_config_path(self):
        config_service = AsyncMock()
        config_service.get_config.return_value = None

        await load_connector_filters(config_service, "confluence", "conf-42")

        config_service.get_config.assert_awaited_once_with(
            "/services/connectors/conf-42/config"
        )

    @pytest.mark.asyncio
    async def test_uses_provided_logger(self):
        logger = MagicMock()
        config_service = AsyncMock()
        config_service.get_config.return_value = None

        await load_connector_filters(config_service, "test", "t-1", logger=logger)

        logger.debug.assert_called()
