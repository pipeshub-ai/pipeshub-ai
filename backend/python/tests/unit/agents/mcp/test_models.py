"""Unit tests for app.agents.mcp.models."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

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
    is_web_url,
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
        assert not hasattr(cfg, "env")

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

    def test_its_kind_is_sent_with_it_and_not_read_back(self) -> None:
        tool = MCPToolInfo(name="delete_page", namespaced_name="mcp_reset_tools_delete_page", annotations={"readOnlyHint": True})
        dumped = tool.model_dump(by_alias=True)
        assert (dumped["kind"], dumped["kindSource"]) == ("destructive", "name")
        # Worked out again from the name and hints, whatever a stored copy says.
        again = MCPToolInfo.model_validate({**dumped, "name": "search", "kind": "destructive"})
        assert (again.kind, again.kind_source) == ("read", "server")

    def test_the_namespace_never_sets_the_kind(self) -> None:
        tool = MCPToolInfo(name="search", namespaced_name="mcp_reset_tools_search", annotations={"readOnlyHint": True})
        assert tool.kind == "read"


class TestUtcnow:
    def test_returns_aware_datetime(self) -> None:
        now = utcnow()
        assert now.tzinfo is not None


class TestIsWebUrl:
    @pytest.mark.parametrize("url", [
        "https://auth.example.com/authorize",
        "http://keycloak.internal:8080/realms/acme/protocol/openid-connect/auth",
        "https://auth.example.com/authorize?tenant=acme",
        "  https://auth.example.com/authorize  ",
    ])
    def test_http_and_https_urls_with_a_host_are_web_urls(self, url: str) -> None:
        assert is_web_url(url) is True

    @pytest.mark.parametrize("url", [
        "javascript:alert(document.domain)//",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "/relative/authorize",
        "https://",
        "https://user:pw@auth.example.com/authorize",
        "https://[::1",
        "",
        None,
        42,
    ])
    def test_anything_else_is_not(self, url: object) -> None:
        assert is_web_url(url) is False


class TestOAuthEndpointsOnInstanceConfig:
    def _config(self, **fields: object) -> MCPServerInstanceConfig:
        return MCPServerInstanceConfig(
            name="Custom", transport=MCPTransport.STREAMABLE_HTTP, auth_mode=MCPAuthMode.OAUTH,
            url="https://mcp.example.com/mcp", **fields,
        )

    @pytest.mark.parametrize("field", ["authorization_url", "token_url"])
    def test_a_script_url_is_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="http or https"):
            self._config(**{field: "javascript:alert(1)"})

    def test_web_urls_are_kept_and_blanks_become_none(self) -> None:
        config = self._config(authorization_url="https://auth.example.com/authorize", token_url="")
        assert config.authorization_url == "https://auth.example.com/authorize"
        assert config.token_url is None


class TestInstanceConfigIsBounded:
    """SEC-9: every text field has a limit, and the credential header can't be one HTTP or MCP uses."""

    @staticmethod
    def _config(**overrides: object) -> "MCPServerInstanceConfig":
        from app.agents.mcp.models import MCPServerInstanceConfig

        return MCPServerInstanceConfig.model_validate({
            "name": "Server", "transport": "streamable_http", "authMode": "headers",
            "url": "https://mcp.example.com/mcp", **overrides,
        })

    @pytest.mark.parametrize("field,value", [
        ("name", "n" * 201),
        ("description", "d" * 2001),
        ("url", "https://mcp.example.com/" + "p" * 2048),
        ("command", "c" * 513),
        ("args", ["a"] * 65),
        ("args", ["a" * 2049]),
        ("requiredEnv", ["E" * 129]),
        ("scopes", ["s"] * 65),
    ])
    def test_oversized_fields_are_refused(self, field: str, value: object) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._config(**{field: value})

    @pytest.mark.parametrize("header", ["Host", "content-length", "Mcp-Session-Id", "Transfer-Encoding", "X Bad", "X:Y"])
    def test_a_header_http_or_mcp_uses_or_an_invalid_name_is_refused(self, header: str) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            self._config(headerName=header)

    @pytest.mark.parametrize("header", ["Authorization", "X-Api-Key", "x-goog-api-key"])
    def test_ordinary_credential_headers_are_fine(self, header: str) -> None:
        assert self._config(headerName=header).header_name == header

    def test_a_blank_header_name_means_the_default(self) -> None:
        assert self._config(headerName="  ").header_name is None
