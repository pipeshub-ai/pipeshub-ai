"""Unit tests for app.agents.mcp.models.

Covers enum correctness, camelCase alias serialisation, field defaults,
and construction of every Pydantic model in the module.
"""

import pytest


class TestMCPTransportEnum:
    def test_stdio_value(self):
        from app.agents.mcp.models import MCPTransport

        assert MCPTransport.STDIO.value == "stdio"

    def test_sse_value(self):
        from app.agents.mcp.models import MCPTransport

        assert MCPTransport.SSE.value == "sse"

    def test_streamable_http_value(self):
        from app.agents.mcp.models import MCPTransport

        assert MCPTransport.STREAMABLE_HTTP.value == "streamable_http"

    def test_is_string_comparable(self):
        from app.agents.mcp.models import MCPTransport

        assert MCPTransport.STDIO == "stdio"
        assert MCPTransport.SSE == "sse"
        assert MCPTransport.STREAMABLE_HTTP == "streamable_http"

    def test_membership(self):
        from app.agents.mcp.models import MCPTransport

        assert "stdio" in [t.value for t in MCPTransport]
        assert "sse" in [t.value for t in MCPTransport]
        assert "streamable_http" in [t.value for t in MCPTransport]


class TestMCPAuthModeEnum:
    def test_none_value(self):
        from app.agents.mcp.models import MCPAuthMode

        assert MCPAuthMode.NONE.value == "none"

    def test_api_token_value(self):
        from app.agents.mcp.models import MCPAuthMode

        assert MCPAuthMode.API_TOKEN.value == "api_token"

    def test_oauth_value(self):
        from app.agents.mcp.models import MCPAuthMode

        assert MCPAuthMode.OAUTH.value == "oauth"

    def test_headers_value(self):
        from app.agents.mcp.models import MCPAuthMode

        assert MCPAuthMode.HEADERS.value == "headers"

    def test_is_string_comparable(self):
        from app.agents.mcp.models import MCPAuthMode

        assert MCPAuthMode.NONE == "none"
        assert MCPAuthMode.API_TOKEN == "api_token"


class TestAuthHint:
    def test_default_values(self):
        from app.agents.mcp.models import AuthHint

        hint = AuthHint()

        assert hint.methods == ["api_key"]
        assert hint.default_method == "api_key"
        assert hint.oauth2_authorization_url == ""
        assert hint.oauth2_token_url == ""
        assert hint.oauth2_scopes == []
        assert hint.env_mapping == {}

    def test_camelcase_serialisation(self):
        from app.agents.mcp.models import AuthHint

        hint = AuthHint(
            methods=["api_key", "oauth2"],
            default_method="oauth2",
            oauth2_authorization_url="https://example.com/auth",
            oauth2_token_url="https://example.com/token",
            oauth2_scopes=["read", "write"],
            env_mapping={"apiKey": "API_TOKEN"},
        )
        data = hint.model_dump(by_alias=True)

        assert "defaultMethod" in data
        assert data["defaultMethod"] == "oauth2"
        assert "oauth2AuthorizationUrl" in data
        assert data["oauth2AuthorizationUrl"] == "https://example.com/auth"
        assert "oauth2TokenUrl" in data
        assert data["oauth2TokenUrl"] == "https://example.com/token"
        assert "oauth2Scopes" in data
        assert data["oauth2Scopes"] == ["read", "write"]
        assert "envMapping" in data
        assert data["envMapping"] == {"apiKey": "API_TOKEN"}

    def test_populate_by_snake_case_name(self):
        from app.agents.mcp.models import AuthHint

        hint = AuthHint(default_method="bearer", oauth2_scopes=["admin"])

        assert hint.default_method == "bearer"
        assert hint.oauth2_scopes == ["admin"]

    def test_snake_case_keys_absent_in_aliased_dump(self):
        from app.agents.mcp.models import AuthHint

        data = AuthHint().model_dump(by_alias=True)

        # All keys must be camelCase when by_alias=True
        assert "default_method" not in data
        assert "oauth2_authorization_url" not in data


class TestMCPServerTemplate:
    def test_required_fields(self):
        from app.agents.mcp.models import MCPServerTemplate

        tpl = MCPServerTemplate(
            type_id="test",
            display_name="Test",
            description="A test server",
        )

        assert tpl.type_id == "test"
        assert tpl.display_name == "Test"
        assert tpl.description == "A test server"

    def test_default_values(self):
        from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport

        tpl = MCPServerTemplate(type_id="t", display_name="T", description="d")

        assert tpl.transport == MCPTransport.STDIO
        assert tpl.auth_mode == MCPAuthMode.NONE
        assert tpl.command == ""
        assert tpl.default_args == []
        assert tpl.required_env == []
        assert tpl.optional_env == []
        assert tpl.tags == []
        assert tpl.url == ""
        assert tpl.use_admin_auth is False
        assert tpl.redirect_uri == ""
        assert tpl.icon_path == ""
        assert tpl.documentation_url == ""

    def test_camelcase_serialisation(self):
        from app.agents.mcp.models import MCPServerTemplate

        tpl = MCPServerTemplate(
            type_id="my_server",
            display_name="My Server",
            description="desc",
            required_env=["API_KEY"],
            use_admin_auth=True,
        )
        data = tpl.model_dump(by_alias=True)

        assert "typeId" in data
        assert data["typeId"] == "my_server"
        assert "displayName" in data
        assert data["displayName"] == "My Server"
        assert "requiredEnv" in data
        assert data["requiredEnv"] == ["API_KEY"]
        assert "useAdminAuth" in data
        assert data["useAdminAuth"] is True

    def test_supported_auth_types_list(self):
        from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate

        tpl = MCPServerTemplate(
            type_id="t",
            display_name="T",
            description="d",
            auth_mode=MCPAuthMode.API_TOKEN,
            supported_auth_types=["API_TOKEN"],
        )

        assert tpl.supported_auth_types == ["API_TOKEN"]


class TestMCPServerConfig:
    def test_name_is_required(self):
        from app.agents.mcp.models import MCPServerConfig

        cfg = MCPServerConfig(name="myserver")

        assert cfg.name == "myserver"

    def test_default_values(self):
        from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(name="s")

        assert cfg.transport == MCPTransport.STDIO
        assert cfg.server_type == ""
        assert cfg.command == ""
        assert cfg.args == []
        assert cfg.env == {}
        assert cfg.url == ""
        assert cfg.headers == {}
        assert cfg.auth_mode == MCPAuthMode.NONE

    def test_full_construction(self):
        from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

        cfg = MCPServerConfig(
            name="remote_srv",
            server_type="jira",
            transport=MCPTransport.STREAMABLE_HTTP,
            url="https://mcp.atlassian.com/v1/mcp",
            headers={"Authorization": "Bearer tok"},
            auth_mode=MCPAuthMode.OAUTH,
        )

        assert cfg.name == "remote_srv"
        assert cfg.server_type == "jira"
        assert cfg.transport == MCPTransport.STREAMABLE_HTTP
        assert cfg.url == "https://mcp.atlassian.com/v1/mcp"
        assert cfg.headers["Authorization"] == "Bearer tok"
        assert cfg.auth_mode == MCPAuthMode.OAUTH


class TestMCPToolInfo:
    def test_required_fields(self):
        from app.agents.mcp.models import MCPToolInfo

        info = MCPToolInfo(name="tool", namespaced_name="mcp_srv_tool")

        assert info.name == "tool"
        assert info.namespaced_name == "mcp_srv_tool"

    def test_default_values(self):
        from app.agents.mcp.models import MCPToolInfo

        info = MCPToolInfo(name="t", namespaced_name="mcp_s_t")

        assert info.description == ""
        assert info.input_schema == {}
        assert info.server_name == ""
        assert info.instance_id == ""
        assert info.fixed_params == {}

    def test_with_fixed_params(self):
        from app.agents.mcp.models import MCPToolInfo

        info = MCPToolInfo(
            name="create_issue",
            namespaced_name="mcp_jira_create_issue",
            fixed_params={"cloudId": "abc-123"},
        )

        assert info.fixed_params == {"cloudId": "abc-123"}

    def test_with_full_schema(self):
        from app.agents.mcp.models import MCPToolInfo

        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        info = MCPToolInfo(
            name="search",
            namespaced_name="mcp_exa_search",
            description="Search the web",
            input_schema=schema,
            server_name="exa",
            instance_id="inst-uuid-42",
        )

        assert info.input_schema == schema
        assert info.server_name == "exa"
        assert info.instance_id == "inst-uuid-42"


class TestOAuthTokens:
    def test_default_values(self):
        from app.agents.mcp.models import OAuthTokens

        tokens = OAuthTokens()

        assert tokens.access_token == ""
        assert tokens.refresh_token == ""
        assert tokens.token_type == "Bearer"
        assert tokens.expires_at == 0
        assert tokens.scope == ""

    def test_with_values(self):
        from app.agents.mcp.models import OAuthTokens

        tokens = OAuthTokens(
            access_token="at-abc",
            refresh_token="rt-xyz",
            token_type="Bearer",
            expires_at=9999999,
            scope="read write",
        )

        assert tokens.access_token == "at-abc"
        assert tokens.refresh_token == "rt-xyz"
        assert tokens.expires_at == 9999999
        assert tokens.scope == "read write"


class TestMCPServerInstanceConfig:
    def test_default_values(self):
        from app.agents.mcp.models import (
            MCPAuthMode,
            MCPServerInstanceConfig,
            MCPTransport,
        )

        cfg = MCPServerInstanceConfig()

        assert cfg.instance_id == ""
        assert cfg.instance_name == ""
        assert cfg.transport == MCPTransport.STDIO
        assert cfg.auth_mode == MCPAuthMode.NONE
        assert cfg.enabled is True
        assert cfg.use_admin_auth is False
        assert cfg.env == {}
        assert cfg.headers == {}
        assert cfg.args == []
        assert cfg.org_id == ""
        assert cfg.created_by == ""

    def test_full_construction(self):
        from app.agents.mcp.models import (
            MCPAuthMode,
            MCPServerInstanceConfig,
            MCPTransport,
        )

        cfg = MCPServerInstanceConfig(
            instance_id="uuid-1",
            instance_name="My Jira",
            server_type="jira",
            transport=MCPTransport.STREAMABLE_HTTP,
            url="https://mcp.atlassian.com/v1/mcp",
            auth_mode=MCPAuthMode.OAUTH,
            enabled=True,
            use_admin_auth=False,
            org_id="org-42",
            created_by="user-7",
        )

        assert cfg.instance_id == "uuid-1"
        assert cfg.server_type == "jira"
        assert cfg.transport == MCPTransport.STREAMABLE_HTTP
        assert cfg.auth_mode == MCPAuthMode.OAUTH
        assert cfg.org_id == "org-42"
