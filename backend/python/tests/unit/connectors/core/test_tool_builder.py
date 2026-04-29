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
from copy import deepcopy
from types import SimpleNamespace
from typing import Dict, List, Optional, Union

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

        assert config["iconPath"] == "/icons/toolsets/default.svg"
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
        assert config2["iconPath"] == "/icons/toolsets/default.svg"

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

# =============================================================================
# Merged from registry/test_tool_builder_coverage.py
# =============================================================================

# ===================================================================
# ToolDefinition._schema_to_parameters edge cases
# ===================================================================


class TestToolDefinitionSchemaToParametersEdgeCases:
    """Cover float, bool, dict type handling and Union types."""

    def test_float_type(self):
        class Schema(BaseModel):
            value: float = Field(description="A float value")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "number" for p in params)

    def test_bool_type(self):
        class Schema(BaseModel):
            flag: bool = Field(description="A boolean flag")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "boolean" for p in params)

    def test_dict_type(self):
        class Schema(BaseModel):
            data: Dict[str, str] = Field(description="A dict")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "object" for p in params)

    def test_optional_int_type(self):
        class Schema(BaseModel):
            count: Optional[int] = Field(default=None, description="An optional int")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        int_param = next(p for p in params if p["name"] == "count")
        assert int_param["type"] == "integer"
        assert int_param["required"] is False
        assert int_param["default"] is None

    def test_optional_list_type(self):
        class Schema(BaseModel):
            items: Optional[List[str]] = Field(default=None, description="Optional list")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        list_param = next(p for p in params if p["name"] == "items")
        assert list_param["type"] == "array"

    def test_no_description_fallback(self):
        class Schema(BaseModel):
            name: str

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any("Parameter name" in p["description"] for p in params)


# ===================================================================
# ToolsetConfigBuilder.add_auth_field
# ===================================================================


class TestToolsetConfigBuilderAddAuthField:
    """Test add_auth_field with and without auth_type."""

    def test_add_auth_field_to_new_auth_type(self):
        from app.connectors.core.registry.types import AuthField

        builder = ToolsetConfigBuilder()
        field = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        builder.add_auth_field(field, auth_type="API_TOKEN")
        config = builder.build()
        assert "API_TOKEN" in config["auth"]["schemas"]
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 1

    def test_add_auth_field_to_existing_auth_type(self):
        from app.connectors.core.registry.types import AuthField

        builder = ToolsetConfigBuilder()
        field1 = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        field2 = AuthField(name="secret", display_name="Secret", field_type="password", required=True)
        builder.add_auth_field(field1, auth_type="API_TOKEN")
        builder.add_auth_field(field2, auth_type="API_TOKEN")
        config = builder.build()
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 2


# ===================================================================
# ToolsetBuilder.with_oauth_config
# ===================================================================


class TestToolsetBuilderWithOauthConfig:
    """Test with_oauth_config on ToolsetBuilder."""

    def test_with_oauth_config_default_auth_type(self):
        from app.connectors.core.registry.auth_builder import OAuthConfig

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["OAUTH"]

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read", "write"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder.with_oauth_config(oauth_config)
        assert "OAUTH" in builder._oauth_configs

    def test_with_oauth_config_explicit_auth_type(self):
        from app.connectors.core.registry.auth_builder import OAuthConfig

        builder = ToolsetBuilder("test_toolset")

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder.with_oauth_config(oauth_config, auth_type="CUSTOM_OAUTH")
        assert "CUSTOM_OAUTH" in builder._oauth_configs


# ===================================================================
# ToolsetBuilder.with_auth
# ===================================================================


class TestToolsetBuilderWithAuth:
    """Test the with_auth() method."""

    def test_with_auth_api_token(self):
        from app.connectors.core.registry.auth_builder import AuthBuilder
        from app.connectors.core.registry.types import AuthField

        field = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        auth_builder = AuthBuilder.type("API_TOKEN").fields([field])

        builder = ToolsetBuilder("test_toolset")
        builder.with_auth([auth_builder])
        assert "API_TOKEN" in builder.supported_auth_types

    def test_with_auth_with_oauth_config(self):
        from app.connectors.core.registry.auth_builder import AuthBuilder, OAuthConfig

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        auth_builder = AuthBuilder.type("OAUTH").oauth_config(oauth_config)

        builder = ToolsetBuilder("test_toolset")
        builder.with_auth([auth_builder])
        assert "OAUTH" in builder.supported_auth_types
        assert "OAUTH" in builder._oauth_configs


# ===================================================================
# ToolsetBuilder._validate_oauth_requirements
# ===================================================================


class TestValidateOauthRequirements:
    """Test the _validate_oauth_requirements method."""

    def test_missing_authorize_url_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_missing_redirect_uri_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_scopes_not_list_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": "not_a_list",
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="must be a list"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_valid_config_does_not_raise(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        # Should not raise
        builder._validate_oauth_requirements(config, "OAUTH")

    def test_fallback_to_top_level_config(self):
        """When auth_type not in oauthConfigs, falls back to top-level."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {},
                "authorizeUrl": "",
                "tokenUrl": "https://example.com/token",
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_top_level_scopes_not_list(self):
        """Top-level scopes (not in oauthConfigs) is not a list."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {},
                "authorizeUrl": "https://example.com/auth",
                "tokenUrl": "https://example.com/token",
                "scopes": "invalid",
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="must be a list"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_none_scopes_does_not_raise(self):
        """None scopes are acceptable (some OAuth providers don't use scopes)."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": None,
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        # Should not raise - None scopes are okay
        builder._validate_oauth_requirements(config, "OAUTH")


# ===================================================================
# ToolsetBuilder._validate_required_auth_fields
# ===================================================================


class TestValidateRequiredAuthFields:
    """Test the _validate_required_auth_fields method."""

    def test_required_field_without_name_raises(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": [{"required": True, "name": ""}]
                    }
                },
                "schema": {"fields": []},
            }
        }
        with pytest.raises(ValueError, match="missing a 'name'"):
            builder._validate_required_auth_fields(config)

    def test_none_auth_type_skipped(self):
        """NONE auth type should be skipped in validation."""
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["NONE"]
        config = {
            "auth": {
                "schemas": {},
                "schema": {"fields": []},
            }
        }
        # Should not raise
        builder._validate_required_auth_fields(config)

    def test_valid_fields_pass(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": [
                            {"required": True, "name": "apiKey"},
                        ]
                    }
                },
                "schema": {"fields": []},
            }
        }
        # Should not raise
        builder._validate_required_auth_fields(config)

    def test_default_schema_used_when_no_type_specific_schema(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {},
                "schema": {
                    "fields": [{"required": True, "name": ""}]
                },
            }
        }
        with pytest.raises(ValueError, match="missing a 'name'"):
            builder._validate_required_auth_fields(config)

    def test_non_dict_field_item_skipped(self):
        """Non-dict items in fields should be skipped (no error)."""
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": ["not_a_dict"]
                    }
                },
                "schema": {"fields": []},
            }
        }
        # Should not raise — non-dict items are silently skipped
        builder._validate_required_auth_fields(config)


# ===================================================================
# ToolsetBuilder.build_decorator
# ===================================================================


class TestBuildDecorator:
    """Test the build_decorator method with various configurations."""

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_with_oauth_configs(self, mock_get_registry, mock_toolset_cls):
        """build_decorator registers OAuth configs and returns Toolset."""
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder.app_group = "test_group"
        builder.category = ToolsetCategory.APP

        result = builder.build_decorator()
        mock_toolset_cls.assert_called_once()

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_renames_oauth_config(self, mock_get_registry, mock_toolset_cls):
        """build_decorator renames oauth config if connector_name differs."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock()
        oauth_config.connector_name = "old_name"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = "/icons/test.svg"
        oauth_config.app_group = "test_group"
        oauth_config.app_description = "Test desc"
        oauth_config.app_categories = ["app"]
        oauth_config.documentation_links = []

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder._oauth_configs = {"OAUTH": oauth_config}

        mock_registry.get_config.return_value = oauth_config
        mock_registry._configs = {"old_name": oauth_config}

        builder.build_decorator()

        assert oauth_config.connector_name == "test_toolset"

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_auto_populates_metadata(self, mock_get_registry, mock_toolset_cls):
        """build_decorator auto-populates oauth config metadata from builder."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock()
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = "/icons/connectors/default.svg"
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder.app_group = "Test Group"
        builder.category = ToolsetCategory.COMMUNICATION
        builder._oauth_configs = {"OAUTH": oauth_config}

        builder.build_decorator()

        assert oauth_config.app_group == "Test Group"
        assert "test_toolset" in oauth_config.app_description
        assert "communication" in oauth_config.app_categories


# ===================================================================
# ToolsetBuilder.configure
# ===================================================================


class TestToolsetBuilderConfigure:
    """Test the configure method."""

    def test_configure_replaces_config_builder(self):
        builder = ToolsetBuilder("test")

        def config_func(cb):
            cb.with_icon("/custom/icon.svg")
            return cb

        builder.configure(config_func)
        config = builder.config_builder.build()
        assert config["iconPath"] == "/custom/icon.svg"

# =============================================================================
# Merged from test_tool_builder_coverage.py
# =============================================================================

# ===================================================================
# ToolDefinition._schema_to_parameters edge cases
# ===================================================================


class TestToolDefinitionSchemaToParametersEdgeCasesCoverage:
    """Cover float, bool, dict type handling and Union types."""

    def test_float_type(self):
        class Schema(BaseModel):
            value: float = Field(description="A float value")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "number" for p in params)

    def test_bool_type(self):
        class Schema(BaseModel):
            flag: bool = Field(description="A boolean flag")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "boolean" for p in params)

    def test_dict_type(self):
        class Schema(BaseModel):
            data: Dict[str, str] = Field(description="A dict")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any(p["type"] == "object" for p in params)

    def test_optional_int_type(self):
        class Schema(BaseModel):
            count: Optional[int] = Field(default=None, description="An optional int")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        int_param = next(p for p in params if p["name"] == "count")
        assert int_param["type"] == "integer"
        assert int_param["required"] is False
        assert int_param["default"] is None

    def test_optional_list_type(self):
        class Schema(BaseModel):
            items: Optional[List[str]] = Field(default=None, description="Optional list")

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        list_param = next(p for p in params if p["name"] == "items")
        assert list_param["type"] == "array"

    def test_no_description_fallback(self):
        class Schema(BaseModel):
            name: str

        td = ToolDefinition(name="t", description="d", args_schema=Schema)
        result = td.to_dict()
        params = result["parameters"]
        assert any("Parameter name" in p["description"] for p in params)


# ===================================================================
# ToolsetConfigBuilder.add_auth_field
# ===================================================================


class TestToolsetConfigBuilderAddAuthFieldCoverage:
    """Test add_auth_field with and without auth_type."""

    def test_add_auth_field_to_new_auth_type(self):
        from app.connectors.core.registry.types import AuthField

        builder = ToolsetConfigBuilder()
        field = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        builder.add_auth_field(field, auth_type="API_TOKEN")
        config = builder.build()
        assert "API_TOKEN" in config["auth"]["schemas"]
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 1

    def test_add_auth_field_to_existing_auth_type(self):
        from app.connectors.core.registry.types import AuthField

        builder = ToolsetConfigBuilder()
        field1 = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        field2 = AuthField(name="secret", display_name="Secret", field_type="password", required=True)
        builder.add_auth_field(field1, auth_type="API_TOKEN")
        builder.add_auth_field(field2, auth_type="API_TOKEN")
        config = builder.build()
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 2


# ===================================================================
# ToolsetBuilder.with_oauth_config
# ===================================================================


class TestToolsetBuilderWithOauthConfigCoverage:
    """Test with_oauth_config on ToolsetBuilder."""

    def test_with_oauth_config_default_auth_type(self):
        from app.connectors.core.registry.auth_builder import OAuthConfig

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["OAUTH"]

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read", "write"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder.with_oauth_config(oauth_config)
        assert "OAUTH" in builder._oauth_configs

    def test_with_oauth_config_explicit_auth_type(self):
        from app.connectors.core.registry.auth_builder import OAuthConfig

        builder = ToolsetBuilder("test_toolset")

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder.with_oauth_config(oauth_config, auth_type="CUSTOM_OAUTH")
        assert "CUSTOM_OAUTH" in builder._oauth_configs


# ===================================================================
# ToolsetBuilder.with_auth
# ===================================================================


class TestToolsetBuilderWithAuthCoverage:
    """Test the with_auth() method."""

    def test_with_auth_api_token(self):
        from app.connectors.core.registry.auth_builder import AuthBuilder
        from app.connectors.core.registry.types import AuthField

        field = AuthField(name="apiKey", display_name="API Key", field_type="password", required=True)
        auth_builder = AuthBuilder.type("API_TOKEN").fields([field])

        builder = ToolsetBuilder("test_toolset")
        builder.with_auth([auth_builder])
        assert "API_TOKEN" in builder.supported_auth_types

    def test_with_auth_with_oauth_config(self):
        from app.connectors.core.registry.auth_builder import AuthBuilder, OAuthConfig

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock(spec=OAuthConfig)
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = ""
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        auth_builder = AuthBuilder.type("OAUTH").oauth_config(oauth_config)

        builder = ToolsetBuilder("test_toolset")
        builder.with_auth([auth_builder])
        assert "OAUTH" in builder.supported_auth_types
        assert "OAUTH" in builder._oauth_configs


# ===================================================================
# ToolsetBuilder._validate_oauth_requirements
# ===================================================================


class TestValidateOauthRequirementsCoverage:
    """Test the _validate_oauth_requirements method."""

    def test_missing_authorize_url_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_missing_redirect_uri_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_scopes_not_list_raises(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": "not_a_list",
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="must be a list"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_valid_config_does_not_raise(self):
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": ["read"],
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        # Should not raise
        builder._validate_oauth_requirements(config, "OAUTH")

    def test_fallback_to_top_level_config(self):
        """When auth_type not in oauthConfigs, falls back to top-level."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {},
                "authorizeUrl": "",
                "tokenUrl": "https://example.com/token",
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="missing"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_top_level_scopes_not_list(self):
        """Top-level scopes (not in oauthConfigs) is not a list."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {},
                "authorizeUrl": "https://example.com/auth",
                "tokenUrl": "https://example.com/token",
                "scopes": "invalid",
                "redirectUri": "https://example.com/callback",
            }
        }
        with pytest.raises(ValueError, match="must be a list"):
            builder._validate_oauth_requirements(config, "OAUTH")

    def test_none_scopes_does_not_raise(self):
        """None scopes are acceptable (some OAuth providers don't use scopes)."""
        builder = ToolsetBuilder("test_toolset")
        config = {
            "auth": {
                "oauthConfigs": {
                    "OAUTH": {
                        "authorizeUrl": "https://example.com/auth",
                        "tokenUrl": "https://example.com/token",
                        "scopes": None,
                    }
                },
                "redirectUri": "https://example.com/callback",
            }
        }
        # Should not raise - None scopes are okay
        builder._validate_oauth_requirements(config, "OAUTH")


# ===================================================================
# ToolsetBuilder._validate_required_auth_fields
# ===================================================================


class TestValidateRequiredAuthFieldsCoverage:
    """Test the _validate_required_auth_fields method."""

    def test_required_field_without_name_raises(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": [{"required": True, "name": ""}]
                    }
                },
                "schema": {"fields": []},
            }
        }
        with pytest.raises(ValueError, match="missing a 'name'"):
            builder._validate_required_auth_fields(config)

    def test_none_auth_type_skipped(self):
        """NONE auth type should be skipped in validation."""
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["NONE"]
        config = {
            "auth": {
                "schemas": {},
                "schema": {"fields": []},
            }
        }
        # Should not raise
        builder._validate_required_auth_fields(config)

    def test_valid_fields_pass(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": [
                            {"required": True, "name": "apiKey"},
                        ]
                    }
                },
                "schema": {"fields": []},
            }
        }
        # Should not raise
        builder._validate_required_auth_fields(config)

    def test_default_schema_used_when_no_type_specific_schema(self):
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {},
                "schema": {
                    "fields": [{"required": True, "name": ""}]
                },
            }
        }
        with pytest.raises(ValueError, match="missing a 'name'"):
            builder._validate_required_auth_fields(config)

    def test_non_dict_field_item_skipped(self):
        """Non-dict items in fields should be skipped (no error)."""
        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        config = {
            "auth": {
                "schemas": {
                    "API_TOKEN": {
                        "fields": ["not_a_dict"]
                    }
                },
                "schema": {"fields": []},
            }
        }
        # Should not raise — non-dict items are silently skipped
        builder._validate_required_auth_fields(config)


# ===================================================================
# ToolsetBuilder.build_decorator
# ===================================================================


class TestBuildDecoratorCoverage:
    """Test the build_decorator method with various configurations."""

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_with_oauth_configs(self, mock_get_registry, mock_toolset_cls):
        """build_decorator registers OAuth configs and returns Toolset."""
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder.app_group = "test_group"
        builder.category = ToolsetCategory.APP

        result = builder.build_decorator()
        mock_toolset_cls.assert_called_once()

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_renames_oauth_config(self, mock_get_registry, mock_toolset_cls):
        """build_decorator renames oauth config if connector_name differs."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock()
        oauth_config.connector_name = "old_name"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = "/icons/test.svg"
        oauth_config.app_group = "test_group"
        oauth_config.app_description = "Test desc"
        oauth_config.app_categories = ["app"]
        oauth_config.documentation_links = []

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder._oauth_configs = {"OAUTH": oauth_config}

        mock_registry.get_config.return_value = oauth_config
        mock_registry._configs = {"old_name": oauth_config}

        builder.build_decorator()

        assert oauth_config.connector_name == "test_toolset"

    @patch("app.agents.registry.toolset_registry.Toolset")
    @patch("app.connectors.core.registry.tool_builder.get_oauth_config_registry")
    def test_build_decorator_auto_populates_metadata(self, mock_get_registry, mock_toolset_cls):
        """build_decorator auto-populates oauth config metadata from builder."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry
        mock_toolset_cls.return_value = MagicMock()

        scopes = MagicMock()
        scopes.get_all_scopes.return_value = ["read"]

        oauth_config = MagicMock()
        oauth_config.connector_name = "test_toolset"
        oauth_config.authorize_url = "https://example.com/auth"
        oauth_config.token_url = "https://example.com/token"
        oauth_config.redirect_uri = "https://example.com/callback"
        oauth_config.scopes = scopes
        oauth_config.auth_fields = []
        oauth_config.icon_path = "/icons/connectors/default.svg"
        oauth_config.app_group = ""
        oauth_config.app_description = ""
        oauth_config.app_categories = []
        oauth_config.documentation_links = []

        builder = ToolsetBuilder("test_toolset")
        builder.supported_auth_types = ["API_TOKEN"]
        builder.app_group = "Test Group"
        builder.category = ToolsetCategory.COMMUNICATION
        builder._oauth_configs = {"OAUTH": oauth_config}

        builder.build_decorator()

        assert oauth_config.app_group == "Test Group"
        assert "test_toolset" in oauth_config.app_description
        assert "communication" in oauth_config.app_categories


# ===================================================================
# ToolsetBuilder.configure
# ===================================================================


class TestToolsetBuilderConfigureCoverage:
    """Test the configure method."""

    def test_configure_replaces_config_builder(self):
        builder = ToolsetBuilder("test")

        def config_func(cb):
            cb.with_icon("/custom/icon.svg")
            return cb

        builder.configure(config_func)
        config = builder.config_builder.build()
        assert config["iconPath"] == "/custom/icon.svg"
