"""Unit tests for app.modules.agents.context.tool_descriptions."""

import logging
from types import SimpleNamespace
from typing import Optional, Union
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.context.tool_descriptions import (
    _extract_parameters_from_schema,
    _format_tool_descriptions,
    _get_cached_tool_descriptions,
    _get_field_type_name,
    _get_field_type_name_v1,
    _tool_description_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger() -> logging.Logger:
    return logging.getLogger("test_tool_descriptions")


class _FakeFieldV2:
    """Mimics a Pydantic v2 FieldInfo."""
    def __init__(self, annotation=None, description=None, default=None, is_required_val=None):
        self.annotation = annotation
        self.description = description
        self.default = default
        self._is_required = is_required_val

    def is_required(self):
        if self._is_required is not None:
            return self._is_required
        return self.default is None


class _FakeFieldV1:
    """Mimics a Pydantic v1 ModelField."""
    def __init__(self, outer_type_=None, required=True, description=None):
        self.outer_type_ = outer_type_
        self.required = required
        self.field_info = SimpleNamespace(description=description)


def _make_tool(name: str, description: str = "", args_schema=None):
    return SimpleNamespace(name=name, description=description, args_schema=args_schema)


# ============================================================================
# _get_field_type_name (Pydantic v2)
# ============================================================================

class TestGetFieldTypeName:
    def test_simple_type_str(self):
        field = _FakeFieldV2(annotation=str)
        assert _get_field_type_name(field) == "str"

    def test_simple_type_int(self):
        field = _FakeFieldV2(annotation=int)
        assert _get_field_type_name(field) == "int"

    def test_simple_type_bool(self):
        field = _FakeFieldV2(annotation=bool)
        assert _get_field_type_name(field) == "bool"

    def test_optional_type_unwraps(self):
        field = _FakeFieldV2(annotation=Optional[str])
        result = _get_field_type_name(field)
        assert result == "str"

    def test_optional_int_unwraps(self):
        field = _FakeFieldV2(annotation=Optional[int])
        result = _get_field_type_name(field)
        assert result == "int"

    def test_union_takes_first_non_none(self):
        field = _FakeFieldV2(annotation=Union[int, str])
        result = _get_field_type_name(field)
        assert result == "int"

    def test_no_name_attribute_returns_string_repr(self):
        """When annotation has __origin__ but args resolve to something without __name__."""
        field = _FakeFieldV2(annotation=list)
        result = _get_field_type_name(field)
        assert result == "list"

    def test_annotation_without_origin_or_name(self):
        """When the annotation is something weird without __name__ or __origin__."""
        class _Weird:
            pass
        _Weird.__name__ = "WeirdType"
        field = _FakeFieldV2(annotation=_Weird)
        assert _get_field_type_name(field) == "weirdtype"

    def test_exception_returns_any(self):
        """When accessing annotation raises, return 'any'."""
        class _BadField:
            @property
            def annotation(self):
                raise RuntimeError("boom")
        result = _get_field_type_name(_BadField())
        assert result == "any"

    def test_no_annotation_attr(self):
        """Object without annotation at all."""
        field = SimpleNamespace()
        result = _get_field_type_name(field)
        assert result == "any"


# ============================================================================
# _get_field_type_name_v1 (Pydantic v1)
# ============================================================================

class TestGetFieldTypeNameV1:
    def test_simple_type_str(self):
        field = _FakeFieldV1(outer_type_=str)
        assert _get_field_type_name_v1(field) == "str"

    def test_simple_type_int(self):
        field = _FakeFieldV1(outer_type_=int)
        assert _get_field_type_name_v1(field) == "int"

    def test_optional_unwraps(self):
        field = _FakeFieldV1(outer_type_=Optional[str])
        result = _get_field_type_name_v1(field)
        assert result == "str"

    def test_union_non_none(self):
        field = _FakeFieldV1(outer_type_=Union[int, str])
        result = _get_field_type_name_v1(field)
        assert result == "int"

    def test_generic_type(self):
        """Generic type like list[str] resolves to 'list'."""
        field = _FakeFieldV1(outer_type_=list[str])
        result = _get_field_type_name_v1(field)
        assert result == "list"

    def test_exception_returns_any(self):
        field = SimpleNamespace()
        # No outer_type_ attribute → AttributeError → "any"
        result = _get_field_type_name_v1(field)
        assert result == "any"

    def test_no_outer_type(self):
        """Object that causes an exception during type extraction."""
        field = _FakeFieldV1()
        field.outer_type_ = None
        result = _get_field_type_name_v1(field)
        # None has no __origin__ and no __name__, str(None) = "None" → "none"
        assert isinstance(result, str)


# ============================================================================
# _extract_parameters_from_schema
# ============================================================================

class TestExtractParametersFromSchema:
    def test_pydantic_v2_model(self):
        """Schema with model_fields (Pydantic v2 style)."""
        schema = MagicMock()
        schema.model_fields = {
            "query": _FakeFieldV2(annotation=str, description="The search query", is_required_val=True),
            "limit": _FakeFieldV2(annotation=int, description="Max results", default=10, is_required_val=False),
        }
        schema.__required_fields__ = {"query"}
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)

        assert "query" in result
        assert result["query"]["required"] is True
        assert result["query"]["description"] == "The search query"
        assert result["query"]["type"] == "str"

        assert "limit" in result
        assert result["limit"]["required"] is False
        assert result["limit"]["description"] == "Max results"

    def test_pydantic_v2_model_no_required_fields_attr(self):
        """Schema without __required_fields__ falls back to is_required()."""
        schema = MagicMock(spec=[])
        schema.model_fields = {
            "name": _FakeFieldV2(annotation=str, description="Name", is_required_val=True),
        }
        # Remove __required_fields__
        if hasattr(schema, '__required_fields__'):
            delattr(schema, '__required_fields__')
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)
        assert "name" in result

    def test_pydantic_v1_model(self):
        """Schema with __fields__ (Pydantic v1 style)."""
        schema = MagicMock(spec=[])
        schema.__fields__ = {
            "query": _FakeFieldV1(outer_type_=str, required=True, description="Search query"),
            "offset": _FakeFieldV1(outer_type_=int, required=False, description="Offset"),
        }
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)

        assert "query" in result
        assert result["query"]["required"] is True
        assert result["query"]["description"] == "Search query"
        assert result["query"]["type"] == "str"

        assert "offset" in result
        assert result["offset"]["required"] is False

    def test_dict_schema(self):
        """JSON schema dict."""
        schema = {
            "properties": {
                "name": {"type": "string", "description": "User name"},
                "age": {"type": "integer", "description": "User age"},
            },
            "required": ["name"],
        }
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)

        assert "name" in result
        assert result["name"]["required"] is True
        assert result["name"]["type"] == "string"
        assert result["name"]["description"] == "User name"

        assert "age" in result
        assert result["age"]["required"] is False

    def test_dict_schema_no_required(self):
        """JSON schema dict with no required list."""
        schema = {
            "properties": {
                "q": {"type": "string"},
            },
        }
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)
        assert result["q"]["required"] is False

    def test_dict_schema_property_no_description(self):
        schema = {
            "properties": {
                "x": {"type": "number"},
            },
        }
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)
        assert result["x"]["description"] == ""

    def test_unsupported_schema_returns_empty(self):
        """Non-dict, non-Pydantic schema returns empty dict."""
        log = _make_logger()
        result = _extract_parameters_from_schema(42, log)
        assert result == {}

    def test_none_schema_returns_empty(self):
        log = _make_logger()
        result = _extract_parameters_from_schema(None, log)
        assert result == {}

    def test_exception_returns_empty(self):
        """If model_fields raises during iteration, return {}."""
        schema = MagicMock()
        schema.model_fields = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        log = _make_logger()
        result = _extract_parameters_from_schema(schema, log)
        assert result == {}

    def test_empty_dict_schema(self):
        schema = {}
        log = _make_logger()
        result = _extract_parameters_from_schema(schema, log)
        assert result == {}

    def test_v2_field_no_description(self):
        schema = MagicMock()
        schema.model_fields = {
            "field1": _FakeFieldV2(annotation=str, description=None),
        }
        schema.__required_fields__ = set()
        log = _make_logger()

        result = _extract_parameters_from_schema(schema, log)
        assert result["field1"]["description"] == ""


# ============================================================================
# _format_tool_descriptions
# ============================================================================

class TestFormatToolDescriptions:
    def test_single_tool_no_schema(self):
        tools = [_make_tool("my_tool", "Does stuff")]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "### my_tool" in result
        assert "Does stuff" in result

    def test_tool_with_dict_schema(self):
        schema = MagicMock()
        schema.model_fields = {
            "query": _FakeFieldV2(annotation=str, description="Search text", is_required_val=True),
        }
        schema.__required_fields__ = {"query"}

        tools = [_make_tool("search", "Search things", args_schema=schema)]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "### search" in result
        assert "Search things" in result
        assert "**Parameters:**" in result
        assert "`query`" in result
        assert "**required**" in result
        assert "Search text" in result

    def test_tool_with_optional_param(self):
        schema = MagicMock()
        schema.model_fields = {
            "limit": _FakeFieldV2(annotation=int, description="Max items", default=10, is_required_val=False),
        }
        schema.__required_fields__ = set()

        tools = [_make_tool("list_items", "List items", args_schema=schema)]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "optional" in result
        assert "`limit`" in result

    def test_multiple_tools(self):
        tools = [
            _make_tool("tool_a", "Description A"),
            _make_tool("tool_b", "Description B"),
            _make_tool("tool_c", "Description C"),
        ]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "### tool_a" in result
        assert "### tool_b" in result
        assert "### tool_c" in result

    def test_tool_limit_30(self):
        """Only first 30 tools are included."""
        tools = [_make_tool(f"tool_{i}", f"Desc {i}") for i in range(40)]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "### tool_29" in result
        assert "### tool_30" not in result

    def test_tool_no_description(self):
        tools = [_make_tool("bare_tool", "")]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)

        assert "### bare_tool" in result

    def test_tool_without_name_attr(self):
        """Tool that's just a string."""
        tool = "some_string_tool"
        log = _make_logger()

        result = _format_tool_descriptions([tool], log)
        assert "### some_string_tool" in result

    def test_schema_extraction_exception_handled(self):
        """If schema extraction raises, tool is still listed."""
        schema = MagicMock()
        schema.model_fields = property(lambda self: (_ for _ in ()).throw(RuntimeError("bad schema")))
        tools = [_make_tool("flaky_tool", "Flaky", args_schema=schema)]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)
        assert "### flaky_tool" in result

    def test_param_without_description(self):
        schema = MagicMock()
        schema.model_fields = {
            "flag": _FakeFieldV2(annotation=bool, description="", is_required_val=True),
        }
        schema.__required_fields__ = {"flag"}

        tools = [_make_tool("flagged", "Has flag", args_schema=schema)]
        log = _make_logger()

        result = _format_tool_descriptions(tools, log)
        assert "`flag` (**required**) [BOOL]" in result

    def test_empty_tools_list(self):
        log = _make_logger()
        result = _format_tool_descriptions([], log)
        assert result == ""


# ============================================================================
# _get_cached_tool_descriptions
# ============================================================================

class TestGetCachedToolDescriptions:
    def setup_method(self):
        _tool_description_cache.clear()

    def test_returns_formatted_descriptions(self):
        mock_tool = _make_tool("test_tool", "Test description")

        state = {
            "org_id": "org-1",
            "agent_toolsets": [{"name": "ts1"}],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[mock_tool],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "### test_tool" in result
        assert "Test description" in result

    def test_caching_works(self):
        mock_tool = _make_tool("cached_tool", "Cached desc")
        call_count = 0

        def mock_get_tools(state):
            nonlocal call_count
            call_count += 1
            return [mock_tool]

        state = {
            "org_id": "org-cache",
            "agent_toolsets": [{"name": "ts_cache"}],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, side_effect=mock_get_tools,
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result1 = _get_cached_tool_descriptions(state, log)
            result2 = _get_cached_tool_descriptions(state, log)

        assert result1 == result2
        assert call_count == 1  # Called only once, second was cached

    def test_no_tools_returns_fallback(self):
        state = {
            "org_id": "org-empty",
            "agent_toolsets": [],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "retrieval" in result
        assert "search_internal_knowledge" in result

    def test_no_tools_anthropic_fallback(self):
        state = {
            "org_id": "org-anthro",
            "agent_toolsets": [],
            "llm": MagicMock(),
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=True,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "retrieval_search_internal_knowledge" in result

    def test_exception_returns_fallback(self):
        state = {
            "org_id": "org-err",
            "agent_toolsets": [{"name": "ts_err"}],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, side_effect=RuntimeError("tool load error"),
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "retrieval.search_internal_knowledge" in result

    def test_has_knowledge_changes_cache_key(self):
        mock_tool = _make_tool("kb_tool", "KB tool")

        base_state = {
            "org_id": "org-kb",
            "agent_toolsets": [{"name": "ts_kb"}],
            "llm": None,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[mock_tool],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            state_no_kb = {**base_state, "has_knowledge": False}
            state_with_kb = {**base_state, "has_knowledge": True}

            _get_cached_tool_descriptions(state_no_kb, log)
            _get_cached_tool_descriptions(state_with_kb, log)

        # Both should be cached separately — 2 cache entries
        kb_keys = [k for k in _tool_description_cache if "org-kb" in k]
        assert len(kb_keys) == 2

    def test_default_org_id(self):
        state = {
            "agent_toolsets": [],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "retrieval" in result

    def test_non_dict_toolsets_skipped(self):
        state = {
            "org_id": "org-mixed",
            "agent_toolsets": [{"name": "valid"}, "not_a_dict", None, 42],
            "llm": None,
            "has_knowledge": False,
        }

        with patch(
            "app.modules.agents.qna.tool_system.get_agent_tools_with_schemas",
            create=True, return_value=[_make_tool("t", "d")],
        ), patch(
            "app.modules.agents.qna.tool_system._requires_sanitized_tool_names",
            create=True, return_value=False,
        ):
            log = _make_logger()
            result = _get_cached_tool_descriptions(state, log)

        assert "### t" in result
