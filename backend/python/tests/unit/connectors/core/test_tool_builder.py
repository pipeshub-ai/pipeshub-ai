"""Unit tests for app.connectors.core.registry.tool_builder.

Covers: ToolDefinition, ToolsetConfigBuilder fluent interface,
ToolsetBuilder, parameter validation, and schema generation.
"""

import logging
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from app.connectors.core.registry.tool_builder import (
    ToolDefinition,
    ToolsetBuilder,
    ToolsetCategory,
    ToolsetCommonFields,
    ToolsetConfigBuilder,
)

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


# ============================================================================
# ToolDefinition
# ============================================================================

class TestToolDefinition:
    def test_to_dict_basic(self):
        """to_dict returns all expected keys."""
        tool = ToolDefinition(
            name="search",
            description="Search for items",
            returns="list of items",
        )
        d = tool.to_dict()
        assert d["name"] == "search"
        assert d["description"] == "Search for items"
        assert d["returns"] == "list of items"
        assert d["parameters"] == []
        assert d["examples"] == []
        assert d["tags"] == []

    def test_to_dict_with_legacy_parameters(self):
        """Legacy parameter format is passed through."""
        params = [{"name": "q", "type": "string", "required": True}]
        tool = ToolDefinition(name="t", description="d", parameters=params)
        d = tool.to_dict()
        assert d["parameters"] == params

    def test_to_dict_with_pydantic_schema(self):
        """args_schema is converted to parameter dict format."""

        class SearchSchema(BaseModel):
            query: str = Field(description="The search query")
            limit: int = Field(default=10, description="Max results")
            include_archived: bool = Field(default=False, description="Include archived")

        tool = ToolDefinition(
            name="search",
            description="Search",
            args_schema=SearchSchema,
        )
        d = tool.to_dict()
        params = d["parameters"]

        names = {p["name"] for p in params}
        assert "query" in names
        assert "limit" in names
        assert "include_archived" in names

        query_param = next(p for p in params if p["name"] == "query")
        assert query_param["type"] == "string"
        assert query_param["description"] == "The search query"

        limit_param = next(p for p in params if p["name"] == "limit")
        assert limit_param["type"] == "integer"
        assert limit_param["default"] == 10

    def test_to_dict_with_optional_fields(self):
        """Optional fields are recognized as not required."""

        class OptSchema(BaseModel):
            name: str = Field(description="Name")
            tag: Optional[str] = Field(default=None, description="Optional tag")

        tool = ToolDefinition(name="t", description="d", args_schema=OptSchema)
        d = tool.to_dict()
        params = d["parameters"]

        tag_param = next(p for p in params if p["name"] == "tag")
        assert tag_param["required"] is False

    def test_to_dict_with_list_type(self):
        """List type fields are mapped to 'array'."""

        class ListSchema(BaseModel):
            items: List[str] = Field(description="Item list")

        tool = ToolDefinition(name="t", description="d", args_schema=ListSchema)
        d = tool.to_dict()
        params = d["parameters"]

        items_param = next(p for p in params if p["name"] == "items")
        assert items_param["type"] == "array"

    def test_schema_to_parameters_fallback(self):
        """If schema conversion fails, legacy parameters are returned."""
        tool = ToolDefinition(
            name="t",
            description="d",
            parameters=[{"name": "fallback"}],
        )
        # Set args_schema to something that will fail conversion
        tool.args_schema = "not_a_real_schema"
        d = tool.to_dict()
        assert d["parameters"] == [{"name": "fallback"}]


# ============================================================================
# ToolsetConfigBuilder (fluent interface)
# ============================================================================

class TestToolsetConfigBuilder:
    def test_default_config(self):
        """Default built config has expected structure."""
        builder = ToolsetConfigBuilder()
        config = builder.build()

        assert config["iconPath"] == "/assets/icons/toolsets/default.svg"
        assert config["auth"]["supportedAuthTypes"] == ["API_TOKEN"]
        assert config["tools"] == []
        assert config["documentationLinks"] == []

    def test_with_icon(self):
        builder = ToolsetConfigBuilder()
        config = builder.with_icon("/icons/custom.svg").build()
        assert config["iconPath"] == "/icons/custom.svg"

    def test_add_documentation_link(self):
        from app.connectors.core.registry.types import DocumentationLink

        link = DocumentationLink(title="Setup", url="https://example.com", doc_type="setup")
        builder = ToolsetConfigBuilder()
        config = builder.add_documentation_link(link).build()

        assert len(config["documentationLinks"]) == 1
        assert config["documentationLinks"][0]["title"] == "Setup"
        assert config["documentationLinks"][0]["url"] == "https://example.com"

    def test_with_supported_auth_types_string(self):
        builder = ToolsetConfigBuilder()
        config = builder.with_supported_auth_types("OAUTH").build()
        assert config["auth"]["supportedAuthTypes"] == ["OAUTH"]

    def test_with_supported_auth_types_list(self):
        builder = ToolsetConfigBuilder()
        config = builder.with_supported_auth_types(["OAUTH", "API_TOKEN"]).build()
        assert config["auth"]["supportedAuthTypes"] == ["OAUTH", "API_TOKEN"]

    def test_with_supported_auth_types_empty_list_raises(self):
        builder = ToolsetConfigBuilder()
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_type_raises(self):
        builder = ToolsetConfigBuilder()
        with pytest.raises(ValueError, match="must be str or List"):
            builder.with_supported_auth_types(123)

    def test_add_supported_auth_type(self):
        builder = ToolsetConfigBuilder()
        config = builder.add_supported_auth_type("OAUTH").build()
        assert "OAUTH" in config["auth"]["supportedAuthTypes"]
        assert "API_TOKEN" in config["auth"]["supportedAuthTypes"]

    def test_add_supported_auth_type_no_duplicate(self):
        builder = ToolsetConfigBuilder()
        config = builder.add_supported_auth_type("API_TOKEN").build()
        assert config["auth"]["supportedAuthTypes"].count("API_TOKEN") == 1

    def test_with_redirect_uri(self):
        builder = ToolsetConfigBuilder()
        config = builder.with_redirect_uri("https://cb.example.com", display=True).build()
        assert config["auth"]["redirectUri"] == "https://cb.example.com"
        assert config["auth"]["displayRedirectUri"] is True

    def test_add_tool(self):
        builder = ToolsetConfigBuilder()
        tool = ToolDefinition(name="search", description="Search items")
        config = builder.add_tool(tool).build()

        assert len(config["tools"]) == 1
        assert config["tools"][0]["name"] == "search"

    def test_add_tools(self):
        builder = ToolsetConfigBuilder()
        tools = [
            ToolDefinition(name="t1", description="d1"),
            ToolDefinition(name="t2", description="d2"),
        ]
        config = builder.add_tools(tools).build()
        assert len(config["tools"]) == 2

    def test_build_resets_state(self):
        """After build(), internal state is reset to defaults."""
        builder = ToolsetConfigBuilder()
        builder.with_icon("/custom.svg")
        builder.build()

        # Second build should have defaults
        config2 = builder.build()
        assert config2["iconPath"] == "/assets/icons/toolsets/default.svg"

    def test_with_oauth_urls(self):
        builder = ToolsetConfigBuilder()
        config = builder.with_oauth_urls(
            "https://auth.example.com/authorize",
            "https://auth.example.com/token",
            scopes=["read", "write"],
        ).build()
        assert config["auth"]["authorizeUrl"] == "https://auth.example.com/authorize"
        assert config["auth"]["tokenUrl"] == "https://auth.example.com/token"
        assert config["auth"]["scopes"] == ["read", "write"]

    def test_fluent_chaining(self):
        """All fluent methods return self for chaining."""
        builder = ToolsetConfigBuilder()
        result = (
            builder
            .with_icon("/i.svg")
            .with_redirect_uri("https://x.com")
            .add_tool(ToolDefinition(name="t", description="d"))
        )
        assert result is builder


# ============================================================================
# ToolsetBuilder
# ============================================================================

class TestToolsetBuilder:
    def test_basic_builder(self):
        builder = ToolsetBuilder("test_toolset")
        assert builder.name == "test_toolset"
        assert builder.category == ToolsetCategory.APP
        assert builder.is_internal is False

    def test_with_description(self):
        builder = ToolsetBuilder("t")
        result = builder.with_description("A test toolset")
        assert builder.description == "A test toolset"
        assert result is builder

    def test_with_category(self):
        builder = ToolsetBuilder("t")
        result = builder.with_category(ToolsetCategory.COMMUNICATION)
        assert builder.category == ToolsetCategory.COMMUNICATION
        assert result is builder

    def test_as_internal(self):
        builder = ToolsetBuilder("t")
        result = builder.as_internal()
        assert builder.is_internal is True
        assert result is builder

    def test_in_group(self):
        builder = ToolsetBuilder("t")
        result = builder.in_group("Google Workspace")
        assert builder.app_group == "Google Workspace"
        assert result is builder

    def test_with_supported_auth_types_string(self):
        builder = ToolsetBuilder("t")
        builder.with_supported_auth_types("OAUTH")
        assert builder.supported_auth_types == ["OAUTH"]

    def test_with_supported_auth_types_empty_raises(self):
        builder = ToolsetBuilder("t")
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_raises(self):
        builder = ToolsetBuilder("t")
        with pytest.raises(ValueError, match="must be str or List"):
            builder.with_supported_auth_types(42)

    def test_add_supported_auth_type(self):
        builder = ToolsetBuilder("t")
        builder.add_supported_auth_type("BEARER")
        assert "BEARER" in builder.supported_auth_types
        assert "API_TOKEN" in builder.supported_auth_types

    def test_add_supported_auth_type_no_duplicate(self):
        builder = ToolsetBuilder("t")
        builder.add_supported_auth_type("API_TOKEN")
        assert builder.supported_auth_types.count("API_TOKEN") == 1

    def test_with_tools(self):
        builder = ToolsetBuilder("t")
        tools = [ToolDefinition(name="search", description="Search")]
        result = builder.with_tools(tools)
        assert builder.tools == tools
        assert result is builder

    def test_configure(self):
        """configure() passes the config builder to a function and stores result."""
        builder = ToolsetBuilder("t")

        def config_fn(cb):
            cb.with_icon("/custom.svg")
            return cb

        result = builder.configure(config_fn)
        assert result is builder

    def test_with_auth_empty_raises(self):
        builder = ToolsetBuilder("t")
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_auth([])


# ============================================================================
# ToolsetCommonFields
# ============================================================================

class TestToolsetCommonFields:
    def test_api_token(self):
        field = ToolsetCommonFields.api_token("My Token", "Enter token")
        assert field.name is not None

    def test_bearer_token(self):
        field = ToolsetCommonFields.bearer_token()
        assert field.name is not None

    def test_client_id(self):
        field = ToolsetCommonFields.client_id("Google")
        assert field.name is not None

    def test_client_secret(self):
        field = ToolsetCommonFields.client_secret("Google")
        assert field.name is not None


# ============================================================================
# ToolsetCategory enum
# ============================================================================

class TestToolsetCategory:
    def test_all_values(self):
        """All categories have string values."""
        assert ToolsetCategory.APP.value == "app"
        assert ToolsetCategory.FILE.value == "file"
        assert ToolsetCategory.WEB_SEARCH.value == "web_search"
        assert ToolsetCategory.UTILITY.value == "utility"
        assert ToolsetCategory.COMMUNICATION.value == "communication"
        assert ToolsetCategory.CALENDAR.value == "calendar"
        assert ToolsetCategory.PROJECT_MANAGEMENT.value == "project_management"
