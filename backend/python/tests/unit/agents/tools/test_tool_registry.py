"""Unit tests for app.agents.tools.registry — ToolRegistry."""

import pytest
from unittest.mock import MagicMock

from app.agents.tools.config import ToolCategory, ToolMetadata
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import Tool, ToolParameter
from app.agents.tools.registry import ToolRegistry, _global_tools_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool(app="testapp", name="testtool", desc="A test tool", params=None, fn=None):
    return Tool(
        app_name=app,
        tool_name=name,
        description=desc,
        function=fn or (lambda **kw: None),
        parameters=params or [],
    )


def _make_metadata(
    app="testapp",
    name="testtool",
    desc="Test",
    category=ToolCategory.UTILITY,
    essential=False,
    requires_auth=False,
    tags=None,
):
    return ToolMetadata(
        app_name=app,
        tool_name=name,
        description=desc,
        category=category,
        is_essential=essential,
        requires_auth=requires_auth,
        tags=tags or [],
    )


# ---------------------------------------------------------------------------
# Basic registration and retrieval
# ---------------------------------------------------------------------------

class TestToolRegistryBasics:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_and_get(self):
        tool = _make_tool()
        self.registry.register(tool)
        assert self.registry.get_tool("testapp", "testtool") is tool

    def test_get_tool_missing(self):
        assert self.registry.get_tool("no", "tool") is None

    def test_get_tool_by_full_name(self):
        tool = _make_tool()
        self.registry.register(tool)
        assert self.registry.get_tool_by_full_name("testapp.testtool") is tool

    def test_get_tool_by_full_name_missing(self):
        assert self.registry.get_tool_by_full_name("no.tool") is None

    def test_duplicate_registration_skipped(self):
        tool1 = _make_tool(desc="first")
        tool2 = _make_tool(desc="second")
        self.registry.register(tool1)
        self.registry.register(tool2)
        # First registration wins
        assert self.registry.get_tool("testapp", "testtool").description == "first"

    def test_list_tools(self):
        self.registry.register(_make_tool("a", "t1"))
        self.registry.register(_make_tool("a", "t2"))
        names = self.registry.list_tools()
        assert "a.t1" in names
        assert "a.t2" in names

    def test_list_tools_empty(self):
        assert self.registry.list_tools() == []

    def test_get_all_tools_returns_copy(self):
        tool = _make_tool()
        self.registry.register(tool)
        all_tools = self.registry.get_all_tools()
        assert "testapp.testtool" in all_tools
        # Mutating copy should not affect registry
        all_tools.clear()
        assert self.registry.get_tool("testapp", "testtool") is tool


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

class TestToolRegistryMetadata:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_register_with_metadata(self):
        tool = _make_tool()
        meta = _make_metadata()
        self.registry.register(tool, meta)
        assert self.registry.get_metadata("testapp.testtool") is meta

    def test_get_metadata_missing(self):
        assert self.registry.get_metadata("no.tool") is None

    def test_category_indexing(self):
        tool = _make_tool()
        meta = _make_metadata(category=ToolCategory.COMMUNICATION)
        self.registry.register(tool, meta)
        tools = self.registry.get_tools_by_category(ToolCategory.COMMUNICATION)
        assert len(tools) == 1
        assert tools[0] is tool

    def test_get_tools_by_category_empty(self):
        assert self.registry.get_tools_by_category(ToolCategory.SEARCH) == []


# ---------------------------------------------------------------------------
# Get tools by app
# ---------------------------------------------------------------------------

class TestGetToolsByApp:
    def setup_method(self):
        self.registry = ToolRegistry()

    def test_returns_matching_tools(self):
        t1 = _make_tool("slack", "send")
        t2 = _make_tool("slack", "read")
        t3 = _make_tool("jira", "create")
        self.registry.register(t1)
        self.registry.register(t2)
        self.registry.register(t3)
        slack_tools = self.registry.get_tools_by_app("slack")
        assert len(slack_tools) == 2
        assert all(t.app_name == "slack" for t in slack_tools)

    def test_returns_empty_for_missing_app(self):
        assert self.registry.get_tools_by_app("unknown") == []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearchTools:
    def setup_method(self):
        self.registry = ToolRegistry()
        # Register several tools with metadata
        self.registry.register(
            _make_tool("slack", "send", "Send a message to Slack"),
            _make_metadata("slack", "send", "Send message", ToolCategory.COMMUNICATION,
                           essential=True, tags=["messaging"]),
        )
        self.registry.register(
            _make_tool("jira", "create", "Create a Jira issue"),
            _make_metadata("jira", "create", "Create issue", ToolCategory.PROJECT_MANAGEMENT,
                           requires_auth=True, tags=["project"]),
        )
        self.registry.register(
            _make_tool("calc", "add", "Add numbers"),
            _make_metadata("calc", "add", "Add", ToolCategory.UTILITY,
                           essential=True, tags=["math"]),
        )

    def test_search_no_filters(self):
        results = self.registry.search_tools()
        assert len(results) == 3

    def test_search_by_query_name(self):
        results = self.registry.search_tools(query="slack")
        assert len(results) == 1
        assert results[0].app_name == "slack"

    def test_search_by_query_description(self):
        results = self.registry.search_tools(query="jira issue")
        assert len(results) == 1

    def test_search_by_category(self):
        results = self.registry.search_tools(category=ToolCategory.UTILITY)
        assert len(results) == 1
        assert results[0].app_name == "calc"

    def test_search_by_tags(self):
        results = self.registry.search_tools(tags=["messaging"])
        assert len(results) == 1
        assert results[0].tool_name == "send"

    def test_search_essential_only(self):
        results = self.registry.search_tools(essential_only=True)
        assert len(results) == 2

    def test_search_combined_filters(self):
        results = self.registry.search_tools(
            category=ToolCategory.COMMUNICATION,
            essential_only=True,
        )
        assert len(results) == 1

    def test_search_no_match(self):
        results = self.registry.search_tools(query="nonexistent")
        assert results == []

    def test_search_tags_no_metadata(self):
        """Tool with no metadata is included when tags are specified
        because the tags filter only applies when metadata exists and has tags.
        When metadata is missing, the tags check is skipped entirely."""
        self.registry.register(_make_tool("bare", "t", "bare tool"))
        results = self.registry.search_tools(tags=["messaging"])
        # The source code skips tags filter when metadata exists and tags match.
        # When metadata is None, the condition `if tags and metadata:` is False,
        # so the tool passes through. Result: bare tool IS included.
        bare_results = [r for r in results if r.app_name == "bare"]
        # bare tool has no metadata so it passes through the tags filter
        assert len(bare_results) == 1


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics:
    def setup_method(self):
        self.registry = ToolRegistry()
        self.registry.register(
            _make_tool("slack", "send"),
            _make_metadata("slack", "send", "Send", ToolCategory.COMMUNICATION,
                           essential=True, requires_auth=True),
        )
        self.registry.register(
            _make_tool("slack", "read"),
            _make_metadata("slack", "read", "Read", ToolCategory.COMMUNICATION,
                           requires_auth=True),
        )
        self.registry.register(
            _make_tool("calc", "add"),
            _make_metadata("calc", "add", "Add", ToolCategory.UTILITY,
                           essential=True),
        )

    def test_total_tools(self):
        stats = self.registry.get_statistics()
        assert stats["total_tools"] == 3

    def test_by_category(self):
        stats = self.registry.get_statistics()
        assert stats["by_category"]["communication"] == 2
        assert stats["by_category"]["utility"] == 1

    def test_by_app(self):
        stats = self.registry.get_statistics()
        assert stats["by_app"]["slack"] == 2
        assert stats["by_app"]["calc"] == 1

    def test_essential_count(self):
        stats = self.registry.get_statistics()
        assert stats["essential_count"] == 2

    def test_requires_auth_count(self):
        stats = self.registry.get_statistics()
        assert stats["requires_auth_count"] == 2


# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

class TestSchemaGeneration:
    def setup_method(self):
        self.registry = ToolRegistry()
        params = [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Max results",
                required=False,
                enum=[5, 10, 20],
            ),
        ]
        self.registry.register(_make_tool("search", "find", "Find items", params=params))

    def test_generate_openai_schema(self):
        schemas = self.registry.generate_openai_schema()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "search.find"
        assert func["description"] == "Find items"
        props = func["parameters"]["properties"]
        assert "query" in props
        assert props["query"]["type"] == "string"
        assert "query" in func["parameters"]["required"]
        assert props["limit"]["enum"] == [5, 10, 20]
        assert "limit" not in func["parameters"]["required"]

    def test_generate_anthropic_schema(self):
        schemas = self.registry.generate_anthropic_schema()
        assert len(schemas) == 1
        schema = schemas[0]
        assert schema["name"] == "search.find"
        assert schema["description"] == "Find items"
        props = schema["input_schema"]["properties"]
        assert "query" in props
        assert "query" in schema["input_schema"]["required"]

    def test_openai_array_items(self):
        params = [
            ToolParameter(
                name="ids",
                type=ParameterType.ARRAY,
                description="List of IDs",
                required=True,
                items={"type": "string"},
            ),
        ]
        registry = ToolRegistry()
        registry.register(_make_tool("a", "b", "desc", params=params))
        schemas = registry.generate_openai_schema()
        assert schemas[0]["function"]["parameters"]["properties"]["ids"]["items"] == "string"

    def test_openai_object_properties(self):
        params = [
            ToolParameter(
                name="config",
                type=ParameterType.OBJECT,
                description="Config object",
                required=True,
                properties={"type": {"key": "string"}},
            ),
        ]
        registry = ToolRegistry()
        registry.register(_make_tool("a", "b", "desc", params=params))
        schemas = registry.generate_openai_schema()
        assert "properties" in schemas[0]["function"]["parameters"]["properties"]["config"]


# ---------------------------------------------------------------------------
# Global registry instance
# ---------------------------------------------------------------------------

class TestGlobalRegistry:
    def test_global_instance_exists(self):
        assert isinstance(_global_tools_registry, ToolRegistry)
