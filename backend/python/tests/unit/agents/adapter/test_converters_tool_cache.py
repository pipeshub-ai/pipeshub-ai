"""Tool-schema conversion cache (agents/agent_loop/converters.py).

Building the LangChain tool object runs pydantic's full schema generation, and
it used to run once per tool per LLM call for output that never varies. These
guard the two properties that make caching it safe: equal schemas must reuse the
same object, and the schema handed to the model must not change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agents.agent_loop import converters as conv
from app.agents.agent_loop.converters import (
    convert_tool_schema_to_langchain,
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


@pytest.fixture(autouse=True)
def _clear_cache() -> "Iterator[None]":
    conv._TOOL_MODEL_CACHE.clear()
    yield
    conv._TOOL_MODEL_CACHE.clear()


class TestCacheIdentity:
    def test_same_schema_object_is_converted_once(self) -> None:
        schema = _schema()
        assert convert_tool_schema_to_langchain(schema) is convert_tool_schema_to_langchain(schema)

    def test_equal_but_distinct_schemas_share_an_entry(self) -> None:
        """ToolRegistry.schemas() rebuilds ToolSchema objects every turn, so
        keying on identity rather than content would never hit."""
        first, second = _schema(), _schema()
        assert first is not second
        assert convert_tool_schema_to_langchain(first) is convert_tool_schema_to_langchain(second)
        assert len(conv._TOOL_MODEL_CACHE) == 1

    def test_property_order_does_not_split_the_entry(self) -> None:
        reordered = dict(SEARCH_SCHEMA)
        reordered["properties"] = {
            "limit": SEARCH_SCHEMA["properties"]["limit"],
            "query": SEARCH_SCHEMA["properties"]["query"],
        }
        a = convert_tool_schema_to_langchain(_schema())
        b = convert_tool_schema_to_langchain(_schema(input_schema=reordered))
        assert a is b


class TestCacheSeparation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"name": "other_tool"},
            {"description": "a different description"},
            {"input_schema": {"type": "object", "properties": {"q": {"type": "string"}}}},
        ],
    )
    def test_any_field_change_gets_its_own_entry(self, kwargs: dict) -> None:
        base = convert_tool_schema_to_langchain(_schema())
        other = convert_tool_schema_to_langchain(_schema(**kwargs))
        assert base is not other
        assert len(conv._TOOL_MODEL_CACHE) == 2


class TestOutputUnchanged:
    def test_cached_tool_exposes_the_same_schema_as_a_fresh_build(self) -> None:
        """The model must see byte-identical tool JSON; a cache hit that
        changed the schema would silently alter behaviour."""
        cached = convert_tool_schema_to_langchain(_schema())
        conv._TOOL_MODEL_CACHE.clear()
        fresh = convert_tool_schema_to_langchain(_schema())

        assert cached is not fresh
        assert cached.name == fresh.name
        assert cached.description == fresh.description
        assert cached.args_schema.model_json_schema() == fresh.args_schema.model_json_schema()

    def test_required_and_optional_fields_survive_caching(self) -> None:
        schema = convert_tool_schema_to_langchain(_schema()).args_schema.model_json_schema()
        assert schema["properties"]["query"]["description"] == "what to look for"
        assert "limit" in schema["properties"]


class TestListConversion:
    def test_list_helper_reuses_cached_entries(self) -> None:
        tools = [_schema(), _schema(name="fetch")]
        first = convert_tool_schemas_to_langchain(tools)
        second = convert_tool_schemas_to_langchain([_schema(), _schema(name="fetch")])

        assert [t.name for t in first] == ["search", "fetch"]
        assert all(a is b for a, b in zip(first, second, strict=True))
        assert len(conv._TOOL_MODEL_CACHE) == 2

    def test_empty_list_still_returns_empty(self) -> None:
        assert convert_tool_schemas_to_langchain([]) == []
        assert convert_tool_schemas_to_langchain(None) == []
        assert not conv._TOOL_MODEL_CACHE


class TestBounding:
    def test_cache_clears_wholesale_when_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(conv, "_TOOL_MODEL_CACHE_MAXSIZE", 3)
        for i in range(3):
            convert_tool_schema_to_langchain(_schema(name=f"tool_{i}"))
        assert len(conv._TOOL_MODEL_CACHE) == 3

        convert_tool_schema_to_langchain(_schema(name="overflow"))
        assert len(conv._TOOL_MODEL_CACHE) == 1
