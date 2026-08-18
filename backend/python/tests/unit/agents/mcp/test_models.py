"""Unit tests for app.agents.mcp.models."""
from datetime import datetime, timedelta, timezone

from app.agents.mcp.models import (
    AuthHint,
    DCRClient,
    DiscoveredOAuthMetadata,
    MCPAuthMode,
    MCPServerConfig,
    MCPServerInstanceConfig,
    MCPServerTemplate,
    MCPToolInfo,
    MCPTransport,
    OAuthTokens,
    utcnow,
)


class TestCamelCaseAliasing:
    def test_template_dumps_camel_case(self) -> None:
        template = MCPServerTemplate(
            type_id="brave_search",
            display_name="Brave Search",
            description="Web search",
            transport=MCPTransport.STDIO,
            default_auth_mode=MCPAuthMode.API_TOKEN,
        )
        dumped = template.model_dump(by_alias=True)
        assert dumped["typeId"] == "brave_search"
        assert dumped["displayName"] == "Brave Search"
        assert dumped["defaultAuthMode"] == "api_token"

    def test_template_accepts_camel_case_input(self) -> None:
        template = MCPServerTemplate(
            typeId="exa",
            displayName="Exa",
            description="Search",
            transport="stdio",
            defaultAuthMode="api_token",
        )
        assert template.type_id == "exa"
        assert template.display_name == "Exa"

    def test_template_accepts_snake_case_input_populate_by_name(self) -> None:
        template = MCPServerTemplate(
            type_id="exa",
            display_name="Exa",
            description="Search",
            transport="stdio",
            default_auth_mode="api_token",
        )
        assert template.type_id == "exa"

    def test_extra_fields_ignored(self) -> None:
        template = MCPServerTemplate(
            type_id="exa",
            display_name="Exa",
            description="Search",
            transport="stdio",
            default_auth_mode="api_token",
            unknownField="whatever",
        )
        assert not hasattr(template, "unknown_field")


class TestAuthHint:
    def test_minimal(self) -> None:
        hint = AuthHint(label="API key")
        assert hint.placeholder is None
        assert hint.help_text is None


class TestMCPServerInstanceConfig:
    def test_defaults(self) -> None:
        cfg = MCPServerInstanceConfig(name="My server", transport=MCPTransport.SSE, auth_mode=MCPAuthMode.NONE)
        assert cfg.use_admin_auth is False
        assert cfg.args == []
        assert cfg.env == {}

    def test_camel_case_round_trip(self) -> None:
        cfg = MCPServerInstanceConfig.model_validate(
            {
                "name": "Custom",
                "transport": "stdio",
                "authMode": "api_token",
                "useAdminAuth": True,
                "headerName": "X-Api-Key",
            }
        )
        assert cfg.use_admin_auth is True
        assert cfg.header_name == "X-Api-Key"


class TestMCPServerConfig:
    def test_id_alias_underscore(self) -> None:
        config = MCPServerConfig(
            _id="inst-1",
            org_id="org-1",
            created_by="user-1",
            name="Server",
            transport=MCPTransport.STREAMABLE_HTTP,
            auth_mode=MCPAuthMode.NONE,
            created_at=1000,
            updated_at=1000,
        )
        assert config.id == "inst-1"
        dumped = config.model_dump(by_alias=True)
        assert dumped["_id"] == "inst-1"

    def test_id_settable_by_field_name(self) -> None:
        config = MCPServerConfig(
            id="inst-2",
            org_id="org-1",
            created_by="user-1",
            name="Server",
            transport=MCPTransport.SSE,
            auth_mode=MCPAuthMode.NONE,
            created_at=1000,
            updated_at=1000,
        )
        assert config.id == "inst-2"


class TestOAuthTokens:
    def test_no_expiry_never_expired(self) -> None:
        tokens = OAuthTokens(access_token="tok")
        assert tokens.is_expired is False
        assert tokens.expires_at_epoch is None

    def test_expired_when_past_expiry(self) -> None:
        created = datetime.now(timezone.utc) - timedelta(seconds=100)
        tokens = OAuthTokens(access_token="tok", expires_in=50, created_at=created)
        assert tokens.is_expired is True

    def test_not_expired_within_window(self) -> None:
        created = datetime.now(timezone.utc)
        tokens = OAuthTokens(access_token="tok", expires_in=3600, created_at=created)
        assert tokens.is_expired is False

    def test_expires_at_epoch_matches_created_plus_expires_in(self) -> None:
        created = datetime.now(timezone.utc)
        tokens = OAuthTokens(access_token="tok", expires_in=100, created_at=created)
        expected = int((created + timedelta(seconds=100)).timestamp())
        assert tokens.expires_at_epoch == expected

    def test_naive_created_at_treated_as_utc(self) -> None:
        naive_created = datetime.now(timezone.utc).replace(tzinfo=None)
        tokens = OAuthTokens(access_token="tok", expires_in=3600, created_at=naive_created)
        # Should not raise despite tz-naive created_at.
        assert isinstance(tokens.is_expired, bool)

    def test_serializes_created_at_to_json_string(self) -> None:
        tokens = OAuthTokens(access_token="tok")
        dumped = tokens.model_dump(by_alias=True, mode="json")
        assert isinstance(dumped["createdAt"], str)


class TestDiscoveredOAuthMetadata:
    def test_is_usable_requires_both_endpoints(self) -> None:
        assert DiscoveredOAuthMetadata(
            authorization_endpoint="https://example.com/authorize",
            token_endpoint="https://example.com/token",
        ).is_usable is True
        assert DiscoveredOAuthMetadata(
            authorization_endpoint="https://example.com/authorize",
        ).is_usable is False
        assert DiscoveredOAuthMetadata(
            token_endpoint="https://example.com/token",
        ).is_usable is False
        assert DiscoveredOAuthMetadata().is_usable is False

    def test_supports_dcr_when_registration_endpoint_present(self) -> None:
        assert DiscoveredOAuthMetadata(
            registration_endpoint="https://example.com/register",
        ).supports_dcr is True
        assert DiscoveredOAuthMetadata().supports_dcr is False


class TestDCRClient:
    def test_minimal(self) -> None:
        client = DCRClient(
            client_id="cid",
            authorization_url="https://example.com/authorize",
            token_url="https://example.com/token",
            registered_at=1000,
        )
        assert client.client_secret is None


class TestMCPToolInfo:
    def test_defaults(self) -> None:
        tool = MCPToolInfo(name="search", namespaced_name="mcp_brave_search_search")
        assert tool.description is None
        assert tool.input_schema == {}


class TestUtcnow:
    def test_returns_aware_datetime(self) -> None:
        now = utcnow()
        assert now.tzinfo is not None
