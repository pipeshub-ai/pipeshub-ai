"""Intercom MCP server template — official remote server (Streamable HTTP), OAuth + DCR."""
from app.agents.mcp.mcp_server_decorator import mcp_server
from app.agents.mcp.models import AuthHint, MCPAuthMode, MCPServerTemplate, MCPTransport


@mcp_server(
    MCPServerTemplate(
        type_id="intercom",
        display_name="Intercom",
        description="Intercom's official hosted MCP server.",
        icon="/icons/connectors/intercom.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH],
        default_url="https://mcp.intercom.com/mcp",
        # Live discovery source: https://mcp.intercom.com/.well-known/oauth-protected-resource/mcp
        authorization_url="https://mcp.intercom.com/authorize",
        token_url="https://mcp.intercom.com/token",
        default_scopes=[],
        supports_dcr=True,
        documentation_url="https://developers.intercom.com/docs/guides/mcp",
        auth_hint=AuthHint(
            label="Intercom OAuth",
            help_text="Sign in with your US Intercom workspace. (EU workspaces require regional endpoint adjustment).",
        ),
    )
)
class IntercomMCPServer:
    """Marker class — see `mcp_template` for the registered catalog entry."""
