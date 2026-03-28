"""
Extended tests for tool_builder.py to reach 85%+ coverage.
Targets: build_decorator, _validate_oauth_requirements, _validate_required_auth_fields,
with_oauth_config, with_auth, and ToolDefinition._schema_to_parameters edge cases.
"""

from copy import deepcopy
from types import SimpleNamespace
from typing import Dict, List, Optional, Union
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
        oauth_config.icon_path = "/assets/icons/test.svg"
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
        oauth_config.icon_path = "/assets/icons/connectors/default.svg"
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
