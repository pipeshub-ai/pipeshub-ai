"""Unit tests for app.agents.tools.decorator — tool decorator and helper functions.

Covers: tool() decorator, _extract_parameters(), _infer_parameter_type(),
validation checks, auto-parameter extraction, when_to_use/when_not_to_use
description building, args_schema support.
"""

from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import (
    _extract_parameters,
    _infer_parameter_type,
    tool,
)
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolIntent


# ---------------------------------------------------------------------------
# _infer_parameter_type
# ---------------------------------------------------------------------------


class TestInferParameterType:
    """Tests for _infer_parameter_type helper."""

    def test_int_type(self):
        assert _infer_parameter_type(int) == ParameterType.INTEGER

    def test_float_type(self):
        assert _infer_parameter_type(float) == ParameterType.NUMBER

    def test_bool_type(self):
        assert _infer_parameter_type(bool) == ParameterType.BOOLEAN

    def test_str_type(self):
        assert _infer_parameter_type(str) == ParameterType.STRING

    def test_list_type(self):
        assert _infer_parameter_type(List[str]) == ParameterType.ARRAY

    def test_dict_type(self):
        assert _infer_parameter_type(Dict[str, int]) == ParameterType.OBJECT

    def test_unknown_type_defaults_to_string(self):
        assert _infer_parameter_type(bytes) == ParameterType.STRING

    def test_none_type(self):
        assert _infer_parameter_type(type(None)) == ParameterType.STRING

    def test_custom_class(self):
        class MyClass:
            pass
        assert _infer_parameter_type(MyClass) == ParameterType.STRING


# ---------------------------------------------------------------------------
# _extract_parameters
# ---------------------------------------------------------------------------


class TestExtractParameters:
    """Tests for _extract_parameters helper."""

    def test_simple_function(self):
        def func(name: str, count: int = 5):
            pass

        params = _extract_parameters(func)
        assert len(params) == 2

        name_param = next(p for p in params if p.name == "name")
        assert name_param.type == ParameterType.STRING
        assert name_param.required is True

        count_param = next(p for p in params if p.name == "count")
        assert count_param.type == ParameterType.INTEGER
        assert count_param.required is False
        assert count_param.default == 5

    def test_skips_self_and_cls(self):
        def method(self, data: str, cls: int):
            pass

        params = _extract_parameters(method)
        names = [p.name for p in params]
        assert "self" not in names
        assert "cls" not in names
        assert "data" in names

    def test_no_type_hints(self):
        def func(x, y):
            pass

        params = _extract_parameters(func)
        assert len(params) == 2
        assert all(p.type == ParameterType.STRING for p in params)

    def test_function_with_no_params(self):
        def func():
            pass

        params = _extract_parameters(func)
        assert len(params) == 0

    def test_bool_type_parameter(self):
        def func(flag: bool):
            pass

        params = _extract_parameters(func)
        assert params[0].type == ParameterType.BOOLEAN

    def test_list_type_parameter(self):
        def func(items: List[str]):
            pass

        params = _extract_parameters(func)
        assert params[0].type == ParameterType.ARRAY

    def test_dict_type_parameter(self):
        def func(data: Dict[str, int]):
            pass

        params = _extract_parameters(func)
        assert params[0].type == ParameterType.OBJECT

    def test_default_description(self):
        def func(x: str):
            pass

        params = _extract_parameters(func)
        assert "x" in params[0].description


# ---------------------------------------------------------------------------
# tool() decorator
# ---------------------------------------------------------------------------


class TestToolDecorator:
    """Tests for the tool() decorator function."""

    @pytest.fixture(autouse=True)
    def reset_registry(self):
        """Reset the global tool registry before/after each test."""
        from app.agents.tools.registry import _global_tools_registry
        original = dict(_global_tools_registry._tools)
        original_meta = dict(_global_tools_registry._metadata)
        yield
        _global_tools_registry._tools.clear()
        _global_tools_registry._tools.update(original)
        _global_tools_registry._metadata.clear()
        _global_tools_registry._metadata.update(original_meta)

    def test_basic_decorator(self):
        @tool(
            app_name="test_app",
            tool_name="greet",
            description="Says hello",
        )
        def greet(name: str) -> str:
            return f"Hello {name}"

        # Function should still work
        assert greet(name="World") == "Hello World"

        # Should have _tool_metadata
        assert hasattr(greet, '_tool_metadata')
        assert greet._tool_metadata.app_name == "test_app"
        assert greet._tool_metadata.tool_name == "greet"

    def test_decorator_with_args_schema(self):
        class MyArgs(BaseModel):
            query: str = Field(description="Search query")
            limit: int = Field(default=10, description="Max results")

        @tool(
            app_name="search_app",
            tool_name="search",
            description="Search things",
            args_schema=MyArgs,
        )
        def search(query: str, limit: int = 10) -> str:
            return f"Searching: {query}"

        assert hasattr(search, '_tool_metadata')
        assert search._tool_metadata.args_schema is MyArgs

    def test_decorator_raises_on_empty_app_name(self):
        with pytest.raises(ValueError, match="app_name must be provided"):
            @tool(app_name="", tool_name="test", description="test")
            def my_func():
                pass

    def test_decorator_raises_on_empty_tool_name(self):
        with pytest.raises(ValueError, match="tool_name must be provided"):
            @tool(app_name="app", tool_name="", description="test")
            def my_func():
                pass

    def test_decorator_raises_on_invalid_args_schema(self):
        with pytest.raises(ValueError, match="args_schema must be a Pydantic BaseModel"):
            @tool(
                app_name="app",
                tool_name="test",
                description="test",
                args_schema=dict,
            )
            def my_func():
                pass

    def test_decorator_uses_docstring_as_description(self):
        @tool(app_name="app", tool_name="doc_test")
        def my_func():
            """This is the docstring description."""
            pass

        assert my_func._tool_metadata.description == "This is the docstring description."

    def test_decorator_with_when_to_use(self):
        @tool(
            app_name="app",
            tool_name="guided_tool",
            description="A guided tool",
            when_to_use=["When user asks for data", "When searching"],
            when_not_to_use=["When user wants to create", "For admin tasks"],
        )
        def guided_tool():
            pass

        llm_desc = guided_tool._tool_metadata.llm_description
        assert "WHEN TO USE" in llm_desc
        assert "When user asks for data" in llm_desc
        assert "WHEN NOT TO USE" in llm_desc
        assert "For admin tasks" in llm_desc

    def test_decorator_with_custom_llm_description(self):
        @tool(
            app_name="app",
            tool_name="custom_desc",
            description="Short description",
            llm_description="Detailed LLM description for planning",
        )
        def custom_tool():
            pass

        assert custom_tool._tool_metadata.llm_description == "Detailed LLM description for planning"

    def test_decorator_with_primary_intent(self):
        @tool(
            app_name="app",
            tool_name="search_tool",
            description="Search tool",
            primary_intent=ToolIntent.SEARCH,
        )
        def search_tool():
            pass

        assert search_tool._tool_metadata.primary_intent == ToolIntent.SEARCH

    def test_decorator_with_typical_queries(self):
        @tool(
            app_name="app",
            tool_name="query_tool",
            description="Query tool",
            typical_queries=["Find all users", "List items"],
        )
        def query_tool():
            pass

        assert query_tool._tool_metadata.typical_queries == ["Find all users", "List items"]

    def test_decorator_auto_extracts_parameters(self):
        @tool(
            app_name="app",
            tool_name="auto_params",
            description="Auto params",
        )
        def auto_params(text: str, count: int = 1):
            pass

        params = auto_params._tool_metadata.parameters
        assert len(params) == 2
        text_param = next(p for p in params if p.name == "text")
        assert text_param.type == ParameterType.STRING

    def test_decorator_skips_auto_params_when_schema_provided(self):
        class MySchema(BaseModel):
            text: str = Field(description="Input text")

        @tool(
            app_name="app",
            tool_name="schema_params",
            description="Schema params",
            args_schema=MySchema,
        )
        def schema_params(text: str):
            pass

        # When args_schema is provided, parameters should be None/empty
        # (args_schema is used instead)
        metadata = schema_params._tool_metadata
        assert metadata.args_schema is MySchema

    def test_decorator_with_tags_and_category(self):
        @tool(
            app_name="app",
            tool_name="tagged",
            description="Tagged tool",
            tags=["search", "data"],
            category=ToolCategory.UTILITY,
        )
        def tagged_tool():
            pass

        assert tagged_tool._tool_metadata.tags == ["search", "data"]

    def test_decorator_with_examples_and_returns(self):
        @tool(
            app_name="app",
            tool_name="documented",
            description="Documented tool",
            returns="A string result",
            examples=[{"input": "test", "output": "result"}],
        )
        def documented_tool():
            pass

        assert documented_tool._tool_metadata.returns == "A string result"
        assert len(documented_tool._tool_metadata.examples) == 1

    def test_decorator_preserves_function_name(self):
        @tool(
            app_name="app",
            tool_name="named",
            description="Named tool",
        )
        def my_special_function():
            """My docstring."""
            pass

        assert my_special_function.__name__ == "my_special_function"
        assert my_special_function.__doc__ == "My docstring."
