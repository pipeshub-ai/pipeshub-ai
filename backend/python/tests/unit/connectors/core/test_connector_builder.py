"""Tests for connector_builder module: ConnectorScope, SyncStrategy, ConnectorConfigBuilder, ConnectorBuilder, CommonFields."""

from unittest.mock import MagicMock, patch

import pytest

from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthConfig,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorConfigBuilder,
    ConnectorScope,
    SyncStrategy,
)
from app.connectors.core.registry.types import AuthField, CustomField, DocumentationLink


# ============================================================================
# ConnectorScope enum tests
# ============================================================================


class TestConnectorScope:
    def test_personal_value(self):
        assert ConnectorScope.PERSONAL.value == "personal"

    def test_team_value(self):
        assert ConnectorScope.TEAM.value == "team"

    def test_is_str_enum(self):
        assert isinstance(ConnectorScope.PERSONAL, str)

    def test_all_members(self):
        assert len(list(ConnectorScope)) == 2


# ============================================================================
# SyncStrategy enum tests
# ============================================================================


class TestSyncStrategy:
    def test_manual_value(self):
        assert SyncStrategy.MANUAL.value == "MANUAL"

    def test_scheduled_value(self):
        assert SyncStrategy.SCHEDULED.value == "SCHEDULED"

    def test_webhook_value(self):
        assert SyncStrategy.WEBHOOK.value == "WEBHOOK"

    def test_all_members(self):
        assert len(list(SyncStrategy)) == 3


# ============================================================================
# ConnectorConfigBuilder tests
# ============================================================================


class TestConnectorConfigBuilder:
    def test_default_config(self):
        builder = ConnectorConfigBuilder()
        config = builder.build()
        assert config["iconPath"] == "/assets/icons/connectors/default.svg"
        assert config["supportsRealtime"] is False
        assert config["supportsSync"] is True
        assert config["supportsAgent"] is True
        assert config["hideConnector"] is False
        assert config["documentationLinks"] == []
        assert config["auth"]["supportedAuthTypes"] == ["OAUTH"]
        assert config["sync"]["supportedStrategies"] == ["MANUAL"]
        assert config["sync"]["selectedStrategy"] == "MANUAL"

    def test_with_icon(self):
        config = ConnectorConfigBuilder().with_icon("/assets/icons/custom.svg").build()
        assert config["iconPath"] == "/assets/icons/custom.svg"

    def test_with_realtime_support(self):
        config = ConnectorConfigBuilder().with_realtime_support(True, "SSE").build()
        assert config["supportsRealtime"] is True
        assert config["sync"]["realtimeConfig"]["supported"] is True
        assert config["sync"]["realtimeConfig"]["connectionType"] == "SSE"

    def test_with_sync_support_disabled(self):
        config = ConnectorConfigBuilder().with_sync_support(False).build()
        assert config["supportsSync"] is False

    def test_with_agent_support_disabled(self):
        config = ConnectorConfigBuilder().with_agent_support(False).build()
        assert config["supportsAgent"] is False

    def test_with_hide_connector(self):
        config = ConnectorConfigBuilder().with_hide_connector(True).build()
        assert config["hideConnector"] is True

    def test_add_documentation_link(self):
        link = DocumentationLink(title="Guide", url="https://docs.test.com", doc_type="setup")
        config = ConnectorConfigBuilder().add_documentation_link(link).build()
        assert len(config["documentationLinks"]) == 1
        assert config["documentationLinks"][0]["title"] == "Guide"
        assert config["documentationLinks"][0]["url"] == "https://docs.test.com"
        assert config["documentationLinks"][0]["type"] == "setup"

    def test_with_supported_auth_types_string(self):
        config = ConnectorConfigBuilder().with_supported_auth_types("API_TOKEN").build()
        assert config["auth"]["supportedAuthTypes"] == ["API_TOKEN"]

    def test_with_supported_auth_types_list(self):
        config = ConnectorConfigBuilder().with_supported_auth_types(["OAUTH", "API_TOKEN"]).build()
        assert config["auth"]["supportedAuthTypes"] == ["OAUTH", "API_TOKEN"]

    def test_with_supported_auth_types_empty_list_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ConnectorConfigBuilder().with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be str or List"):
            ConnectorConfigBuilder().with_supported_auth_types(123)

    def test_add_supported_auth_type(self):
        builder = ConnectorConfigBuilder()
        builder.add_supported_auth_type("API_TOKEN")
        config = builder.build()
        assert "OAUTH" in config["auth"]["supportedAuthTypes"]
        assert "API_TOKEN" in config["auth"]["supportedAuthTypes"]

    def test_add_supported_auth_type_no_duplicates(self):
        builder = ConnectorConfigBuilder()
        builder.add_supported_auth_type("OAUTH")
        config = builder.build()
        assert config["auth"]["supportedAuthTypes"].count("OAUTH") == 1

    def test_add_auth_field(self):
        field = AuthField(name="token", display_name="Token")
        config = ConnectorConfigBuilder().add_auth_field(field, "API_TOKEN").build()
        assert "API_TOKEN" in config["auth"]["schemas"]
        assert len(config["auth"]["schemas"]["API_TOKEN"]["fields"]) == 1
        assert config["auth"]["schemas"]["API_TOKEN"]["fields"][0]["name"] == "token"

    def test_add_auth_field_no_auth_type_raises(self):
        field = AuthField(name="token", display_name="Token")
        with pytest.raises(ValueError, match="auth_type is required"):
            ConnectorConfigBuilder().add_auth_field(field, "")

    def test_with_sync_strategies(self):
        config = (
            ConnectorConfigBuilder()
            .with_sync_strategies([SyncStrategy.MANUAL, SyncStrategy.SCHEDULED], SyncStrategy.SCHEDULED)
            .build()
        )
        assert config["sync"]["supportedStrategies"] == ["MANUAL", "SCHEDULED"]
        assert config["sync"]["selectedStrategy"] == "SCHEDULED"

    def test_with_webhook_config(self):
        config = (
            ConnectorConfigBuilder()
            .with_webhook_config(supported=True, events=["create", "update"])
            .build()
        )
        assert config["sync"]["webhookConfig"]["supported"] is True
        assert config["sync"]["webhookConfig"]["events"] == ["create", "update"]
        assert "WEBHOOK" in config["sync"]["supportedStrategies"]

    def test_with_scheduled_config(self):
        config = (
            ConnectorConfigBuilder()
            .with_scheduled_config(supported=True, interval_minutes=30)
            .build()
        )
        assert config["sync"]["scheduledConfig"]["intervalMinutes"] == 30
        assert "SCHEDULED" in config["sync"]["supportedStrategies"]

    def test_add_sync_custom_field(self):
        field = CustomField(
            name="batchSize",
            display_name="Batch Size",
            field_type="SELECT",
            description="Batch size desc",
            default_value="50",
            options=["25", "50", "100"],
        )
        config = ConnectorConfigBuilder().add_sync_custom_field(field).build()
        assert len(config["sync"]["customFields"]) == 1
        assert config["sync"]["customFields"][0]["name"] == "batchSize"
        assert config["sync"]["customFields"][0]["options"] == ["25", "50", "100"]

    def test_add_conditional_display(self):
        config = (
            ConnectorConfigBuilder()
            .add_conditional_display("tenantId", "authType", "equals", "OAUTH")
            .build()
        )
        assert "tenantId" in config["auth"]["conditionalDisplay"]
        cond = config["auth"]["conditionalDisplay"]["tenantId"]
        assert cond["showWhen"]["field"] == "authType"
        assert cond["showWhen"]["operator"] == "equals"
        assert cond["showWhen"]["value"] == "OAUTH"

    def test_build_resets_state(self):
        builder = ConnectorConfigBuilder()
        builder.with_icon("/assets/icons/first.svg")
        config1 = builder.build()
        config2 = builder.build()
        assert config1["iconPath"] == "/assets/icons/first.svg"
        assert config2["iconPath"] == "/assets/icons/connectors/default.svg"

    def test_build_returns_deep_copy(self):
        builder = ConnectorConfigBuilder()
        config = builder.build()
        config["auth"]["supportedAuthTypes"].append("API_TOKEN")
        # Builder was reset, so new build should be clean
        config2 = builder.build()
        assert config2["auth"]["supportedAuthTypes"] == ["OAUTH"]

    def test_fluent_chaining(self):
        """Test that all builder methods return self for chaining."""
        builder = ConnectorConfigBuilder()
        result = (
            builder.with_icon("/test.svg")
            .with_realtime_support(True)
            .with_sync_support(True)
            .with_agent_support(True)
            .with_hide_connector(False)
        )
        assert result is builder


# ============================================================================
# ConnectorBuilder tests
# ============================================================================


class TestConnectorBuilder:
    def test_init_defaults(self):
        builder = ConnectorBuilder("TestConnector")
        assert builder.name == "TestConnector"
        assert builder.app_group == ""
        assert builder.supported_auth_types == ["OAUTH"]
        assert builder.app_description == ""
        assert builder.app_categories == []
        assert builder.connector_scopes == []

    def test_in_group(self):
        builder = ConnectorBuilder("Test").in_group("Google Workspace")
        assert builder.app_group == "Google Workspace"
        assert isinstance(builder, ConnectorBuilder)

    def test_with_description(self):
        builder = ConnectorBuilder("Test").with_description("A test connector")
        assert builder.app_description == "A test connector"

    def test_with_categories(self):
        builder = ConnectorBuilder("Test").with_categories(["email", "productivity"])
        assert builder.app_categories == ["email", "productivity"]

    def test_with_scopes(self):
        builder = ConnectorBuilder("Test").with_scopes([ConnectorScope.PERSONAL, ConnectorScope.TEAM])
        assert builder.connector_scopes == [ConnectorScope.PERSONAL, ConnectorScope.TEAM]

    def test_with_info(self):
        builder = ConnectorBuilder("Test").with_info("Some info text")
        assert builder.connector_info == "Some info text"

    def test_with_supported_auth_types_string(self):
        builder = ConnectorBuilder("Test").with_supported_auth_types("API_TOKEN")
        assert builder.supported_auth_types == ["API_TOKEN"]

    def test_with_supported_auth_types_list(self):
        builder = ConnectorBuilder("Test").with_supported_auth_types(["OAUTH", "API_TOKEN"])
        assert builder.supported_auth_types == ["OAUTH", "API_TOKEN"]

    def test_with_supported_auth_types_empty_list_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ConnectorBuilder("Test").with_supported_auth_types([])

    def test_with_supported_auth_types_invalid_type_raises(self):
        with pytest.raises(ValueError, match="must be str or List"):
            ConnectorBuilder("Test").with_supported_auth_types(123)

    def test_with_auth_empty_list_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            ConnectorBuilder("Test").with_auth([])

    def test_with_auth_sets_supported_types(self):
        builder = ConnectorBuilder("Test").with_auth([
            AuthBuilder.type(AuthType.API_TOKEN).fields([
                AuthField(name="token", display_name="Token")
            ]),
        ])
        assert builder.supported_auth_types == ["API_TOKEN"]

    def test_with_auth_multiple_types(self):
        builder = ConnectorBuilder("Test").with_auth([
            AuthBuilder.type(AuthType.API_TOKEN).fields([
                AuthField(name="token", display_name="Token")
            ]),
            AuthBuilder.type(AuthType.BASIC_AUTH).fields([
                AuthField(name="username", display_name="Username"),
                AuthField(name="password", display_name="Password"),
            ]),
        ])
        assert builder.supported_auth_types == ["API_TOKEN", "BASIC_AUTH"]

    def test_configure_method(self):
        def config_func(config_builder):
            return config_builder.with_icon("/custom.svg")

        builder = ConnectorBuilder("Test").configure(config_func)
        assert builder.config_builder.config["iconPath"] == "/custom.svg"

    def test_fluent_chaining(self):
        builder = ConnectorBuilder("Test")
        result = (
            builder.in_group("TestGroup")
            .with_description("desc")
            .with_categories(["cat1"])
            .with_scopes([ConnectorScope.PERSONAL])
            .with_info("info")
        )
        assert result is builder

    @patch("app.connectors.core.registry.connector_builder.get_oauth_config_registry")
    def test_build_decorator_api_token(self, mock_get_registry):
        """Test build_decorator for a simple API token connector."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        builder = (
            ConnectorBuilder("TestAPIConn")
            .in_group("TestGroup")
            .with_description("API connector")
            .with_categories(["api"])
            .with_scopes([ConnectorScope.PERSONAL])
            .with_auth([
                AuthBuilder.type(AuthType.API_TOKEN).fields([
                    AuthField(name="apiToken", display_name="API Token", is_secret=True)
                ])
            ])
        )
        decorator = builder.build_decorator()
        assert callable(decorator)

        # Apply decorator to a test class
        @decorator
        class MyConnector:
            pass

        assert hasattr(MyConnector, "_connector_metadata")
        assert hasattr(MyConnector, "_is_connector")
        assert MyConnector._is_connector is True
        assert MyConnector._connector_metadata["name"] == "TestAPIConn"
        assert MyConnector._connector_metadata["appGroup"] == "TestGroup"
        assert MyConnector._connector_metadata["appDescription"] == "API connector"
        assert MyConnector._connector_metadata["appCategories"] == ["api"]

    @patch("app.connectors.core.registry.connector_builder.get_oauth_config_registry")
    def test_build_decorator_oauth_connector(self, mock_get_registry):
        """Test build_decorator for OAuth connector - validates OAuth requirements."""
        mock_registry = MagicMock()
        mock_registry.get_config.return_value = None
        mock_get_registry.return_value = mock_registry

        builder = (
            ConnectorBuilder("TestOAuthConn")
            .in_group("TestGroup")
            .with_auth([
                AuthBuilder.type(AuthType.OAUTH).oauth(
                    connector_name="TestOAuthConn",
                    authorize_url="https://auth.example.com",
                    token_url="https://token.example.com",
                    redirect_uri="connectors/oauth/callback/TestOAuthConn",
                    scopes=OAuthScopeConfig(personal_sync=["read", "write"]),
                    fields=[
                        AuthField(name="clientId", display_name="Client ID"),
                        AuthField(name="clientSecret", display_name="Client Secret"),
                    ],
                )
            ])
        )
        decorator = builder.build_decorator()

        @decorator
        class OAuthConn:
            pass

        assert OAuthConn._connector_metadata["name"] == "TestOAuthConn"
        assert OAuthConn._connector_metadata["supportedAuthTypes"] == ["OAUTH"]
        # Verify OAuth config was registered
        mock_registry.register.assert_called_once()

    @patch("app.connectors.core.registry.connector_builder.get_oauth_config_registry")
    def test_build_decorator_validates_oauth_missing_config(self, mock_get_registry):
        """Test that build_decorator validates OAuth requirements when no OAuth config is provided."""
        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        builder = (
            ConnectorBuilder("IncompleteOAuth")
            .with_supported_auth_types("OAUTH")
        )
        # Should raise because OAuth config is missing
        with pytest.raises(ValueError, match="OAuth configuration incomplete"):
            builder.build_decorator()


# ============================================================================
# CommonFields tests
# ============================================================================


class TestCommonFields:
    def test_client_id_default(self):
        field = CommonFields.client_id()
        assert field.name == "clientId"
        assert field.display_name == "Client ID"
        assert "OAuth Provider" in field.description
        assert field.placeholder == "Enter your Client ID"

    def test_client_id_custom_provider(self):
        field = CommonFields.client_id("Google")
        assert "Google" in field.description

    def test_client_secret_default(self):
        field = CommonFields.client_secret()
        assert field.name == "clientSecret"
        assert field.display_name == "Client Secret"
        assert field.field_type == "PASSWORD"
        assert field.is_secret is True

    def test_client_secret_custom_provider(self):
        field = CommonFields.client_secret("Microsoft")
        assert "Microsoft" in field.description

    def test_api_token_default(self):
        field = CommonFields.api_token()
        assert field.name == "apiToken"
        assert field.display_name == "API Token"
        assert field.field_type == "PASSWORD"
        assert field.is_secret is True
        assert field.max_length == 2000
        assert field.required is True

    def test_api_token_custom(self):
        field = CommonFields.api_token(
            token_name="Personal Token",
            placeholder="Enter your personal token",
            field_name="personalToken",
            required=False,
        )
        assert field.name == "personalToken"
        assert field.display_name == "Personal Token"
        assert field.placeholder == "Enter your personal token"
        assert field.required is False

    def test_bearer_token(self):
        field = CommonFields.bearer_token()
        assert field.name == "bearerToken"
        assert field.field_type == "PASSWORD"
        assert field.is_secret is True
        assert field.max_length == 8000

    def test_username(self):
        field = CommonFields.username()
        assert field.name == "username"
        assert field.min_length == 3

    def test_password(self):
        field = CommonFields.password()
        assert field.name == "password"
        assert field.field_type == "PASSWORD"
        assert field.is_secret is True
        assert field.min_length == 8

    def test_base_url(self):
        field = CommonFields.base_url("jira")
        assert field.name == "baseUrl"
        assert field.field_type == "URL"
        assert "jira" in field.placeholder
        assert "jira" in field.description

    def test_file_extension_filter(self):
        field = CommonFields.file_extension_filter()
        assert field.name == "file_extensions"
        assert field.display_name == "File Extensions"
        # Should have options from ExtensionTypes enum
        assert len(field.options) > 0

    def test_batch_size_field(self):
        field = CommonFields.batch_size_field()
        assert field.name == "batchSize"
        assert field.field_type == "SELECT"
        assert field.default_value == "50"
        assert "25" in field.options
        assert "50" in field.options
        assert "100" in field.options


# ============================================================================
# DocumentationLink creation tests
# ============================================================================


class TestDocumentationLinkInBuilder:
    def test_add_to_config_builder(self):
        link = DocumentationLink(title="Setup", url="https://setup.example.com", doc_type="setup")
        config = ConnectorConfigBuilder().add_documentation_link(link).build()
        assert len(config["documentationLinks"]) == 1
        assert config["documentationLinks"][0] == {
            "title": "Setup",
            "url": "https://setup.example.com",
            "type": "setup",
        }

    def test_multiple_documentation_links(self):
        link1 = DocumentationLink(title="Setup", url="https://setup.example.com", doc_type="setup")
        link2 = DocumentationLink(title="API Ref", url="https://api.example.com", doc_type="reference")
        config = (
            ConnectorConfigBuilder()
            .add_documentation_link(link1)
            .add_documentation_link(link2)
            .build()
        )
        assert len(config["documentationLinks"]) == 2
