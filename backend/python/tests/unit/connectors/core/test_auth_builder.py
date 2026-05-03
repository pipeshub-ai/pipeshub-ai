"""Tests for auth_builder module: AuthType, OAuthScopeType, OAuthScopeConfig, OAuthConfig, AuthBuilder."""

import pytest

from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthConfig,
    OAuthScopeConfig,
    OAuthScopeType,
)
from app.connectors.core.registry.types import AuthField, DocumentationLink


# ============================================================================
# AuthType tests
# ============================================================================


class TestAuthType:
    """Test AuthType constant values."""

    def test_oauth(self):
        assert AuthType.OAUTH == "OAUTH"

    def test_api_token(self):
        assert AuthType.API_TOKEN == "API_TOKEN"

    def test_bearer_token(self):
        assert AuthType.BEARER_TOKEN == "BEARER_TOKEN"

    def test_basic_auth(self):
        assert AuthType.BASIC_AUTH == "BASIC_AUTH"

    def test_access_key(self):
        assert AuthType.ACCESS_KEY == "ACCESS_KEY"

    def test_account_key(self):
        assert AuthType.ACCOUNT_KEY == "ACCOUNT_KEY"

    def test_connection_string(self):
        assert AuthType.CONNECTION_STRING == "CONNECTION_STRING"

    def test_oauth_admin_consent(self):
        assert AuthType.OAUTH_ADMIN_CONSENT == "OAUTH_ADMIN_CONSENT"

    def test_custom(self):
        assert AuthType.CUSTOM == "CUSTOM"


# ============================================================================
# OAuthScopeType enum tests
# ============================================================================


class TestOAuthScopeType:
    """Test OAuthScopeType enum values."""

    def test_personal_sync_value(self):
        assert OAuthScopeType.PERSONAL_SYNC.value == "personal_sync"

    def test_team_sync_value(self):
        assert OAuthScopeType.TEAM_SYNC.value == "team_sync"

    def test_agent_value(self):
        assert OAuthScopeType.AGENT.value == "agent"

    def test_is_str_enum(self):
        assert isinstance(OAuthScopeType.PERSONAL_SYNC, str)

    def test_all_members(self):
        members = list(OAuthScopeType)
        assert len(members) == 3


# ============================================================================
# OAuthScopeConfig tests
# ============================================================================


class TestOAuthScopeConfig:
    """Test OAuthScopeConfig dataclass."""

    def test_default_empty_scopes(self):
        config = OAuthScopeConfig()
        assert config.personal_sync == []
        assert config.team_sync == []
        assert config.agent == []

    def test_init_with_scopes(self):
        config = OAuthScopeConfig(
            personal_sync=["read", "write"],
            team_sync=["admin"],
            agent=["execute"],
        )
        assert config.personal_sync == ["read", "write"]
        assert config.team_sync == ["admin"]
        assert config.agent == ["execute"]

    def test_get_scopes_for_type_personal_sync(self):
        config = OAuthScopeConfig(personal_sync=["scope1", "scope2"])
        result = config.get_scopes_for_type(OAuthScopeType.PERSONAL_SYNC)
        assert result == ["scope1", "scope2"]

    def test_get_scopes_for_type_team_sync(self):
        config = OAuthScopeConfig(team_sync=["team_scope"])
        result = config.get_scopes_for_type(OAuthScopeType.TEAM_SYNC)
        assert result == ["team_scope"]

    def test_get_scopes_for_type_agent(self):
        config = OAuthScopeConfig(agent=["agent_scope"])
        result = config.get_scopes_for_type(OAuthScopeType.AGENT)
        assert result == ["agent_scope"]

    def test_get_scopes_for_unknown_type_returns_empty(self):
        """If somehow a non-matching value is passed, returns empty list."""
        config = OAuthScopeConfig(personal_sync=["scope1"])
        # Create a mock scope type that doesn't match any branch
        result = config.get_scopes_for_type("unknown_type")
        assert result == []

    def test_get_all_scopes_deduplication(self):
        config = OAuthScopeConfig(
            personal_sync=["read", "write"],
            team_sync=["read", "admin"],
            agent=["write", "execute"],
        )
        result = config.get_all_scopes()
        assert result == sorted(["read", "write", "admin", "execute"])

    def test_get_all_scopes_empty(self):
        config = OAuthScopeConfig()
        result = config.get_all_scopes()
        assert result == []

    def test_get_all_scopes_sorted(self):
        config = OAuthScopeConfig(personal_sync=["z_scope", "a_scope"])
        result = config.get_all_scopes()
        assert result == ["a_scope", "z_scope"]

    def test_to_dict(self):
        config = OAuthScopeConfig(
            personal_sync=["ps1"],
            team_sync=["ts1"],
            agent=["ag1"],
        )
        d = config.to_dict()
        assert d == {
            "personal_sync": ["ps1"],
            "team_sync": ["ts1"],
            "agent": ["ag1"],
        }

    def test_from_dict(self):
        data = {
            "personal_sync": ["ps1"],
            "team_sync": ["ts1"],
            "agent": ["ag1"],
        }
        config = OAuthScopeConfig.from_dict(data)
        assert config.personal_sync == ["ps1"]
        assert config.team_sync == ["ts1"]
        assert config.agent == ["ag1"]

    def test_from_dict_missing_keys_uses_defaults(self):
        config = OAuthScopeConfig.from_dict({})
        assert config.personal_sync == []
        assert config.team_sync == []
        assert config.agent == []

    def test_roundtrip_to_dict_from_dict(self):
        original = OAuthScopeConfig(
            personal_sync=["a", "b"],
            team_sync=["c"],
            agent=["d", "e"],
        )
        restored = OAuthScopeConfig.from_dict(original.to_dict())
        assert restored.personal_sync == original.personal_sync
        assert restored.team_sync == original.team_sync
        assert restored.agent == original.agent


# ============================================================================
# OAuthConfig tests
# ============================================================================


class TestOAuthConfig:
    """Test OAuthConfig dataclass."""

    def _make_config(self, **overrides):
        defaults = {
            "connector_name": "TestConnector",
            "authorize_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
            "redirect_uri": "connectors/oauth/callback/TestConnector",
        }
        defaults.update(overrides)
        return OAuthConfig(**defaults)

    def test_minimal_creation(self):
        config = self._make_config()
        assert config.connector_name == "TestConnector"
        assert config.authorize_url == "https://example.com/auth"
        assert config.token_url == "https://example.com/token"
        assert config.redirect_uri == "connectors/oauth/callback/TestConnector"

    def test_default_values(self):
        config = self._make_config()
        assert isinstance(config.scopes, OAuthScopeConfig)
        assert config.auth_fields == []
        assert config.token_access_type is None
        assert config.additional_params == {}
        assert config.scope_parameter_name == "scope"
        assert config.token_response_path is None
        assert config.icon_path == "/icons/connectors/default.svg"
        assert config.app_group == ""
        assert config.app_description == ""
        assert config.app_categories == []
        assert config.documentation_links == []

    def test_with_auth_fields(self):
        field = AuthField(name="clientId", display_name="Client ID")
        config = self._make_config(auth_fields=[field])
        assert len(config.auth_fields) == 1
        assert config.auth_fields[0].name == "clientId"

    def test_with_documentation_links(self):
        link = DocumentationLink(title="Setup", url="https://docs.example.com", doc_type="setup")
        config = self._make_config(documentation_links=[link])
        assert len(config.documentation_links) == 1
        assert config.documentation_links[0].title == "Setup"

    def test_to_dict(self):
        field = AuthField(name="clientId", display_name="Client ID")
        link = DocumentationLink(title="Guide", url="https://docs.example.com", doc_type="guide")
        config = self._make_config(
            auth_fields=[field],
            token_access_type="offline",
            additional_params={"access_type": "offline"},
            scope_parameter_name="user_scope",
            token_response_path="authed_user",
            icon_path="/icons/test.svg",
            app_group="TestGroup",
            app_description="Test app",
            app_categories=["category1"],
            documentation_links=[link],
        )
        d = config.to_dict()
        assert d["connector_name"] == "TestConnector"
        assert d["authorize_url"] == "https://example.com/auth"
        assert d["token_url"] == "https://example.com/token"
        assert d["redirect_uri"] == "connectors/oauth/callback/TestConnector"
        assert d["token_access_type"] == "offline"
        assert d["additional_params"] == {"access_type": "offline"}
        assert d["scope_parameter_name"] == "user_scope"
        assert d["token_response_path"] == "authed_user"
        assert d["icon_path"] == "/icons/test.svg"
        assert d["app_group"] == "TestGroup"
        assert d["app_description"] == "Test app"
        assert d["app_categories"] == ["category1"]
        assert len(d["auth_fields"]) == 1
        assert d["auth_fields"][0]["name"] == "clientId"
        assert len(d["documentation_links"]) == 1
        assert d["documentation_links"][0]["title"] == "Guide"

    def test_from_dict_minimal(self):
        data = {
            "connector_name": "TestConn",
            "authorize_url": "https://auth.test.com",
            "token_url": "https://token.test.com",
        }
        config = OAuthConfig.from_dict(data)
        assert config.connector_name == "TestConn"
        assert config.authorize_url == "https://auth.test.com"
        assert config.redirect_uri == ""

    def test_from_dict_with_auth_fields(self):
        data = {
            "connector_name": "TestConn",
            "authorize_url": "https://auth.test.com",
            "token_url": "https://token.test.com",
            "auth_fields": [
                {
                    "name": "clientId",
                    "display_name": "Client ID",
                    "field_type": "TEXT",
                    "placeholder": "Enter ID",
                    "description": "OAuth client ID",
                    "required": True,
                    "usage": "BOTH",
                    "default_value": "",
                    "min_length": 1,
                    "max_length": 500,
                    "is_secret": False,
                }
            ],
        }
        config = OAuthConfig.from_dict(data)
        assert len(config.auth_fields) == 1
        assert config.auth_fields[0].name == "clientId"
        assert config.auth_fields[0].display_name == "Client ID"
        assert config.auth_fields[0].max_length == 500

    def test_from_dict_with_documentation_links(self):
        data = {
            "connector_name": "TestConn",
            "authorize_url": "https://auth.test.com",
            "token_url": "https://token.test.com",
            "documentation_links": [
                {"title": "Setup Guide", "url": "https://docs.test.com", "type": "setup"}
            ],
        }
        config = OAuthConfig.from_dict(data)
        assert len(config.documentation_links) == 1
        assert config.documentation_links[0].title == "Setup Guide"
        assert config.documentation_links[0].doc_type == "setup"

    def test_from_dict_documentation_links_with_doc_type_key(self):
        """Test backward compat: doc_type key in documentation links."""
        data = {
            "connector_name": "TestConn",
            "authorize_url": "https://auth.test.com",
            "token_url": "https://token.test.com",
            "documentation_links": [
                {"title": "Guide", "url": "https://docs.test.com", "doc_type": "guide"}
            ],
        }
        config = OAuthConfig.from_dict(data)
        assert config.documentation_links[0].doc_type == "guide"

    def test_from_dict_token_response_path_legacy_key(self):
        """Test backward compat: tokenResponsePath camelCase key."""
        data = {
            "connector_name": "TestConn",
            "authorize_url": "https://auth.test.com",
            "token_url": "https://token.test.com",
            "tokenResponsePath": "authed_user",
        }
        config = OAuthConfig.from_dict(data)
        assert config.token_response_path == "authed_user"

    def test_roundtrip_to_dict_from_dict(self):
        field = AuthField(name="clientId", display_name="Client ID")
        link = DocumentationLink(title="Guide", url="https://docs.test.com", doc_type="guide")
        original = self._make_config(
            auth_fields=[field],
            scopes=OAuthScopeConfig(personal_sync=["scope1"]),
            documentation_links=[link],
        )
        restored = OAuthConfig.from_dict(original.to_dict())
        assert restored.connector_name == original.connector_name
        assert restored.authorize_url == original.authorize_url
        assert len(restored.auth_fields) == 1
        assert restored.auth_fields[0].name == "clientId"


# ============================================================================
# AuthBuilder tests
# ============================================================================


class TestAuthBuilder:
    """Test AuthBuilder fluent interface."""

    def test_type_creates_builder(self):
        builder = AuthBuilder.type(AuthType.OAUTH)
        assert isinstance(builder, AuthBuilder)
        assert builder.auth_type == "OAUTH"

    def test_type_with_string(self):
        builder = AuthBuilder.type("api_token")
        assert builder.auth_type == "API_TOKEN"

    def test_type_uppercases_string(self):
        builder = AuthBuilder.type("bearer_token")
        assert builder.auth_type == "BEARER_TOKEN"

    def test_type_with_enum_having_value(self):
        """Test that type() handles objects with .value attribute."""
        builder = AuthBuilder.type(OAuthScopeType.PERSONAL_SYNC)
        assert builder.auth_type == "PERSONAL_SYNC"

    def test_fields_method(self):
        field1 = AuthField(name="token", display_name="API Token")
        field2 = AuthField(name="key", display_name="API Key")
        builder = AuthBuilder.type(AuthType.API_TOKEN).fields([field1, field2])
        assert isinstance(builder, AuthBuilder)
        result = builder.build()
        assert len(result["fields"]) == 2

    def test_add_field_method(self):
        field = AuthField(name="token", display_name="API Token")
        builder = AuthBuilder.type(AuthType.API_TOKEN).add_field(field)
        assert isinstance(builder, AuthBuilder)
        result = builder.build()
        assert len(result["fields"]) == 1
        assert result["fields"][0].name == "token"

    def test_oauth_method_returns_self(self):
        builder = AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
        )
        assert isinstance(builder, AuthBuilder)

    def test_oauth_creates_oauth_config(self):
        builder = AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
            scopes=OAuthScopeConfig(personal_sync=["read"]),
        )
        config = builder.get_oauth_config()
        assert config is not None
        assert config.connector_name == "TestConn"
        assert config.authorize_url == "https://auth.example.com"
        assert config.scopes.personal_sync == ["read"]

    def test_oauth_with_all_params(self):
        link = DocumentationLink(title="Guide", url="https://docs.test.com", doc_type="setup")
        field = AuthField(name="clientId", display_name="Client ID")
        builder = AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
            scopes=OAuthScopeConfig(personal_sync=["read"]),
            fields=[field],
            token_access_type="offline",
            additional_params={"access_type": "offline"},
            scope_parameter_name="user_scope",
            token_response_path="authed_user",
            icon_path="/icons/test.svg",
            app_group="TestGroup",
            app_description="Test desc",
            app_categories=["cat1"],
            documentation_links=[link],
        )
        config = builder.get_oauth_config()
        assert config.token_access_type == "offline"
        assert config.additional_params == {"access_type": "offline"}
        assert config.scope_parameter_name == "user_scope"
        assert config.token_response_path == "authed_user"
        assert config.icon_path == "/icons/test.svg"
        assert config.app_group == "TestGroup"
        assert config.app_description == "Test desc"
        assert config.app_categories == ["cat1"]
        assert len(config.documentation_links) == 1

    def test_oauth_defaults_none_params(self):
        builder = AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
        )
        config = builder.get_oauth_config()
        assert isinstance(config.scopes, OAuthScopeConfig)
        assert config.auth_fields == []
        assert config.additional_params == {}
        assert config.scope_parameter_name == "scope"
        assert config.icon_path == "/icons/connectors/default.svg"
        assert config.app_group == ""
        assert config.app_description == ""
        assert config.app_categories == []
        assert config.documentation_links == []

    def test_oauth_config_method_backward_compat(self):
        existing_config = OAuthConfig(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
        )
        builder = AuthBuilder.type(AuthType.OAUTH).oauth_config(existing_config)
        assert builder.get_oauth_config() is existing_config

    def test_build_basic(self):
        field = AuthField(name="token", display_name="API Token")
        result = AuthBuilder.type(AuthType.API_TOKEN).fields([field]).build()
        assert result["auth_type"] == "API_TOKEN"
        assert len(result["fields"]) == 1
        assert result["fields"][0].name == "token"
        assert "oauth_config" not in result

    def test_build_with_oauth(self):
        field = AuthField(name="clientId", display_name="Client ID")
        result = (
            AuthBuilder.type(AuthType.OAUTH)
            .oauth(
                connector_name="TestConn",
                authorize_url="https://auth.example.com",
                token_url="https://token.example.com",
                redirect_uri="connectors/oauth/callback/TestConn",
                fields=[field],
            )
            .build()
        )
        assert result["auth_type"] == "OAUTH"
        assert "oauth_config" in result
        # OAuth config fields take precedence
        assert len(result["fields"]) == 1
        assert result["fields"][0].name == "clientId"

    def test_build_oauth_fields_take_precedence_over_manual(self):
        """When oauth_config has fields and manual fields are set, oauth fields win."""
        manual_field = AuthField(name="manual", display_name="Manual Field")
        oauth_field = AuthField(name="clientId", display_name="Client ID")
        result = (
            AuthBuilder.type(AuthType.OAUTH)
            .fields([manual_field])
            .oauth(
                connector_name="TestConn",
                authorize_url="https://auth.example.com",
                token_url="https://token.example.com",
                redirect_uri="connectors/oauth/callback/TestConn",
                fields=[oauth_field],
            )
            .build()
        )
        # OAuth config fields take precedence
        assert len(result["fields"]) == 1
        assert result["fields"][0].name == "clientId"

    def test_build_no_oauth_fields_uses_manual(self):
        """When oauth_config has no fields, manual fields are used."""
        manual_field = AuthField(name="manual", display_name="Manual Field")
        result = (
            AuthBuilder.type(AuthType.OAUTH)
            .fields([manual_field])
            .oauth(
                connector_name="TestConn",
                authorize_url="https://auth.example.com",
                token_url="https://token.example.com",
                redirect_uri="connectors/oauth/callback/TestConn",
                # No fields in oauth
            )
            .build()
        )
        # Manual fields are used since oauth has no auth_fields
        assert len(result["fields"]) == 1
        assert result["fields"][0].name == "manual"

    def test_build_returns_copy_of_fields(self):
        field = AuthField(name="token", display_name="Token")
        builder = AuthBuilder.type(AuthType.API_TOKEN).fields([field])
        result = builder.build()
        # Modifying result should not affect builder
        result["fields"].append(AuthField(name="extra", display_name="Extra"))
        assert len(builder._fields) == 1

    def test_get_auth_type(self):
        builder = AuthBuilder.type(AuthType.BEARER_TOKEN)
        assert builder.get_auth_type() == "BEARER_TOKEN"

    def test_get_fields_returns_oauth_fields_if_available(self):
        oauth_field = AuthField(name="clientId", display_name="Client ID")
        builder = AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="TestConn",
            authorize_url="https://auth.example.com",
            token_url="https://token.example.com",
            redirect_uri="connectors/oauth/callback/TestConn",
            fields=[oauth_field],
        )
        fields = builder.get_fields()
        assert len(fields) == 1
        assert fields[0].name == "clientId"

    def test_get_fields_returns_manual_fields_when_no_oauth(self):
        manual_field = AuthField(name="token", display_name="Token")
        builder = AuthBuilder.type(AuthType.API_TOKEN).fields([manual_field])
        fields = builder.get_fields()
        assert len(fields) == 1
        assert fields[0].name == "token"

    def test_get_fields_returns_copy(self):
        field = AuthField(name="token", display_name="Token")
        builder = AuthBuilder.type(AuthType.API_TOKEN).fields([field])
        fields = builder.get_fields()
        fields.append(AuthField(name="extra", display_name="Extra"))
        assert len(builder._fields) == 1

    def test_get_oauth_config_returns_none_when_not_set(self):
        builder = AuthBuilder.type(AuthType.API_TOKEN)
        assert builder.get_oauth_config() is None


# ============================================================================
# AuthField creation and serialization tests
# ============================================================================


class TestAuthField:
    """Test AuthField dataclass creation and serialization."""

    def test_minimal_creation(self):
        field = AuthField(name="clientId", display_name="Client ID")
        assert field.name == "clientId"
        assert field.display_name == "Client ID"

    def test_default_values(self):
        field = AuthField(name="test", display_name="Test")
        assert field.field_type == "TEXT"
        assert field.placeholder == ""
        assert field.description == ""
        assert field.required is True
        assert field.default_value == ""
        assert field.min_length == 1
        assert field.max_length == 1000
        assert field.is_secret is False
        assert field.usage == "BOTH"

    def test_full_creation(self):
        field = AuthField(
            name="clientSecret",
            display_name="Client Secret",
            field_type="PASSWORD",
            placeholder="Enter secret",
            description="OAuth2 client secret",
            required=True,
            default_value="",
            min_length=5,
            max_length=500,
            is_secret=True,
            usage="AUTHENTICATE",
        )
        assert field.name == "clientSecret"
        assert field.field_type == "PASSWORD"
        assert field.is_secret is True
        assert field.usage == "AUTHENTICATE"
        assert field.min_length == 5
        assert field.max_length == 500

    def test_usage_options(self):
        for usage in ["CONFIGURE", "AUTHENTICATE", "BOTH"]:
            field = AuthField(name="test", display_name="Test", usage=usage)
            assert field.usage == usage


# ============================================================================
# DocumentationLink tests
# ============================================================================


class TestDocumentationLink:
    """Test DocumentationLink dataclass."""

    def test_creation(self):
        link = DocumentationLink(
            title="Setup Guide",
            url="https://docs.example.com/setup",
            doc_type="setup",
        )
        assert link.title == "Setup Guide"
        assert link.url == "https://docs.example.com/setup"
        assert link.doc_type == "setup"
