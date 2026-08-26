"""Tool-schema conversion (agents/agent_loop/converters.py).

`convert_tool_schema_to_langchain_dict` replaced a per-call round-trip
through a dynamically-built Pydantic model (cached in `_TOOL_MODEL_CACHE`,
since `create_model()` was 8.8% of query-service CPU) with a direct OpenAI
function-calling dict built from `ToolSchema.input_schema`. Building a dict
is cheap enough that the cache was dropped entirely rather than ported —
these tests guard the properties that made the old cache correct even
without one: equal schemas must produce equal (JSON-comparable) output, and
the conversion must not mutate or lose any of the input schema's content.
"""

from __future__ import annotations

from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop.converters import (
    convert_tool_schema_to_langchain_dict,
    convert_tool_schemas_to_langchain,
)

SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "what to look for"},
        "limit": {"type": "integer", "description": "max results"},
    },
    "required": ["query"],
}


def _schema(name: str = "search", description: str = "Search records", **overrides) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description,
        input_schema=overrides.get("input_schema", SEARCH_SCHEMA),
    )


class TestDeterminism:
    def test_equal_but_distinct_schemas_produce_equal_output(self) -> None:
        """`ToolRegistry.schemas()` rebuilds `ToolSchema` objects every
        turn, so this must hold on content equality, not object identity —
        the old cache relied on the same property."""
        first, second = _schema(), _schema()
        assert first is not second
        assert convert_tool_schema_to_langchain_dict(first) == convert_tool_schema_to_langchain_dict(second)

    def test_property_order_does_not_change_the_result(self) -> None:
        reordered = dict(SEARCH_SCHEMA)
        reordered["properties"] = {
            "limit": SEARCH_SCHEMA["properties"]["limit"],
            "query": SEARCH_SCHEMA["properties"]["query"],
        }
        a = convert_tool_schema_to_langchain_dict(_schema())
        b = convert_tool_schema_to_langchain_dict(_schema(input_schema=reordered))
        assert a == b

    def test_does_not_mutate_the_input_schema(self) -> None:
        """The sanitizer (`_sanitize_tool_input_schema`) must return a new
        structure, not strip keys from the caller's own dict in place —
        `ToolSchema.input_schema` can be shared/reused across calls."""
        original = {"type": "object", "properties": {}, "$schema": "http://json-schema.org/draft-07/schema#"}
        schema = _schema(input_schema=original)
        convert_tool_schema_to_langchain_dict(schema)
        assert "$schema" in original


class TestFieldSeparation:
    def test_any_field_change_changes_the_output(self) -> None:
        base = convert_tool_schema_to_langchain_dict(_schema())
        other_name = convert_tool_schema_to_langchain_dict(_schema(name="other_tool"))
        other_desc = convert_tool_schema_to_langchain_dict(_schema(description="a different description"))
        other_schema = convert_tool_schema_to_langchain_dict(
            _schema(input_schema={"type": "object", "properties": {"q": {"type": "string"}}})
        )
        assert base != other_name
        assert base != other_desc
        assert base != other_schema


class TestOutputContent:
    def test_required_and_optional_fields_survive_conversion(self) -> None:
        parameters = convert_tool_schema_to_langchain_dict(_schema())["function"]["parameters"]
        assert parameters["properties"]["query"]["description"] == "what to look for"
        assert "limit" in parameters["properties"]
        assert parameters["required"] == ["query"]

    def test_repeated_conversion_of_the_same_schema_is_stable(self) -> None:
        schema = _schema()
        assert convert_tool_schema_to_langchain_dict(schema) == convert_tool_schema_to_langchain_dict(schema)


class TestListConversion:
    def test_list_helper_converts_every_tool_in_order(self) -> None:
        tools = [_schema(), _schema(name="fetch")]
        result = convert_tool_schemas_to_langchain(tools)
        assert [t["function"]["name"] for t in result] == ["search", "fetch"]

    def test_empty_list_still_returns_empty(self) -> None:
        assert convert_tool_schemas_to_langchain([]) == []
        assert convert_tool_schemas_to_langchain(None) == []
