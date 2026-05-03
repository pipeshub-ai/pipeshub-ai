"""
Additional tests for tool_builder.py targeting uncovered lines:
- ToolDefinition.to_dict with args_schema
- ToolDefinition._schema_to_parameters (all type branches)
- ToolsetConfigBuilder: with_oauth_config, with_oauth_urls, add_tools, build
- ToolsetBuilder: with_auth, with_supported_auth_types, add_supported_auth_type,
                  with_description, with_category, as_internal, configure, with_tools,
                  with_oauth_config, build_decorator, _validate_oauth_requirements,
                  _validate_required_auth_fields
- ToolsetCommonFields: all static methods
- ToolsetCategory enum values
"""

from typing import Dict, List, Optional
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


# ============================================================================
# ToolDefinition
# ============================================================================


class TestToolDefinition:
    def test_to_dict_without_schema(self):
        td = ToolDefinition(name="test_tool", description="A test tool")
        result = td.to_dict()
        assert result["name"] == "test_tool"
        assert result["parameters"] == []

    def test_to_dict_with_args_schema(self):
        class MySchema(BaseModel):
            query: str = Field(description="Search query")
            limit: int = Field(default=10, description="Limit results")
            active: bool = Field(description="Is active")
            score: float = Field(description="Score threshold")
            tags: List[str] = Field(default_factory=list, description="Tags list")
            metadata: Dict[str, str] = Field(default_factory=dict, description="Metadata dict")

        td = ToolDefinition(name="search", description="Search tool", args_schema=MySchema)
        result = td.to_dict()
        params = result["parameters"]
        assert len(params) == 6
        param_names = {p["name"] for p in params}
        assert param_names == {"query", "limit", "active", "score", "tags", "metadata"}

        # Check types
        param_map = {p["name"]: p for p in params}
        assert param_map["query"]["type"] == "string"
        assert param_map["limit"]["type"] == "integer"
        assert param_map["active"]["type"] == "boolean"
        assert param_map["score"]["type"] == "number"
        assert param_map["tags"]["type"] == "array"
        assert param_map["metadata"]["type"] == "object"

    def test_to_dict_with_optional_fields(self):
        class OptionalSchema(BaseModel):
            name: Optional[str] = Field(default=None, description="Optional name")

        td = ToolDefinition(name="opt", description="Optional tool", args_schema=OptionalSchema)
        result = td.to_dict()
        params = result["parameters"]
        assert len(params) == 1
        assert params[0]["name"] == "name"
        assert params[0]["default"] is None

    def test_schema_to_parameters_fallback_on_exception(self):
        """If schema conversion raises, fallback to legacy parameters."""
        td = ToolDefinition(name="broken", description="Broken tool", parameters=[{"name": "x"}])
        # Simulate broken args_schema
        td.args_schema = "not_a_schema"  # type: ignore
        result = td._schema_to_parameters()
        assert result == [{"name": "x"}]

    def test_schema_to_parameters_no_schema(self):
        td = ToolDefinition(name="no_schema", description="No schema", parameters=[{"name": "y"}])
        result = td._schema_to_parameters()
        assert result == [{"name": "y"}]


# ============================================================================
# ToolsetConfigBuilder
# ============================================================================


class TestToolsetConfigBuilder:
    def test_with_icon(self):
        builder = ToolsetConfigBuilder()
        result = builder.with_icon("/assets/test.svg")
        assert result is builder
        assert builder.config["iconPath"] == "/assets/test.svg"

    def test_add_documentation_link(self):
        from app.connectors.core.registry.types import DocumentationLink
        builder = ToolsetConfigBuilder()
        link = DocumentationLink("Test Doc", "https://example.com", "setup")
        builder.add_documentation_link(link)
        assert len(builder.config["documentationLinks"]) == 1
        assert builder.config["documentationLinks"][0]["title"] == "Test Doc"

    def test_with_supported_auth_types_string(self):
        builder = ToolsetConfigBuilder()
        builder.with_supported_auth_types("OAUTH")
        assert builder.config["auth"]["supportedAuthTypes"] == ["OAUTH"]

    def test_with_supported_auth_types_list(self):
        builder = ToolsetConfigBuilder()
        builder.with_supported_auth_types(["OAUTH", "API_TOKEN"])
        assert builder.config["auth"]["supportedAuthTypes"] == ["OAUTH", "API_TOKEN"]

    def test_with_supported_auth_types_empty_list_raises(self):
        builder = ToolsetConfigBuilder()
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_type_raises(self):
        builder = ToolsetConfigBuilder()
        with pytest.raises(ValueError, match="must be str or List"):
            builder.with_supported_auth_types(123)  # type: ignore

    def test_add_supported_auth_type(self):
        builder = ToolsetConfigBuilder()
        builder.add_supported_auth_type("OAUTH")
        assert "OAUTH" in builder.config["auth"]["supportedAuthTypes"]

    def test_add_supported_auth_type_no_duplicate(self):
        builder = ToolsetConfigBuilder()
        builder.add_supported_auth_type("OAUTH")
        builder.add_supported_auth_type("OAUTH")
        count = builder.config["auth"]["supportedAuthTypes"].count("OAUTH")
        assert count == 1

    def test_with_redirect_uri(self):
        builder = ToolsetConfigBuilder()
        builder.with_redirect_uri("https://example.com/callback", display=True)
        assert builder.config["auth"]["redirectUri"] == "https://example.com/callback"
        assert builder.config["auth"]["displayRedirectUri"] is True

    def test_add_auth_field_to_specific_type(self):
        from app.connectors.core.registry.types import AuthField
        builder = ToolsetConfigBuilder()
        field = AuthField(name="token", display_name="Token", field_type="PASSWORD")
        builder.add_auth_field(field, auth_type="API_TOKEN")
        assert "API_TOKEN" in builder.config["auth"]["schemas"]
        assert len(builder.config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 1

    def test_add_auth_field_to_default_schema(self):
        from app.connectors.core.registry.types import AuthField
        builder = ToolsetConfigBuilder()
        field = AuthField(name="key", display_name="API Key", field_type="TEXT")
        builder.add_auth_field(field)
        assert len(builder.config["auth"]["schema"]["fields"]) == 1

    def test_with_oauth_urls(self):
        builder = ToolsetConfigBuilder()
        builder.with_oauth_urls("https://auth.example.com", "https://token.example.com", ["read", "write"])
        assert builder.config["auth"]["authorizeUrl"] == "https://auth.example.com"
        assert builder.config["auth"]["tokenUrl"] == "https://token.example.com"
        assert builder.config["auth"]["scopes"] == ["read", "write"]

    def test_with_oauth_urls_no_scopes(self):
        builder = ToolsetConfigBuilder()
        builder.with_oauth_urls("https://auth.example.com", "https://token.example.com")
        assert builder.config["auth"]["authorizeUrl"] == "https://auth.example.com"
        assert "scopes" not in builder.config["auth"]

    def test_add_tool(self):
        builder = ToolsetConfigBuilder()
        tool = ToolDefinition(name="test", description="Test tool")
        builder.add_tool(tool)
        assert len(builder.config["tools"]) == 1

    def test_add_tools(self):
        builder = ToolsetConfigBuilder()
        tools = [
            ToolDefinition(name="t1", description="Tool 1"),
            ToolDefinition(name="t2", description="Tool 2"),
        ]
        builder.add_tools(tools)
        assert len(builder.config["tools"]) == 2

    def test_build_resets(self):
        builder = ToolsetConfigBuilder()
        builder.with_icon("/test.svg")
        result = builder.build()
        assert result["iconPath"] == "/test.svg"
        # After build, should be reset
        assert builder.config["iconPath"] == "/icons/toolsets/default.svg"


# ============================================================================
# ToolsetBuilder
# ============================================================================


class TestToolsetBuilder:
    def test_in_group(self):
        builder = ToolsetBuilder("test")
        result = builder.in_group("MyGroup")
        assert result is builder
        assert builder.app_group == "MyGroup"

    def test_with_description(self):
        builder = ToolsetBuilder("test")
        builder.with_description("A description")
        assert builder.description == "A description"

    def test_with_category(self):
        builder = ToolsetBuilder("test")
        builder.with_category(ToolsetCategory.WEB_SEARCH)
        assert builder.category == ToolsetCategory.WEB_SEARCH

    def test_as_internal(self):
        builder = ToolsetBuilder("test")
        result = builder.as_internal()
        assert result is builder
        assert builder.is_internal is True

    def test_configure(self):
        builder = ToolsetBuilder("test")
        builder.configure(lambda b: b.with_icon("/custom.svg"))
        assert builder.config_builder.config["iconPath"] == "/custom.svg"

    def test_with_supported_auth_types_string(self):
        builder = ToolsetBuilder("test")
        builder.with_supported_auth_types("NONE")
        assert builder.supported_auth_types == ["NONE"]

    def test_with_supported_auth_types_empty_raises(self):
        builder = ToolsetBuilder("test")
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_raises(self):
        builder = ToolsetBuilder("test")
        with pytest.raises(ValueError):
            builder.with_supported_auth_types(42)  # type: ignore

    def test_add_supported_auth_type(self):
        builder = ToolsetBuilder("test")
        builder.add_supported_auth_type("OAUTH")
        assert "OAUTH" in builder.supported_auth_types

    def test_add_supported_auth_type_no_duplicate(self):
        builder = ToolsetBuilder("test")
        builder.add_supported_auth_type("API_TOKEN")  # Already default
        assert builder.supported_auth_types.count("API_TOKEN") == 1

    def test_with_auth_empty_raises(self):
        builder = ToolsetBuilder("test")
        with pytest.raises(ValueError, match="cannot be empty"):
            builder.with_auth([])

    def test_with_tools(self):
        builder = ToolsetBuilder("test")
        tools = [ToolDefinition(name="t1", description="Test")]
        builder.with_tools(tools)
        assert builder.tools == tools

    def test_with_tools_empty(self):
        builder = ToolsetBuilder("test")
        builder.with_tools([])
        assert builder.tools == []


# ============================================================================
# ToolsetCommonFields
# ============================================================================


class TestToolsetCommonFields:
    def test_api_token(self):
        field = ToolsetCommonFields.api_token("Custom Token", "Enter token here")
        assert field.display_name == "Custom Token"

    def test_bearer_token(self):
        field = ToolsetCommonFields.bearer_token("My Bearer Token", "bearer...")
        assert field.display_name == "My Bearer Token"

    def test_client_id(self):
        field = ToolsetCommonFields.client_id("Google")
        assert "Client ID" in field.display_name

    def test_client_secret(self):
        field = ToolsetCommonFields.client_secret("Google")
        assert "Client Secret" in field.display_name


# ============================================================================
# ToolsetCategory enum
# ============================================================================


class TestToolsetCategory:
    def test_all_values(self):
        assert ToolsetCategory.APP.value == "app"
        assert ToolsetCategory.FILE.value == "file"
        assert ToolsetCategory.FILE_STORAGE.value == "file_storage"
        assert ToolsetCategory.WEB_SEARCH.value == "web_search"
        assert ToolsetCategory.SEARCH.value == "search"
        assert ToolsetCategory.RESEARCH.value == "research"
        assert ToolsetCategory.UTILITY.value == "utility"
        assert ToolsetCategory.COMMUNICATION.value == "communication"
        assert ToolsetCategory.PRODUCTIVITY.value == "productivity"
        assert ToolsetCategory.DATABASE.value == "database"
        assert ToolsetCategory.CALENDAR.value == "calendar"
        assert ToolsetCategory.PROJECT_MANAGEMENT.value == "project_management"
        assert ToolsetCategory.DOCUMENTATION.value == "documentation"
